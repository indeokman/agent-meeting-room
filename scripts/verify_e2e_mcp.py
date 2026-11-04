from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


async def mcp_call(client: Any, agent_id: str, tool_name: str, arguments: dict[str, Any]) -> Any:
    resp = await client.post(
        f"/mcp?agent_id={agent_id}&client=E2ETestAgent",
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

        app = main_mod.app
        stop_workers = False

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            print("[1/6] MCP initialize & tools/list 검증...", flush=True)
            init_res = await client.post(
                "/mcp?agent_id=agent_d",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2025-03-26", "clientInfo": {"name": "ClaudeCode"}},
                },
            )
            assert init_res.status_code == 200
            tools_res = await client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            )
            tools = tools_res.json()["result"]["tools"]
            assert len(tools) == 13, f"Expected 13 tools, got {len(tools)}"
            print(f"      -> MCP 툴 {len(tools)}개 정상 노출 확인", flush=True)

            print("[2/6] 오프라인 에이전트 부재 시 동적 역할 재분배(Failover) 검증...", flush=True)
            await mcp_call(client, "agent_a", "list_servers", {})
            await mcp_call(client, "agent_d", "list_servers", {})
            role_map_2 = app.state.orchestrator.recompute_dynamic_roles()
            assert set(role_map_2.keys()) == {"ROLE_ORCH", "ROLE_GPU", "ROLE_REMOTE", "ROLE_CONFIG"}
            assert set(role_map_2.values()) == {"agent_a", "agent_d"}
            print(f"      -> 2명 온라인 시 4개 역할 자동 분배 성공: {role_map_2}", flush=True)

            print("[3/6] 4개 개인 에이전트 동시 wait_for_turn 대기 및 순차 논의 -> 시나리오 승인 카드 검증...", flush=True)
            events_log: list[str] = []

            async def simulated_mcp_agent_worker(agent_id: str) -> None:
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

                    if t_type in ("DISCUSSION_OPENING", "SPECIALIST_OPINION"):
                        snap = await mcp_call(
                            client,
                            agent_id,
                            "get_environment_snapshot",
                            {"server_id": "gpu-node-01" if agent_id != "agent_b" else "remote-mem-01"},
                        )
                        assert "snapshot" in snap
                        await mcp_call(
                            client,
                            agent_id,
                            "post_message",
                            {
                                "room_id": room_id,
                                "content": f"[{agent_id}] 담당 영역 점검 및 시나리오 의견 제출 완료",
                            },
                        )
                    elif t_type == "FINALIZE_SCENARIO":
                        await mcp_call(
                            client,
                            agent_id,
                            "submit_scenario_proposal",
                            {
                                "room_id": room_id,
                                "title": "E2E TP4 + Mooncake CXL/DRAM 비교 시나리오",
                                "role_assignments": {
                                    "agent_a": "GPU TP4 점검",
                                    "agent_b": "Remote CXL 점검",
                                    "agent_c": "YAML 패치",
                                    "agent_d": "벤치마크 및 리포트",
                                },
                                "steps_dag": [
                                    {
                                        "step_id": "s_gpu",
                                        "title": "GPU 점검",
                                        "assigned_agent_id": "agent_a",
                                        "role_code": "ROLE_GPU",
                                        "depends_on": [],
                                        "server_id": "gpu-node-01",
                                    },
                                    {
                                        "step_id": "s_rem",
                                        "title": "Remote 점검",
                                        "assigned_agent_id": "agent_b",
                                        "role_code": "ROLE_REMOTE",
                                        "depends_on": [],
                                        "server_id": "remote-mem-01",
                                    },
                                    {
                                        "step_id": "s_cfg",
                                        "title": "YAML 업데이트",
                                        "assigned_agent_id": "agent_c",
                                        "role_code": "ROLE_CONFIG",
                                        "depends_on": ["s_gpu", "s_rem"],
                                        "server_id": "remote-mem-01",
                                    },
                                    {
                                        "step_id": "s_bench",
                                        "title": "벤치마크 수행",
                                        "assigned_agent_id": "agent_d",
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

            worker_tasks = [
                asyncio.create_task(simulated_mcp_agent_worker(aid))
                for aid in ("agent_a", "agent_b", "agent_c", "agent_d")
            ]
            try:
                await asyncio.sleep(0.15)

                post_resp = await client.post(
                    "/api/rooms/room-main/messages",
                    json={
                        "user_id": "member_a",
                        "content": "TP4 + Mooncake 기반 CXL과 DRAM 오프로딩 시나리오 작성해 줘",
                    },
                )
                assert post_resp.status_code == 200

                for _ in range(40):
                    rm = (await client.get("/api/rooms/room-main/messages")).json()["room"]
                    if rm["workflow_state"] == "APPROVAL_PENDING":
                        break
                    await asyncio.sleep(0.1)
                rm = (await client.get("/api/rooms/room-main/messages")).json()["room"]
                assert rm["workflow_state"] == "APPROVAL_PENDING", f"Unexpected state: {rm}"
                campaign_id = rm["active_campaign_id"]
                print(f"      -> 4개 에이전트 순차 논의 완료 및 APPROVAL_PENDING 도달 (campaign={campaign_id})", flush=True)

                print("[4/6] 사용자 승인 후 병렬/순차 DAG 실행 및 최종 리포트 자동 완료 검증...", flush=True)
                app_resp = await client.post(
                    f"/api/campaigns/{campaign_id}/approve",
                    json={"user_id": "member_d"},
                )
                assert app_resp.status_code == 200

                await asyncio.wait_for(worker_tasks[3], timeout=10.0)
            finally:
                stop_workers = True
                for wt in worker_tasks:
                    if not wt.done():
                        wt.cancel()
                await asyncio.gather(*worker_tasks, return_exceptions=True)

            rm_after = (await client.get("/api/rooms/room-main/messages")).json()["room"]
            assert rm_after["workflow_state"] == "COMPLETED"
            print(f"      -> 전체 실행 이벤트 흐름: {events_log}", flush=True)

            print("[5/6] 벤치마크 실행 중 무간섭(Zero-Noise) SSH 보호 가드 검증...", flush=True)
            app.state.db.set_server_lock("gpu-node-01", "BENCHMARK_RUNNING", "agent_d", "Active Latency Test")
            guard_resp = (
                await client.post("/api/servers/gpu-node-01/refresh", json={"force": False})
            ).json()
            assert guard_resp["benchmark_guard_deferred"] is True
            app.state.db.set_server_lock("gpu-node-01", "FREE", None, None)
            print("      -> 측정 중 웹 새로고침 시 SSH 호출 차단 및 캐시 스냅샷 반환 정상 동작", flush=True)

            print("[6/6] 내장 Git Side-by-Side Diff 비교, 원클릭 롤백 및 Markdown 리포트 내보내기 검증...", flush=True)
            commits = (await client.get("/api/configs/tree")).json()["commits"]
            assert len(commits) >= 2
            diff_res = (
                await client.get(
                    f"/api/configs/diff?base_hash={commits[-1]['git_hash']}&target_hash={commits[0]['git_hash']}"
                )
            ).json()
            assert diff_res["changed_file_count"] >= 1

            rb_res = await client.post(
                "/api/configs/rollback",
                json={"target_hash": commits[-1]["git_hash"]},
            )
            assert rb_res.status_code == 200

            reports = (await client.get("/api/campaigns")).json()["reports"]
            assert len(reports) == 1
            md_export = await client.get(f"/api/reports/{reports[0]['id']}/export.md")
            assert md_export.status_code == 200
            assert "CXL_MEMORY" in md_export.text
            print("      -> Git Diff / 원클릭 Rollback / Markdown 리포트 다운로드 검증 완료!", flush=True)

            # Also verify built static SPA serving!
            spa_index = await client.get("/")
            assert spa_index.status_code == 200
            assert "<div id=\"root\"></div>" in spa_index.text
            print("      -> 단일 포트 정적 React SPA 서빙 검증 완료!", flush=True)
            print("\n=== ALL E2E VERIFICATION CHECKS PASSED SUCCESSFULLY! ===", flush=True)

        app.state.db.close()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(run_verification())
