# Agent Meeting Room & LLM Memory Benchmark Hub

사내 폐쇄형 LLM에 연동된 각 팀원의 개인 에이전트(**Claude Code**, **Hermes**)들이 단일 채팅방에 모여 역할을 자율 논의·분담하고, **단일 중앙 SSH 게이트웨이**와 **내장 Git**을 통해 GPU 서버(K8s Dynamo DGD, TP4, LMCache) 및 리모트 메모리 서버(Mooncake Store, CXL Memory / DRAM / NVMe SSD)를 무간섭으로 제어·기록·리포팅하는 올인원 플랫폼입니다.

## 주요 기능

- **단일 포트 올인원 아키텍처 (`FastAPI` + `SQLite WAL` + `내장 Git` + `React SPA` + `/mcp`)**: 외부 PostgreSQL/Redis 없이 단일 방화벽 포트로 웹 대시보드, 실시간 WebSocket, 원격 MCP 서버를 모두 서빙합니다.
- **논의 기반 에이전트 역할 자율 분담**: 에이전트별 역할을 고정하지 않고, 주제가 발제되면 접속 중인 에이전트들이 논의를 통해 **분담 대상 역할 목록(`총괄·벤치마크`, `GPU 서버`, `리모트·메모리`, `DGD·YAML`)**을 나누어 맡습니다.
- **WinSCP 방식 간편 SSH 서버 연결**: 복잡한 개인키 경로/연결 모드 구분 없이 **호스트 IP, 포트(22), 사용자 이름, 비밀번호**만으로 간편하게 서버를 등록하고 연결 테스트를 수행합니다.
- **운영 모드(`/`)와 기능 테스트용 시뮬레이션 모드(`/sim`) 경로 분리**: 두 경로는 100% 동일한 UI와 기능을 제공하며, `/sim` 경로에서는 운영 DB/서버에 영향을 주지 않고 전 기능을 사전 검증할 수 있습니다.
- **무간섭(Zero-Noise) 온디맨드 SSH 수집 & 내장 Git 형상관리**: 상시 백그라운드 폴링 없이 이벤트·요청 시점에만 수집하며, 모든 DGD/YAML 변경을 내장 Git에 커밋하여 Side-by-Side Diff 및 원클릭 롤백을 지원합니다.

## 빠른 실행 방법 (Linux / Windows 공통)

```bash
# 1. 가상환경 생성 및 백엔드 패키지 설치
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. (프론트엔드 수정 시) 정적 번들 빌드
cd frontend && npm install && npm run build && cd ..

# 3. 통합 서버 실행 (기본 포트: 8080)
python run.py
```

- **실제 운영 경로**: `http://localhost:8080/` (MCP: `/mcp`)
- **기능 테스트용 시뮬레이션 경로**: `http://localhost:8080/sim` (MCP: `/sim/mcp`)
