from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .auth import resolve_identity
from .config import settings
from .db import Database
from .git_store import EmbeddedGitStore
from .mcp_endpoint import create_mcp_router
from .orchestrator import MeetingRoomOrchestrator
from .ssh_gateway import SSHGateway


class WSManager:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    async def broadcast(self, event: dict[str, Any]) -> None:
        if not self.clients:
            return
        text = json.dumps(event, ensure_ascii=False)
        dead: list[WebSocket] = []
        for ws in list(self.clients):
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)


# --- Request Schemas ---

class CreateRoomPayload(BaseModel):
    name: str
    room_type: str = "group"
    target_agent_id: str | None = None


class PostUserMessagePayload(BaseModel):
    content: str
    user_id: str | None = None


class SimulateMeetingPayload(BaseModel):
    topic: str | None = None
    user_id: str | None = None


class ApprovePayload(BaseModel):
    user_id: str | None = None


class FeedbackPayload(BaseModel):
    feedback: str
    user_id: str | None = None


class RefreshServerPayload(BaseModel):
    force: bool = False


class UpdateConfigPayload(BaseModel):
    server_id: str
    repo_path: str
    content: str
    commit_message: str
    apply_command: str | None = None


class RollbackPayload(BaseModel):
    target_hash: str
    server_id: str | None = None


class UpsertServerPayload(BaseModel):
    id: str | None = None
    name: str
    host: str
    ssh_port: int = 22
    ssh_user: str = "ubuntu"
    ssh_key_path: str = ""
    ssh_password: str | None = None
    server_role: str = "GPU"
    connection_mode: str = "ssh"
    k8s_namespace: str = "dynamo-llm"
    watched_configs: list[dict[str, Any]] = Field(default_factory=list)


def create_app() -> FastAPI:
    settings.ensure_dirs()
    ws_manager = WSManager()
    db = Database(settings.db_path)
    git_store = EmbeddedGitStore(settings.config_repo_dir)
    ssh_gateway = SSHGateway(db=db, git_store=git_store, broadcast_fn=ws_manager.broadcast)
    orchestrator = MeetingRoomOrchestrator(
        db=db, ssh_gateway=ssh_gateway, broadcast_fn=ws_manager.broadcast
    )

    app = FastAPI(
        title="Agent Meeting Room & LLM Memory Benchmark Hub",
        version="1.0.0",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount Single-Port MCP Endpoint (/mcp & /mcp/sse)
    app.include_router(create_mcp_router(db, git_store, ssh_gateway, orchestrator))

    # Store references on app.state for programmatic tests
    app.state.db = db
    app.state.git_store = git_store
    app.state.ssh_gateway = ssh_gateway
    app.state.orchestrator = orchestrator

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket) -> None:
        await ws_manager.connect(ws)
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            ws_manager.disconnect(ws)
        except Exception:
            ws_manager.disconnect(ws)

    @app.get("/api/bootstrap")
    async def api_bootstrap(request: Request) -> dict[str, Any]:
        ident = resolve_identity(request)
        orchestrator.get_online_agent_ids()
        servers = db.list_servers()
        latest_snapshots = {
            s["id"]: db.get_latest_snapshot(s["id"]) for s in servers
        }
        return {
            "identity": {
                "actor_id": ident.actor_id,
                "auth_mode": settings.auth_mode,
            },
            "users": db.list_users(),
            "agents": db.list_agents(),
            "rooms": db.list_rooms(),
            "servers": servers,
            "latest_snapshots": latest_snapshots,
            "head_git_hash": git_store.get_head_hash(),
            "commits": db.list_config_commits(limit=40),
            "campaigns": db.list_campaigns(),
            "runs": db.list_benchmark_runs(),
            "reports": db.list_reports(),
            "ssh_logs": db.list_ssh_audit_logs(limit=50),
        }

    @app.get("/api/rooms")
    async def api_list_rooms(request: Request) -> list[dict[str, Any]]:
        resolve_identity(request)
        return db.list_rooms()

    @app.post("/api/rooms")
    async def api_create_room(payload: CreateRoomPayload, request: Request) -> dict[str, Any]:
        resolve_identity(request)
        room = db.create_room(
            name=payload.name,
            room_type=payload.room_type,
            target_agent_id=payload.target_agent_id,
        )
        await ws_manager.broadcast({"type": "room_created", "room": room})
        return room

    @app.get("/api/rooms/{room_id}/messages")
    async def api_list_messages(room_id: str, request: Request) -> dict[str, Any]:
        resolve_identity(request)
        room = db.get_room(room_id)
        if not room:
            raise HTTPException(status_code=404, detail="Room not found")
        active_campaign = (
            db.get_campaign(room["active_campaign_id"])
            if room.get("active_campaign_id")
            else None
        )
        return {
            "room": room,
            "active_campaign": active_campaign,
            "messages": db.list_messages(room_id, limit=150),
        }

    @app.post("/api/rooms/{room_id}/messages")
    async def api_post_message(
        room_id: str, payload: PostUserMessagePayload, request: Request
    ) -> dict[str, Any]:
        ident = resolve_identity(request)
        uid = payload.user_id or ident.actor_id
        return await orchestrator.handle_user_message(
            room_id=room_id, user_id=uid, content=payload.content
        )

    @app.post("/api/rooms/{room_id}/simulate")
    async def api_simulate_meeting(
        room_id: str, payload: SimulateMeetingPayload, request: Request
    ) -> dict[str, Any]:
        ident = resolve_identity(request)
        uid = payload.user_id or ident.actor_id
        return await orchestrator.start_simulated_meeting(
            room_id=room_id, user_id=uid, topic=payload.topic
        )

    @app.post("/api/campaigns/{campaign_id}/approve")
    async def api_approve_campaign(
        campaign_id: str, payload: ApprovePayload, request: Request
    ) -> dict[str, Any]:
        ident = resolve_identity(request)
        uid = payload.user_id or ident.actor_id
        return await orchestrator.approve_campaign(campaign_id=campaign_id, user_id=uid)

    @app.post("/api/campaigns/{campaign_id}/request-changes")
    async def api_request_changes(
        campaign_id: str, payload: FeedbackPayload, request: Request
    ) -> dict[str, Any]:
        ident = resolve_identity(request)
        uid = payload.user_id or ident.actor_id
        return await orchestrator.request_campaign_changes(
            campaign_id=campaign_id, user_id=uid, feedback=payload.feedback
        )

    # --- Servers & On-Demand SSH Environment Collection ---

    @app.get("/api/servers")
    async def api_list_servers(request: Request) -> dict[str, Any]:
        resolve_identity(request)
        servers = db.list_servers()
        snapshots = {s["id"]: db.get_latest_snapshot(s["id"]) for s in servers}
        return {"servers": servers, "latest_snapshots": snapshots}

    @app.post("/api/servers")
    async def api_upsert_server(payload: UpsertServerPayload, request: Request) -> dict[str, Any]:
        ident = resolve_identity(request)
        srv = db.upsert_server(payload.model_dump())
        await ws_manager.broadcast({"type": "servers_updated", "servers": db.list_servers()})
        try:
            snap_res = await ssh_gateway.collect_snapshot(
                server_id=srv["id"],
                trigger_type="SERVER_UPSERT",
                triggered_by=ident.actor_id,
                force=True,
            )
            return {"server": srv, "snapshot_result": snap_res}
        except Exception as exc:
            return {"server": srv, "collect_warning": str(exc)}

    @app.post("/api/servers/{server_id}/refresh")
    async def api_refresh_server(
        server_id: str, payload: RefreshServerPayload, request: Request
    ) -> dict[str, Any]:
        ident = resolve_identity(request)
        try:
            return await ssh_gateway.collect_snapshot(
                server_id=server_id,
                trigger_type="ON_DEMAND_WEB",
                triggered_by=ident.actor_id,
                force=payload.force,
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc))

    @app.post("/api/servers/refresh-all")
    async def api_refresh_all_servers(
        payload: RefreshServerPayload, request: Request
    ) -> dict[str, Any]:
        ident = resolve_identity(request)
        results: dict[str, Any] = {}
        errors: dict[str, str] = {}
        for srv in db.list_servers():
            try:
                results[srv["id"]] = await ssh_gateway.collect_snapshot(
                    server_id=srv["id"],
                    trigger_type="ON_DEMAND_WEB",
                    triggered_by=ident.actor_id,
                    force=payload.force,
                )
            except Exception as exc:
                errors[srv["id"]] = str(exc)
        return {"results": results, "errors": errors}

    # --- Embedded Git Config Explorer, Diff & 1-Click Rollback ---

    @app.get("/api/configs/tree")
    async def api_get_config_tree(request: Request, commit_hash: str | None = None) -> dict[str, Any]:
        resolve_identity(request)
        head = git_store.get_head_hash()
        target = commit_hash or head
        files = git_store.get_commit_tree(target) if target else git_store.get_current_working_tree()
        return {
            "head_hash": head,
            "selected_hash": target,
            "files": files,
            "commits": db.list_config_commits(limit=50),
        }

    @app.get("/api/configs/diff")
    async def api_get_config_diff(
        request: Request,
        base_hash: str | None = None,
        target_hash: str | None = None,
        server_id: str | None = None,
    ) -> dict[str, Any]:
        resolve_identity(request)
        commits = db.list_config_commits(limit=50)
        if not target_hash and commits:
            target_hash = commits[0]["git_hash"]
        if not base_hash and len(commits) >= 2:
            base_hash = commits[1]["git_hash"]
        return git_store.compute_diff(
            base_hash=base_hash, target_hash=target_hash, server_prefix=server_id
        )

    @app.post("/api/configs/update")
    async def api_update_config(payload: UpdateConfigPayload, request: Request) -> dict[str, Any]:
        ident = resolve_identity(request)
        return await ssh_gateway.update_server_config(
            server_id=payload.server_id,
            actor_id=ident.actor_id,
            repo_path=payload.repo_path,
            content=payload.content,
            commit_message=payload.commit_message,
            apply_command=payload.apply_command,
        )

    @app.post("/api/configs/rollback")
    async def api_rollback_config(payload: RollbackPayload, request: Request) -> dict[str, Any]:
        ident = resolve_identity(request)
        return await ssh_gateway.rollback_config(
            target_hash=payload.target_hash,
            actor_id=ident.actor_id,
            server_id=payload.server_id,
        )

    # --- Campaigns, Runs, Logs & Reports ---

    @app.get("/api/ssh-logs")
    async def api_ssh_logs(request: Request, room_id: str | None = None) -> list[dict[str, Any]]:
        resolve_identity(request)
        return db.list_ssh_audit_logs(room_id=room_id, limit=100)

    @app.get("/api/campaigns")
    async def api_campaigns(request: Request) -> dict[str, Any]:
        resolve_identity(request)
        return {
            "campaigns": db.list_campaigns(),
            "runs": db.list_benchmark_runs(),
            "reports": db.list_reports(),
        }

    @app.get("/api/runs/{run_id}/log")
    async def api_get_run_log(run_id: str, request: Request) -> dict[str, Any]:
        resolve_identity(request)
        run = db.get_benchmark_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        raw_text = ""
        if run.get("raw_log_path") and Path(run["raw_log_path"]).exists():
            raw_text = Path(run["raw_log_path"]).read_text(encoding="utf-8", errors="replace")
        return {"run": run, "raw_log": raw_text}

    @app.get("/api/reports/{report_id}/export.md")
    async def api_export_report_md(report_id: str, request: Request) -> PlainTextResponse:
        resolve_identity(request)
        report = db.get_report(report_id)
        if not report:
            raise HTTPException(status_code=404, detail="Report not found")
        runs = db.list_benchmark_runs(campaign_id=report["campaign_id"])
        lines = [
            f"# {report['title']}",
            "",
            f"- **캠페인 ID**: `{report['campaign_id']}`",
            f"- **작성 에이전트**: `{report['author_agent_id']}`",
            f"- **생성 시각**: `{report['created_at']}`",
            "",
            "## 정량 벤치마크 지표 요약",
            "",
            "| 실험군 (Variant) | 오프로딩 타겟 | Git 커밋 | TTFT p50 (ms) | TTFT p99 (ms) | TPOT p50 (ms) | 처리량 (tok/s) | KV 히트율 (%) | 전송 대역폭 (GB/s) |",
            "| :--- | :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for r in runs:
            m = r.get("metrics") or {}
            lines.append(
                f"| {r['variant_label']} | `{r['offload_target']}` | `{r.get('git_hash') or '-'}` | "
                f"{m.get('ttft_p50_ms', '-')} | {m.get('ttft_p99_ms', '-')} | {m.get('tpot_p50_ms', '-')} | "
                f"{m.get('throughput_tok_s', '-')} | {m.get('kv_cache_hit_rate_pct', '-')} | {m.get('offload_bw_gbps', '-')} |"
            )
        lines.extend(["", report["summary_markdown"], ""])
        return PlainTextResponse(
            "\n".join(lines),
            media_type="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="benchmark_report_{report["campaign_id"]}.md"'
            },
        )

    # --- Serve Built Frontend Static Bundle on the Same Single Port ---
    static_dir = settings.static_dir
    assets_dir = static_dir / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str) -> Any:
        if full_path.startswith(("api/", "mcp", "ws")):
            raise HTTPException(status_code=404, detail="Not Found")
        index_file = static_dir / "index.html"
        target_file = (static_dir / full_path).resolve()
        if (
            full_path
            and str(target_file).startswith(str(static_dir.resolve()))
            and target_file.is_file()
        ):
            return FileResponse(str(target_file))
        if index_file.exists():
            return FileResponse(str(index_file))
        return JSONResponse(
            {
                "status": "Agent Meeting Room Backend Running",
                "note": "Frontend bundle not built yet in frontend/dist.",
                "mcp_endpoint": "/mcp",
            }
        )

    return app


app = create_app()
