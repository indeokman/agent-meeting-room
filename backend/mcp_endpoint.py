from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from .auth import resolve_identity
from .db import Database
from .git_store import EmbeddedGitStore
from .orchestrator import MeetingRoomOrchestrator
from .ssh_gateway import SSHGateway


MCP_TOOLS_SCHEMA: list[dict[str, Any]] = [
    {
        "name": "wait_for_turn",
        "description": (
            "미팅룸 대기 루프(Standby) 핵심 툴. 본인의 발언 차례(논의/멘션/1:1 대화)나 승인된 실행 스텝이 배정될 때까지 대기합니다. "
            "HEARTBEAT_IDLE 응답을 받으면 긴 텍스트 출력 없이 즉시 wait_for_turn을 다시 호출하세요."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "에이전트 ID (agent_a | agent_b | agent_c | agent_d)",
                },
                "room_id": {
                    "type": "string",
                    "description": "특정 방 ID 또는 전체 방 수신 시 생략/'*'",
                },
                "max_wait_sec": {
                    "type": "integer",
                    "description": "최대 대기 초 (기본 45초, MCP_TOOL_TIMEOUT 상향 시 600~1800초 가능)",
                },
            },
        },
    },
    {
        "name": "get_room_context",
        "description": "미팅룸의 현재 진행 상태, 온라인 에이전트 역할 배정 현황, 최근 메시지 및 활성 시나리오 정보를 조회합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "room_id": {"type": "string", "description": "조회할 채팅방 ID (예: room-main)"},
                "limit": {"type": "integer", "description": "가져올 최근 메시지 수 (기본 30)"},
            },
            "required": ["room_id"],
        },
    },
    {
        "name": "post_message",
        "description": "미팅룸 채팅방에 의견, 기술 검토 내용 또는 작업 진행 상황을 게시합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "발언 에이전트 ID"},
                "room_id": {"type": "string", "description": "채팅방 ID"},
                "content": {"type": "string", "description": "마크다운 형식 메시지 본문"},
                "mention_agents": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "추가 의견을 요청할 동료 에이전트 ID 목록 (예: ['agent_a'])",
                },
            },
            "required": ["room_id", "content"],
        },
    },
    {
        "name": "submit_scenario_proposal",
        "description": (
            "총괄(ROLE_ORCH) 에이전트가 팀원 논의 결과를 취합해 사용자 승인 대기 카드를 제출합니다. "
            "호출 즉시 회의 상태가 APPROVAL_PENDING으로 전환되며 사용자의 승인을 기다립니다."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "room_id": {"type": "string"},
                "title": {"type": "string", "description": "벤치마크 시나리오 제목"},
                "role_assignments": {
                    "type": "object",
                    "description": "역할/에이전트별 담당 업무 요약 맵",
                },
                "steps_dag": {
                    "type": "array",
                    "description": "실행 스텝 배열 (depends_on이 비어 있으면 병렬 실행됨)",
                    "items": {
                        "type": "object",
                        "properties": {
                            "step_id": {"type": "string"},
                            "title": {"type": "string"},
                            "assigned_agent_id": {"type": "string"},
                            "role_code": {"type": "string"},
                            "depends_on": {"type": "array", "items": {"type": "string"}},
                            "server_id": {"type": "string"},
                            "action_summary": {"type": "string"},
                        },
                        "required": ["step_id", "title", "assigned_agent_id"],
                    },
                },
                "target_configs_preview": {
                    "type": "object",
                    "description": "변경할 DGD / YAML 파일별 핵심 설정 요약",
                },
                "summary_comment": {"type": "string", "description": "채팅방 안내 코멘트"},
            },
            "required": ["room_id", "title", "role_assignments", "steps_dag"],
        },
    },
    {
        "name": "complete_scenario_step",
        "description": "할당받은 실행 DAG 스텝 작업을 완료 처리하고 후속 종속 스텝 에이전트를 깨웁니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "campaign_id": {"type": "string"},
                "step_id": {"type": "string"},
                "result_note": {"type": "string", "description": "수행 결과 요약"},
            },
            "required": ["campaign_id", "step_id", "result_note"],
        },
    },
    {
        "name": "list_servers",
        "description": "등록된 테스트 서버(GPU 서버, 리모트 메모리 서버) 목록과 현재 잠금(Lock) 상태를 조회합니다.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_environment_snapshot",
        "description": "대상 서버의 하드웨어 스펙(GPU TP4, CXL/DRAM NUMA, SSD, RDMA), 소프트웨어 스펙, 활성 DGD/YAML 스냅샷을 조회합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "server_id": {"type": "string", "description": "예: gpu-node-01 또는 remote-mem-01"},
                "force_refresh": {
                    "type": "boolean",
                    "description": "True이면 SSH로 즉시 새로 수집 (기본 False: 최신 스냅샷 조회)",
                },
            },
            "required": ["server_id"],
        },
    },
    {
        "name": "ssh_exec",
        "description": "중앙 SSH 게이트웨이를 통해 테스트 서버에서 명령어를 실행하고 감사 로그를 자동 기록합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "server_id": {"type": "string"},
                "command": {"type": "string"},
                "purpose": {"type": "string"},
                "room_id": {"type": "string"},
                "campaign_id": {"type": "string"},
                "timeout": {"type": "integer"},
            },
            "required": ["server_id", "command"],
        },
    },
    {
        "name": "update_server_config",
        "description": "서버의 K8s DGD 매니페스트 또는 로컬 YAML(LMCache, Mooncake 등)을 배포하고 내장 Git에 자동 커밋합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "server_id": {"type": "string"},
                "repo_path": {
                    "type": "string",
                    "description": "예: gpu-node-01/k8s/dgd-active.yaml 또는 remote-mem-01/configs/mooncake_store.yaml",
                },
                "content": {"type": "string", "description": "전체 YAML/설정 텍스트"},
                "commit_message": {"type": "string", "description": "변경 사유 요약"},
                "apply_command": {"type": "string", "description": "파일 반영 후 추가 재시작 명령(선택)"},
                "room_id": {"type": "string"},
                "campaign_id": {"type": "string"},
            },
            "required": ["server_id", "repo_path", "content", "commit_message"],
        },
    },
    {
        "name": "diff_server_configs",
        "description": "두 Git 커밋 시점 간의 DGD / YAML 설정 변경점(Unified Diff)을 비교합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "base_hash": {"type": "string"},
                "target_hash": {"type": "string"},
                "server_id": {"type": "string"},
            },
        },
    },
    {
        "name": "rollback_server_config",
        "description": "특정 과거 커밋 해시 시점의 DGD/YAML 설정을 원클릭 복원하고 SSH로 서버에 재반영합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "target_hash": {"type": "string"},
                "server_id": {"type": "string"},
                "room_id": {"type": "string"},
            },
            "required": ["target_hash"],
        },
    },
    {
        "name": "run_benchmark_step",
        "description": (
            "벤치마크 실행 전후 자동 환경 스냅샷을 찍고 측정 구간 동안 서버에 BENCHMARK_RUNNING 잠금을 걸어 "
            "타 작업 및 SSH 간섭 없이 벤치마크를 수행한 뒤 지표를 기록합니다."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "campaign_id": {"type": "string"},
                "server_id": {"type": "string"},
                "variant_label": {"type": "string", "description": "예: Variant 1 - CXL Memory"},
                "offload_target": {"type": "string", "description": "CXL_MEMORY | DRAM | NVME_SSD"},
                "command": {"type": "string", "description": "벤치마크 실행 커맨드"},
                "metrics_override": {
                    "type": "object",
                    "description": "에이전트가 추출한 표준 지표 JSON (ttft_p50_ms, ttft_p99_ms, tpot_p50_ms, throughput_tok_s, kv_cache_hit_rate_pct, offload_bw_gbps)",
                },
                "room_id": {"type": "string"},
            },
            "required": ["campaign_id", "server_id", "variant_label", "offload_target", "command"],
        },
    },
    {
        "name": "publish_benchmark_report",
        "description": "시나리오 캠페인의 최종 비교 분석 마크다운 리포트를 발행하고 회의를 완료합니다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "campaign_id": {"type": "string"},
                "title": {"type": "string"},
                "summary_markdown": {"type": "string"},
                "comparison_highlights": {"type": "object"},
            },
            "required": ["campaign_id", "title", "summary_markdown"],
        },
    },
]


def create_mcp_router(
    db: Database,
    git_store: EmbeddedGitStore,
    ssh_gateway: SSHGateway,
    orchestrator: MeetingRoomOrchestrator,
) -> APIRouter:
    router = APIRouter()
    sse_sessions: dict[str, tuple[asyncio.Queue[dict[str, Any]], dict[str, str]]] = {}

    async def dispatch_tool(
        name: str, args: dict[str, Any], default_agent_id: str, client_hint: str
    ) -> Any:
        agent_id = (args.get("agent_id") or default_agent_id or "agent_d").strip()

        if name == "wait_for_turn":
            return await orchestrator.wait_for_turn(
                agent_id=agent_id,
                room_id=args.get("room_id"),
                client_type=client_hint,
                max_wait_sec=args.get("max_wait_sec"),
            )

        if name == "get_room_context":
            orchestrator.touch_agent(agent_id, client_type=client_hint)
            rid = args["room_id"]
            room = db.get_room(rid)
            active_camp = (
                db.get_campaign(room["active_campaign_id"])
                if room and room.get("active_campaign_id")
                else None
            )
            return {
                "room": room,
                "agents": db.list_agents(),
                "active_campaign": active_camp,
                "recent_messages": db.list_messages(rid, limit=int(args.get("limit", 30))),
            }

        if name == "post_message":
            return await orchestrator.handle_agent_message(
                agent_id=agent_id,
                room_id=args["room_id"],
                content=args["content"],
                mention_agents=args.get("mention_agents"),
            )

        if name == "submit_scenario_proposal":
            return await orchestrator.submit_scenario_proposal(
                agent_id=agent_id,
                room_id=args["room_id"],
                title=args["title"],
                role_assignments=args.get("role_assignments", {}),
                steps_dag=args.get("steps_dag", []),
                target_configs_preview=args.get("target_configs_preview", {}),
                summary_comment=args.get("summary_comment", ""),
            )

        if name == "complete_scenario_step":
            return await orchestrator.complete_scenario_step(
                agent_id=agent_id,
                campaign_id=args["campaign_id"],
                step_id=args["step_id"],
                result_note=args["result_note"],
            )

        if name == "list_servers":
            orchestrator.touch_agent(agent_id, client_type=client_hint)
            return {"servers": db.list_servers(), "head_git_hash": git_store.get_head_hash()}

        if name == "get_environment_snapshot":
            orchestrator.touch_agent(agent_id, client_type=client_hint)
            sid = args["server_id"]
            if args.get("force_refresh"):
                return await ssh_gateway.collect_snapshot(
                    server_id=sid,
                    trigger_type="AGENT_INSPECT",
                    triggered_by=agent_id,
                )
            latest = db.get_latest_snapshot(sid)
            if latest:
                return {"snapshot": latest, "cached": True}
            return await ssh_gateway.collect_snapshot(
                server_id=sid,
                trigger_type="AGENT_INSPECT",
                triggered_by=agent_id,
            )

        if name == "ssh_exec":
            orchestrator.touch_agent(agent_id, client_type=client_hint, working=True)
            return await ssh_gateway.execute_command(
                server_id=args["server_id"],
                actor_id=agent_id,
                command=args["command"],
                purpose=args.get("purpose", "MCP 에이전트 원격 실행"),
                room_id=args.get("room_id"),
                campaign_id=args.get("campaign_id"),
                timeout=int(args.get("timeout", 120)),
            )

        if name == "update_server_config":
            orchestrator.touch_agent(agent_id, client_type=client_hint, working=True)
            return await ssh_gateway.update_server_config(
                server_id=args["server_id"],
                actor_id=agent_id,
                repo_path=args["repo_path"],
                content=args["content"],
                commit_message=args["commit_message"],
                apply_command=args.get("apply_command"),
                room_id=args.get("room_id"),
                campaign_id=args.get("campaign_id"),
            )

        if name == "diff_server_configs":
            orchestrator.touch_agent(agent_id, client_type=client_hint)
            return git_store.compute_diff(
                base_hash=args.get("base_hash"),
                target_hash=args.get("target_hash"),
                server_prefix=args.get("server_id"),
            )

        if name == "rollback_server_config":
            orchestrator.touch_agent(agent_id, client_type=client_hint, working=True)
            return await ssh_gateway.rollback_config(
                target_hash=args["target_hash"],
                actor_id=agent_id,
                server_id=args.get("server_id"),
                room_id=args.get("room_id"),
            )

        if name == "run_benchmark_step":
            orchestrator.touch_agent(agent_id, client_type=client_hint, working=True)
            return await ssh_gateway.run_benchmark_step(
                campaign_id=args["campaign_id"],
                server_id=args["server_id"],
                actor_id=agent_id,
                variant_label=args["variant_label"],
                offload_target=args["offload_target"],
                command=args["command"],
                metrics_override=args.get("metrics_override"),
                room_id=args.get("room_id"),
            )

        if name == "publish_benchmark_report":
            return await orchestrator.publish_report(
                agent_id=agent_id,
                campaign_id=args["campaign_id"],
                title=args["title"],
                summary_markdown=args["summary_markdown"],
                comparison_highlights=args.get("comparison_highlights"),
            )

        raise ValueError(f"알 수 없는 MCP 툴입니다: {name}")

    async def handle_jsonrpc_message(
        rpc: dict[str, Any], default_agent_id: str, client_hint: str
    ) -> dict[str, Any] | None:
        method = rpc.get("method", "")
        req_id = rpc.get("id")
        params = rpc.get("params") or {}

        # JSON-RPC Notifications (no id) -> no response object needed
        if req_id is None:
            return None

        if method == "initialize":
            client_proto = params.get("protocolVersion", "2025-03-26")
            c_info = params.get("clientInfo") or {}
            if c_info.get("name") and default_agent_id:
                orchestrator.touch_agent(default_agent_id, client_type=c_info["name"])
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": client_proto,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {
                        "name": "agent-meeting-room-mcp",
                        "version": "1.0.0",
                    },
                    "instructions": (
                        "사내 에이전트 미팅룸 및 LLM 메모리 벤치마크 중앙 게이트웨이입니다. "
                        "대기 모드 진입 시 wait_for_turn을 호출하고, 차례가 부여되면 작업 수행 후 다시 wait_for_turn으로 복귀하세요."
                    ),
                },
            }

        if method == "ping":
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}

        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": MCP_TOOLS_SCHEMA}}

        if method == "resources/list":
            return {"jsonrpc": "2.0", "id": req_id, "result": {"resources": []}}

        if method == "prompts/list":
            return {"jsonrpc": "2.0", "id": req_id, "result": {"prompts": []}}

        if method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments") or {}
            try:
                output = await dispatch_tool(
                    tool_name, tool_args, default_agent_id=default_agent_id, client_hint=client_hint
                )
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(output, ensure_ascii=False, indent=2),
                            }
                        ],
                        "isError": False,
                    },
                }
            except Exception as exc:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": f"오류 발생: {exc}"}],
                        "isError": True,
                    },
                }

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"지원하지 않는 메서드입니다: {method}"},
        }

    # --- 1) Streamable HTTP MCP Transport (`/mcp`) ---

    @router.post("/mcp")
    async def mcp_streamable_post(request: Request) -> Response:
        resolve_identity(request)
        default_agent_id = (
            request.query_params.get("agent_id")
            or request.headers.get("X-Agent-Id")
            or ""
        )
        client_hint = (
            request.query_params.get("client")
            or request.headers.get("User-Agent", "MCP Client")[:40]
        )
        session_id = request.headers.get("Mcp-Session-Id") or str(uuid.uuid4())
        body = await request.json()

        if isinstance(body, list):
            responses = []
            for item in body:
                res = await handle_jsonrpc_message(item, default_agent_id, client_hint)
                if res is not None:
                    responses.append(res)
            if not responses:
                return Response(status_code=202, headers={"Mcp-Session-Id": session_id})
            return JSONResponse(responses, headers={"Mcp-Session-Id": session_id})

        res = await handle_jsonrpc_message(body, default_agent_id, client_hint)
        if res is None:
            return Response(status_code=202, headers={"Mcp-Session-Id": session_id})
        return JSONResponse(res, headers={"Mcp-Session-Id": session_id})

    @router.get("/mcp")
    async def mcp_streamable_get(request: Request) -> Response:
        resolve_identity(request)
        accept = request.headers.get("Accept", "")
        if "text/event-stream" in accept:
            async def keepalive_stream():
                try:
                    while True:
                        yield ": keepalive\n\n"
                        await asyncio.sleep(20)
                except asyncio.CancelledError:
                    return

            return StreamingResponse(keepalive_stream(), media_type="text/event-stream")

        return JSONResponse(
            {
                "service": "agent-meeting-room-mcp",
                "transport": ["streamable-http (/mcp)", "sse (/mcp/sse)"],
                "tool_count": len(MCP_TOOLS_SCHEMA),
                "tools": [t["name"] for t in MCP_TOOLS_SCHEMA],
            }
        )

    @router.delete("/mcp")
    async def mcp_streamable_delete() -> Response:
        return Response(status_code=204)

    # --- 2) Legacy HTTP + SSE MCP Transport (`/mcp/sse` + `/mcp/messages`) ---

    @router.get("/mcp/sse")
    async def mcp_legacy_sse(request: Request) -> StreamingResponse:
        resolve_identity(request)
        sid = str(uuid.uuid4())
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        meta = {
            "agent_id": request.query_params.get("agent_id", ""),
            "client": request.query_params.get("client", "SSE MCP Client"),
        }
        sse_sessions[sid] = (q, meta)

        query_suffix = f"session_id={sid}"
        if meta["agent_id"]:
            query_suffix += f"&agent_id={meta['agent_id']}"

        async def event_gen():
            try:
                yield f"event: endpoint\ndata: /mcp/messages?{query_suffix}\n\n"
                while True:
                    try:
                        msg = await asyncio.wait_for(q.get(), timeout=20)
                        yield f"event: message\ndata: {json.dumps(msg, ensure_ascii=False)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
            finally:
                sse_sessions.pop(sid, None)

        return StreamingResponse(event_gen(), media_type="text/event-stream")

    @router.post("/mcp/messages")
    async def mcp_legacy_messages(request: Request) -> Response:
        resolve_identity(request)
        sid = request.query_params.get("session_id", "")
        sess = sse_sessions.get(sid)
        if not sess:
            return JSONResponse({"error": "Invalid SSE session"}, status_code=400)
        q, meta = sess
        default_agent_id = request.query_params.get("agent_id") or meta.get("agent_id", "")
        client_hint = meta.get("client", "SSE MCP Client")
        body = await request.json()
        res = await handle_jsonrpc_message(body, default_agent_id, client_hint)
        if res is not None:
            await q.put(res)
        return Response(status_code=202)

    return router
