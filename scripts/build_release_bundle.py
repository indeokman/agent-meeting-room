from __future__ import annotations

import io
import os
import tarfile
import time
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = PROJECT_ROOT / "dist_package"

EXCLUDE_DIRS = {
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    "data",
    "data_e2e_tmp",
    "dist_package",
    "wheels",
    ".git",
}

TEXT_UNIX_EXTS = {
    ".py",
    ".sh",
    ".md",
    ".txt",
    ".yaml",
    ".yml",
    ".json",
    ".ts",
    ".tsx",
    ".css",
    ".html",
    ".service",
}


def iter_release_files() -> list[tuple[Path, str]]:
    items: list[tuple[Path, str]] = []
    for root, dirs, files in os.walk(PROJECT_ROOT):
        dirs[:] = [d for d in sorted(dirs) if d not in EXCLUDE_DIRS]
        root_path = Path(root)
        for fname in sorted(files):
            if fname.endswith((".pyc", ".pyo", ".db", ".db-shm", ".db-wal", ".tsbuildinfo")):
                continue
            fpath = root_path / fname
            rel_posix = fpath.relative_to(PROJECT_ROOT).as_posix()
            items.append((fpath, rel_posix))
    return items


def read_normalized_bytes(fpath: Path) -> bytes:
    raw = fpath.read_bytes()
    if fpath.suffix.lower() in TEXT_UNIX_EXTS:
        # Normalize CRLF -> LF so Linux bash / systemd / python shebangs never fail
        return raw.replace(b"\r\n", b"\n")
    return raw


def build_bundles() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = OUT_DIR / "agent-meeting-room-release.zip"
    tgz_path = OUT_DIR / "agent-meeting-room-release.tar.gz"

    files = iter_release_files()
    now_ts = int(time.time())

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for fpath, rel in files:
            data = read_normalized_bytes(fpath)
            zi = zipfile.ZipInfo(rel)
            zi.compress_type = zipfile.ZIP_DEFLATED
            # Preserve executable bit for shell scripts & stdio bridge inside zip external_attr
            is_exec = fpath.suffix == ".sh" or rel == "integrations/mcp_stdio_bridge.py"
            mode = 0o755 if is_exec else 0o644
            zi.external_attr = (mode & 0xFFFF) << 16
            zf.writestr(zi, data)

    with tarfile.open(tgz_path, "w:gz") as tf:
        for fpath, rel in files:
            data = read_normalized_bytes(fpath)
            ti = tarfile.TarInfo(name=rel)
            ti.size = len(data)
            ti.mtime = now_ts
            is_exec = fpath.suffix == ".sh" or rel == "integrations/mcp_stdio_bridge.py"
            ti.mode = 0o755 if is_exec else 0o644
            tf.addfile(ti, io.BytesIO(data))

    print(f"Created ZIP : {zip_path} ({zip_path.stat().st_size // 1024} KB)")
    print(f"Created TGZ : {tgz_path} ({tgz_path.stat().st_size // 1024} KB)")
    print(f"Total files : {len(files)}")


if __name__ == "__main__":
    build_bundles()
