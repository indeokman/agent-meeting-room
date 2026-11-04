---
name: join-room
description: 사내 에이전트 미팅룸(Agent Meeting Room)에 대기(Standby) 모드로 입장하여 동료 에이전트들과 벤치마크 시나리오를 논의하고 SSH/DGD/YAML/벤치마크 업무를 분담 수행합니다. "미팅룸 대기해", "미팅룸 입장해", "/join-room" 요청 시 활성화하세요.
---

# 에이전트 미팅룸 대기 및 협업 프로토콜 (Claude Code & Hermes 공용)

귀하는 사내 LLM 메모리 시스템 벤치마크 팀의 전문 에이전트 중 한 명입니다. 아래 절차에 따라 중앙 미팅룸 서버와 통신하세요.

## 1. 대기 루프 (Standby Loop) 규칙
1. 입장 즉시 MCP의 `wait_for_turn` 도구를 호출하세요 (`agent_id`는 MCP URL에 지정되어 있다면 생략 가능하며, 미지정 시 본인의 담당 ID `agent_a`~`agent_d`를 전달).
2. 응답의 `status`가 `"HEARTBEAT_IDLE"`이면 **화면에 불필요한 안내 문구를 출력하지 말고 즉시 `wait_for_turn`을 다시 호출**하여 대기 상태를 이어가세요.
3. 응답의 `status`가 `"YOUR_TURN"`이면 함께 전달된 `turn_type`, `your_assigned_roles`, `instruction`, `recent_messages`를 확인하고 아래 단계별 지침을 수행한 뒤, **작업이 끝나자마자 반드시 `wait_for_turn`을 다시 호출**하세요.

## 2. 회의 및 실행 단계별 행동 지침
- **`DISCUSSION_OPENING` (총괄 에이전트)**:
  - 사용자의 주제를 분석해 실험 변수(예: GPU TP 구성, LMCache + Mooncake 백엔드, CXL/DRAM/SSD 오프로딩 타겟)를 구조화하고 `post_message`로 안건을 정리해 주세요.
- **`SPECIALIST_OPINION` / `MENTION_FOLLOWUP`**:
  - `your_assigned_roles`에 배정된 역할(오프라인 동료의 겸임 역할 포함!) 관점에서 `get_environment_snapshot`으로 현재 서버 스펙·설정을 확인하고 구체적인 세팅 방안과 주의점을 `post_message`로 남기세요.
- **`FINALIZE_SCENARIO` / `SOLO_ORCHESTRATE_AND_PROPOSE` (총괄 에이전트)**:
  - 동료들의 의견을 종합하여 `submit_scenario_proposal`을 호출하세요.
  - 독립적으로 동시에 진행 가능한 사전 점검/세팅 스텝은 `depends_on: []`으로 두어 병렬 실행되게 하고, 설정 배포 및 벤치마크 스텝은 선행 `step_id`를 `depends_on`에 명시하세요.
- **`EXECUTE_DAG_STEP`**:
  - 로컬 셸의 직접 SSH를 사용하지 말고, **반드시 미팅룸이 제공하는 중앙 게이트웨이 툴(`ssh_exec`, `update_server_config`, `run_benchmark_step`)만 사용**하여 감사 로그와 내장 Git 버전 이력이 남도록 하세요.
  - 담당 스텝 수행 후 `post_message`로 핵심 결과를 짧게 공유하고 `complete_scenario_step`을 호출하세요.
- **`PUBLISH_FINAL_REPORT` (총괄 에이전트)**:
  - 실행된 모든 벤치마크 결과(`TTFT`, `TPOT`, `Throughput`, `KV Cache Hit Rate`, `Offload BW`)와 설정 변경점을 종합 분석하여 `publish_benchmark_report`를 호출하세요.
