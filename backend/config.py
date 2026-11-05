from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = BASE_DIR / "data"


@dataclass
class ModePaths:
    root_dir: Path
    db_path: Path
    config_repo_dir: Path
    benchmark_logs_dir: Path

    def ensure_dirs(self) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.config_repo_dir.mkdir(parents=True, exist_ok=True)
        self.benchmark_logs_dir.mkdir(parents=True, exist_ok=True)


@dataclass
class Settings:
    host: str = field(default_factory=lambda: os.getenv("AMR_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(os.getenv("AMR_PORT", "8080")))
    data_dir: Path = field(
        default_factory=lambda: Path(os.getenv("AMR_DATA_DIR", str(DEFAULT_DATA_DIR)))
    )
    # Auth modes: "none" (initial zero-auth profile mode), "token", "sso_header"
    auth_mode: str = field(default_factory=lambda: os.getenv("AMR_AUTH_MODE", "none"))
    sso_user_header: str = field(
        default_factory=lambda: os.getenv("AMR_SSO_USER_HEADER", "X-Forwarded-User")
    )
    shared_api_token: str = field(
        default_factory=lambda: os.getenv("AMR_API_TOKEN", "")
    )
    mcp_default_wait_sec: int = field(
        default_factory=lambda: int(os.getenv("AMR_MCP_WAIT_SEC", "45"))
    )
    mcp_max_wait_sec: int = field(
        default_factory=lambda: int(os.getenv("AMR_MCP_MAX_WAIT_SEC", "1800"))
    )
    presence_grace_sec: int = field(
        default_factory=lambda: int(os.getenv("AMR_PRESENCE_GRACE_SEC", "90"))
    )
    max_discussion_rounds: int = field(
        default_factory=lambda: int(os.getenv("AMR_MAX_DISCUSSION_ROUNDS", "2"))
    )

    def get_mode_paths(self, mode: str = "live") -> ModePaths:
        sub = "sim" if mode == "sim" else "live"
        root = self.data_dir / sub
        return ModePaths(
            root_dir=root,
            db_path=root / "agent_meeting_room.db",
            config_repo_dir=root / "config_repo",
            benchmark_logs_dir=root / "benchmark_logs",
        )

    @property
    def db_path(self) -> Path:
        return self.get_mode_paths("live").db_path

    @property
    def config_repo_dir(self) -> Path:
        return self.get_mode_paths("live").config_repo_dir

    @property
    def benchmark_logs_dir(self) -> Path:
        return self.get_mode_paths("live").benchmark_logs_dir

    @property
    def static_dir(self) -> Path:
        return BASE_DIR / "frontend" / "dist"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.get_mode_paths("live").ensure_dirs()
        self.get_mode_paths("sim").ensure_dirs()


settings = Settings()
