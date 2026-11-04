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

ROLE_LABELS = {
    "ROLE_ORCH": "총괄·벤치마크",
    "ROLE_GPU": "GPU 서버",
    "ROLE_REMOTE": "리모트·메모리",
    "ROLE_CONFIG": "DGD·YAML",
}

ROLE_PREFERENCES: dict[str, list[str]] = {
    "ROLE_ORCH": ["agent_d", "agent_c", "agent_a", "agent_b"],
    "ROLE_GPU": ["agent_a", "agent_c", "agent_d", "agent_b"],
    "ROLE_REMOTE": ["agent_b", "agent_a", "agent_d", "agent_c"],
    "ROLE_CONFIG": ["agent_c", "agent_d", "agent_a", "agent_b"],
}


class MeetingRoomOrchestrator:
    """Manages agent standby presence, dynamic role assignment, turn routing, and DAG execution."""

    def __init__(
        self,
        db: Database,
        ssh_gateway: SSHGateway,
        broadcast_fn: BroadcastCallback | None = None,
    ) -> None:
        self.db = db
        self.ssh_gateway = ssh_gateway
        self._broadcast_fn = broadcast_fn

        self._inboxes: dict[str, deque[dict[str, Any]]] = {
            f"agent_{x}": deque() for x in ("a", "b", "c", "d")
        }
        self._events: dict[str, asyncio.Event] = {
            f"agent_{x}": asyncio.Event() for x in ("a", "b", "c", "d")
        }
        self._active_waiters: dict[str, int] = {}
        self._last_heartbeat: dict[str, float] = {}

        # Per-room discussion state: queue of (agent_id, turn_kind, extra_instruction)
        self._discussion_queues: dict[str, deque[tuple[str, str, str]]] = {}
        self._agent_speak_counts: dict[str, dict[str, int]] = {}
        # Track simulated demo campaigns so human approval resumes simulated execution
        self._demo_campaigns: set[str] = set()

    def set_broadcaster(self, fn: BroadcastCallback) -> None:
        self._broadcast_fn = fn

    async def _emit(self, event: dict[str, Any]) -> None:
        if self._broadcast_fn:
            await self._broadcast_fn(event)

    # --- Presence & Dynamic Role Allocation ---

    def touch_agent(self, agent_id: str, client_type: str | None = None, working: bool = False) -> None:
        if agent_id not in self._inboxes:
            self._inboxes[agent_id] = deque()
            self._events[agent_id] = asyncio.Event()
        self._last_heartbeat[agent_id] = time.monotonic()
        status = "WORKING" if working else "ONLINE_IDLE"
        self.db.update_agent_presence(agent_id, status=status, client_type=client_type)

    def get_online_agent_ids(self) -> list[str]:
        now = time.monotonic()
        online: list[str] = []
        for agent in self.db.list_agents():
            aid = agent["id"]
            waiting = self._active_waiters.get(aid, 0) > 0
            recent = (now - self._last_heartbeat.get(aid, -99999.0)) <= settings.presence_grace_sec
            if waiting or recent:
                online.append(aid)
            elif agent["status"] != "OFFLINE":
                self.db.update_agent_presence(aid, status="OFFLINE", assigned_roles=[])
        return online

    def recompute_dynamic_roles(self, candidate_ids: list[str] | None = None) -> dict[str, str]:
        """Map [ROLE_ORCH, ROLE_GPU, ROLE_REMOTE, ROLE_CONFIG] onto currently online agents.

        If all 4 agents are online, each takes its primary specialty. If some agents are
        offline, online agents dynamically inherit missing roles balanced by load.
        """
        online = candidate_ids if candidate_ids is not None else self.get_online_agent_ids()
        all_agents = {a["id"]: a for a in self.db.list_agents()}

        if not online:
            for aid in all_agents:
                self.db.set_agent_assigned_roles(aid, [])
            return {}

        role_to_agent: dict[str, str] = {}
        agent_to_roles: dict[str, list[str]] = {aid: [] for aid in all_agents}

        # Step 1: Assign primary roles to online owners first
        for aid in online:
            a = all_agents.get(aid)
            if a and a["primary_role"] not in role_to_agent:
                p_role = a["primary_role"]
                role_to_agent[p_role] = aid
                agent_to_roles[aid].append(p_role)

        # Step 2: Assign remaining unassigned roles by preference + load balancing
        for role_code, prefs in ROLE_PREFERENCES.items():
            if role_code in role_to_agent:
                continue
            online_candidates = [c for c in prefs if c in online] or list(online)
            # Pick candidate with fewest currently assigned roles (preserving preference order on ties)
            chosen = min(
                online_candidates,
                key=lambda cid: (len(agent_to_roles[cid]), online_candidates.index(cid)),
            )
            role_to_agent[role_code] = chosen
            agent_to_roles[chosen].append(role_code)

        for aid, roles in agent_to_roles.items():
            self.db.set_agent_assigned_roles(aid, roles)

        return role_to_agent

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
        self.touch_agent(agent_id, client_type=client_type, working=False)
        self.recompute_dynamic_roles()
        await self._emit({"type": "agents_updated", "agents": self.db.list_agents()})

        try:
            deadline = time.monotonic() + wait_sec
            while True:
                # Pop matching turn if available
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
                    self.touch_agent(agent_id, client_type=client_type, working=True)
                    await self._emit({"type": "agents_updated", "agents": self.db.list_agents()})
                    return turn

                self._events[agent_id].clear()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.touch_agent(agent_id, client_type=client_type, working=False)
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
                    self.touch_agent(agent_id, client_type=client_type, working=False)
                    return {
                        "status": "HEARTBEAT_IDLE",
                        "agent_id": agent_id,
                        "instruction": (
                            "대기 타임아웃 주기 도달 (유휴 상태). 즉시 wait_for_turn을 다시 호출해 미팅룸 대기를 계속하세요."
                        ),
                    }
        finally:
            self._active_waiters[agent_id] = max(0, self._active_waiters.get(agent_id, 1) - 1)
            self._last_heartbeat[agent_id] = time.monotonic()

    # --- User & Agent Messaging + State Machine ---

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
            self._enqueue_turn(
                target_aid,
                {
                    "status": "YOUR_TURN",
                    "turn_type": "DIRECT_MESSAGE",
                    "room_id": room_id,
                    "room_name": room["name"],
                    "user_message": content,
                    "recent_messages": self.db.list_messages(room_id, limit=15),
                    "instruction": (
                        f"사용자({user['name']})가 1:1 대화방에서 요청했습니다. "
                        "필요 시 서버 스냅샷/설정을 조회하거나 점검한 뒤 post_message로 답변하고 다시 wait_for_turn을 호출하세요."
                    ),
                },
            )
            return {"message": msg, "room": self.db.get_room(room_id)}

        # Group Meeting Room -> Start or continue structured DISCUSSION
        online_ids = self.get_online_agent_ids()
        role_map = self.recompute_dynamic_roles(online_ids)
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
            warn_msg = self.db.add_message(
                room_id=room_id,
                sender_type="system",
                sender_id="system",
                sender_name="미팅룸 코디네이터",
                role_badge="SYSTEM",
                msg_kind="system",
                content=(
                    "현재 대기(Online) 중인 개인 에이전트가 없습니다. "
                    "터미널에서 Claude Code 또는 Hermes로 `wait_for_turn` 대기 모드를 시작하거나, "
                    "우측 상단 `[⚡ 4개 에이전트 협업 시뮬레이션]` 버튼으로 즉시 동작을 체험할 수 있습니다."
                ),
            )
            await self._emit({"type": "message_created", "message": warn_msg})
            return {"message": msg, "room": updated_room}

        self._build_and_start_discussion_queue(room_id, content, online_ids, role_map)
        return {"message": msg, "room": updated_room}

    def _build_and_start_discussion_queue(
        self,
        room_id: str,
        topic: str,
        online_ids: list[str],
        role_map: dict[str, str],
    ) -> None:
        q: deque[tuple[str, str, str]] = deque()
        self._agent_speak_counts[room_id] = {aid: 0 for aid in online_ids}

        lead_id = role_map.get("ROLE_ORCH", online_ids[0])
        other_specialists: list[str] = []
        for rcode in ("ROLE_GPU", "ROLE_REMOTE", "ROLE_CONFIG"):
            holder = role_map.get(rcode)
            if holder and holder != lead_id and holder not in other_specialists:
                other_specialists.append(holder)

        if not other_specialists:
            # Single online agent holds all 4 roles!
            q.append(
                (
                    lead_id,
                    "SOLO_ORCHESTRATE_AND_PROPOSE",
                    (
                        "현재 온라인 에이전트가 귀하 1명뿐이므로 4개 역할(총괄·GPU·리모트·DGD/YAML)을 모두 겸임합니다. "
                        "현재 서버 환경 스냅샷을 확인한 뒤 채팅방에 요약 의견을 남기고 곧바로 `submit_scenario_proposal`을 호출해 승인 카드를 제출하세요."
                    ),
                )
            )
        else:
            q.append(
                (
                    lead_id,
                    "DISCUSSION_OPENING",
                    (
                        "총괄(ROLE_ORCH) 에이전트로서 사용자의 주제를 분석해 핵심 실험 축(예: TP 구성, LMCache+Mooncake, 오프로딩 타겟)을 정리하고 "
                        "온라인 동료 에이전트들에게 담당 영역 검토 의견을 요청하는 짧은 발언을 `post_message`로 남기세요."
                    ),
                )
            )
            for spec_id in other_specialists:
                agent_info = self.db.get_agent(spec_id)
                roles_ko = ", ".join(ROLE_LABELS.get(r, r) for r in (agent_info["assigned_roles"] if agent_info else []))
                q.append(
                    (
                        spec_id,
                        "SPECIALIST_OPINION",
                        (
                            f"귀하는 현재 [{roles_ko}] 역할을 담당하고 있습니다. "
                            "필요 시 `get_environment_snapshot`으로 현재 서버 설정을 확인하고, 이번 주제 수행을 위해 본인이 세팅할 구체적 항목과 주의사항을 `post_message`로 발언하세요."
                        ),
                    )
                )
            q.append(
                (
                    lead_id,
                    "FINALIZE_SCENARIO",
                    (
                        "모든 온라인 에이전트의 기술 검토 의견이 취합되었습니다! "
                        "이제 논의 내용을 종합해 `submit_scenario_proposal` 툴을 호출하여 역할 분담표와 실행 DAG(병렬/순차 스텝), 변경 예정 DGD/YAML 요약을 제출하세요."
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
    ) -> dict[str, Any]:
        self.touch_agent(agent_id, working=True)
        agent = self.db.get_agent(agent_id) or {
            "id": agent_id,
            "name": agent_id,
            "assigned_roles": [],
        }
        badge = " + ".join(ROLE_LABELS.get(r, r) for r in agent.get("assigned_roles", [])) or "AGENT"
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
        if room and room["workflow_state"] == "DISCUSSION":
            counts = self._agent_speak_counts.setdefault(room_id, {})
            counts[agent_id] = counts.get(agent_id, 0) + 1

            # Handle explicit @mentions if under max_discussion_rounds cap
            q = self._discussion_queues.setdefault(room_id, deque())
            online_set = set(self.get_online_agent_ids())
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
                            f"{agent['name']}님이 귀하를 멘션했습니다. 짧게 보충 답변을 `post_message`로 남기세요.",
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
    ) -> dict[str, Any]:
        self.touch_agent(agent_id, working=True)
        room = self.db.get_room(room_id)
        if not room:
            raise ValueError(f"채팅방을 찾을 수 없습니다: {room_id}")

        campaign = self.db.create_campaign(
            room_id=room_id,
            title=title,
            topic_prompt=room.get("current_topic") or title,
            lead_agent_id=agent_id,
            role_assignments=role_assignments,
            steps_dag=steps_dag,
            target_configs_preview=target_configs_preview,
        )
        # Clear remaining discussion queue and pause at APPROVAL_PENDING
        self._discussion_queues.pop(room_id, None)
        self.db.update_room_workflow(
            room_id=room_id,
            workflow_state="APPROVAL_PENDING",
            active_campaign_id=campaign["id"],
        )

        agent = self.db.get_agent(agent_id) or {"name": agent_id, "assigned_roles": ["ROLE_ORCH"]}
        badge = " + ".join(ROLE_LABELS.get(r, r) for r in agent.get("assigned_roles", [])) or "총괄·벤치마크"
        msg = self.db.add_message(
            room_id=room_id,
            sender_type="agent",
            sender_id=agent_id,
            sender_name=agent["name"],
            role_badge=badge,
            msg_kind="scenario_proposal",
            content=summary_comment or f"시나리오 제안서 **{title}** 작성이 완료되었습니다. 검토 후 승인해 주시면 역할별로 실행을 시작합니다.",
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
            content=f"✅ {user['name']}님이 시나리오 '{campaign['title']}'을(를) 승인했습니다. DAG 순서에 따라 병렬/순차 작업 실행을 시작합니다.",
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

        if campaign_id not in self._demo_campaigns:
            online_ids = self.get_online_agent_ids()
            role_map = self.recompute_dynamic_roles(online_ids)
            lead_id = role_map.get("ROLE_ORCH", updated["lead_agent_id"])
            self._enqueue_turn(
                lead_id,
                {
                    "status": "YOUR_TURN",
                    "turn_type": "SCENARIO_REVISION",
                    "room_id": room_id,
                    "campaign_id": campaign_id,
                    "feedback": feedback,
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

        steps = campaign["steps_dag"]
        completed_ids = {s["step_id"] for s in steps if s["status"] == "COMPLETED"}
        online_ids = self.get_online_agent_ids()
        role_map = self.recompute_dynamic_roles(online_ids)

        newly_running: list[dict[str, Any]] = []
        for s in steps:
            if s["status"] == "PENDING" and all(dep in completed_ids for dep in s.get("depends_on", [])):
                s["status"] = "RUNNING"
                # Failover if originally assigned agent went offline!
                target_agent = s["assigned_agent_id"]
                if online_ids and target_agent not in online_ids:
                    fallback = role_map.get(s.get("role_code", "ROLE_ORCH"), online_ids[0])
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
                        "room_id": campaign["room_id"],
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

        # Check if all steps completed -> trigger REPORTING phase
        if steps and all(s["status"] == "COMPLETED" for s in steps):
            room_id = campaign["room_id"]
            self.db.update_room_workflow(room_id=room_id, workflow_state="REPORTING")
            await self._emit({"type": "room_updated", "room": self.db.get_room(room_id)})

            lead_id = role_map.get("ROLE_ORCH", campaign["lead_agent_id"])
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
    ) -> dict[str, Any]:
        self.touch_agent(agent_id, working=True)
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

        agent = self.db.get_agent(agent_id) or {"name": agent_id, "assigned_roles": ["ROLE_ORCH"]}
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
        await self._emit({"type": "report_created", "report": report})
        await self._emit({"type": "campaign_updated", "campaign": updated_camp})
        await self._emit({"type": "room_updated", "room": self.db.get_room(campaign["room_id"])})
        await self._emit({"type": "message_created", "message": msg})
        return report

    # --- Interactive 4-Agent Simulation Demo (Zero-Client Trial Mode) ---

    async def start_simulated_meeting(
        self, room_id: str, user_id: str, topic: str | None = None
    ) -> dict[str, Any]:
        """Simulates all 4 specialist agents discussing a topic and submitting an approval card."""
        prompt = topic or (
            "Llama-3.1-70B를 TP4로 구성하고 LMCache 백엔드를 Mooncake Store로 연동한 뒤, "
            "KV 캐시 오프로딩 데스티네이션을 CXL 메모리 / DRAM / NVMe SSD 3가지로 변경하며 성능 비교 시나리오를 작성해 줘."
        )
        user = self.db.get_user(user_id) or {"id": user_id, "name": "팀원 A"}
        for aid in ("agent_a", "agent_b", "agent_c", "agent_d"):
            self.touch_agent(aid, client_type="Simulated MCP Agent", working=False)
        self.recompute_dynamic_roles(["agent_a", "agent_b", "agent_c", "agent_d"])
        await self._emit({"type": "agents_updated", "agents": self.db.list_agents()})

        umsg = self.db.add_message(
            room_id=room_id,
            sender_type="user",
            sender_id=user["id"],
            sender_name=user["name"],
            role_badge="USER",
            msg_kind="chat",
            content=prompt,
        )
        self.db.update_room_workflow(
            room_id=room_id, workflow_state="DISCUSSION", current_topic=prompt, discussion_round=1
        )
        await self._emit({"type": "message_created", "message": umsg})
        await self._emit({"type": "room_updated", "room": self.db.get_room(room_id)})

        asyncio.create_task(self._run_simulated_discussion(room_id, prompt))
        return {"status": "started", "room_id": room_id}

    async def _run_simulated_discussion(self, room_id: str, prompt: str) -> None:
        dialogue = [
            (
                "agent_d",
                "총괄·벤치마크",
                "주제 확인했습니다. **TP4 GPU 서빙 + LMCache(Mooncake Store RDMA) 기반 KV 캐시 오프로딩 3종(CXL Memory vs DDR5 DRAM vs Gen5 NVMe SSD)** 비교 캠페인을 설계하겠습니다. @agent_a GPU 토폴로지 상태, @agent_b 리모트 메모리 풀 구성, @agent_c DGD/YAML 변경 포인트를 순서대로 점검해 주세요.",
            ),
            (
                "agent_a",
                "GPU 서버",
                "`gpu-node-01` 확인 결과 H100 80GB 4기가 NVSwitch Full Mesh(900GB/s)로 연결되어 있어 **TP4 구성에 최적**이며, `mlx5_0` 400GbE RoCEv2 포트가 리모트 메모리 노드와 정상 링크업 상태입니다. GPU 클럭 고정 및 K8s 파드 상태를 제가 사전 검증하겠습니다.",
            ),
            (
                "agent_b",
                "리모트·메모리",
                "`remote-mem-01`에 **NUMA Node 2(CPU-less CXL Type-3 512GB, `/dev/dax2.0`)**, **NUMA Node 0(DDR5 DRAM HugePages 192GB)**, **PCIe Gen5 NVMe(`/mnt/nvme_kv_pool`, Direct I/O)** 3개 티어가 모두 준비되어 있습니다. 각 실험 단계마다 Mooncake Store 데몬의 스토리지 백엔드 바인딩을 전환하겠습니다.",
            ),
            (
                "agent_c",
                "DGD·YAML",
                "K8s `DynamoGraphDeployment`(`vllm-disagg-tp4`)에서 `--tensor-parallel-size 4` 및 `LMCacheConnectorV1`을 고정하고, `lmcache_config.yaml`과 `mooncake_store.yaml`의 `active_destination`만 `CXL_MEMORY` → `DRAM` → `NVME_SSD` 순으로 패치해 내장 Git에 커밋 남기겠습니다.",
            ),
        ]
        for aid, badge, text in dialogue:
            await asyncio.sleep(0.55)
            self.touch_agent(aid, working=True)
            msg = self.db.add_message(
                room_id=room_id,
                sender_type="agent",
                sender_id=aid,
                sender_name=aid.replace("agent_", "Agent ").upper(),
                role_badge=badge,
                msg_kind="chat",
                content=text,
            )
            await self._emit({"type": "message_created", "message": msg})
            self.touch_agent(aid, working=False)

        await asyncio.sleep(0.45)
        steps_dag = [
            {
                "step_id": "step_gpu_check",
                "title": "GPU TP4 NVLink 토폴로지 및 RoCE RDMA 링크 검증",
                "assigned_agent_id": "agent_a",
                "role_code": "ROLE_GPU",
                "depends_on": [],
                "server_id": "gpu-node-01",
                "action_summary": "nvidia-smi topo -m 및 K8s Dynamo 파드 헬스체크 (병렬 실행)",
            },
            {
                "step_id": "step_remote_prep",
                "title": "리모트 서버 CXL(/dev/dax2.0)·DRAM·NVMe 풀 및 Mooncake 데몬 점검",
                "assigned_agent_id": "agent_b",
                "role_code": "ROLE_REMOTE",
                "depends_on": [],
                "server_id": "remote-mem-01",
                "action_summary": "numactl -H / cxl list 점검 및 Mooncake Master RDMA 포트 확인 (병렬 실행)",
            },
            {
                "step_id": "step_dgd_apply",
                "title": "K8s Dynamo DGD(TP4) 및 LMCache Mooncake 기본 매니페스트 배포",
                "assigned_agent_id": "agent_c",
                "role_code": "ROLE_CONFIG",
                "depends_on": ["step_gpu_check", "step_remote_prep"],
                "server_id": "gpu-node-01",
                "action_summary": "dgd-active.yaml 및 lmcache_config.yaml 배포 + 내장 Git 커밋",
            },
            {
                "step_id": "step_bench_3tiers",
                "title": "CXL Memory → DDR5 DRAM → NVMe SSD 순차 설정 전환 및 벤치마크 수행",
                "assigned_agent_id": "agent_d",
                "role_code": "ROLE_ORCH",
                "depends_on": ["step_dgd_apply"],
                "server_id": "gpu-node-01",
                "action_summary": "각 오프로딩 티어별 YAML Git 커밋 후 Pre/Post 무간섭 스냅샷과 함께 AIPerf 벤치마크 구동",
            },
        ]
        campaign = await self.submit_scenario_proposal(
            agent_id="agent_d",
            room_id=room_id,
            title="TP4 + LMCache(Mooncake) 기반 CXL vs DRAM vs SSD KV 오프로딩 성능 평가",
            role_assignments={
                "Agent A (GPU 서버)": "TP4 토폴로지 및 K8s GPU 파드 검증",
                "Agent B (리모트·메모리)": "CXL NUMA Node 2 / DRAM / Gen5 SSD 및 Mooncake RDMA 준비",
                "Agent C (DGD·YAML)": "Dynamo DGD 및 오프로딩 타겟별 YAML 패치·Git 버전 관리",
                "Agent D (총괄·벤치마크)": "3종 티어 순차 벤치마크 실행 및 정량 비교 리포트 발행",
            },
            steps_dag=steps_dag,
            target_configs_preview={
                "gpu-node-01/k8s/dgd-active.yaml": "TP4 (4x H100), LMCacheConnectorV1 활성화",
                "gpu-node-01/configs/lmcache_config.yaml": "remote_url: mooncakestore://10.20.30.102:50051 (RDMA)",
                "remote-mem-01/configs/mooncake_store.yaml": "active_destination: CXL_MEMORY -> DRAM -> NVME_SSD 비교",
            },
            summary_comment=(
                "논의 결과를 바탕으로 **4인 역할 분담 및 실행 DAG 시나리오**를 구성했습니다. "
                "아래 카드에서 `[승인 및 실행]`을 누르시면 Step 1·2는 병렬로, 이후 설정 배포와 3종 비교 벤치마크는 순차로 자동 진행됩니다."
            ),
        )
        self._demo_campaigns.add(campaign["id"])

    async def _execute_simulated_campaign(self, campaign_id: str) -> None:
        campaign = self.db.get_campaign(campaign_id)
        if not campaign:
            return
        room_id = campaign["room_id"]

        # Parallel Step 1 & Step 2
        steps = campaign["steps_dag"]
        steps[0]["status"] = "RUNNING"
        steps[1]["status"] = "RUNNING"
        self.db.update_campaign(campaign_id=campaign_id, steps_dag=steps)
        await self._emit({"type": "campaign_updated", "campaign": self.db.get_campaign(campaign_id)})

        await asyncio.gather(
            self.ssh_gateway.execute_command(
                server_id="gpu-node-01",
                actor_id="agent_a",
                command="nvidia-smi topo -m && kubectl get pods -n dynamo-llm",
                purpose="TP4 GPU 토폴로지 및 Dynamo 파드 점검",
                room_id=room_id,
                campaign_id=campaign_id,
            ),
            self.ssh_gateway.execute_command(
                server_id="remote-mem-01",
                actor_id="agent_b",
                command="numactl -H && cxl list -Mu && mooncake_master --health",
                purpose="CXL NUMA Node 2 및 Mooncake Store 점검",
                room_id=room_id,
                campaign_id=campaign_id,
            ),
        )
        await self.handle_agent_message(
            "agent_a", room_id, "✔️ `gpu-node-01` TP4 NVSwitch 연결 및 K8s 파드 Ready 확인 완료했습니다."
        )
        await self.handle_agent_message(
            "agent_b", room_id, "✔️ `remote-mem-01` CXL 512GB(`/dev/dax2.0`), DRAM, NVMe 풀 및 Mooncake RDMA 상태 정상입니다."
        )
        steps = self.db.get_campaign(campaign_id)["steps_dag"]  # type: ignore[index]
        steps[0]["status"] = "COMPLETED"
        steps[0]["result_note"] = "TP4 & RoCEv2 정상"
        steps[1]["status"] = "COMPLETED"
        steps[1]["result_note"] = "CXL/DRAM/SSD 준비 완료"
        steps[2]["status"] = "RUNNING"
        self.db.update_campaign(campaign_id=campaign_id, steps_dag=steps)
        await self._emit({"type": "campaign_updated", "campaign": self.db.get_campaign(campaign_id)})

        # Step 3: Agent C applies baseline DGD
        await self.ssh_gateway.update_server_config(
            server_id="gpu-node-01",
            actor_id="agent_c",
            repo_path="gpu-node-01/k8s/dgd-active.yaml",
            content=DEFAULT_GPU_DGD_YAML,
            commit_message=f"[{campaign_id}] TP4 + LMCacheConnectorV1 DGD 매니페스트 확정 배포",
            room_id=room_id,
            campaign_id=campaign_id,
        )
        await self.handle_agent_message(
            "agent_c", room_id, "✔️ `dgd-active.yaml` 배포 및 Git 스냅샷 기록을 마쳤습니다. 이제 @agent_d 티어별 벤치마크를 시작해 주세요."
        )

        steps = self.db.get_campaign(campaign_id)["steps_dag"]  # type: ignore[index]
        steps[2]["status"] = "COMPLETED"
        steps[2]["result_note"] = "DGD 배포 완료"
        steps[3]["status"] = "RUNNING"
        self.db.update_campaign(campaign_id=campaign_id, steps_dag=steps)
        await self._emit({"type": "campaign_updated", "campaign": self.db.get_campaign(campaign_id)})

        # Step 4: Run 3 variants (CXL_MEMORY -> DRAM -> NVME_SSD) with Git config commits!
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
                server_id="gpu-node-01",
                actor_id="agent_c",
                repo_path="gpu-node-01/configs/lmcache_config.yaml",
                content=lmc_yaml,
                commit_message=f"[{campaign_id}] LMCache kv_offload_tier -> {tier_code}",
                room_id=room_id,
                campaign_id=campaign_id,
            )
            await self.ssh_gateway.update_server_config(
                server_id="remote-mem-01",
                actor_id="agent_b",
                repo_path="remote-mem-01/configs/mooncake_store.yaml",
                content=mc_yaml,
                commit_message=f"[{campaign_id}] Mooncake active_destination -> {tier_code}",
                room_id=room_id,
                campaign_id=campaign_id,
            )
            await self.ssh_gateway.run_benchmark_step(
                campaign_id=campaign_id,
                server_id="gpu-node-01",
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
        )
