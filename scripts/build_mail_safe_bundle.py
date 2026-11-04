from __future__ import annotations

import os
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = PROJECT_ROOT / "dist_package"
PLAN_ARTIFACT = Path(
    r"C:\Users\indeo\.gemini\antigravity\brain\5a5aca2c-bd6b-4cf0-8eee-b474b994d433\agent_meeting_room_plan.md"
)

# Directories excluded because they contain binaries, virtualenvs, node packages,
# runtime DBs, or compiled .js bundles blocked by Gmail / Exchange / corporate mail gateways.
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
    "dist",  # frontend/dist contains compiled .js files which email gateways block!
}

# Executable / binary extensions strictly excluded from email attachments
BLOCKED_BINARY_EXTS = {
    ".exe",
    ".dll",
    ".pyd",
    ".so",
    ".dylib",
    ".whl",
    ".pyc",
    ".pyo",
    ".db",
    ".db-shm",
    ".db-wal",
    ".msi",
    ".jar",
    ".bat",
    ".cmd",
    ".vbs",
    ".com",
    ".scr",
    ".js",
    ".mjs",
    ".cjs",
    ".tsbuildinfo",
}

# Script extensions that mail gateways block if left raw -> convert to `<name>.txt`
RENAME_TO_TXT_EXTS = {".sh", ".ps1"}

MAIL_RESTORE_GUIDE = """# 메일 발송용 압축본 복원 안내 (MAIL_RESTORE_README)

사내 메일 보안 게이트웨이 및 상용 메일(Gmail/Outlook 등) 첨부파일 차단 정책을 통과하기 위해,
아래 항목들은 본 압축 파일(`agent-meeting-room-mail-safe.zip`)에서 제외되거나 안전한 확장자(`.txt`)로 변환되었습니다.

## 1. 메일 송부 불가로 제외·변환된 항목 목록
1. **실행 바이너리 및 가상환경 폴더 (제외)**:
   - `.venv/` (Python 가상환경 및 `.exe`, `.pyd`, `.dll` 바이너리)
   - `frontend/node_modules/` (Node 패키지 폴더)
   - `data/` (로컬 테스트용 SQLite `.db` 파일)
2. **컴파일된 `.js` 번들 폴더 (제외)**:
   - `frontend/dist/` (메일 보안 필터가 압축파일 내부의 `.js` 확장자를 악성 스크립트로 오탐·차단하므로 제외, 원본 TypeScript/React 소스는 `frontend/src/`에 100% 포함)
3. **셸/파워셸 스크립트 확장자 (`.txt`로 안전 변환하여 포함)**:
   - `deploy/start_linux.sh.txt` → 수신 후 `deploy/start_linux.sh`로 이름 변경
   - `deploy/download_linux_wheels.ps1.txt` → 수신 후 `deploy/download_linux_wheels.ps1`로 이름 변경
   - `integrations/claude_code_setup.sh.txt` → 수신 후 `integrations/claude_code_setup.sh`로 이름 변경

---

## 2. 사내 업무 환경 수신 후 복원 및 실행 방법

```bash
# 1) 압축 해제
mkdir -p agent-meeting-room && unzip agent-meeting-room-mail-safe.zip -d agent-meeting-room
cd agent-meeting-room

# 2) .txt로 변환된 셸 스크립트 원래 확장자로 복원
mv deploy/start_linux.sh.txt deploy/start_linux.sh
mv integrations/claude_code_setup.sh.txt integrations/claude_code_setup.sh
chmod +x deploy/start_linux.sh integrations/claude_code_setup.sh

# 3) 프론트엔드 정적 파일 빌드 (최초 1회)
cd frontend
npm install
npm run build
cd ..

# 4) 리눅스 통합 서버 실행 (기본 포트 8080)
./deploy/start_linux.sh
```

> 기획서 원본 문서는 `docs/agent_meeting_room_plan.md` 경로에 함께 동봉되어 있습니다.
"""


def build_mail_safe_zip() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = OUT_DIR / "agent-meeting-room-mail-safe.zip"

    included_files: list[str] = []

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # 1. Add mail restore guide at root
        zf.writestr("MAIL_RESTORE_README.md", MAIL_RESTORE_GUIDE.encode("utf-8"))
        included_files.append("MAIL_RESTORE_README.md")

        # 2. Include the planning specification document
        if PLAN_ARTIFACT.exists():
            plan_bytes = PLAN_ARTIFACT.read_bytes().replace(b"\r\n", b"\n")
            zf.writestr("docs/agent_meeting_room_plan.md", plan_bytes)
            included_files.append("docs/agent_meeting_room_plan.md")

        # 3. Walk project source files
        for root, dirs, files in os.walk(PROJECT_ROOT):
            dirs[:] = [d for d in sorted(dirs) if d not in EXCLUDE_DIRS]
            root_path = Path(root)
            for fname in sorted(files):
                fpath = root_path / fname
                suffix = fpath.suffix.lower()
                if suffix in BLOCKED_BINARY_EXTS:
                    continue

                rel_posix = fpath.relative_to(PROJECT_ROOT).as_posix()
                if suffix in RENAME_TO_TXT_EXTS:
                    rel_posix = f"{rel_posix}.txt"

                raw = fpath.read_bytes().replace(b"\r\n", b"\n")
                zf.writestr(rel_posix, raw)
                included_files.append(rel_posix)

    print(f"Mail-safe ZIP : {zip_path}")
    print(f"Size          : {zip_path.stat().st_size / 1024:.1f} KB")
    print(f"File count    : {len(included_files)}")
    print("--- Included Files ---")
    for p in included_files:
        print(f"  {p}")


if __name__ == "__main__":
    build_mail_safe_zip()
