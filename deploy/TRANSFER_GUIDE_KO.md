# 사내 폐쇄망 업무 환경 반입 및 설치·운영 가이드

개발 PC와 사내 실제 운영 환경(리눅스 웹 서버, GPU 서버, 리모트 메모리 서버)이 분리된 환경을 위해 구성된 반입·설치 절차입니다.

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
2. 외부 인터넷이 되는 PC에서 아래 스크립트를 실행해 리눅스용 바이너리 `.whl` 파일을 `./wheels` 폴더에 받은 뒤 다시 패키징합니다:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\deploy\download_linux_wheels.ps1 -PythonVersion 3.11
   .\.venv\Scripts\python.exe .\scripts\build_release_bundle.py
   ```
3. 사내 리눅스 서버에서 압축을 풀고 `./deploy/start_linux.sh`를 실행하면 인터넷 연결 없이 자동으로 `./wheels`에서 설치 후 기동됩니다.

---

## 3. 운영 모드(`/`) vs 기능 테스트용 시뮬레이션 모드(`/sim`) 접속 경로

두 모드는 **제공하는 화면과 모든 기능이 100% 동일**하며, 저장소(DB·내장 Git)와 원격 실행 백엔드만 완전히 분리되어 있습니다.

| 구분 | 접속 URL | MCP 엔드포인트 | 특징 |
| :--- | :--- | :--- | :--- |
| **실제 운영 모드** | `http://<서버IP>:8080/` | `http://<서버IP>:8080/mcp` | 실제 GPU·리모트 서버에 SSH 접속하여 수집·배포·벤치마크 수행 (`data/live/` 사용) |
| **기능 테스트 시뮬레이션 모드** | `http://<서버IP>:8080/sim` | `http://<서버IP>:8080/sim/mcp` | 운영 데이터 오염 없이 가상 SSH 서버 및 가상/실제 에이전트로 전 기능 사전 테스트 (`data/sim/` 사용) |

---

## 4. WinSCP 스타일 간편 SSH 서버 등록

1. 웹 브라우저에서 `http://<미팅룸_리눅스_IP>:8080/` 접속 → 상단 **`[서버 환경 & 스펙]`** 탭 이동
2. **`[+ 서버 추가]`** 버튼을 클릭하고 WinSCP와 동일하게 아래 정보만 입력합니다:
   - **호스트 이름**: GPU 서버 또는 리모트 서버 IP/도메인
   - **포트 번호**: 기본 `22`
   - **사용자 이름 / 비밀번호**: SSH 접속 계정 ID 및 패스워드
   - **서버 구분**: `GPU 서버 (K8s DGD / 서빙)` 또는 `리모트 서버 (Mooncake / CXL / SSD)`
3. 모달 하단의 **`[연결 테스트]`** 로 응답 확인 후 **`[저장 및 연결]`** 클릭!

---

## 5. 팀원 4명의 개인 에이전트 (Claude Code / Hermes) 연결 및 자율 역할 분담

에이전트별 역할은 사전에 고정되어 있지 않으며, 주제가 발제되면 온라인 상태인 에이전트들이 논의를 통해 **분담 대상 역할 목록(`총괄·벤치마크`, `GPU 서버`, `리모트·메모리`, `DGD·YAML`)**을 자율적으로 나누어 맡습니다.

- **팀원별 에이전트 등록 예시**:
  ```bash
  MCP_TOOL_TIMEOUT=1800000 claude mcp add --transport http amr-agent-a "http://<미팅룸_리눅스_IP>:8080/mcp?agent_id=agent_a"
  ```
  (`agent_a`, `agent_b`, `agent_c`, `agent_d` 슬롯 사용 · 시뮬레이션 테스트 시 `/sim/mcp?agent_id=...` 사용 가능)
- 등록 후 `integrations/SKILL.md`를 스킬 폴더에 넣거나 프롬프트에 **"미팅룸 대기 모드로 입장해 줘"** 라고 입력하면 즉시 `wait_for_turn` 대기 루프가 시작됩니다.
