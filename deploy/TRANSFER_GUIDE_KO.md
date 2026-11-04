# 사내 폐쇄망 업무 환경 반입 및 설치·운영 가이드

개발 PC와 사내 실제 운영 환경(리눅스 웹 서버, GPU 서버, 리모트 메모리 서버)이 완전히 분리된 환경을 위해 구성된 반입·설치 절차입니다.

---

## 1. 반입할 배포 파일 위치

프론트엔드 정적 빌드 번들(`frontend/dist/`)이 **이미 컴파일되어 포함**되어 있으므로, **사내 리눅스 서버에는 Node.js나 npm을 설치할 필요가 전혀 없습니다.**

- `dist_package/agent-meeting-room-release.tar.gz` (리눅스 서버 직접 업로드 권장 · 실행 권한 및 LF 줄바꿈 자동 정규화 완료)
- `dist_package/agent-meeting-room-release.zip` (윈도우 업무 PC 경유 업로드용)

---

## 2. 사내망 패키지 반입 시나리오별 설치 방법

### 케이스 A: 사내 리눅스 서버에 사내 PyPI 미러(pip 설치)가 허용된 경우
```bash
mkdir -p ~/agent-meeting-room
tar -xzf agent-meeting-room-release.tar.gz -C ~/agent-meeting-room
cd ~/agent-meeting-room
chmod +x deploy/start_linux.sh integrations/claude_code_setup.sh
./deploy/start_linux.sh
```

### 케이스 B: 사내 리눅스 서버에서 외부/내부 `pip` 접근이 완전히 차단된 경우 (완전 오프라인)
1. 사내 리눅스 서버의 Python 버전을 먼저 확인합니다 (`python3 --version`, 예: `3.11` 또는 `3.12`).
2. 외부 인터넷이 되는 현재 개발 PC에서 아래 스크립트 1줄을 실행해 리눅스용 바이너리 `.whl` 파일을 `./wheels` 폴더에 받은 뒤 다시 패키징합니다:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\deploy\download_linux_wheels.ps1 -PythonVersion 3.11
   .\.venv\Scripts\python.exe .\scripts\build_release_bundle.py
   ```
3. 새로 생성된 `dist_package/agent-meeting-room-release.tar.gz` 안에는 `./wheels` 폴더가 통째로 들어 있으므로, 사내 리눅스 서버에서 압축만 풀고 `./deploy/start_linux.sh`를 실행하면 인터넷 연결 없이 자동으로 `./wheels`에서 설치 후 기동됩니다.

---

## 3. 실제 GPU 서버 · 리모트 메모리 서버 SSH 연결 전환

1. 사내 리눅스 미팅룸 서버에서 테스트 서버들로 무패스워드 SSH 접속이 되도록 키를 등록합니다 (또는 기존 `~/.ssh/config` 활용):
   ```bash
   ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519
   ssh-copy-id ubuntu@<GPU_서버_IP>
   ssh-copy-id ubuntu@<REMOTE_서버_IP>
   ```
2. 웹 브라우저에서 `http://<미팅룸_리눅스_IP>:8080` 접속 → 상단 **`[서버 환경 & 스펙]`** 탭 이동
3. 각 서버 카드 우측 상단의 **`⚙️ 설정`** 버튼을 눌러 연결 모드를 변경합니다:
   - `Simulation` → **`Live SSH — asyncssh 연결 풀`** 또는 **`OpenSSH CLI — 리눅스 기본 ssh 명령어 / ~/.ssh/config 활용`**
   - 실제 IP, 포트, 계정명, SSH 키 경로(`~/.ssh/id_ed25519`), K8s 네임스페이스 입력 후 **`[저장 및 연결 반영]`** 클릭!

---

## 4. 팀원 4명의 개인 에이전트 (Claude Code / Hermes) 연결

각 팀원의 업무 PC 또는 개인 서버 세션에서 미팅룸 단일 포트의 `/mcp` 엔드포인트를 등록합니다 (웹 상단 **`[🔌 MCP 에이전트 연결 가이드]`** 버튼에서도 원클릭 복사 가능).

- **팀원 A (`agent_a` · GPU 서버 주특기)**:
  ```bash
  MCP_TOOL_TIMEOUT=1800000 claude mcp add --transport http amr-agent-a "http://<미팅룸_리눅스_IP>:8080/mcp?agent_id=agent_a"
  ```
- **팀원 B (`agent_b` · 리모트/CXL 주특기)**: `agent_id=agent_b`
- **팀원 C (`agent_c` · DGD/YAML 주특기)**: `agent_id=agent_c`
- **팀원 D (`agent_d` · 총괄/벤치마크 주특기)**: `agent_id=agent_d`

등록 후 `integrations/SKILL.md`를 스킬 폴더에 넣거나 프롬프트에 **"미팅룸 대기 모드로 입장해 줘"** 라고 입력하면 즉시 `wait_for_turn` 대기 루프가 활성화됩니다.
