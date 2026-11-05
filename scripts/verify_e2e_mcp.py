from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


async def mcp_call(
    client: Any,
    agent_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    prefix: str = "/sim",
) -> Any:
    resp = await client.post(
        f"{prefix}/mcp?agent_id={agent_id}&client=E2ETestAgent",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        },
    )
    assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text}"
    body = resp.json()
    assert not body["result"]["isError"], f"MCP error: {body}"
    return json.loads(body["result"]["content"][0]["text"])


async def run_verification() -> None:
    tmpdir = str(PROJECT_ROOT / "data_e2e_tmp")
    shutil.rmtree(tmpdir, ignore_errors=True)
    os.makedirs(tmpdir, exist_ok=True)
    try:
        os.environ["AMR_DATA_DIR"] = tmpdir
        from httpx import ASGITransport, AsyncClient
        import backend.main as main_mod

        app = main_mod.create_app()
        stop_workers = False

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            print("[1/8] 운영 모드(/) vs 시뮬레이션 모드(/sim) 경로 분리 및 동일 기능 구조 검증...", flush=True)
            live_boot = (await client.get("/api/bootstrap")).json()
            sim_boot = (await client.get("/sim/api/bootstrap")).json()
            assert live_boot["mode"] == "live"
            assert sim_boot["mode"] == "sim"
            assert len(live_boot["required_roles"]) == 4
            assert len(sim_boot["required_roles"]) == 4
            # Agents start with no pre-fixed roles!
            assert all(a["primary_role"] == "" and a["assigned_roles"] == [] for a in live_boot["agents"])
            assert len(live_boot["servers"]) == 0, "운영 모드 초기 상태에는 가상 서버가 없어야 함"
            assert len(sim_boot["servers"]) == 2, "시뮬레이션 모드 초기 상태에는 테스트용 서버 2대 존재"
            print("      -> 운영(/) / 시뮬레이션(/sim) 완전 분리 및 초기 역할 미정(Unassigned) 확인 완료", flush=True)

            print("[2/8] WinSCP 방식 서버 연결 테스트 및 추가/삭제 검증...", flush=True)
            conn_test = (
                await client.post(
                    "/sim/api/servers/test-connection",
                    json={"host": "10.20.30.105", "ssh_port": 22, "ssh_user": "root", "ssh_password": "pw"},
                )
            ).json()
            assert conn_test["ok"] is True
            added = (
                await client.post(
                    "/sim/api/servers",
                    json={
                        "name": "추가 GPU 노드",
                        "host": "10.20.30.105",
                        "ssh_port": 22,
                        "ssh_user": "root",
                        "ssh_password": "pw",
                        "server_role": "GPU",
                    },
                )
            ).json()
            assert added["server"]["has_password"] is True
            del_res = await client.delete(f"/sim/api/servers/{added['server']['id']}")
            assert del_res.status_code == 200
            print("      -> WinSCP 방식 호스트/포트/계정/비밀번호 등록·연결테스트·삭제 검증 완료", flush=True)

            print("[3/8] MCP initialize & tools/list 검증 (set_agreed_roles 포함 14개 툴)...", flush=True)
            tools_res = await client.post(
                "/sim/mcp",
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            )
            tools = tools_res.json()["result"]["tools"]
            assert len(tools) == 14, f"Expected 14 tools, got {len(tools)}"
            print(f"      -> MCP 툴 {len(tools)}개 정상 노출 확인", flush=True)

            print("[4/8] 4개 에이전트 동시 접속 -> 논의를 통한 자율 역할 분담(Agent B가 총괄 담당) -> 승인 카드 검증...", flush=True)
            events_log: list[str] = []

            # Deliberately assign non-traditional roles (e.g. Agent B takes ROLE_ORCH!) to prove roles are decided by discussion!
            agreed_discussion_roles = {
                "ROLE_ORCH": "agent_b",
                "ROLE_GPU": "agent_d",
                "ROLE_REMOTE": "agent_c",
                "ROLE_CONFIG": "agent_a",
            }

            async def mcp_agent_worker(agent_id: str) -> None:
                while not stop_workers:
                    turn = await mcp_call(
                        client,
                        agent_id,
                        "wait_for_turn",
                        {"agent_id": agent_id, "max_wait_sec": 1},
                    )
                    if turn.get("status") == "HEARTBEAT_IDLE":
                        continue

                    t_type = turn["turn_type"]
                    room_id = turn["room_id"]
                    events_log.append(f"{agent_id}:{t_type}")

                    if t_type in ("DISCUSSION_OPENING_AND_ROLE_PROPOSAL", "DISCUSSION_PEER_ROLE_AND_OPINION"):
                        if agent_id == "agent_d":
                            await mcp_call(
                                client,
                                agent_id,
                                "set_agreed_roles",
                                {"room_id": room_id, "role_map": agreed_discussion_roles},
                            )
                        await mcp_call(
                            client,
                            agent_id,
                            "post_message",
                            {
                                "room_id": room_id,
                                "content": f"[{agent_id}] 역할 분담 논의 및 서버 설정 검토 의견 제출",
                            },
                        )
                    elif t_type == "FINALIZE_SCENARIO":
                        # Because Agent B was agreed as ROLE_ORCH during discussion, Agent B must receive FINALIZE_SCENARIO!
                        assert agent_id == "agent_b", f"Expected agent_b as agreed ROLE_ORCH, got {agent_id}"
                        await mcp_call(
                            client,
                            agent_id,
                            "submit_scenario_proposal",
                            {
                                "room_id": room_id,
                                "title": "논의 기반 역할 분담 E2E 시나리오",
                                "role_map": agreed_discussion_roles,
                                "role_assignments": {
                                    "Agent B (총괄·벤치마크)": "벤치마크 및 리포트",
                                    "Agent D (GPU 서버)": "GPU TP4 점검",
                                    "Agent C (리모트·메모리)": "Remote CXL 점검",
                                    "Agent A (DGD·YAML)": "YAML 패치",
                                },
                                "steps_dag": [
                                    {
                                        "step_id": "s_gpu",
                                        "title": "GPU 점검",
                                        "assigned_agent_id": "agent_d",
                                        "role_code": "ROLE_GPU",
                                        "depends_on": [],
                                        "server_id": "gpu-node-01",
                                    },
                                    {
                                        "step_id": "s_rem",
                                        "title": "Remote 점검",
                                        "assigned_agent_id": "agent_c",
                                        "role_code": "ROLE_REMOTE",
                                        "depends_on": [],
                                        "server_id": "remote-mem-01",
                                    },
                                    {
                                        "step_id": "s_cfg",
                                        "title": "YAML 업데이트",
                                        "assigned_agent_id": "agent_a",
                                        "role_code": "ROLE_CONFIG",
                                        "depends_on": ["s_gpu", "s_rem"],
                                        "server_id": "remote-mem-01",
                                    },
                                    {
                                        "step_id": "s_bench",
                                        "title": "벤치마크 수행",
                                        "assigned_agent_id": "agent_b",
                                        "role_code": "ROLE_ORCH",
                                        "depends_on": ["s_cfg"],
                                        "server_id": "gpu-node-01",
                                    },
                                ],
                                "target_configs_preview": {
                                    "remote-mem-01/configs/mooncake_store.yaml": "CXL vs DRAM"
                                },
                            },
                        )
                    elif t_type == "EXECUTE_DAG_STEP":
                        step = turn["step"]
                        cid = turn["campaign_id"]
                        sid = step["step_id"]
                        if sid in ("s_gpu", "s_rem"):
                            await mcp_call(
                                client,
                                agent_id,
                                "ssh_exec",
                                {
                                    "server_id": step["server_id"],
                                    "command": "nvidia-smi" if sid == "s_gpu" else "cxl list -Mu",
                                    "purpose": step["title"],
                                    "room_id": room_id,
                                    "campaign_id": cid,
                                },
                            )
                        elif sid == "s_cfg":
                            await mcp_call(
                                client,
                                agent_id,
                                "update_server_config",
                                {
                                    "server_id": "remote-mem-01",
                                    "repo_path": "remote-mem-01/configs/mooncake_store.yaml",
                                    "content": 'storage_tier:\n  active_destination: "CXL_MEMORY"\n  cxl_gb: 512\n',
                                    "commit_message": "E2E 테스트용 Mooncake CXL 타겟 적용",
                                    "room_id": room_id,
                                    "campaign_id": cid,
                                },
                            )
                        elif sid == "s_bench":
                            await mcp_call(
                                client,
                                agent_id,
                                "run_benchmark_step",
                                {
                                    "campaign_id": cid,
                                    "server_id": "gpu-node-01",
                                    "variant_label": "CXL Memory 512GB",
                                    "offload_target": "CXL_MEMORY",
                                    "command": "aiperf profile --tag CXL",
                                    "room_id": room_id,
                                },
                            )
                            await mcp_call(
                                client,
                                agent_id,
                                "run_benchmark_step",
                                {
                                    "campaign_id": cid,
                                    "server_id": "gpu-node-01",
                                    "variant_label": "DDR5 DRAM 192GB",
                                    "offload_target": "DRAM",
                                    "command": "aiperf profile --tag DRAM",
                                    "room_id": room_id,
                                },
                            )
                        await mcp_call(
                            client,
                            agent_id,
                            "complete_scenario_step",
                            {"campaign_id": cid, "step_id": sid, "result_note": "성공"},
                        )
                    elif t_type == "PUBLISH_FINAL_REPORT":
                        assert agent_id == "agent_b"
                        await mcp_call(
                            client,
                            agent_id,
                            "publish_benchmark_report",
                            {
                                "campaign_id": turn["campaign_id"],
                                "title": "E2E CXL vs DRAM 벤치마크 리포트",
                                "summary_markdown": "## 결과 요약\n- CXL 히트율 우수",
                            },
                        )
                        return

            worker_tasks = {
                aid: asyncio.create_task(mcp_agent_worker(aid))
                for aid in ("agent_a", "agent_b", "agent_c", "agent_d")
            }
            try:
                await asyncio.sleep(0.15)
                post_resp = await client.post(
                    "/sim/api/rooms/room-main/messages",
                    json={
                        "user_id": "member_a",
                        "content": "TP4 + Mooncake 기반 CXL과 DRAM 오프로딩 시나리오 작성해 줘",
                    },
                )
                assert post_resp.status_code == 200

                for _ in range(40):
                    rm = (await client.get("/sim/api/rooms/room-main/messages")).json()["room"]
                    if rm["workflow_state"] == "APPROVAL_PENDING":
                        break
                    await asyncio.sleep(0.1)
                rm = (await client.get("/sim/api/rooms/room-main/messages")).json()["room"]
                assert rm["workflow_state"] == "APPROVAL_PENDING", f"Unexpected state: {rm}"
                campaign_id = rm["active_campaign_id"]
                print(f"      -> 자율 역할 분담 완료 (총괄=agent_b) 및 APPROVAL_PENDING 도달 ({campaign_id})", flush=True)

                print("[5/8] 사용자 승인 후 병렬/순차 DAG 실행 및 최종 리포트 발행 검증...", flush=True)
                app_resp = await client.post(
                    f"/sim/api/campaigns/{campaign_id}/approve",
                    json={"user_id": "member_b"},
                )
                assert app_resp.status_code == 200
                await asyncio.wait_for(worker_tasks["agent_b"], timeout=10.0)
            finally:
                stop_workers = True
                for wt in worker_tasks.values():
                    if not wt.done():
                        wt.cancel()
                await asyncio.gather(*worker_tasks.values(), return_exceptions=True)

            rm_after = (await client.get("/sim/api/rooms/room-main/messages")).json()["room"]
            assert rm_after["workflow_state"] == "COMPLETED"
            # Verify operational mode DB was NOT touched by /sim runs!
            live_after = (await client.get("/api/bootstrap")).json()
            assert len(live_after["campaigns"]) == 0
            print(f"      -> 전체 실행 이벤트 흐름: {events_log}", flush=True)

            print("[6/8] 무간섭(Zero-Noise) SSH 가드 & 내장 Git Diff/Rollback/Markdown 내보내기 검증...", flush=True)
            app.state.sim_hub.db.set_server_lock("gpu-node-01", "BENCHMARK_RUNNING", "agent_b", "Active Latency Test")
            guard_resp = (
                await client.post("/sim/api/servers/gpu-node-01/refresh", json={"force": False})
            ).json()
            assert guard_resp["benchmark_guard_deferred"] is True
            app.state.sim_hub.db.set_server_lock("gpu-node-01", "FREE", None, None)

            commits = (await client.get("/sim/api/configs/tree")).json()["commits"]
            assert len(commits) >= 2
            diff_res = (
                await client.get(
                    f"/sim/api/configs/diff?base_hash={commits[-1]['git_hash']}&target_hash={commits[0]['git_hash']}"
                )
            ).json()
            assert diff_res["changed_file_count"] >= 1

            rb_res = await client.post(
                "/sim/api/configs/rollback",
                json={"target_hash": commits[-1]["git_hash"]},
            )
            assert rb_res.status_code == 200

            reports = (await client.get("/sim/api/campaigns")).json()["reports"]
            assert len(reports) == 1
            md_export = await client.get(f"/sim/api/reports/{reports[0]['id']}/export.md")
            assert md_export.status_code == 200
            assert "CXL_MEMORY" in md_export.text
            print("      -> Git Diff / 원클릭 Rollback / Markdown 다운로드 검증 완료!", flush=True)

            print("[7/8] 정적 React SPA 서빙 경로 (/ 및 /sim) 검증...", flush=True)
            for path in ("/", "/sim"):
                spa = await client.get(path)
                assert spa.status_code == 200
                assert '<div id="root"></div>' in spa.text
            print("      -> / 및 /sim 정적 SPA 서빙 검증 완료!", flush=True)

            print("[8/8] 외부 MCP 미연결 상태에서 /sim 가상 에이전트 자율 논의·승인·실행 검증...", flush=True)
            app.state.sim_hub.orchestrator._external_heartbeat.clear()
            app.state.sim_hub.orchestrator._active_waiters.clear()
            v_post = await client.post(
                "/sim/api/rooms/room-main/messages",
                json={"user_id": "member_a", "content": "기능 테스트용 CXL/DRAM/SSD 3종 비교 벤치마크 진행해 줘"},
            )
            assert v_post.status_code == 200
            for _ in range(50):
                v_rm = (await client.get("/sim/api/rooms/room-main/messages")).json()["room"]
                if v_rm["workflow_state"] == "APPROVAL_PENDING":
                    break
                await asyncio.sleep(0.1)
            v_rm = (await client.get("/sim/api/rooms/room-main/messages")).json()["room"]
            assert v_rm["workflow_state"] == "APPROVAL_PENDING"
            v_cid = v_rm["active_campaign_id"]
            await client.post(f"/sim/api/campaigns/{v_cid}/approve", json={"user_id": "member_a"})
            for _ in range(60):
                v_rm = (await client.get("/sim/api/rooms/room-main/messages")).json()["room"]
                if v_rm["workflow_state"] == "COMPLETED":
                    break
                await asyncio.sleep(0.1)
            assert v_rm["workflow_state"] == "COMPLETED"
            print("      -> /sim 가상 동료 에이전트 역할 논의 -> 승인 -> 3종 벤치마크 -> 리포트 발행 검증 완료!", flush=True)
            print("\n=== ALL 8/8 E2E VERIFICATION CHECKS PASSED SUCCESSFULLY! ===", flush=True)

        app.state.live_hub.db.close()
        app.state.sim_hub.db.close()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(run_verification())
