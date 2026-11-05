from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any, Callable, Coroutine

from .config import settings
from .db import Database
from .ssh_gateway import (
    DEFAULT_GPU_DGD_YAML,
    DEFAULT_LMCACHE_YAML,
    DEFAULT_MOONCAKE_YAML,
    SSHGateway,
)

BroadcastCallback = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]

REQUIRED_ROLES: list[dict[str, str]] = [
    {
        "code": "ROLE_ORCH",
        "name": "총괄 · 벤치마크 진행",
        "description": "논의 취합, 시나리오 제안서 작성, 벤치마크 구동 및 최종 비교 리포트 발행",
    },
    {
        "code": "ROLE_GPU",
        "name": "GPU 서버 점검·제어",
        "description": "GPU 토폴로지(TP4 등), VRAM/클럭 및 K8s GPU 파드 상태 점검",
    },
    {
        "code": "ROLE_REMOTE",
        "name": "리모트 · 메모리 서버 제어",
        "description": "Mooncake Store 데몬, CXL Memory(NUMA/DAX)·DRAM·NVMe SSD 및 RDMA 점검",
    },
    {
        "code": "ROLE_CONFIG",
        "name": "DGD · YAML 설정 구성",
        "description": "K8s DynamoGraphDeployment 매니페스트 및 LMCache/Mooncake YAML 작성·배포",
    },
]

ROLE_LABELS: dict[str, str] = {r["code"]: r["name"] for r in REQUIRED_ROLES}


class MeetingRoomOrchestrator:
    """Manages agent standby presence, discussion-driven role negotiation, turn routing, and DAG execution."""

    def __init__(
        self,
        db: Database,
        ssh_gateway: SSHGateway,
        broadcast_fn: BroadcastCallback | None = None,
        simulation_mode: bool = False,
    ) -> None:
        self.db = db
        self.ssh_gateway = ssh_gateway
        self._broadcast_fn = broadcast_fn
        self.simulation_mode = simulation_mode

        self._inboxes: dict[str, deque[dict[str, Any]]] = {
            f"agent_{x}": deque() for x in ("a", "b", "c", "d")
        }
        self._events: dict[str, asyncio.Event] = {
            f"agent_{x}": asyncio.Event() for x in ("a", "b", "c", "d")
        }
        self._active_waiters: dict[str, int] = {}
        self._last_heartbeat: dict[str, float] = {}
        self._external_heartbeat: dict[str, float] = {}

        # Per-room discussion state: queue of (agent_id_or_sentinel, turn_kind, extra_instruction)
        self._discussion_queues: dict[str, deque[tuple[str, str, str]]] = {}
        self._agent_speak_counts: dict[str, dict[str, int]] = {}
        self._room_role_maps: dict[str, dict[str, str]] = {}
        # Track virtual simulated campaigns in /sim when no external MCP client is connected
        self._demo_campaigns: set[str] = set()

    def set_broadcaster(self, fn: BroadcastCallback) -> None:
        self._broadcast_fn = fn

    async def _emit(self, event: dict[str, Any]) -> None:
        if self._broadcast_fn:
            await self._broadcast_fn(event)

    # --- Presence & Discussion-Driven Role Assignment ---

    def touch_agent(
        self,
        agent_id: str,
        client_type: str | None = None,
        working: bool = False,
        external: bool = True,
    ) -> None:
        if agent_id not in self._inboxes:
            self._inboxes[agent_id] = deque()
            self._events[agent_id] = asyncio.Event()
        now = time.monotonic()
        self._last_heartbeat[agent_id] = now
        if external:
            self._external_heartbeat[agent_id] = now
        status = "WORKING" if working else "ONLINE_IDLE"
        self.db.update_agent_presence(agent_id, status=status, client_type=client_type)

    def get_online_agent_ids(self, external_only: bool = False) -> list[str]:
        now = time.monotonic()
        online: list[str] = []
        for agent in self.db.list_agents():
            aid = agent["id"]
            waiting = self._active_waiters.get(aid, 0) > 0
            hb_map = self._external_heartbeat if external_only else self._last_heartbeat
            recent = (now - hb_map.get(aid, -99999.0)) <= settings.presence_grace_sec
            if waiting or recent:
                online.append(aid)
            elif not external_only and agent["status"] != "OFFLINE":
                self.db.update_agent_presence(aid, status="OFFLINE", assigned_roles=[])
        return online

    async def set_agreed_roles(
        self,
        room_id: str,
        role_map: dict[str, str],
        partial: bool = False,
    ) -> dict[str, str]:
        """Persist roles decided by agents during discussion and broadcast to Web UI."""
        all_agents = {a["id"]: a for a in self.db.list_agents()}
        current_map = dict(self._room_role_maps.get(room_id, {})) if partial else {}

        for r_code, aid in role_map.items():
            norm_code = r_code.strip().upper()
            norm_aid = str(aid).strip().lower()
            if norm_code in ROLE_LABELS and norm_aid in all_agents:
                current_map[norm_code] = norm_aid

        self._room_role_maps[room_id] = current_map

        agent_to_roles: dict[str, list[str]] = {aid: [] for aid in all_agents}
        for r_info in REQUIRED_ROLES:
            rcode = r_info["code"]
            holder = current_map.get(rcode)
            if holder in agent_to_roles:
                agent_to_roles[holder].append(rcode)

        for aid, roles in agent_to_roles.items():
            self.db.set_agent_assigned_roles(aid, roles)

        # If FINALIZE_SCENARIO was already enqueued before ROLE_ORCH was registered, re-route it to the agreed ROLE_ORCH
        new_orch = current_map.get("ROLE_ORCH")
        if new_orch and new_orch in self._inboxes:
            for other_aid, inbox in list(self._inboxes.items()):
                if other_aid == new_orch or not inbox:
                    continue
                kept: list[dict[str, Any]] = []
                moved: list[dict[str, Any]] = []
                for turn in inbox:
                    if turn.get("room_id") == room_id and turn.get("turn_type") == "FINALIZE_SCENARIO":
                        turn["current_agreed_roles"] = dict(current_map)
                        orch_obj = self.db.get_agent(new_orch)
                        turn["your_assigned_roles"] = orch_obj["assigned_roles"] if orch_obj else []
                        moved.append(turn)
                    else:
                        kept.append(turn)
                if moved:
                    inbox.clear()
                    inbox.extend(kept)
                    if not inbox:
                        self._events[other_aid].clear()
                    for m_turn in moved:
                        self._enqueue_turn(new_orch, m_turn)

        await self._emit({"type": "agents_updated", "agents": self.db.list_agents()})
        return current_map

    def _infer_role_map_from_proposal(
        self,
        room_id: str,
        lead_agent_id: str,
        explicit_role_map: dict[str, str] | None,
        role_assignments: dict[str, Any],
        steps_dag: list[dict[str, Any]],
    ) -> dict[str, str]:
        all_agent_ids = [a["id"] for a in self.db.list_agents()]
        merged: dict[str, str] = dict(self._room_role_maps.get(room_id, {}))

        if explicit_role_map:
            for k, v in explicit_role_map.items():
                kc = str(k).strip().upper()
                vc = str(v).strip().lower()
                if kc in ROLE_LABELS and vc in all_agent_ids:
                    merged[kc] = vc

        for s in steps_dag:
            rc = str(s.get("role_code") or "").strip().upper()
            aid = str(s.get("assigned_agent_id") or "").strip().lower()
            if rc in ROLE_LABELS and aid in all_agent_ids and rc not in merged:
                merged[rc] = aid

        # Also inspect human-readable role_assignments text if needed
        keyword_to_role = [
            ("ROLE_GPU", ("gpu", "tp4", "파드")),
            ("ROLE_REMOTE", ("remote", "리모트", "mooncake", "cxl", "메모리")),
            ("ROLE_CONFIG", ("dgd", "yaml", "형상", "매니페스트", "설정")),
            ("ROLE_ORCH", ("총괄", "벤치마크", "리포트", "lead", "orch")),
        ]
        for k_text, v_text in (role_assignments or {}).items():
            combined = f"{k_text} {v_text}".lower()
            matched_aid = next(
                (
                    aid
                    for aid in all_agent_ids
                    if aid in combined or aid.replace("_", " ") in combined
                ),
                None,
            )
            if matched_aid:
                for rcode, kw_list in keyword_to_role:
                    if rcode not in merged and any(kw in combined for kw in kw_list):
                        merged[rcode] = matched_aid

        if "ROLE_ORCH" not in merged and lead_agent_id in all_agent_ids:
            merged["ROLE_ORCH"] = lead_agent_id

        # Ensure every required role has an owner among active participants
        participants = list(
            dict.fromkeys(
                list(merged.values())
                + [s.get("assigned_agent_id", "") for s in steps_dag if s.get("assigned_agent_id") in all_agent_ids]
                + self.get_online_agent_ids()
                + [lead_agent_id]
            )
        )
        participants = [p for p in participants if p in all_agent_ids] or [lead_agent_id]

        counts = {p: 0 for p in participants}
        for holder in merged.values():
            if holder in counts:
                counts[holder] += 1

        for r_info in REQUIRED_ROLES:
            rcode = r_info["code"]
            if rcode not in merged:
                chosen = min(participants, key=lambda pid: (counts.get(pid, 0), participants.index(pid)))
                merged[rcode] = chosen
                counts[chosen] = counts.get(chosen, 0) + 1

        return merged

    def _enqueue_turn(self, agent_id: str, turn_payload: dict[str, Any]) -> None:
        if agent_id not in self._inboxes:
            self._inboxes[agent_id] = deque()
            self._events[agent_id] = asyncio.Event()
        self._inboxes[agent_id].append(turn_payload)
        self._events[agent_id].set()

    async def wait_for_turn(
        self,
        agent_id: str,
        room_id: str | None = None,
        client_type: str | None = None,
        max_wait_sec: int | None = None,
    ) -> dict[str, Any]:
        """Blocking long-poll called by Hermes / Claude Code in Standby Mode."""
        wait_sec = min(
            max(1, int(max_wait_sec or settings.mcp_default_wait_sec)),
            settings.mcp_max_wait_sec,
        )
        if agent_id not in self._inboxes:
            self._inboxes[agent_id] = deque()
            self._events[agent_id] = asyncio.Event()

        self._active_waiters[agent_id] = self._active_waiters.get(agent_id, 0) + 1
        self.touch_agent(agent_id, client_type=client_type, working=False, external=True)
        await self._emit({"type": "agents_updated", "agents": self.db.list_agents()})

        try:
            deadline = time.monotonic() + wait_sec
            while True:
                inbox = self._inboxes[agent_id]
                matched_idx: int | None = None
                for idx, item in enumerate(inbox):
                    if room_id in (None, "", "*") or item.get("room_id") == room_id:
                        matched_idx = idx
                        break

                if matched_idx is not None:
                    items = list(inbox)
                    turn = items.pop(matched_idx)
                    inbox.clear()
                    inbox.extend(items)
                    if not inbox:
                        self._events[agent_id].clear()
                    self.touch_agent(agent_id, client_type=client_type, working=True, external=True)
                    await self._emit({"type": "agents_updated", "agents": self.db.list_agents()})
                    return turn

                self._events[agent_id].clear()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.touch_agent(agent_id, client_type=client_type, working=False, external=True)
                    return {
                        "status": "HEARTBEAT_IDLE",
                        "agent_id": agent_id,
                        "instruction": (
                            "현재 배정된 새 발언 차례나 실행 작업이 없습니다. "
                            "불필요한 텍스트 응답을 길게 출력하지 말고 즉시 wait_for_turn을 다시 호출해 대기를 유지하세요."
                        ),
                    }

                try:
                    await asyncio.wait_for(self._events[agent_id].wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    self.touch_agent(agent_id, client_type=client_type, working=False, external=True)
                    return {
                        "status": "HEARTBEAT_IDLE",
                        "agent_id": agent_id,
                        "instruction": (
                            "대기 타임아웃 주기 도달 (유휴 상태). 즉시 wait_for_turn을 다시 호출해 미팅룸 대기를 계속하세요."
                        ),
                    }
        finally:
            self._active_waiters[agent_id] = max(0, self._active_waiters.get(agent_id, 1) - 1)
            now = time.monotonic()
            self._last_heartbeat[agent_id] = now
            self._external_heartbeat[agent_id] = now

    # --- User & Agent Messaging + Discussion-Driven Workflow ---

    async def handle_user_message(
        self,
        room_id: str,
        user_id: str,
        content: str,
    ) -> dict[str, Any]:
        room = self.db.get_room(room_id)
        if not room:
            raise ValueError(f"채팅방을 찾을 수 없습니다: {room_id}")

        user = self.db.get_user(user_id) or {"id": user_id, "name": user_id}
        msg = self.db.add_message(
            room_id=room_id,
            sender_type="user",
            sender_id=user["id"],
            sender_name=user["name"],
            role_badge="USER",
            msg_kind="chat",
            content=content,
        )
        await self._emit({"type": "message_created", "message": msg})

        if room["room_type"] == "direct" and room.get("target_agent_id"):
            target_aid = room["target_agent_id"]
            ext_online = self.get_online_agent_ids(external_only=True)
            if self.simulation_mode and target_aid not in ext_online:
                asyncio.create_task(self._run_simulated_direct_reply(room_id, target_aid, content))
                return {"message": msg, "room": self.db.get_room(room_id)}

            self._enqueue_turn(
                target_aid,
                {
                    "status": "YOUR_TURN",
                    "turn_type": "DIRECT_MESSAGE",
                    "room_id": room_id,
                    "room_name": room["name"],
                    "user_message": content,
                    "required_roles": REQUIRED_ROLES,
                    "recent_messages": self.db.list_messages(room_id, limit=15),
                    "instruction": (
                        f"사용자({user['name']})가 1:1 대화방에서 요청했습니다. "
                        "필요 시 서버 스냅샷/설정을 조회하거나 점검한 뒤 post_message로 답변하고 다시 wait_for_turn을 호출하세요."
                    ),
                },
            )
            return {"message": msg, "room": self.db.get_room(room_id)}

        # Group Meeting Room -> Reset role assignments to "unassigned" so agents decide roles through discussion!
        self._room_role_maps.pop(room_id, None)
        self.db.clear_all_agent_roles()
        online_ids = self.get_online_agent_ids(external_only=self.simulation_mode)
        await self._emit({"type": "agents_updated", "agents": self.db.list_agents()})

        self.db.update_room_workflow(
            room_id=room_id,
            workflow_state="DISCUSSION",
            current_topic=content,
            discussion_round=1,
        )
        updated_room = self.db.get_room(room_id)
        await self._emit({"type": "room_updated", "room": updated_room})

        if not online_ids:
            if self.simulation_mode:
                # In /sim functional test mode, when no external MCP client is connected,
                # virtual peer agents automatically run the full discussion & role division!
                for aid in ("agent_a", "agent_b", "agent_c", "agent_d"):
                    self.touch_agent(aid, client_type="Virtual Sim Agent", working=False, external=False)
                await self._emit({"type": "agents_updated", "agents": self.db.list_agents()})
                asyncio.create_task(self._run_simulated_discussion(room_id, content))
                return {"message": msg, "room": updated_room}

            warn_msg = self.db.add_message(
                room_id=room_id,
                sender_type="system",
                sender_id="system",
                sender_name="미팅룸 코디네이터",
                role_badge="SYSTEM",
                msg_kind="system",
                content=(
                    "현재 대기(Online) 중인 에이전트가 없습니다. "
                    "우측 상단 `[MCP 에이전트 연결 가이드]`를 참고해 각 팀원의 Claude Code 또는 Hermes에서 `wait_for_turn` 대기 모드를 시작해 주세요."
                ),
            )
            await self._emit({"type": "message_created", "message": warn_msg})
            return {"message": msg, "room": updated_room}

        # Order online agents neutrally (starting with sender's paired agent if online)
        paired_aid = user_id.replace("member_", "agent_")
        ordered_online = (
            [paired_aid] + [x for x in online_ids if x != paired_aid]
            if paired_aid in online_ids
            else list(online_ids)
        )
        self._build_and_start_discussion_queue(room_id, content, ordered_online)
        return {"message": msg, "room": updated_room}

    def _build_and_start_discussion_queue(
        self,
        room_id: str,
        topic: str,
        online_ids: list[str],
    ) -> None:
        q: deque[tuple[str, str, str]] = deque()
        self._agent_speak_counts[room_id] = {aid: 0 for aid in online_ids}
        roles_summary = ", ".join(f"{r['code']}({r['name']})" for r in REQUIRED_ROLES)

        if len(online_ids) == 1:
            solo_id = online_ids[0]
            q.append(
                (
                    solo_id,
                    "SOLO_DISCUSS_AND_PROPOSE",
                    (
                        f"현재 온라인 에이전트가 귀하({solo_id}) 1명뿐이므로 분담 대상 4개 역할 [{roles_summary}]을 모두 맡아 진행합니다. "
                        "현재 서버 환경 스냅샷을 확인한 뒤 채팅방에 요약 의견을 남기고, `set_agreed_roles` 또는 `submit_scenario_proposal`을 호출해 승인 카드를 제출하세요."
                    ),
                )
            )
        else:
            opener_id = online_ids[0]
            peers = online_ids[1:]
            q.append(
                (
                    opener_id,
                    "DISCUSSION_OPENING_AND_ROLE_PROPOSAL",
                    (
                        f"새 주제가 발제되었으며 아직 에이전트별 역할이 정해지지 않았습니다. "
                        f"현재 온라인 동료({', '.join(online_ids)})들이 나눠 맡아야 할 역할 목록은 [{roles_summary}] 입니다. "
                        "주제를 분석해 본인이 희망하는 역할(필요 시 `set_agreed_roles(partial=true)` 선호출 가능)과 동료들에게 제안할 역할 분담안, "
                        "그리고 본인 영역의 초기 검토 의견을 `post_message`로 발언하세요. "
                        "(`post_message` 호출 시 즉시 다음 동료에게 차례가 넘어가므로, 역할 등록 시에는 `post_message` 호출 전에 `set_agreed_roles`를 먼저 호출하세요.)"
                    ),
                )
            )
            for idx, peer_id in enumerate(peers):
                is_last_peer = idx == len(peers) - 1
                extra_tail = (
                    " 귀하가 이번 논의 라운드의 마지막 발언자이므로, 반드시 `post_message`를 호출하기 **전에** 먼저 `set_agreed_roles` 툴을 호출하여 4개 역할 배정표(특히 `ROLE_ORCH` 담당자)를 확정한 뒤 `post_message`로 발언을 마무리하세요."
                    if is_last_peer
                    else " 본인 담당 역할을 선등록할 경우 `post_message` 호출 전에 `set_agreed_roles(partial=true)`를 먼저 호출하세요."
                )
                q.append(
                    (
                        peer_id,
                        "DISCUSSION_PEER_ROLE_AND_OPINION",
                        (
                            f"앞선 동료들의 발언을 확인하고, 분담 대상 역할 목록 [{roles_summary}] 중 빠진 역할이 없도록 본인이 맡을 역할을 합의·확정하세요. "
                            f"필요 시 `get_environment_snapshot`으로 서버 상태를 확인한 뒤 담당 영역의 구체적 설정 방안을 `post_message`로 남기세요.{extra_tail}"
                        ),
                    )
                )
            q.append(
                (
                    "__AGREED_ORCH__",
                    "FINALIZE_SCENARIO",
                    (
                        "온라인 에이전트 간 역할 분담 및 기술 논의가 완료되었습니다! "
                        "합의된 역할 분담(`role_map` 및 `role_assignments`)과 단계별 실행 DAG(`steps_dag`), 변경 예정 설정 요약을 담아 "
                        "`submit_scenario_proposal` 툴을 호출하여 사용자 승인 카드를 제출하세요."
                    ),
                )
            )

        self._discussion_queues[room_id] = q
        self._pop_and_dispatch_next_discussion_turn(room_id, topic)

    def _pop_and_dispatch_next_discussion_turn(self, room_id: str, topic: str | None = None) -> None:
        q = self._discussion_queues.get(room_id)
        if not q:
            return
        room = self.db.get_room(room_id)
        if not room or room["workflow_state"] != "DISCUSSION":
            return

        next_agent_id, turn_kind, instruction = q.popleft()
        if next_agent_id == "__AGREED_ORCH__":
            agreed_map = self._room_role_maps.get(room_id, {})
            online_now = self.get_online_agent_ids(external_only=self.simulation_mode) or ["agent_a"]
            next_agent_id = agreed_map.get("ROLE_ORCH") or online_now[0]

        agent_obj = self.db.get_agent(next_agent_id)
        self._enqueue_turn(
            next_agent_id,
            {
                "status": "YOUR_TURN",
                "turn_type": turn_kind,
                "room_id": room_id,
                "room_name": room["name"],
                "workflow_state": room["workflow_state"],
                "topic": topic or room.get("current_topic") or "",
                "required_roles": REQUIRED_ROLES,
                "current_agreed_roles": self._room_role_maps.get(room_id, {}),
                "your_assigned_roles": agent_obj["assigned_roles"] if agent_obj else [],
                "online_agents": self.db.list_agents(),
                "recent_messages": self.db.list_messages(room_id, limit=20),
                "instruction": instruction,
            },
        )

    async def handle_agent_message(
        self,
        agent_id: str,
        room_id: str,
        content: str,
        mention_agents: list[str] | None = None,
        external: bool = True,
    ) -> dict[str, Any]:
        self.touch_agent(agent_id, working=True, external=external)
        agent = self.db.get_agent(agent_id) or {
            "id": agent_id,
            "name": agent_id,
            "assigned_roles": [],
        }
        roles = agent.get("assigned_roles", [])
        badge = " + ".join(ROLE_LABELS.get(r, r) for r in roles) if roles else "역할 논의 중"
        msg = self.db.add_message(
            room_id=room_id,
            sender_type="agent",
            sender_id=agent_id,
            sender_name=agent["name"],
            role_badge=badge,
            msg_kind="chat",
            content=content,
            metadata={"mentions": mention_agents or []},
        )
        await self._emit({"type": "message_created", "message": msg})

        room = self.db.get_room(room_id)
        if external and room and room["workflow_state"] == "DISCUSSION":
            counts = self._agent_speak_counts.setdefault(room_id, {})
            counts[agent_id] = counts.get(agent_id, 0) + 1

            q = self._discussion_queues.setdefault(room_id, deque())
            online_set = set(self.get_online_agent_ids(external_only=self.simulation_mode))
            for m_id in reversed(mention_agents or []):
                if (
                    m_id in online_set
                    and m_id != agent_id
                    and counts.get(m_id, 0) < settings.max_discussion_rounds
                    and not any(item[0] == m_id for item in q)
                ):
                    q.appendleft(
                        (
                            m_id,
                            "MENTION_FOLLOWUP",
                            f"{agent['name']}님이 귀하를 멘션했습니다. 역할 합의 또는 기술 검토 보충 답변을 `post_message`로 남기세요.",
                        )
                    )

            self._pop_and_dispatch_next_discussion_turn(room_id)

        return msg

    async def submit_scenario_proposal(
        self,
        agent_id: str,
        room_id: str,
        title: str,
        role_assignments: dict[str, Any],
        steps_dag: list[dict[str, Any]],
        target_configs_preview: dict[str, Any],
        summary_comment: str = "",
        role_map: dict[str, str] | None = None,
        external: bool = True,
    ) -> dict[str, Any]:
        self.touch_agent(agent_id, working=True, external=external)
        room = self.db.get_room(room_id)
        if not room:
            raise ValueError(f"채팅방을 찾을 수 없습니다: {room_id}")

        inferred_map = self._infer_role_map_from_proposal(
            room_id=room_id,
            lead_agent_id=agent_id,
            explicit_role_map=role_map,
            role_assignments=role_assignments,
            steps_dag=steps_dag,
        )
        await self.set_agreed_roles(room_id=room_id, role_map=inferred_map, partial=False)

        campaign = self.db.create_campaign(
            room_id=room_id,
            title=title,
            topic_prompt=room.get("current_topic") or title,
            lead_agent_id=inferred_map.get("ROLE_ORCH", agent_id),
            role_assignments=role_assignments,
            steps_dag=steps_dag,
            target_configs_preview=target_configs_preview,
        )
        self._discussion_queues.pop(room_id, None)
        self.db.update_room_workflow(
            room_id=room_id,
            workflow_state="APPROVAL_PENDING",
            active_campaign_id=campaign["id"],
        )

        agent = self.db.get_agent(agent_id) or {"name": agent_id, "assigned_roles": ["ROLE_ORCH"]}
        badge = " + ".join(ROLE_LABELS.get(r, r) for r in agent.get("assigned_roles", [])) or "시나리오 제안"
        msg = self.db.add_message(
            room_id=room_id,
            sender_type="agent",
            sender_id=agent_id,
            sender_name=agent["name"],
            role_badge=badge,
            msg_kind="scenario_proposal",
            content=summary_comment or f"논의를 통해 결정된 역할 분담 및 시나리오 제안서 **{title}** 작성이 완료되었습니다. 검토 후 승인해 주시면 실행을 시작합니다.",
            metadata={"campaign_id": campaign["id"], "campaign": campaign},
        )
        await self._emit({"type": "campaign_updated", "campaign": campaign})
        await self._emit({"type": "room_updated", "room": self.db.get_room(room_id)})
        await self._emit({"type": "message_created", "message": msg})
        return campaign

    async def approve_campaign(self, campaign_id: str, user_id: str) -> dict[str, Any]:
        campaign = self.db.get_campaign(campaign_id)
        if not campaign:
            raise ValueError(f"시나리오를 찾을 수 없습니다: {campaign_id}")
        if campaign["status"] != "APPROVAL_PENDING":
            return campaign

        user = self.db.get_user(user_id) or {"id": user_id, "name": user_id}
        campaign = self.db.update_campaign(
            campaign_id=campaign_id,
            status="EXECUTING",
            approved_by=user["name"],
        )
        assert campaign is not None
        room_id = campaign["room_id"]
        self.db.update_room_workflow(room_id=room_id, workflow_state="EXECUTION")

        sys_msg = self.db.add_message(
            room_id=room_id,
            sender_type="system",
            sender_id=user["id"],
            sender_name=user["name"],
            role_badge="APPROVED",
            msg_kind="system",
            content=f"✅ {user['name']}님이 시나리오 '{campaign['title']}'을(를) 승인했습니다. 논의에서 확정된 역할 분담 및 DAG 순서에 따라 실행을 시작합니다.",
            metadata={"campaign_id": campaign_id},
        )
        await self._emit({"type": "message_created", "message": sys_msg})
        await self._emit({"type": "campaign_updated", "campaign": campaign})
        await self._emit({"type": "room_updated", "room": self.db.get_room(room_id)})

        if campaign_id in self._demo_campaigns:
            asyncio.create_task(self._execute_simulated_campaign(campaign_id))
        else:
            await self._dispatch_ready_dag_steps(campaign_id)
        return campaign

    async def request_campaign_changes(
        self, campaign_id: str, user_id: str, feedback: str
    ) -> dict[str, Any]:
        campaign = self.db.get_campaign(campaign_id)
        if not campaign:
            raise ValueError(f"시나리오를 찾을 수 없습니다: {campaign_id}")

        user = self.db.get_user(user_id) or {"id": user_id, "name": user_id}
        updated = self.db.update_campaign(
            campaign_id=campaign_id,
            status="REVISION_REQUESTED",
            feedback_note=feedback,
        )
        assert updated is not None
        room_id = updated["room_id"]
        self.db.update_room_workflow(room_id=room_id, workflow_state="DISCUSSION")

        msg = self.db.add_message(
            room_id=room_id,
            sender_type="user",
            sender_id=user["id"],
            sender_name=user["name"],
            role_badge="FEEDBACK",
            msg_kind="chat",
            content=f"✏️ [시나리오 수정 요청] {feedback}",
            metadata={"campaign_id": campaign_id},
        )
        await self._emit({"type": "message_created", "message": msg})
        await self._emit({"type": "campaign_updated", "campaign": updated})
        await self._emit({"type": "room_updated", "room": self.db.get_room(room_id)})

        if campaign_id in self._demo_campaigns:
            asyncio.create_task(self._revise_simulated_campaign(updated, feedback))
        else:
            online_ids = self.get_online_agent_ids()
            agreed_map = self._room_role_maps.get(room_id, {})
            lead_id = agreed_map.get("ROLE_ORCH") or updated["lead_agent_id"]
            if online_ids and lead_id not in online_ids:
                lead_id = online_ids[0]
            self._enqueue_turn(
                lead_id,
                {
                    "status": "YOUR_TURN",
                    "turn_type": "SCENARIO_REVISION",
                    "room_id": room_id,
                    "campaign_id": campaign_id,
                    "feedback": feedback,
                    "required_roles": REQUIRED_ROLES,
                    "previous_campaign": updated,
                    "instruction": (
                        f"사용자가 시나리오 수정을 요청했습니다: '{feedback}'. "
                        "요청 사항을 반영해 `submit_scenario_proposal`로 수정된 시나리오 제안서를 다시 제출하세요."
                    ),
                },
            )
        return updated

    async def _dispatch_ready_dag_steps(self, campaign_id: str) -> None:
        campaign = self.db.get_campaign(campaign_id)
        if not campaign or campaign["status"] != "EXECUTING":
            return

        room_id = campaign["room_id"]
        steps = campaign["steps_dag"]
        completed_ids = {s["step_id"] for s in steps if s["status"] == "COMPLETED"}
        online_ids = self.get_online_agent_ids()
        agreed_map = self._room_role_maps.get(room_id, {})

        newly_running: list[dict[str, Any]] = []
        for s in steps:
            if s["status"] == "PENDING" and all(dep in completed_ids for dep in s.get("depends_on", [])):
                s["status"] = "RUNNING"
                target_agent = s["assigned_agent_id"]
                if online_ids and target_agent not in online_ids:
                    fallback = agreed_map.get("ROLE_ORCH")
                    if not fallback or fallback not in online_ids:
                        fallback = online_ids[0]
                    s["assigned_agent_id"] = fallback
                newly_running.append(s)

        if newly_running:
            updated = self.db.update_campaign(campaign_id=campaign_id, steps_dag=steps)
            await self._emit({"type": "campaign_updated", "campaign": updated})
            for s in newly_running:
                self._enqueue_turn(
                    s["assigned_agent_id"],
                    {
                        "status": "YOUR_TURN",
                        "turn_type": "EXECUTE_DAG_STEP",
                        "room_id": room_id,
                        "campaign_id": campaign_id,
                        "step": s,
                        "campaign_title": campaign["title"],
                        "target_configs_preview": campaign["target_configs_preview"],
                        "instruction": (
                            f"시나리오 실행 단계 [{s['step_id']}: {s['title']}] 차례입니다. "
                            "미팅룸의 중앙 SSH/설정/벤치마크 MCP 툴(`ssh_exec`, `update_server_config`, `run_benchmark_step`)을 사용해 작업을 수행하고, "
                            "진행 결과를 채팅방에 공유한 뒤 반드시 `complete_scenario_step`을 호출하세요."
                        ),
                    },
                )
            return

        if steps and all(s["status"] == "COMPLETED" for s in steps):
            self.db.update_room_workflow(room_id=room_id, workflow_state="REPORTING")
            await self._emit({"type": "room_updated", "room": self.db.get_room(room_id)})

            lead_id = agreed_map.get("ROLE_ORCH") or campaign["lead_agent_id"]
            if online_ids and lead_id not in online_ids:
                lead_id = online_ids[0]
            runs = self.db.list_benchmark_runs(campaign_id=campaign_id)
            self._enqueue_turn(
                lead_id,
                {
                    "status": "YOUR_TURN",
                    "turn_type": "PUBLISH_FINAL_REPORT",
                    "room_id": room_id,
                    "campaign_id": campaign_id,
                    "benchmark_runs": runs,
                    "instruction": (
                        "모든 실행 스텝이 완료되었습니다! `publish_benchmark_report` 툴을 호출하여 "
                        "수집된 정량 지표들과 아키텍처 비교 분석 마크다운 리포트를 최종 발행하세요."
                    ),
                },
            )

    async def complete_scenario_step(
        self,
        agent_id: str,
        campaign_id: str,
        step_id: str,
        result_note: str,
    ) -> dict[str, Any]:
        self.touch_agent(agent_id, working=True)
        campaign = self.db.get_campaign(campaign_id)
        if not campaign:
            raise ValueError(f"시나리오를 찾을 수 없습니다: {campaign_id}")

        steps = campaign["steps_dag"]
        matched = False
        for s in steps:
            if s["step_id"] == step_id:
                s["status"] = "COMPLETED"
                s["result_note"] = result_note
                matched = True
                break
        if not matched:
            raise ValueError(f"스텝 ID를 찾을 수 없습니다: {step_id}")

        updated = self.db.update_campaign(campaign_id=campaign_id, steps_dag=steps)
        assert updated is not None
        await self._emit({"type": "campaign_updated", "campaign": updated})
        await self._dispatch_ready_dag_steps(campaign_id)
        return updated

    async def publish_report(
        self,
        agent_id: str,
        campaign_id: str,
        title: str,
        summary_markdown: str,
        comparison_highlights: dict[str, Any] | None = None,
        external: bool = True,
    ) -> dict[str, Any]:
        self.touch_agent(agent_id, working=True, external=external)
        campaign = self.db.get_campaign(campaign_id)
        if not campaign:
            raise ValueError(f"시나리오를 찾을 수 없습니다: {campaign_id}")

        runs = self.db.list_benchmark_runs(campaign_id=campaign_id)
        commits = [
            c for c in self.db.list_config_commits(limit=50) if c.get("campaign_id") == campaign_id
        ]
        comparison_payload = {
            "highlights": comparison_highlights or {},
            "run_count": len(runs),
            "commit_hashes": [c["git_hash"] for c in commits],
        }
        report = self.db.upsert_report(
            campaign_id=campaign_id,
            room_id=campaign["room_id"],
            title=title,
            author_agent_id=agent_id,
            summary_markdown=summary_markdown,
            comparison=comparison_payload,
        )
        updated_camp = self.db.update_campaign(
            campaign_id=campaign_id, status="COMPLETED", completed=True
        )
        self.db.update_room_workflow(room_id=campaign["room_id"], workflow_state="COMPLETED")

        agent = self.db.get_agent(agent_id) or {"name": agent_id}
        msg = self.db.add_message(
            room_id=campaign["room_id"],
            sender_type="agent",
            sender_id=agent_id,
            sender_name=agent["name"],
            role_badge="REPORT",
            msg_kind="report_published",
            content=f"📊 최종 벤치마크 비교 리포트 **{title}** 생성이 완료되었습니다. `[테스트 히스토리 & 리포트]` 탭에서 상세 차트와 설정 Diff를 확인할 수 있습니다.",
            metadata={"report_id": report["id"], "campaign_id": campaign_id, "report": report},
        )
        self.touch_agent(agent_id, working=False, external=external)
        await self._emit({"type": "agents_updated", "agents": self.db.list_agents()})
        await self._emit({"type": "report_created", "report": report})
        await self._emit({"type": "campaign_updated", "campaign": updated_camp})
        await self._emit({"type": "room_updated", "room": self.db.get_room(campaign["room_id"])})
        await self._emit({"type": "message_created", "message": msg})
        return report

    # --- Virtual Peer Agents for /sim Functional Test Mode ---

    def _pick_sim_servers(self) -> tuple[str, str]:
        servers = self.db.list_servers()
        gpu_srv = next((s["id"] for s in servers if s["server_role"] == "GPU"), None)
        rem_srv = next((s["id"] for s in servers if s["server_role"] == "REMOTE"), None)
        if not gpu_srv and servers:
            gpu_srv = servers[0]["id"]
        if not rem_srv and servers:
            rem_srv = servers[-1]["id"]
        return (gpu_srv or "gpu-node-01", rem_srv or "remote-mem-01")

    async def _run_simulated_direct_reply(
        self, room_id: str, agent_id: str, user_content: str
    ) -> None:
        await asyncio.sleep(0.4)
        self.touch_agent(agent_id, client_type="Virtual Sim Agent", working=True, external=False)
        await self._emit({"type": "agents_updated", "agents": self.db.list_agents()})
        gpu_id, rem_id = self._pick_sim_servers()
        await self.handle_agent_message(
            agent_id=agent_id,
            room_id=room_id,
            content=(
                f"`[시뮬레이션 1:1 응답]` 요청하신 **\"{user_content}\"** 항목을 확인했습니다.\n"
                f"- 등록된 GPU 노드(`{gpu_id}`): 4x H100 TP4 NVSwitch 정상, Dynamo DGD `Ready`\n"
                f"- 등록된 리모트 메모리 노드(`{rem_id}`): CXL 512GB(NUMA 2) / DDR5 DRAM / Gen5 NVMe 및 Mooncake RDMA 포트 활성 상태입니다."
            ),
            external=False,
        )
        self.touch_agent(agent_id, client_type="Virtual Sim Agent", working=False, external=False)
        await self._emit({"type": "agents_updated", "agents": self.db.list_agents()})

    async def start_simulated_meeting(
        self, room_id: str, user_id: str, topic: str | None = None
    ) -> dict[str, Any]:
        prompt = topic or (
            "Llama-3.1-70B를 TP4로 구성하고 LMCache 백엔드를 Mooncake Store로 연동한 뒤, "
            "KV 캐시 오프로딩 데스티네이션을 CXL 메모리 / DRAM / NVMe SSD 3가지로 변경하며 성능 비교 시나리오를 작성해 줘."
        )
        return await self.handle_user_message(room_id=room_id, user_id=user_id, content=prompt)

    async def _run_simulated_discussion(self, room_id: str, prompt: str) -> None:
        gpu_id, rem_id = self._pick_sim_servers()
        steps_dialogue = [
            (
                "agent_a",
                {"ROLE_GPU": "agent_a"},
                (
                    f"발제된 주제를 수행하려면 우리 4명이 **① 총괄·벤치마크, ② GPU 서버 점검, ③ 리모트·메모리 제어, ④ DGD·YAML 설정** 역할을 나눠 맡아야 합니다. "
                    f"우선 제가 **GPU 서버 점검·제어(`ROLE_GPU`)**를 맡아 `{gpu_id}`의 H100 TP4 NVSwitch 토폴로지와 RoCEv2 RDMA 링크 상태를 검증하겠습니다. 나머지 세 분께서 남은 역할을 분담해 주시겠어요?"
                ),
            ),
            (
                "agent_b",
                {"ROLE_REMOTE": "agent_b"},
                (
                    f"좋습니다! 그럼 제가 **리모트·메모리 서버 제어(`ROLE_REMOTE`)** 역할을 맡겠습니다. "
                    f"`{rem_id}`에 구성된 CXL Type-3 512GB(`NUMA Node 2`), DDR5 DRAM 풀, Gen5 NVMe SSD와 Mooncake Store 마스터 데몬을 담당하겠습니다."
                ),
            ),
            (
                "agent_c",
                {"ROLE_CONFIG": "agent_c"},
                (
                    f"저는 **DGD · YAML 설정 구성(`ROLE_CONFIG`)** 역할을 맡겠습니다. "
                    f"`{gpu_id}`의 K8s `DynamoGraphDeployment` TP4 매니페스트와 `lmcache_config.yaml`, `{rem_id}`의 `mooncake_store.yaml`을 티어별로 패치하고 내장 Git에 커밋 이력을 남기겠습니다."
                ),
            ),
            (
                "agent_d",
                {"ROLE_ORCH": "agent_d"},
                (
                    "네, 그럼 제가 **총괄 · 벤치마크 진행(`ROLE_ORCH`)** 역할을 맡아 지금 합의된 역할 분담표를 등록하고, "
                    "`CXL_MEMORY` → `DRAM` → `NVME_SSD` 3종 순차 벤치마크 구동과 최종 비교 리포트 발행을 진행하겠습니다."
                ),
            ),
        ]

        for aid, role_claim, text in steps_dialogue:
            await asyncio.sleep(0.55)
            self.touch_agent(aid, client_type="Virtual Sim Agent", working=True, external=False)
            await self.set_agreed_roles(room_id=room_id, role_map=role_claim, partial=True)
            await self.handle_agent_message(
                agent_id=aid,
                room_id=room_id,
                content=text,
                external=False,
            )
            self.touch_agent(aid, client_type="Virtual Sim Agent", working=False, external=False)
            await self._emit({"type": "agents_updated", "agents": self.db.list_agents()})

        await asyncio.sleep(0.45)
        steps_dag = [
            {
                "step_id": "step_gpu_check",
                "title": "GPU TP4 NVLink 토폴로지 및 RoCE RDMA 링크 검증",
                "assigned_agent_id": "agent_a",
                "role_code": "ROLE_GPU",
                "depends_on": [],
                "server_id": gpu_id,
                "action_summary": "nvidia-smi topo -m 및 K8s Dynamo 파드 헬스체크 (병렬 실행)",
            },
            {
                "step_id": "step_remote_prep",
                "title": "리모트 서버 CXL(/dev/dax2.0)·DRAM·NVMe 풀 및 Mooncake 데몬 점검",
                "assigned_agent_id": "agent_b",
                "role_code": "ROLE_REMOTE",
                "depends_on": [],
                "server_id": rem_id,
                "action_summary": "numactl -H / cxl list 점검 및 Mooncake Master RDMA 포트 확인 (병렬 실행)",
            },
            {
                "step_id": "step_dgd_apply",
                "title": "K8s Dynamo DGD(TP4) 및 LMCache Mooncake 기본 매니페스트 배포",
                "assigned_agent_id": "agent_c",
                "role_code": "ROLE_CONFIG",
                "depends_on": ["step_gpu_check", "step_remote_prep"],
                "server_id": gpu_id,
                "action_summary": "dgd-active.yaml 및 lmcache_config.yaml 배포 + 내장 Git 커밋",
            },
            {
                "step_id": "step_bench_3tiers",
                "title": "CXL Memory → DDR5 DRAM → NVMe SSD 순차 설정 전환 및 벤치마크 수행",
                "assigned_agent_id": "agent_d",
                "role_code": "ROLE_ORCH",
                "depends_on": ["step_dgd_apply"],
                "server_id": gpu_id,
                "action_summary": "각 오프로딩 티어별 YAML Git 커밋 후 Pre/Post 무간섭 스냅샷과 함께 AIPerf 벤치마크 구동",
            },
        ]
        campaign = await self.submit_scenario_proposal(
            agent_id="agent_d",
            room_id=room_id,
            title="TP4 + LMCache(Mooncake) 기반 CXL vs DRAM vs SSD KV 오프로딩 성능 평가",
            role_map={
                "ROLE_GPU": "agent_a",
                "ROLE_REMOTE": "agent_b",
                "ROLE_CONFIG": "agent_c",
                "ROLE_ORCH": "agent_d",
            },
            role_assignments={
                "Agent A → GPU 서버 점검·제어": f"{gpu_id} TP4 토폴로지 및 K8s GPU 파드 검증",
                "Agent B → 리모트·메모리 제어": f"{rem_id} CXL NUMA Node 2 / DRAM / Gen5 SSD 및 Mooncake RDMA 준비",
                "Agent C → DGD·YAML 설정 구성": "Dynamo DGD 및 오프로딩 타겟별 YAML 패치·Git 버전 관리",
                "Agent D → 총괄·벤치마크 진행": "3종 티어 순차 벤치마크 실행 및 정량 비교 리포트 발행",
            },
            steps_dag=steps_dag,
            target_configs_preview={
                f"{gpu_id}/k8s/dgd-active.yaml": "TP4 (4x H100), LMCacheConnectorV1 활성화",
                f"{gpu_id}/configs/lmcache_config.yaml": "remote_url: mooncakestore://10.20.30.102:50051 (RDMA)",
                f"{rem_id}/configs/mooncake_store.yaml": "active_destination: CXL_MEMORY -> DRAM -> NVME_SSD 비교",
            },
            summary_comment=(
                "에이전트 간 논의를 통해 확정된 **역할 분담표 및 실행 DAG 시나리오**입니다. "
                "아래 카드에서 `[승인 및 역할별 실행 시작]`을 누르시면 Step 1·2는 병렬로, 이후 설정 배포와 3종 비교 벤치마크는 순차로 진행됩니다."
            ),
            external=False,
        )
        self.touch_agent("agent_d", client_type="Virtual Sim Agent", working=False, external=False)
        await self._emit({"type": "agents_updated", "agents": self.db.list_agents()})
        self._demo_campaigns.add(campaign["id"])

    async def _revise_simulated_campaign(
        self, prev_campaign: dict[str, Any], feedback: str
    ) -> None:
        await asyncio.sleep(0.5)
        room_id = prev_campaign["room_id"]
        lead_id = prev_campaign["lead_agent_id"]
        self.touch_agent(lead_id, client_type="Virtual Sim Agent", working=True, external=False)
        revised_camp = await self.submit_scenario_proposal(
            agent_id=lead_id,
            room_id=room_id,
            title=f"{prev_campaign['title']} (수정 반영)",
            role_map=self._room_role_maps.get(room_id),
            role_assignments=prev_campaign["role_assignments"],
            steps_dag=prev_campaign["steps_dag"],
            target_configs_preview={
                **prev_campaign["target_configs_preview"],
                "사용자 피드백 반영 사항": feedback,
            },
            summary_comment=f"요청하신 피드백(**\"{feedback}\"**)을 반영하여 시나리오 실행 계획을 업데이트했습니다. 확인 후 승인해 주세요.",
            external=False,
        )
        self.touch_agent(lead_id, client_type="Virtual Sim Agent", working=False, external=False)
        await self._emit({"type": "agents_updated", "agents": self.db.list_agents()})
        self._demo_campaigns.add(revised_camp["id"])

    async def _execute_simulated_campaign(self, campaign_id: str) -> None:
        campaign = self.db.get_campaign(campaign_id)
        if not campaign:
            return
        room_id = campaign["room_id"]
        gpu_id, rem_id = self._pick_sim_servers()

        steps = campaign["steps_dag"]
        steps[0]["status"] = "RUNNING"
        steps[1]["status"] = "RUNNING"
        self.db.update_campaign(campaign_id=campaign_id, steps_dag=steps)
        await self._emit({"type": "campaign_updated", "campaign": self.db.get_campaign(campaign_id)})

        await asyncio.gather(
            self.ssh_gateway.execute_command(
                server_id=gpu_id,
                actor_id="agent_a",
                command="nvidia-smi topo -m && kubectl get pods -n dynamo-llm",
                purpose="TP4 GPU 토폴로지 및 Dynamo 파드 점검",
                room_id=room_id,
                campaign_id=campaign_id,
            ),
            self.ssh_gateway.execute_command(
                server_id=rem_id,
                actor_id="agent_b",
                command="numactl -H && cxl list -Mu && mooncake_master --health",
                purpose="CXL NUMA Node 2 및 Mooncake Store 점검",
                room_id=room_id,
                campaign_id=campaign_id,
            ),
        )
        await self.handle_agent_message(
            "agent_a",
            room_id,
            f"✔️ `{gpu_id}` TP4 NVSwitch 연결 및 K8s 파드 Ready 확인 완료했습니다.",
            external=False,
        )
        await self.handle_agent_message(
            "agent_b",
            room_id,
            f"✔️ `{rem_id}` CXL 512GB(`/dev/dax2.0`), DRAM, NVMe 풀 및 Mooncake RDMA 상태 정상입니다.",
            external=False,
        )
        steps = self.db.get_campaign(campaign_id)["steps_dag"]  # type: ignore[index]
        steps[0]["status"] = "COMPLETED"
        steps[0]["result_note"] = "TP4 & RoCEv2 정상"
        steps[1]["status"] = "COMPLETED"
        steps[1]["result_note"] = "CXL/DRAM/SSD 준비 완료"
        steps[2]["status"] = "RUNNING"
        self.db.update_campaign(campaign_id=campaign_id, steps_dag=steps)
        await self._emit({"type": "campaign_updated", "campaign": self.db.get_campaign(campaign_id)})

        await self.ssh_gateway.update_server_config(
            server_id=gpu_id,
            actor_id="agent_c",
            repo_path=f"{gpu_id}/k8s/dgd-active.yaml",
            content=DEFAULT_GPU_DGD_YAML,
            commit_message=f"[{campaign_id}] TP4 + LMCacheConnectorV1 DGD 매니페스트 확정 배포",
            room_id=room_id,
            campaign_id=campaign_id,
        )
        await self.handle_agent_message(
            "agent_c",
            room_id,
            "✔️ `dgd-active.yaml` 배포 및 Git 스냅샷 기록을 마쳤습니다. 이제 @agent_d 티어별 벤치마크를 시작해 주세요.",
            external=False,
        )

        steps = self.db.get_campaign(campaign_id)["steps_dag"]  # type: ignore[index]
        steps[2]["status"] = "COMPLETED"
        steps[2]["result_note"] = "DGD 배포 완료"
        steps[3]["status"] = "RUNNING"
        self.db.update_campaign(campaign_id=campaign_id, steps_dag=steps)
        await self._emit({"type": "campaign_updated", "campaign": self.db.get_campaign(campaign_id)})

        variants = [
            ("Variant 1 · CXL Memory (512GB NUMA 2)", "CXL_MEMORY", "true", "false", "false"),
            ("Variant 2 · Host DDR5 DRAM (192GB)", "DRAM", "false", "true", "false"),
            ("Variant 3 · PCIe Gen5 NVMe SSD", "NVME_SSD", "false", "false", "true"),
        ]
        for v_label, tier_code, cxl_en, dram_en, ssd_en in variants:
            lmc_yaml = DEFAULT_LMCACHE_YAML.replace('kv_offload_tier: "CXL_MEMORY"', f'kv_offload_tier: "{tier_code}"')
            mc_yaml = (
                DEFAULT_MOONCAKE_YAML.replace('active_destination: "CXL_MEMORY"', f'active_destination: "{tier_code}"')
                .replace("enabled: true\n    numa_node: 2", f"enabled: {cxl_en}\n    numa_node: 2")
                .replace("dram_pool:\n    enabled: false", f"dram_pool:\n    enabled: {dram_en}")
                .replace("nvme_ssd:\n    enabled: false", f"nvme_ssd:\n    enabled: {ssd_en}")
            )
            await self.ssh_gateway.update_server_config(
                server_id=gpu_id,
                actor_id="agent_c",
                repo_path=f"{gpu_id}/configs/lmcache_config.yaml",
                content=lmc_yaml,
                commit_message=f"[{campaign_id}] LMCache kv_offload_tier -> {tier_code}",
                room_id=room_id,
                campaign_id=campaign_id,
            )
            await self.ssh_gateway.update_server_config(
                server_id=rem_id,
                actor_id="agent_b",
                repo_path=f"{rem_id}/configs/mooncake_store.yaml",
                content=mc_yaml,
                commit_message=f"[{campaign_id}] Mooncake active_destination -> {tier_code}",
                room_id=room_id,
                campaign_id=campaign_id,
            )
            await self.ssh_gateway.run_benchmark_step(
                campaign_id=campaign_id,
                server_id=gpu_id,
                actor_id="agent_d",
                variant_label=v_label,
                offload_target=tier_code,
                command=f"aiperf profile --model Llama-3.1-70B-Instruct --concurrency 32 --isl 16384 --osl 512 --tag {tier_code}",
                room_id=room_id,
            )

        steps = self.db.get_campaign(campaign_id)["steps_dag"]  # type: ignore[index]
        steps[3]["status"] = "COMPLETED"
        steps[3]["result_note"] = "3종 오프로딩 벤치마크 측정 완료"
        self.db.update_campaign(campaign_id=campaign_id, steps_dag=steps)
        await self._emit({"type": "campaign_updated", "campaign": self.db.get_campaign(campaign_id)})

        for aid in ("agent_a", "agent_b", "agent_c"):
            self.touch_agent(aid, client_type="Virtual Sim Agent", working=False, external=False)

        report_md = (
            "## 종합 성능 분석 결과 (Llama-3.1-70B · TP4 · LMCache + Mooncake RDMA)\n\n"
            "### 1. 핵심 요약\n"
            "- **CXL Memory (NUMA Node 2, 512GB)**: Host DRAM 대비 TTFT p50 차이가 **+4.7ms (38.4ms → 43.1ms)** 수준에 불과하면서도 풀 용량을 2.6배 확장해 **KV Cache Hit Rate가 89.4% → 94.8%로 상승**했습니다. 대용량 멀티턴 워크로드에서 가격·용량 대비 가장 우수한 효율을 보입니다.\n"
            "- **Host DDR5 DRAM (192GB)**: 단일 접근 지연시간(TTFT p50 **38.4ms**, 처리량 **4,820 tok/s**)은 가장 빠르나 용량 한계로 인한 LRU 축출(Eviction)이 발생해 히트율이 CXL 대비 5.4%p 낮았습니다.\n"
            "- **PCIe Gen5 NVMe SSD (3.84TB)**: 초미세 지연시간에 민감한 구간에서는 TTFT p99가 **246.5ms**로 증가했으나, 오프로딩 대역폭 **11.4 GB/s**로 재연산(Prefill Recomputation) 대비 높은 처리량(3,190 tok/s)을 방어했습니다.\n\n"
            "### 2. 권장 아키텍처\n"
            "- 1차 Hot/Warm KV 캐시 풀은 **Mooncake Store + CXL Memory Tier (`numa_node: 2`)**로 구성하고, Cold 컨텍스트만 NVMe SSD로 계층화하는 구성을 권장합니다."
        )
        await self.publish_report(
            agent_id="agent_d",
            campaign_id=campaign_id,
            title="TP4 + Mooncake 기반 CXL vs DRAM vs SSD 오프로딩 성능 비교 리포트",
            summary_markdown=report_md,
            comparison_highlights={
                "recommended_tier": "CXL_MEMORY",
                "cxl_vs_dram_ttft_delta_ms": "+4.7 ms",
                "cxl_hit_rate_gain_pct": "+5.4 %p",
            },
            external=False,
        )
