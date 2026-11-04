from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    """SQLite (WAL mode) repository for the Agent Meeting Room & Benchmark Hub."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL;")
            cur.execute("PRAGMA synchronous=NORMAL;")
            cur.execute("PRAGMA foreign_keys=ON;")

            cur.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    role_label TEXT NOT NULL,
                    avatar_color TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS agents (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL,
                    primary_role TEXT NOT NULL,
                    specialty_desc TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'OFFLINE',
                    assigned_roles_json TEXT NOT NULL DEFAULT '[]',
                    client_type TEXT NOT NULL DEFAULT 'Claude Code / Hermes',
                    last_seen_at TEXT
                );

                CREATE TABLE IF NOT EXISTS rooms (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    room_type TEXT NOT NULL,
                    target_agent_id TEXT,
                    workflow_state TEXT NOT NULL DEFAULT 'IDLE',
                    current_topic TEXT,
                    active_campaign_id TEXT,
                    discussion_round INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    room_id TEXT NOT NULL,
                    sender_type TEXT NOT NULL,
                    sender_id TEXT NOT NULL,
                    sender_name TEXT NOT NULL,
                    role_badge TEXT,
                    msg_kind TEXT NOT NULL DEFAULT 'chat',
                    content TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (room_id) REFERENCES rooms(id)
                );

                CREATE TABLE IF NOT EXISTS scenario_campaigns (
                    id TEXT PRIMARY KEY,
                    room_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    topic_prompt TEXT NOT NULL,
                    lead_agent_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'APPROVAL_PENDING',
                    role_assignments_json TEXT NOT NULL DEFAULT '{}',
                    steps_dag_json TEXT NOT NULL DEFAULT '[]',
                    target_configs_preview_json TEXT NOT NULL DEFAULT '{}',
                    approved_by TEXT,
                    approved_at TEXT,
                    feedback_note TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS servers (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    host TEXT NOT NULL,
                    ssh_port INTEGER NOT NULL DEFAULT 22,
                    ssh_user TEXT NOT NULL DEFAULT 'ubuntu',
                    ssh_key_path TEXT NOT NULL DEFAULT '',
                    ssh_password TEXT NOT NULL DEFAULT '',
                    server_role TEXT NOT NULL,
                    connection_mode TEXT NOT NULL DEFAULT 'simulation',
                    k8s_namespace TEXT NOT NULL DEFAULT 'dynamo-llm',
                    watched_configs_json TEXT NOT NULL DEFAULT '[]',
                    lock_status TEXT NOT NULL DEFAULT 'FREE',
                    lock_holder TEXT,
                    lock_reason TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS env_snapshots (
                    id TEXT PRIMARY KEY,
                    server_id TEXT NOT NULL,
                    trigger_type TEXT NOT NULL,
                    triggered_by TEXT NOT NULL,
                    campaign_id TEXT,
                    git_hash TEXT,
                    hw_specs_json TEXT NOT NULL,
                    sw_specs_json TEXT NOT NULL,
                    active_configs_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (server_id) REFERENCES servers(id)
                );

                CREATE TABLE IF NOT EXISTS config_commits (
                    git_hash TEXT PRIMARY KEY,
                    parent_hash TEXT,
                    native_git_hash TEXT,
                    author TEXT NOT NULL,
                    server_id TEXT,
                    campaign_id TEXT,
                    summary TEXT NOT NULL,
                    changed_files_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS ssh_audit_logs (
                    id TEXT PRIMARY KEY,
                    room_id TEXT,
                    campaign_id TEXT,
                    server_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    command TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    exit_code INTEGER NOT NULL,
                    stdout TEXT NOT NULL,
                    stderr TEXT NOT NULL,
                    duration_ms INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS benchmark_runs (
                    id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL,
                    server_id TEXT NOT NULL,
                    variant_label TEXT NOT NULL,
                    offload_target TEXT NOT NULL,
                    command TEXT NOT NULL,
                    git_hash TEXT,
                    pre_snapshot_id TEXT,
                    post_snapshot_id TEXT,
                    metrics_json TEXT NOT NULL DEFAULT '{}',
                    raw_log_path TEXT,
                    status TEXT NOT NULL DEFAULT 'RUNNING',
                    executed_by TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT
                );

                CREATE TABLE IF NOT EXISTS reports (
                    id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL UNIQUE,
                    room_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    author_agent_id TEXT NOT NULL,
                    summary_markdown TEXT NOT NULL,
                    comparison_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                """
            )
            self._conn.commit()
            self._seed_defaults()

    def _seed_defaults(self) -> None:
        cur = self._conn.cursor()
        user_count = cur.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if user_count == 0:
            users = [
                ("member_a", "팀원 A (GPU 인프라)", "GPU System Engineer", "#3b82f6"),
                ("member_b", "팀원 B (메모리/CXL)", "Memory Architecture Eng", "#10b981"),
                ("member_c", "팀원 C (서빙/DGD)", "LLM Serving Engineer", "#f59e0b"),
                ("member_d", "팀원 D (성능평가/리드)", "Benchmark Tech Lead", "#8b5cf6"),
            ]
            cur.executemany(
                "INSERT INTO users (id, name, role_label, avatar_color) VALUES (?, ?, ?, ?)",
                users,
            )

        agent_count = cur.execute("SELECT COUNT(*) FROM agents").fetchone()[0]
        if agent_count == 0:
            agents = [
                (
                    "agent_a",
                    "Agent A",
                    "member_a",
                    "ROLE_GPU",
                    "GPU 서버 토폴로지(TP4)·MIG·클럭·K8s GPU 파드 점검 및 설정",
                ),
                (
                    "agent_b",
                    "Agent B",
                    "member_b",
                    "ROLE_REMOTE",
                    "리모트 서버 Mooncake Store 데몬, CXL Memory(NUMA/DAX)·DRAM·NVMe SSD 구성",
                ),
                (
                    "agent_c",
                    "Agent C",
                    "member_c",
                    "ROLE_CONFIG",
                    "K8s DynamoGraphDeployment(DGD) 매니페스트 및 LMCache/Mooncake YAML 작성·배포",
                ),
                (
                    "agent_d",
                    "Agent D",
                    "member_d",
                    "ROLE_ORCH",
                    "미팅룸 총괄 진행, 시나리오 취합, 벤치마크 순차 구동 및 비교 리포트 생성",
                ),
            ]
            cur.executemany(
                """
                INSERT INTO agents (id, name, owner_user_id, primary_role, specialty_desc)
                VALUES (?, ?, ?, ?, ?)
                """,
                agents,
            )

        room_count = cur.execute("SELECT COUNT(*) FROM rooms").fetchone()[0]
        now = utc_now_iso()
        if room_count == 0:
            rooms = [
                ("room-main", "전체 에이전트 미팅룸 (벤치마크 기획·실행)", "group", None, "IDLE", now),
                ("room-cxl-eval", "CXL vs DRAM vs SSD 오프로딩 평가방", "group", None, "IDLE", now),
                ("room-dm-agent-a", "1:1 대화 · Agent A (GPU 서버)", "direct", "agent_a", "IDLE", now),
                ("room-dm-agent-b", "1:1 대화 · Agent B (리모트/메모리)", "direct", "agent_b", "IDLE", now),
                ("room-dm-agent-c", "1:1 대화 · Agent C (DGD/YAML)", "direct", "agent_c", "IDLE", now),
                ("room-dm-agent-d", "1:1 대화 · Agent D (총괄/벤치마크)", "direct", "agent_d", "IDLE", now),
            ]
            cur.executemany(
                """
                INSERT INTO rooms (id, name, room_type, target_agent_id, workflow_state, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                rooms,
            )
            cur.execute(
                """
                INSERT INTO messages (id, room_id, sender_type, sender_id, sender_name, role_badge, msg_kind, content, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    "room-main",
                    "system",
                    "system",
                    "시스템 안내",
                    "SYSTEM",
                    "system",
                    "에이전트 미팅룸이 준비되었습니다. 각 팀원의 Claude Code / Hermes에서 MCP 서버(`/mcp`)를 연결해 대기 모드로 입장하거나, 채팅창에 벤치마크 주제를 입력해 회의를 시작하세요.",
                    "{}",
                    now,
                ),
            )

        server_count = cur.execute("SELECT COUNT(*) FROM servers").fetchone()[0]
        if server_count == 0:
            gpu_watched = json.dumps(
                [
                    {"kind": "k8s_dgd", "name": "vllm-disagg-tp4", "repo_path": "gpu-node-01/k8s/dgd-active.yaml"},
                    {"kind": "file", "remote_path": "/etc/lmcache/lmcache_config.yaml", "repo_path": "gpu-node-01/configs/lmcache_config.yaml"},
                ],
                ensure_ascii=False,
            )
            remote_watched = json.dumps(
                [
                    {"kind": "file", "remote_path": "/etc/mooncake/mooncake_store.yaml", "repo_path": "remote-mem-01/configs/mooncake_store.yaml"},
                    {"kind": "file", "remote_path": "/etc/mooncake/cxl_tiering.env", "repo_path": "remote-mem-01/configs/cxl_tiering.env"},
                ],
                ensure_ascii=False,
            )
            servers = [
                (
                    "gpu-node-01",
                    "GPU 서빙 서버 #1 (TP4 · Dynamo K8s)",
                    "10.20.30.101",
                    22,
                    "ubuntu",
                    "~/.ssh/id_ed25519",
                    "",
                    "GPU",
                    "simulation",
                    "dynamo-llm",
                    gpu_watched,
                    "FREE",
                    now,
                ),
                (
                    "remote-mem-01",
                    "리모트 메모리 서버 #1 (Mooncake · CXL/DRAM/SSD)",
                    "10.20.30.102",
                    22,
                    "ubuntu",
                    "~/.ssh/id_ed25519",
                    "",
                    "REMOTE",
                    "simulation",
                    "dynamo-llm",
                    remote_watched,
                    "FREE",
                    now,
                ),
            ]
            cur.executemany(
                """
                INSERT INTO servers (
                    id, name, host, ssh_port, ssh_user, ssh_key_path, ssh_password,
                    server_role, connection_mode, k8s_namespace, watched_configs_json, lock_status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                servers,
            )

        self._conn.commit()

    def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        with self._lock:
            self._conn.execute(sql, params)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- Domain Helpers ---

    def list_users(self) -> list[dict[str, Any]]:
        return self.fetch_all("SELECT * FROM users ORDER BY id")

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        return self.fetch_one("SELECT * FROM users WHERE id = ?", (user_id,))

    def list_agents(self) -> list[dict[str, Any]]:
        rows = self.fetch_all("SELECT * FROM agents ORDER BY id")
        for r in rows:
            r["assigned_roles"] = json.loads(r.pop("assigned_roles_json") or "[]")
        return rows

    def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        r = self.fetch_one("SELECT * FROM agents WHERE id = ?", (agent_id,))
        if r:
            r["assigned_roles"] = json.loads(r.pop("assigned_roles_json") or "[]")
        return r

    def update_agent_presence(
        self,
        agent_id: str,
        status: str,
        client_type: str | None = None,
        assigned_roles: list[str] | None = None,
    ) -> None:
        now = utc_now_iso()
        current = self.get_agent(agent_id)
        if not current:
            return
        roles = assigned_roles if assigned_roles is not None else current["assigned_roles"]
        ctype = client_type or current["client_type"]
        self.execute(
            """
            UPDATE agents
            SET status = ?, client_type = ?, assigned_roles_json = ?, last_seen_at = ?
            WHERE id = ?
            """,
            (status, ctype, json.dumps(roles, ensure_ascii=False), now, agent_id),
        )

    def set_agent_assigned_roles(self, agent_id: str, roles: list[str]) -> None:
        self.execute(
            "UPDATE agents SET assigned_roles_json = ? WHERE id = ?",
            (json.dumps(roles, ensure_ascii=False), agent_id),
        )

    def list_rooms(self) -> list[dict[str, Any]]:
        return self.fetch_all("SELECT * FROM rooms ORDER BY room_type DESC, id ASC")

    def get_room(self, room_id: str) -> dict[str, Any] | None:
        return self.fetch_one("SELECT * FROM rooms WHERE id = ?", (room_id,))

    def create_room(self, name: str, room_type: str = "group", target_agent_id: str | None = None) -> dict[str, Any]:
        room_id = f"room-{uuid.uuid4().hex[:8]}"
        now = utc_now_iso()
        self.execute(
            """
            INSERT INTO rooms (id, name, room_type, target_agent_id, workflow_state, created_at)
            VALUES (?, ?, ?, ?, 'IDLE', ?)
            """,
            (room_id, name, room_type, target_agent_id, now),
        )
        return self.get_room(room_id)  # type: ignore[return-value]

    def update_room_workflow(
        self,
        room_id: str,
        workflow_state: str,
        current_topic: str | None = None,
        active_campaign_id: str | None = None,
        discussion_round: int | None = None,
    ) -> None:
        room = self.get_room(room_id)
        if not room:
            return
        self.execute(
            """
            UPDATE rooms
            SET workflow_state = ?,
                current_topic = ?,
                active_campaign_id = ?,
                discussion_round = ?
            WHERE id = ?
            """,
            (
                workflow_state,
                current_topic if current_topic is not None else room["current_topic"],
                active_campaign_id if active_campaign_id is not None else room["active_campaign_id"],
                discussion_round if discussion_round is not None else room["discussion_round"],
                room_id,
            ),
        )

    def add_message(
        self,
        room_id: str,
        sender_type: str,
        sender_id: str,
        sender_name: str,
        content: str,
        role_badge: str | None = None,
        msg_kind: str = "chat",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        msg_id = str(uuid.uuid4())
        now = utc_now_iso()
        meta_str = json.dumps(metadata or {}, ensure_ascii=False)
        self.execute(
            """
            INSERT INTO messages (
                id, room_id, sender_type, sender_id, sender_name,
                role_badge, msg_kind, content, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (msg_id, room_id, sender_type, sender_id, sender_name, role_badge, msg_kind, content, meta_str, now),
        )
        return self.get_message(msg_id)  # type: ignore[return-value]

    def get_message(self, msg_id: str) -> dict[str, Any] | None:
        r = self.fetch_one("SELECT * FROM messages WHERE id = ?", (msg_id,))
        if r:
            r["metadata"] = json.loads(r.pop("metadata_json") or "{}")
        return r

    def list_messages(self, room_id: str, limit: int = 120) -> list[dict[str, Any]]:
        rows = self.fetch_all(
            """
            SELECT * FROM (
                SELECT * FROM messages WHERE room_id = ? ORDER BY created_at DESC LIMIT ?
            ) ORDER BY created_at ASC
            """,
            (room_id, limit),
        )
        for r in rows:
            r["metadata"] = json.loads(r.pop("metadata_json") or "{}")
        return rows

    def create_campaign(
        self,
        room_id: str,
        title: str,
        topic_prompt: str,
        lead_agent_id: str,
        role_assignments: dict[str, Any],
        steps_dag: list[dict[str, Any]],
        target_configs_preview: dict[str, Any],
    ) -> dict[str, Any]:
        cid = f"camp-{uuid.uuid4().hex[:8]}"
        now = utc_now_iso()
        # Normalize step statuses
        normalized_steps = []
        for idx, s in enumerate(steps_dag):
            step = {
                "step_id": s.get("step_id") or f"step_{idx + 1}",
                "title": s.get("title", f"Step {idx + 1}"),
                "assigned_agent_id": s.get("assigned_agent_id", lead_agent_id),
                "role_code": s.get("role_code", "ROLE_ORCH"),
                "depends_on": s.get("depends_on", []),
                "server_id": s.get("server_id"),
                "action_summary": s.get("action_summary", ""),
                "status": "PENDING",
                "result_note": None,
            }
            normalized_steps.append(step)

        self.execute(
            """
            INSERT INTO scenario_campaigns (
                id, room_id, title, topic_prompt, lead_agent_id, status,
                role_assignments_json, steps_dag_json, target_configs_preview_json, created_at
            ) VALUES (?, ?, ?, ?, ?, 'APPROVAL_PENDING', ?, ?, ?, ?)
            """,
            (
                cid,
                room_id,
                title,
                topic_prompt,
                lead_agent_id,
                json.dumps(role_assignments, ensure_ascii=False),
                json.dumps(normalized_steps, ensure_ascii=False),
                json.dumps(target_configs_preview, ensure_ascii=False),
                now,
            ),
        )
        return self.get_campaign(cid)  # type: ignore[return-value]

    def get_campaign(self, campaign_id: str) -> dict[str, Any] | None:
        r = self.fetch_one("SELECT * FROM scenario_campaigns WHERE id = ?", (campaign_id,))
        if not r:
            return None
        r["role_assignments"] = json.loads(r.pop("role_assignments_json") or "{}")
        r["steps_dag"] = json.loads(r.pop("steps_dag_json") or "[]")
        r["target_configs_preview"] = json.loads(r.pop("target_configs_preview_json") or "{}")
        return r

    def list_campaigns(self, room_id: str | None = None) -> list[dict[str, Any]]:
        if room_id:
            rows = self.fetch_all(
                "SELECT * FROM scenario_campaigns WHERE room_id = ? ORDER BY created_at DESC",
                (room_id,),
            )
        else:
            rows = self.fetch_all("SELECT * FROM scenario_campaigns ORDER BY created_at DESC")
        for r in rows:
            r["role_assignments"] = json.loads(r.pop("role_assignments_json") or "{}")
            r["steps_dag"] = json.loads(r.pop("steps_dag_json") or "[]")
            r["target_configs_preview"] = json.loads(r.pop("target_configs_preview_json") or "{}")
        return rows

    def update_campaign(
        self,
        campaign_id: str,
        status: str | None = None,
        steps_dag: list[dict[str, Any]] | None = None,
        approved_by: str | None = None,
        feedback_note: str | None = None,
        completed: bool = False,
    ) -> dict[str, Any] | None:
        c = self.get_campaign(campaign_id)
        if not c:
            return None
        now = utc_now_iso()
        self.execute(
            """
            UPDATE scenario_campaigns
            SET status = ?,
                steps_dag_json = ?,
                approved_by = ?,
                approved_at = ?,
                feedback_note = ?,
                completed_at = ?
            WHERE id = ?
            """,
            (
                status or c["status"],
                json.dumps(steps_dag if steps_dag is not None else c["steps_dag"], ensure_ascii=False),
                approved_by if approved_by is not None else c["approved_by"],
                now if approved_by is not None else c["approved_at"],
                feedback_note if feedback_note is not None else c["feedback_note"],
                now if completed else c["completed_at"],
                campaign_id,
            ),
        )
        return self.get_campaign(campaign_id)

    # --- Servers, Snapshots, Commits, SSH Logs, Runs, Reports ---

    def list_servers(self) -> list[dict[str, Any]]:
        rows = self.fetch_all("SELECT * FROM servers ORDER BY id")
        for r in rows:
            r["watched_configs"] = json.loads(r.pop("watched_configs_json") or "[]")
            r.pop("ssh_password", None)
        return rows

    def get_server(self, server_id: str, include_secret: bool = False) -> dict[str, Any] | None:
        r = self.fetch_one("SELECT * FROM servers WHERE id = ?", (server_id,))
        if not r:
            return None
        r["watched_configs"] = json.loads(r.pop("watched_configs_json") or "[]")
        if not include_secret:
            r.pop("ssh_password", None)
        return r

    def upsert_server(self, payload: dict[str, Any]) -> dict[str, Any]:
        sid = payload.get("id") or f"srv-{uuid.uuid4().hex[:6]}"
        existing = self.get_server(sid, include_secret=True)
        now = utc_now_iso()
        watched = payload.get(
            "watched_configs",
            existing["watched_configs"] if existing else [],
        )
        password = payload.get("ssh_password")
        if password is None and existing:
            password = existing.get("ssh_password", "")
        password = password or ""

        if existing:
            self.execute(
                """
                UPDATE servers
                SET name = ?, host = ?, ssh_port = ?, ssh_user = ?, ssh_key_path = ?,
                    ssh_password = ?, server_role = ?, connection_mode = ?, k8s_namespace = ?,
                    watched_configs_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    payload.get("name", existing["name"]),
                    payload.get("host", existing["host"]),
                    int(payload.get("ssh_port", existing["ssh_port"])),
                    payload.get("ssh_user", existing["ssh_user"]),
                    payload.get("ssh_key_path", existing["ssh_key_path"]),
                    password,
                    payload.get("server_role", existing["server_role"]),
                    payload.get("connection_mode", existing["connection_mode"]),
                    payload.get("k8s_namespace", existing["k8s_namespace"]),
                    json.dumps(watched, ensure_ascii=False),
                    now,
                    sid,
                ),
            )
        else:
            self.execute(
                """
                INSERT INTO servers (
                    id, name, host, ssh_port, ssh_user, ssh_key_path, ssh_password,
                    server_role, connection_mode, k8s_namespace, watched_configs_json, lock_status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'FREE', ?)
                """,
                (
                    sid,
                    payload["name"],
                    payload["host"],
                    int(payload.get("ssh_port", 22)),
                    payload.get("ssh_user", "ubuntu"),
                    payload.get("ssh_key_path", ""),
                    password,
                    payload.get("server_role", "GPU"),
                    payload.get("connection_mode", "ssh"),
                    payload.get("k8s_namespace", "dynamo-llm"),
                    json.dumps(watched, ensure_ascii=False),
                    now,
                ),
            )
        return self.get_server(sid)  # type: ignore[return-value]

    def set_server_lock(
        self, server_id: str, lock_status: str, holder: str | None = None, reason: str | None = None
    ) -> None:
        self.execute(
            """
            UPDATE servers
            SET lock_status = ?, lock_holder = ?, lock_reason = ?, updated_at = ?
            WHERE id = ?
            """,
            (lock_status, holder, reason, utc_now_iso(), server_id),
        )

    def save_snapshot(
        self,
        server_id: str,
        trigger_type: str,
        triggered_by: str,
        hw_specs: dict[str, Any],
        sw_specs: dict[str, Any],
        active_configs: dict[str, Any],
        campaign_id: str | None = None,
        git_hash: str | None = None,
    ) -> dict[str, Any]:
        snap_id = f"snap-{uuid.uuid4().hex[:8]}"
        now = utc_now_iso()
        self.execute(
            """
            INSERT INTO env_snapshots (
                id, server_id, trigger_type, triggered_by, campaign_id, git_hash,
                hw_specs_json, sw_specs_json, active_configs_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snap_id,
                server_id,
                trigger_type,
                triggered_by,
                campaign_id,
                git_hash,
                json.dumps(hw_specs, ensure_ascii=False),
                json.dumps(sw_specs, ensure_ascii=False),
                json.dumps(active_configs, ensure_ascii=False),
                now,
            ),
        )
        return self.get_snapshot(snap_id)  # type: ignore[return-value]

    def get_snapshot(self, snap_id: str) -> dict[str, Any] | None:
        r = self.fetch_one("SELECT * FROM env_snapshots WHERE id = ?", (snap_id,))
        if not r:
            return None
        r["hw_specs"] = json.loads(r.pop("hw_specs_json") or "{}")
        r["sw_specs"] = json.loads(r.pop("sw_specs_json") or "{}")
        r["active_configs"] = json.loads(r.pop("active_configs_json") or "{}")
        return r

    def get_latest_snapshot(self, server_id: str) -> dict[str, Any] | None:
        r = self.fetch_one(
            "SELECT * FROM env_snapshots WHERE server_id = ? ORDER BY created_at DESC LIMIT 1",
            (server_id,),
        )
        if not r:
            return None
        r["hw_specs"] = json.loads(r.pop("hw_specs_json") or "{}")
        r["sw_specs"] = json.loads(r.pop("sw_specs_json") or "{}")
        r["active_configs"] = json.loads(r.pop("active_configs_json") or "{}")
        return r

    def list_snapshots(self, server_id: str | None = None, limit: int = 30) -> list[dict[str, Any]]:
        if server_id:
            rows = self.fetch_all(
                "SELECT * FROM env_snapshots WHERE server_id = ? ORDER BY created_at DESC LIMIT ?",
                (server_id, limit),
            )
        else:
            rows = self.fetch_all(
                "SELECT * FROM env_snapshots ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        for r in rows:
            r["hw_specs"] = json.loads(r.pop("hw_specs_json") or "{}")
            r["sw_specs"] = json.loads(r.pop("sw_specs_json") or "{}")
            r["active_configs"] = json.loads(r.pop("active_configs_json") or "{}")
        return rows

    def record_config_commit(self, commit: dict[str, Any]) -> None:
        existing = self.fetch_one(
            "SELECT git_hash FROM config_commits WHERE git_hash = ?",
            (commit["git_hash"],),
        )
        if existing:
            return
        self.execute(
            """
            INSERT INTO config_commits (
                git_hash, parent_hash, native_git_hash, author, server_id,
                campaign_id, summary, changed_files_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                commit["git_hash"],
                commit.get("parent_hash"),
                commit.get("native_git_hash"),
                commit["author"],
                commit.get("server_id"),
                commit.get("campaign_id"),
                commit["message"],
                json.dumps(commit.get("changed_files", []), ensure_ascii=False),
                commit["created_at"],
            ),
        )

    def list_config_commits(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.fetch_all(
            "SELECT * FROM config_commits ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        for r in rows:
            r["changed_files"] = json.loads(r.pop("changed_files_json") or "[]")
        return rows

    def add_ssh_audit_log(
        self,
        server_id: str,
        actor_id: str,
        command: str,
        purpose: str,
        exit_code: int,
        stdout: str,
        stderr: str,
        duration_ms: int,
        room_id: str | None = None,
        campaign_id: str | None = None,
    ) -> dict[str, Any]:
        log_id = f"ssh-{uuid.uuid4().hex[:8]}"
        now = utc_now_iso()
        self.execute(
            """
            INSERT INTO ssh_audit_logs (
                id, room_id, campaign_id, server_id, actor_id, command,
                purpose, exit_code, stdout, stderr, duration_ms, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                log_id,
                room_id,
                campaign_id,
                server_id,
                actor_id,
                command,
                purpose,
                exit_code,
                stdout,
                stderr,
                duration_ms,
                now,
            ),
        )
        return self.fetch_one("SELECT * FROM ssh_audit_logs WHERE id = ?", (log_id,))  # type: ignore[return-value]

    def list_ssh_audit_logs(self, room_id: str | None = None, limit: int = 80) -> list[dict[str, Any]]:
        if room_id:
            return self.fetch_all(
                "SELECT * FROM ssh_audit_logs WHERE room_id = ? ORDER BY created_at DESC LIMIT ?",
                (room_id, limit),
            )
        return self.fetch_all(
            "SELECT * FROM ssh_audit_logs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )

    def create_benchmark_run(
        self,
        campaign_id: str,
        server_id: str,
        variant_label: str,
        offload_target: str,
        command: str,
        git_hash: str | None,
        pre_snapshot_id: str | None,
        executed_by: str,
    ) -> dict[str, Any]:
        run_id = f"run-{uuid.uuid4().hex[:8]}"
        now = utc_now_iso()
        self.execute(
            """
            INSERT INTO benchmark_runs (
                id, campaign_id, server_id, variant_label, offload_target, command,
                git_hash, pre_snapshot_id, status, executed_by, started_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'RUNNING', ?, ?)
            """,
            (
                run_id,
                campaign_id,
                server_id,
                variant_label,
                offload_target,
                command,
                git_hash,
                pre_snapshot_id,
                executed_by,
                now,
            ),
        )
        return self.get_benchmark_run(run_id)  # type: ignore[return-value]

    def finish_benchmark_run(
        self,
        run_id: str,
        status: str,
        post_snapshot_id: str | None,
        metrics: dict[str, Any],
        raw_log_path: str | None,
    ) -> dict[str, Any] | None:
        self.execute(
            """
            UPDATE benchmark_runs
            SET status = ?, post_snapshot_id = ?, metrics_json = ?, raw_log_path = ?, finished_at = ?
            WHERE id = ?
            """,
            (
                status,
                post_snapshot_id,
                json.dumps(metrics, ensure_ascii=False),
                raw_log_path,
                utc_now_iso(),
                run_id,
            ),
        )
        return self.get_benchmark_run(run_id)

    def get_benchmark_run(self, run_id: str) -> dict[str, Any] | None:
        r = self.fetch_one("SELECT * FROM benchmark_runs WHERE id = ?", (run_id,))
        if r:
            r["metrics"] = json.loads(r.pop("metrics_json") or "{}")
        return r

    def list_benchmark_runs(self, campaign_id: str | None = None) -> list[dict[str, Any]]:
        if campaign_id:
            rows = self.fetch_all(
                "SELECT * FROM benchmark_runs WHERE campaign_id = ? ORDER BY started_at ASC",
                (campaign_id,),
            )
        else:
            rows = self.fetch_all("SELECT * FROM benchmark_runs ORDER BY started_at DESC")
        for r in rows:
            r["metrics"] = json.loads(r.pop("metrics_json") or "{}")
        return rows

    def upsert_report(
        self,
        campaign_id: str,
        room_id: str,
        title: str,
        author_agent_id: str,
        summary_markdown: str,
        comparison: dict[str, Any],
    ) -> dict[str, Any]:
        existing = self.fetch_one(
            "SELECT id FROM reports WHERE campaign_id = ?", (campaign_id,)
        )
        now = utc_now_iso()
        if existing:
            rid = existing["id"]
            self.execute(
                """
                UPDATE reports
                SET title = ?, author_agent_id = ?, summary_markdown = ?, comparison_json = ?, created_at = ?
                WHERE id = ?
                """,
                (
                    title,
                    author_agent_id,
                    summary_markdown,
                    json.dumps(comparison, ensure_ascii=False),
                    now,
                    rid,
                ),
            )
        else:
            rid = f"rep-{uuid.uuid4().hex[:8]}"
            self.execute(
                """
                INSERT INTO reports (
                    id, campaign_id, room_id, title, author_agent_id, summary_markdown, comparison_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rid,
                    campaign_id,
                    room_id,
                    title,
                    author_agent_id,
                    summary_markdown,
                    json.dumps(comparison, ensure_ascii=False),
                    now,
                ),
            )
        return self.get_report(rid)  # type: ignore[return-value]

    def get_report(self, report_id: str) -> dict[str, Any] | None:
        r = self.fetch_one("SELECT * FROM reports WHERE id = ?", (report_id,))
        if r:
            r["comparison"] = json.loads(r.pop("comparison_json") or "{}")
        return r

    def list_reports(self) -> list[dict[str, Any]]:
        rows = self.fetch_all("SELECT * FROM reports ORDER BY created_at DESC")
        for r in rows:
            r["comparison"] = json.loads(r.pop("comparison_json") or "{}")
        return rows
