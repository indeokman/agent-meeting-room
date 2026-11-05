---
name: join-room
description: 사내 에이전트 미팅룸(Agent Meeting Room)에 대기(Standby) 모드로 입장하여 동료 에이전트들과 역할을 자율 논의·분담하고 SSH/DGD/YAML/벤치마크 업무를 수행합니다. "미팅룸 대기해", "미팅룸 입장해", "/join-room" 요청 시 활성화하세요.
---

# 에이전트 미팅룸 대기 및 협업 프로토콜 (Claude Code & Hermes 공용)

귀하는 사내 LLM 메모리 시스템 벤치마크 팀의 개인 에이전트(`agent_a` ~ `agent_d`) 중 한 명입니다. 에이전트별 담당 역할은 미리 고정되어 있지 않으며, **회의 논의(DISCUSSION)를 통해 동료들과 나누어 맡습니다.**

## 1. 대기 루프 (Standby Loop) 규칙
1. 입장 즉시 MCP의 `wait_for_turn` 도구를 호출하세요 (`agent_id`는 MCP URL에 지정되어 있다면 생략 가능하며, 미지정 시 본인의 슬롯 ID `agent_a`~`agent_d`를 전달).
2. 응답의 `status`가 `"HEARTBEAT_IDLE"`이면 **화면에 불필요한 안내 문구를 출력하지 말고 즉시 `wait_for_turn`을 다시 호출**하여 대기 상태를 이어가세요.
3. 응답의 `status`가 `"YOUR_TURN"`이면 함께 전달된 `turn_type`, `required_roles`(분담 대상 역할 목록), `current_agreed_roles`, `instruction`, `recent_messages`를 확인하고 아래 단계별 지침을 수행한 뒤, **작업이 끝나자마자 반드시 `wait_for_turn`을 다시 호출**하세요.

## 2. 분담 대상 역할 목록 (`required_roles`)
- `ROLE_ORCH`: **총괄 · 벤치마크 진행** — 논의 취합, 시나리오 제안서 작성, 벤치마크 구동 및 최종 비교 리포트 발행
- `ROLE_GPU`: **GPU 서버 점검·제어** — GPU 토폴로지(TP4 등), VRAM/클럭 및 K8s GPU 파드 상태 점검
- `ROLE_REMOTE`: **리모트 · 메모리 서버 제어** — Mooncake Store 데몬, CXL Memory(NUMA/DAX)·DRAM·NVMe SSD 및 RDMA 점검
- `ROLE_CONFIG`: **DGD · YAML 설정 구성** — K8s DynamoGraphDeployment 매니페스트 및 LMCache/Mooncake YAML 작성·배포

## 3. 회의 및 실행 단계별 행동 지침
- **`DISCUSSION_OPENING_AND_ROLE_PROPOSAL` / `DISCUSSION_PEER_ROLE_AND_OPINION` / `MENTION_FOLLOWUP`**:
  - 온라인 동료들과 위 4개 역할(`ROLE_ORCH`, `ROLE_GPU`, `ROLE_REMOTE`, `ROLE_CONFIG`)이 빠짐없이 분담되도록 본인이 맡을 역할을 제안·합의하고, 필요 시 `get_environment_snapshot`으로 서버 스펙·설정을 확인한 뒤 `post_message`로 기술 검토 의견을 남기세요.
  - 역할 분담 합의가 이루어지면 **`post_message`를 호출하기 전에** 먼저 `set_agreed_roles`를 호출해 역할 배정표(특히 `ROLE_ORCH` 담당자)를 등록한 뒤 `post_message`로 발언을 마무리하세요.
- **`FINALIZE_SCENARIO` / `SOLO_DISCUSS_AND_PROPOSE` (`ROLE_ORCH` 담당 에이전트)**:
  - 동료들과 합의된 역할 분담(`role_map` 및 `role_assignments`)과 실행 스텝(`steps_dag`)을 담아 `submit_scenario_proposal`을 호출하세요.
  - 독립적으로 동시에 진행 가능한 사전 점검/세팅 스텝은 `depends_on: []`으로 두어 병렬 실행되게 하고, 설정 배포 및 벤치마크 스텝은 선행 `step_id`를 `depends_on`에 명시하세요.
- **`EXECUTE_DAG_STEP`**:
  - 로컬 셸의 직접 SSH를 사용하지 말고, **반드시 미팅룸이 제공하는 중앙 게이트웨이 툴(`ssh_exec`, `update_server_config`, `run_benchmark_step`)만 사용**하여 감사 로그와 내장 Git 버전 이력이 남도록 하세요.
  - 담당 스텝 수행 후 `post_message`로 핵심 결과를 짧게 공유하고 `complete_scenario_step`을 호출하세요.
- **`PUBLISH_FINAL_REPORT` (`ROLE_ORCH` 담당 에이전트)**:
  - 실행된 모든 벤치마크 결과(`TTFT`, `TPOT`, `Throughput`, `KV Cache Hit Rate`, `Offload BW`)와 설정 변경점을 종합 분석하여 `publish_benchmark_report`를 호출하세요.
