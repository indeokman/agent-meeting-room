from __future__ import annotations

import difflib
import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class EmbeddedGitStore:
    """Embedded configuration versioning engine for DGD, LMCache, and Mooncake YAMLs.

    Maintains a deterministic content-addressed commit store in `.amr_vcs/` so Diff
    and 1-click Rollback work in any air-gapped environment even without a system
    `git` binary, while also mirroring every commit into a native `.git` repository
    whenever `git` CLI is installed on the host Linux server.
    """

    def __init__(self, repo_dir: Path) -> None:
        self.repo_dir = repo_dir
        self.vcs_dir = repo_dir / ".amr_vcs"
        self.commits_dir = self.vcs_dir / "commits"
        self.head_file = self.vcs_dir / "HEAD"
        self._git_bin = shutil.which("git")
        self._ensure_initialized()

    def _ensure_initialized(self) -> None:
        self.repo_dir.mkdir(parents=True, exist_ok=True)
        self.commits_dir.mkdir(parents=True, exist_ok=True)
        gitignore = self.repo_dir / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text(".amr_vcs/\n", encoding="utf-8")

        if self._git_bin and not (self.repo_dir / ".git").exists():
            try:
                subprocess.run(
                    [self._git_bin, "init"],
                    cwd=str(self.repo_dir),
                    check=False,
                    capture_output=True,
                    text=True,
                )
                subprocess.run(
                    [self._git_bin, "config", "user.name", "Agent Meeting Room"],
                    cwd=str(self.repo_dir),
                    check=False,
                    capture_output=True,
                    text=True,
                )
                subprocess.run(
                    [self._git_bin, "config", "user.email", "amr@internal.local"],
                    cwd=str(self.repo_dir),
                    check=False,
                    capture_output=True,
                    text=True,
                )
            except Exception:
                pass

    def get_head_hash(self) -> str | None:
        if not self.head_file.exists():
            return None
        val = self.head_file.read_text(encoding="utf-8").strip()
        return val or None

    def get_current_working_tree(self) -> dict[str, str]:
        tree: dict[str, str] = {}
        for path in sorted(self.repo_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(self.repo_dir).as_posix()
            if rel.startswith(".git/") or rel.startswith(".amr_vcs/") or rel == ".gitignore":
                continue
            tree[rel] = path.read_text(encoding="utf-8")
        return tree

    def get_commit(self, commit_hash: str) -> dict[str, Any] | None:
        commit_path = self.commits_dir / f"{commit_hash}.json"
        if not commit_path.exists():
            return None
        return json.loads(commit_path.read_text(encoding="utf-8"))

    def get_commit_tree(self, commit_hash: str | None) -> dict[str, str]:
        if not commit_hash:
            return {}
        commit_data = self.get_commit(commit_hash)
        if not commit_data:
            return {}
        return dict(commit_data.get("files", {}))

    def commit_files(
        self,
        updates: dict[str, str],
        author: str,
        message: str,
        server_id: str | None = None,
        campaign_id: str | None = None,
    ) -> dict[str, Any]:
        """Write updated config files and create an immutable commit snapshot.

        `updates` maps repo-relative paths like `gpu-node-01/k8s/dgd-active.yaml`
        to their full YAML/JSON/env text content.
        """
        parent_hash = self.get_head_hash()
        prev_tree = self.get_commit_tree(parent_hash)

        for rel_path, content in updates.items():
            clean_rel = rel_path.lstrip("/").replace("\\", "/")
            full_path = (self.repo_dir / clean_rel).resolve()
            if not str(full_path).startswith(str(self.repo_dir.resolve())):
                raise ValueError(f"Invalid repository path: {rel_path}")
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content, encoding="utf-8")

        new_tree = self.get_current_working_tree()
        now_iso = datetime.now(timezone.utc).isoformat()

        changed_files = [
            k
            for k in sorted(set(prev_tree.keys()) | set(new_tree.keys()))
            if prev_tree.get(k) != new_tree.get(k)
        ]

        # Even if content matches HEAD (e.g. initial baseline sync), ensure at least HEAD exists
        if not changed_files and parent_hash is not None:
            existing = self.get_commit(parent_hash)
            if existing:
                return {**existing, "no_change": True}

        hasher = hashlib.sha1()
        hasher.update((parent_hash or "ROOT").encode("utf-8"))
        hasher.update(now_iso.encode("utf-8"))
        hasher.update(author.encode("utf-8"))
        hasher.update(message.encode("utf-8"))
        for k, v in sorted(new_tree.items()):
            hasher.update(k.encode("utf-8"))
            hasher.update(v.encode("utf-8"))
        commit_hash = hasher.hexdigest()[:10]

        native_git_hash: str | None = None
        if self._git_bin and (self.repo_dir / ".git").exists():
            try:
                subprocess.run(
                    [self._git_bin, "add", "-A"],
                    cwd=str(self.repo_dir),
                    check=False,
                    capture_output=True,
                    text=True,
                )
                subprocess.run(
                    [self._git_bin, "commit", "-m", f"[{author}] {message}"],
                    cwd=str(self.repo_dir),
                    check=False,
                    capture_output=True,
                    text=True,
                )
                rev = subprocess.run(
                    [self._git_bin, "rev-parse", "--short=10", "HEAD"],
                    cwd=str(self.repo_dir),
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if rev.returncode == 0 and rev.stdout.strip():
                    native_git_hash = rev.stdout.strip()
            except Exception:
                pass

        commit_record = {
            "git_hash": commit_hash,
            "native_git_hash": native_git_hash,
            "parent_hash": parent_hash,
            "author": author,
            "server_id": server_id,
            "campaign_id": campaign_id,
            "message": message,
            "changed_files": changed_files or sorted(new_tree.keys()),
            "created_at": now_iso,
            "files": new_tree,
        }
        (self.commits_dir / f"{commit_hash}.json").write_text(
            json.dumps(commit_record, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.head_file.write_text(commit_hash, encoding="utf-8")
        return commit_record

    def compute_diff(
        self,
        base_hash: str | None,
        target_hash: str | None,
        server_prefix: str | None = None,
    ) -> dict[str, Any]:
        """Compute both Unified Diff and structured Side-by-Side Diff between two commits."""
        base_tree = self.get_commit_tree(base_hash)
        target_tree = (
            self.get_commit_tree(target_hash)
            if target_hash
            else self.get_current_working_tree()
        )
        all_paths = sorted(set(base_tree.keys()) | set(target_tree.keys()))
        if server_prefix:
            prefix = server_prefix.rstrip("/") + "/"
            all_paths = [p for p in all_paths if p.startswith(prefix)]

        file_diffs: list[dict[str, Any]] = []
        unified_chunks: list[str] = []

        for path in all_paths:
            old_text = base_tree.get(path, "")
            new_text = target_tree.get(path, "")
            if old_text == new_text:
                continue

            old_lines = old_text.splitlines()
            new_lines = new_text.splitlines()
            u_lines = list(
                difflib.unified_diff(
                    old_lines,
                    new_lines,
                    fromfile=f"a/{path} ({base_hash or 'empty'})",
                    tofile=f"b/{path} ({target_hash or 'working'})",
                    lineterm="",
                )
            )
            unified_text = "\n".join(u_lines)
            unified_chunks.append(unified_text)

            rows = self._build_side_by_side_rows(old_lines, new_lines)
            added_count = sum(1 for r in rows if r["kind"] in ("insert", "replace") and r["right_text"] is not None)
            removed_count = sum(1 for r in rows if r["kind"] in ("delete", "replace") and r["left_text"] is not None)

            file_diffs.append(
                {
                    "path": path,
                    "status": (
                        "added"
                        if path not in base_tree
                        else ("deleted" if path not in target_tree else "modified")
                    ),
                    "added_lines": added_count,
                    "removed_lines": removed_count,
                    "unified_diff": unified_text,
                    "side_by_side": rows,
                }
            )

        return {
            "base_hash": base_hash,
            "target_hash": target_hash or self.get_head_hash(),
            "changed_file_count": len(file_diffs),
            "unified_diff": "\n\n".join(unified_chunks),
            "files": file_diffs,
        }

    @staticmethod
    def _build_side_by_side_rows(
        old_lines: list[str], new_lines: list[str]
    ) -> list[dict[str, Any]]:
        matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
        rows: list[dict[str, Any]] = []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                for offset in range(i2 - i1):
                    rows.append(
                        {
                            "kind": "equal",
                            "left_num": i1 + offset + 1,
                            "left_text": old_lines[i1 + offset],
                            "right_num": j1 + offset + 1,
                            "right_text": new_lines[j1 + offset],
                        }
                    )
            elif tag == "delete":
                for idx in range(i1, i2):
                    rows.append(
                        {
                            "kind": "delete",
                            "left_num": idx + 1,
                            "left_text": old_lines[idx],
                            "right_num": None,
                            "right_text": None,
                        }
                    )
            elif tag == "insert":
                for jdx in range(j1, j2):
                    rows.append(
                        {
                            "kind": "insert",
                            "left_num": None,
                            "left_text": None,
                            "right_num": jdx + 1,
                            "right_text": new_lines[jdx],
                        }
                    )
            elif tag == "replace":
                max_len = max(i2 - i1, j2 - j1)
                for offset in range(max_len):
                    l_idx = i1 + offset if (i1 + offset) < i2 else None
                    r_idx = j1 + offset if (j1 + offset) < j2 else None
                    rows.append(
                        {
                            "kind": "replace",
                            "left_num": (l_idx + 1) if l_idx is not None else None,
                            "left_text": old_lines[l_idx] if l_idx is not None else None,
                            "right_num": (r_idx + 1) if r_idx is not None else None,
                            "right_text": new_lines[r_idx] if r_idx is not None else None,
                        }
                    )
        return rows

    def rollback_to_commit(
        self, target_hash: str, author: str, server_prefix: str | None = None
    ) -> dict[str, Any]:
        """Restore files from `target_hash` (all files or filtered by `server_prefix`) and create a rollback commit."""
        target_commit = self.get_commit(target_hash)
        if not target_commit:
            raise ValueError(f"Commit {target_hash}을(를) 찾을 수 없습니다.")

        target_files: dict[str, str] = dict(target_commit.get("files", {}))
        if server_prefix:
            prefix = server_prefix.rstrip("/") + "/"
            updates = {k: v for k, v in target_files.items() if k.startswith(prefix)}
        else:
            # Also remove files that didn't exist in target_commit
            current = self.get_current_working_tree()
            for p in current:
                if p not in target_files:
                    fp = self.repo_dir / p
                    if fp.exists():
                        fp.unlink()
            updates = target_files

        commit = self.commit_files(
            updates=updates,
            author=author,
            message=f"Rollback 설정 복원 -> {target_hash} ({target_commit.get('message', '')})",
            server_id=server_prefix,
        )
        return {"commit": commit, "restored_files": updates}
