# Agent Meeting Room & LLM Memory Benchmark Hub

사내 폐쇄형 LLM에 연동된 각 팀원의 개인 에이전트(**Claude Code**, **Hermes**) 4개가 단일 채팅방에 모여 테스트 시나리오를 논의·분담하고, **단일 중앙 SSH 게이트웨이**와 **내장 Git**을 통해 GPU 서버(K8s Dynamo DGD, TP4, LMCache) 및 리모트 메모리 서버(Mooncake Store, CXL Memory / DRAM / NVMe SSD)를 무간섭으로 제어·기록·리포팅하는 올인원 플랫폼입니다.

## 주요 기능

- **단일 포트 올인원 아키텍처 (`FastAPI` + `SQLite WAL` + `내장 Git` + `React SPA` + `/mcp`)**: 외부 PostgreSQL/Redis 없이 단일 방화벽 포트로 웹 대시보드, 실시간 WebSocket, 원격 MCP 서버를 모두 서빙합니다.
- **순수 MCP 대기 루프 (`wait_for_turn`)**: 별도 클라이언트 프로그램 설치 없이 Claude Code와 Hermes 모두 동일하게 대기 모드로 참여합니다.
- **선호 역할 기반 동적 역할 분배**: 일부 팀원 에이전트가 오프라인이어도 온라인 에이전트들이 빈 역할(`GPU` / `리모트·메모리` / `DGD·YAML` / `총괄·벤치마크`)을 자동 겸임하여 `.md` 파일 인수인계 왜곡을 해소합니다.
- **무간섭(Zero-Noise) 온디맨드 SSH 수집**: 상시 백그라운드 폴링 없이 **웹 접속/새로고침 시점** 및 **설정 변경·벤치마크 전후**에만 수집하며, 벤치마크 측정 중(`BENCHMARK_RUNNING`)에는 실시간 SSH 간섭을 자동 유예합니다.
- **내장 Git 버전 관리 & 원클릭 롤백**: 모든 DGD/YAML 변경을 자동 커밋하여 Side-by-Side Diff 비교와 특정 시점 원클릭 서버 복원을 지원합니다.

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

브라우저에서 `http://localhost:8080` 에 접속하면 바로 사용할 수 있으며, 상단 **`[🔌 MCP 에이전트 연결 가이드]`** 버튼에서 팀원별 Claude Code / Hermes 연결 명령어를 복사할 수 있습니다.

## 환경변수 설정 (추후 인증 확장 포함)

| 환경변수 | 기본값 | 설명 |
| :--- | :--- | :--- |
| `AMR_HOST` | `0.0.0.0` | 바인딩 호스트 |
| `AMR_PORT` | `8080` | 단일 서비스 포트 (웹 + API + WS + MCP) |
| `AMR_DATA_DIR` | `./data` | SQLite DB, 내장 Git 저장소, 벤치마크 원본 로그 경로 |
| `AMR_AUTH_MODE` | `none` | `none` (초기 무인증 프로필 선택) \| `token` \| `sso_header` |
| `AMR_API_TOKEN` | `""` | `AMR_AUTH_MODE=token` 사용 시 검증할 토큰 |
| `AMR_SSO_USER_HEADER` | `X-Forwarded-User` | `AMR_AUTH_MODE=sso_header` 사용 시 사내 SSO 프록시 헤더명 |
