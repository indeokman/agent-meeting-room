from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import time
from pathlib import Path
from typing import Any, Callable, Coroutine

try:
    import asyncssh  # type: ignore[import-not-found]
except ImportError:
    asyncssh = None  # Graceful fallback to system OpenSSH (`ssh` CLI) in air-gapped setups

import yaml

from .config import settings
from .db import Database
from .git_store import EmbeddedGitStore

BroadcastCallback = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


DEFAULT_GPU_DGD_YAML = """apiVersion: nvidia.com/v1alpha1
kind: DynamoGraphDeployment
metadata:
  name: vllm-disagg-tp4
  namespace: dynamo-llm
  labels:
    app.kubernetes.io/part-of: memory-eval-bench
spec:
  backendFramework: vllm
  services:
    Frontend:
      dynamoNamespace: memory-eval
      componentType: frontend
      replicas: 1
    VllmDecodeWorker:
      dynamoNamespace: memory-eval
      componentType: worker
      replicas: 1
      resources:
        limits:
          nvidia.com/gpu: "4"
      extraPodSpec:
        mainContainer:
          image: nvcr.io/nvidia/ai-dynamo/vllm-runtime:0.6.1
          args:
            - "--model"
            - "/models/Llama-3.1-70B-Instruct"
            - "--tensor-parallel-size"
            - "4"
            - "--gpu-memory-utilization"
            - "0.90"
            - "--kv-transfer-config"
            - '{"kv_connector":"LMCacheConnectorV1","kv_role":"kv_both"}'
          env:
            - name: LMCACHE_CONFIG_FILE
              value: "/etc/lmcache/lmcache_config.yaml"
            - name: LMCACHE_USE_EXPERIMENTAL
              value: "True"
"""

DEFAULT_LMCACHE_YAML = """chunk_size: 256
local_cpu: false
max_local_cpu_size: 0.0
remote_url: "mooncakestore://10.20.30.102:50051/"
remote_serde: "naive"
pipelined_backend: true
save_decode_cache: true
extra_config:
  mooncake_master_server: "10.20.30.102:50051"
  mooncake_metadata_server: "http://10.20.30.102:8090/metadata"
  mooncake_protocol: "rdma"
  mooncake_device_name: "mlx5_0"
  kv_offload_tier: "CXL_MEMORY"
"""

DEFAULT_MOONCAKE_YAML = """master_server:
  listen_address: "0.0.0.0:50051"
  http_metadata_port: 8090
  eviction_policy: "LRU"
storage_tier:
  active_destination: "CXL_MEMORY"
  cxl_memory:
    enabled: true
    numa_node: 2
    dax_device: "/dev/dax2.0"
    capacity_gb: 512
  dram_pool:
    enabled: false
    numa_node: 0
    hugepages_gb: 192
  nvme_ssd:
    enabled: false
    mount_path: "/mnt/nvme_kv_pool"
    direct_io: true
    capacity_gb: 3840
transport:
  protocol: "rdma_roce_v2"
  rdma_devices: ["mlx5_0", "mlx5_1"]
  max_bandwidth_gbps: 400
"""

DEFAULT_CXL_ENV = """# Remote Memory Node Tiering Environment
MOONCAKE_OFFLOAD_TARGET=CXL_MEMORY
MOONCAKE_NUMA_BIND=2
CXL_MEM_MODE=system-ram-interleave-off
RDMA_GID_INDEX=3
"""


REMOTE_INSPECT_PYTHON = r'''
import json, os, platform, shutil, subprocess

def run_cmd(cmd, timeout=8):
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return p.stdout.strip() if p.returncode == 0 else ""
    except Exception:
        return ""

payload = json.loads(os.environ.get("AMR_INSPECT_ARGS", "{}"))
ns = payload.get("k8s_namespace", "default")
watched = payload.get("watched_configs", [])

lscpu_out = run_cmd("lscpu")
cpu_info = {}
for line in lscpu_out.splitlines():
    if ":" in line:
        k, v = line.split(":", 1)
        cpu_info[k.strip()] = v.strip()

gpus = []
smi = run_cmd("nvidia-smi --query-gpu=index,name,driver_version,memory.total,memory.used,utilization.gpu,temperature.gpu,pstate --format=csv,noheader,nounits")
driver_ver = ""
for row in smi.splitlines():
    parts = [x.strip() for x in row.split(",")]
    if len(parts) >= 8:
        driver_ver = parts[2]
        gpus.append({
            "index": int(parts[0]),
            "name": parts[1],
            "memory_total_mb": int(float(parts[3])),
            "memory_used_mb": int(float(parts[4])),
            "utilization_pct": int(float(parts[5])),
            "temp_c": int(float(parts[6])),
            "pstate": parts[7],
        })

gpu_topo = run_cmd("nvidia-smi topo -m")
numa_raw = run_cmd("numactl -H")
cxl_raw = run_cmd("cxl list -Mu")
lsblk_raw = run_cmd("lsblk -J -o NAME,MODEL,SIZE,TYPE,MOUNTPOINT")
rdma_raw = run_cmd("ibstat || rdma link")

numa_nodes = []
for line in numa_raw.splitlines():
    if line.startswith("node ") and "size:" in line:
        toks = line.split()
        nid = int(toks[1])
        size_mb = int(toks[3])
        numa_nodes.append({"node_id": nid, "total_gb": round(size_mb / 1024, 1), "tier": "CXL_MEMORY" if "cpus:" in numa_raw and f"node {nid} cpus:\n" in (numa_raw + "\n") else "DRAM"})

storage_devs = []
if lsblk_raw:
    try:
        for b in json.loads(lsblk_raw).get("blockdevices", []):
            if b.get("type") == "disk":
                storage_devs.append({"name": b.get("name"), "model": b.get("model") or "Disk", "size": b.get("size"), "mount": b.get("mountpoint") or ""})
    except Exception:
        pass

k8s_pods = []
pods_raw = run_cmd(f"kubectl get pods -n {ns} -o json")
if pods_raw:
    try:
        for item in json.loads(pods_raw).get("items", []):
            k8s_pods.append({
                "name": item["metadata"]["name"],
                "phase": item.get("status", {}).get("phase", "Unknown"),
                "node": item.get("spec", {}).get("nodeName", ""),
            })
    except Exception:
        pass

configs = {}
for item in watched:
    kind = item.get("kind")
    repo_path = item.get("repo_path")
    if kind == "k8s_dgd":
        name = item.get("name", "")
        y = run_cmd(f"kubectl get dgd {name} -n {ns} -o yaml")
        if y:
            configs[repo_path] = y
    elif kind == "file":
        rpath = item.get("remote_path", "")
        if rpath and os.path.exists(rpath):
            try:
                with open(rpath, "r", encoding="utf-8", errors="replace") as f:
                    configs[repo_path] = f.read()
            except Exception:
                pass

result = {
    "hw_specs": {
        "hostname": platform.node(),
        "cpu_model": cpu_info.get("Model name", platform.processor() or "Linux x86_64"),
        "cpu_cores": cpu_info.get("CPU(s)", ""),
        "sockets": cpu_info.get("Socket(s)", "1"),
        "gpus": gpus,
        "gpu_topology_matrix": gpu_topo,
        "numa_nodes": numa_nodes,
        "cxl_raw": cxl_raw,
        "storage": storage_devs,
        "rdma_status": rdma_raw[:800] if rdma_raw else "N/A",
    },
    "sw_specs": {
        "os_kernel": f"{platform.system()} {platform.release()}",
        "gpu_driver": driver_ver or "N/A",
        "cuda_version": run_cmd("nvcc --version | tail -n 1") or "12.6",
        "k8s_namespace": ns,
        "k8s_pods": k8s_pods,
        "packages": {
            "vllm": run_cmd("python3 -c 'import vllm; print(vllm.__version__)'") or "containerized",
            "lmcache": run_cmd("python3 -c 'import lmcache; print(lmcache.__version__)'") or "containerized",
        },
    },
    "active_configs": configs,
}
print(json.dumps(result))
'''


class SSHGateway:
    """Central Agentless SSH Gateway, Server Resource Lock Manager, and Lab Simulator."""

    def __init__(
        self,
        db: Database,
        git_store: EmbeddedGitStore,
        broadcast_fn: BroadcastCallback | None = None,
    ) -> None:
        self.db = db
        self.git_store = git_store
        self._broadcast_fn = broadcast_fn
        self._pool: dict[str, Any] = {}
        self._server_locks: dict[str, asyncio.Lock] = {}
        self._sim_configs: dict[str, str] = {}
        self._bootstrap_initial_configs()

    def set_broadcaster(self, fn: BroadcastCallback) -> None:
        self._broadcast_fn = fn

    async def _emit(self, event: dict[str, Any]) -> None:
        if self._broadcast_fn:
            await self._broadcast_fn(event)

    def _get_asyncio_lock(self, server_id: str) -> asyncio.Lock:
        if server_id not in self._server_locks:
            self._server_locks[server_id] = asyncio.Lock()
        return self._server_locks[server_id]

    def _bootstrap_initial_configs(self) -> None:
        """Seed baseline DGD/LMCache/Mooncake configs into EmbeddedGitStore on first startup."""
        current_tree = self.git_store.get_current_working_tree()
        defaults = {
            "gpu-node-01/k8s/dgd-active.yaml": DEFAULT_GPU_DGD_YAML,
            "gpu-node-01/configs/lmcache_config.yaml": DEFAULT_LMCACHE_YAML,
            "remote-mem-01/configs/mooncake_store.yaml": DEFAULT_MOONCAKE_YAML,
            "remote-mem-01/configs/cxl_tiering.env": DEFAULT_CXL_ENV,
        }
        if not current_tree:
            commit = self.git_store.commit_files(
                updates=defaults,
                author="system_init",
                message="초기 베이스라인 구성 (TP4 Dynamo DGD + LMCache Mooncake 백엔드)",
            )
            self.db.record_config_commit(commit)
            self._sim_configs = dict(defaults)
        else:
            self._sim_configs = {**defaults, **current_tree}

        # Seed initial snapshot per server if none exists yet
        for srv in self.db.list_servers():
            if not self.db.get_latest_snapshot(srv["id"]):
                hw, sw, cfgs = self._build_simulated_state(srv)
                self.db.save_snapshot(
                    server_id=srv["id"],
                    trigger_type="INITIAL_BOOT",
                    triggered_by="system",
                    hw_specs=hw,
                    sw_specs=sw,
                    active_configs=cfgs,
                    git_hash=self.git_store.get_head_hash(),
                )

    async def _get_ssh_conn(self, server: dict[str, Any]) -> Any:
        sid = server["id"]
        conn = self._pool.get(sid)
        if conn is not None and not conn.is_closed():
            return conn

        kwargs: dict[str, Any] = {
            "host": server["host"],
            "port": int(server.get("ssh_port", 22)),
            "username": server.get("ssh_user") or "ubuntu",
            "known_hosts": None,
            "connect_timeout": 10,
        }
        key_path = (server.get("ssh_key_path") or "").strip()
        if key_path:
            expanded = os.path.expanduser(key_path)
            if os.path.exists(expanded):
                kwargs["client_keys"] = [expanded]
        if server.get("ssh_password"):
            kwargs["password"] = server["ssh_password"]

        conn = await asyncssh.connect(**kwargs)
        self._pool[sid] = conn
        return conn

    async def _run_remote(
        self,
        server: dict[str, Any],
        command: str,
        input_text: str | None = None,
        timeout: int = 120,
    ) -> tuple[str, str, int]:
        """Run a remote command via `asyncssh` pool or system OpenSSH (`ssh` CLI) fallback."""
        use_cli = server.get("connection_mode") == "ssh_cli" or asyncssh is None
        if not use_cli:
            conn = await self._get_ssh_conn(server)
            res = await conn.run(command, input=input_text, check=False, timeout=timeout)
            return (res.stdout or "", res.stderr or "", int(res.exit_status or 0))

        ssh_bin = shutil.which("ssh")
        if not ssh_bin:
            raise RuntimeError("시스템에 'ssh' 바이너리 또는 'asyncssh' 패키지가 없습니다.")

        args = [
            ssh_bin,
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "ConnectTimeout=10",
            "-p",
            str(int(server.get("ssh_port", 22))),
        ]
        key_path = (server.get("ssh_key_path") or "").strip()
        if key_path:
            expanded = os.path.expanduser(key_path)
            if os.path.exists(expanded):
                args.extend(["-i", expanded])

        user_prefix = f"{server['ssh_user']}@" if server.get("ssh_user") else ""
        args.extend([f"{user_prefix}{server['host']}", command])

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE if input_text is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(input=input_text.encode("utf-8") if input_text is not None else None),
            timeout=timeout,
        )
        return (
            stdout_b.decode("utf-8", errors="replace"),
            stderr_b.decode("utf-8", errors="replace"),
            int(proc.returncode or 0),
        )

    def _build_simulated_state(
        self, server: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        sid = server["id"]
        tree = self.git_store.get_current_working_tree()
        watched = server.get("watched_configs", [])
        active_configs: dict[str, str] = {}
        for item in watched:
            rpath = item.get("repo_path", "")
            if rpath in tree:
                active_configs[rpath] = tree[rpath]
            elif rpath in self._sim_configs:
                active_configs[rpath] = self._sim_configs[rpath]

        if server["server_role"] == "GPU":
            # Parse TP size & offload tier from active configs so UI reflects live agent mutations!
            dgd_text = active_configs.get("gpu-node-01/k8s/dgd-active.yaml", DEFAULT_GPU_DGD_YAML)
            lmc_text = active_configs.get("gpu-node-01/configs/lmcache_config.yaml", DEFAULT_LMCACHE_YAML)
            tp_size = "4"
            try:
                parsed_lmc = yaml.safe_load(lmc_text) or {}
                offload_tier = parsed_lmc.get("extra_config", {}).get("kv_offload_tier", "CXL_MEMORY")
            except Exception:
                offload_tier = "CXL_MEMORY"
            if '"2"' in dgd_text and "--tensor-parallel-size" in dgd_text:
                tp_size = "2"

            hw_specs = {
                "hostname": "gpu-srv-h100-01.lab.internal",
                "cpu_model": "2x Intel Xeon Platinum 8580 (120 Cores / 240 Threads)",
                "cpu_cores": "120",
                "sockets": "2",
                "gpus": [
                    {"index": 0, "name": "NVIDIA H100 80GB HBM3", "memory_total_mb": 81559, "memory_used_mb": 71420, "utilization_pct": 12, "temp_c": 41, "pstate": "P0"},
                    {"index": 1, "name": "NVIDIA H100 80GB HBM3", "memory_total_mb": 81559, "memory_used_mb": 71388, "utilization_pct": 11, "temp_c": 42, "pstate": "P0"},
                    {"index": 2, "name": "NVIDIA H100 80GB HBM3", "memory_total_mb": 81559, "memory_used_mb": 71402, "utilization_pct": 12, "temp_c": 40, "pstate": "P0"},
                    {"index": 3, "name": "NVIDIA H100 80GB HBM3", "memory_total_mb": 81559, "memory_used_mb": 71395, "utilization_pct": 11, "temp_c": 41, "pstate": "P0"},
                ],
                "gpu_topology_matrix": (
                    f"Active Tensor Parallel: TP{tp_size} (NVSwitch 900GB/s Full Mesh)\n"
                    "        GPU0    GPU1    GPU2    GPU3    NIC0(mlx5_0)\n"
                    "GPU0     X      NV18    NV18    NV18    PIX\n"
                    "GPU1    NV18     X      NV18    NV18    PIX\n"
                    "GPU2    NV18    NV18     X      NV18    NODE\n"
                    "GPU3    NV18    NV18    NV18     X      NODE"
                ),
                "numa_nodes": [
                    {"node_id": 0, "tier": "DRAM", "total_gb": 512.0, "used_gb": 94.2, "cpus": "0-59", "desc": "DDR5-5600 Socket 0"},
                    {"node_id": 1, "tier": "DRAM", "total_gb": 512.0, "used_gb": 82.6, "cpus": "60-119", "desc": "DDR5-5600 Socket 1"},
                ],
                "storage": [
                    {"name": "nvme0n1", "model": "Samsung PM9A3 3.84TB", "size": "3.5T", "mount": "/models"}
                ],
                "rdma_status": "mlx5_0: Port 1 ACTIVE (RoCEv2 400Gb/s -> remote-mem-01)",
            }
            sw_specs = {
                "os_kernel": "Ubuntu 24.04.1 LTS (Linux 6.8.0-49-generic)",
                "gpu_driver": "560.35.03",
                "cuda_version": "CUDA 12.6",
                "k8s_namespace": server.get("k8s_namespace", "dynamo-llm"),
                "dgd_summary": {
                    "name": "vllm-disagg-tp4",
                    "state": "Ready (1/1 Frontend, 1/1 VllmDecodeWorker)",
                    "tensor_parallel": f"TP{tp_size}",
                    "kv_connector": "LMCacheConnectorV1 -> MooncakeStore",
                    "active_offload_tier": offload_tier,
                },
                "k8s_pods": [
                    {"name": "vllm-disagg-tp4-frontend-6d8f9", "phase": "Running", "node": "gpu-srv-h100-01"},
                    {"name": "vllm-disagg-tp4-decode-tp4-9b2k1", "phase": "Running", "node": "gpu-srv-h100-01"},
                ],
                "packages": {
                    "ai-dynamo": "0.6.1",
                    "vllm": "0.8.4",
                    "lmcache": "0.3.2",
                    "mooncake-transfer-engine": "0.3.4",
                },
            }
            return hw_specs, sw_specs, active_configs

        # REMOTE Memory Server
        mc_text = active_configs.get("remote-mem-01/configs/mooncake_store.yaml", DEFAULT_MOONCAKE_YAML)
        try:
            parsed_mc = yaml.safe_load(mc_text) or {}
            active_dest = parsed_mc.get("storage_tier", {}).get("active_destination", "CXL_MEMORY")
        except Exception:
            active_dest = "CXL_MEMORY"

        cxl_used = 318.4 if active_dest == "CXL_MEMORY" else 12.0
        dram_used = 214.0 if active_dest == "DRAM" else 48.5
        ssd_used_note = "312GB KV Cache Active" if active_dest == "NVME_SSD" else "Idle (8.4GB Metadata)"

        hw_specs = {
            "hostname": "remote-mem-cxl-01.lab.internal",
            "cpu_model": "2x Intel Xeon Rapids 6530 (64 Cores / CXL 2.0 Type-3 Enabled)",
            "cpu_cores": "64",
            "sockets": "2",
            "gpus": [],
            "gpu_topology_matrix": "GPU 미장착 (원격 KV 캐시 메모리 풀 전용 서버)",
            "numa_nodes": [
                {"node_id": 0, "tier": "DRAM", "total_gb": 256.0, "used_gb": dram_used, "cpus": "0-31", "desc": "Host DDR5-5600 Node 0"},
                {"node_id": 1, "tier": "DRAM", "total_gb": 256.0, "used_gb": 41.2, "cpus": "32-63", "desc": "Host DDR5-5600 Node 1"},
                {"node_id": 2, "tier": "CXL_MEMORY", "total_gb": 512.0, "used_gb": cxl_used, "cpus": "CPU-less (CXLMem)", "desc": "CXL 2.0 Type-3 AIC Expander (/dev/dax2.0 · PCIe Gen5 x16)"},
            ],
            "cxl_raw": '[{"memdev":"mem0","ram_size":"512.00 GiB","numa_node":2,"state":"enabled","media":"CXL Type-3 DDR5"}]',
            "storage": [
                {"name": "nvme0n1", "model": "Samsung PM1743 PCIe Gen5 NVMe", "size": "3.84TB", "mount": f"/mnt/nvme_kv_pool ({ssd_used_note})"},
                {"name": "nvme1n1", "model": "Samsung PM1743 PCIe Gen5 NVMe", "size": "3.84TB", "mount": "/mnt/nvme_kv_replica"},
            ],
            "rdma_status": "mlx5_0 & mlx5_1: Port 1 ACTIVE (RoCEv2 Dual-Port 400Gb/s · GID 3)",
        }
        sw_specs = {
            "os_kernel": "Ubuntu 24.04.1 LTS (Linux 6.11.0-cxl-numa-tuned)",
            "gpu_driver": "N/A",
            "cuda_version": "N/A",
            "k8s_namespace": "host-systemd / docker",
            "dgd_summary": {
                "name": "mooncake-store-master",
                "state": "Active (Port 50051 RDMA + 8090 Metadata)",
                "tensor_parallel": "N/A",
                "kv_connector": "Mooncake Transfer Engine (Zero-Copy RDMA)",
                "active_offload_tier": active_dest,
            },
            "k8s_pods": [
                {"name": "mooncake-master-daemon (docker)", "phase": "Running", "node": "remote-mem-cxl-01"},
                {"name": "cxl-tier-exporter (systemd)", "phase": "Running", "node": "remote-mem-cxl-01"},
            ],
            "packages": {
                "mooncake-store": "0.3.4",
                "cxl-cli / ndctl": "79.0",
                "rdma-core": "52.0",
            },
        }
        return hw_specs, sw_specs, active_configs

    async def collect_snapshot(
        self,
        server_id: str,
        trigger_type: str,
        triggered_by: str,
        force: bool = False,
        campaign_id: str | None = None,
    ) -> dict[str, Any]:
        """Collect server HW/SW/Config state via Agentless SSH (or Simulation).

        Zero-Noise Benchmark Guard: If `lock_status == 'BENCHMARK_RUNNING'` and `force`
        is False, skips SSH invocation completely so memory latency/bandwidth tests
        experience zero SSH process jitter, returning the latest cached snapshot.
        """
        server = self.db.get_server(server_id, include_secret=True)
        if not server:
            raise ValueError(f"등록되지 않은 서버 ID입니다: {server_id}")

        if server["lock_status"] == "BENCHMARK_RUNNING" and not force and trigger_type == "ON_DEMAND_WEB":
            cached = self.db.get_latest_snapshot(server_id)
            if cached:
                return {
                    "snapshot": cached,
                    "benchmark_guard_deferred": True,
                    "guard_message": (
                        f"현재 '{server['name']}'에서 벤치마크({server.get('lock_reason') or '측정 중'})가 진행 중입니다. "
                        "CXL/DRAM/SSD 레이턴시 간섭을 막기 위해 실시간 SSH 호출을 생략하고 직전 스냅샷을 반환했습니다."
                    ),
                }

        if server["connection_mode"] == "simulation":
            await asyncio.sleep(0.08)
            hw_specs, sw_specs, active_configs = self._build_simulated_state(server)
        else:
            env_arg = json.dumps(
                {
                    "k8s_namespace": server.get("k8s_namespace", "default"),
                    "watched_configs": server.get("watched_configs", []),
                }
            )
            cmd = f"AMR_INSPECT_ARGS={shlex.quote(env_arg)} python3 -"
            stdout_s, stderr_s, exit_c = await self._run_remote(
                server, cmd, input_text=REMOTE_INSPECT_PYTHON, timeout=25
            )
            if exit_c != 0 or not stdout_s.strip():
                raise RuntimeError(
                    f"SSH 수집 실패 ({server['host']}): {stderr_s.strip() or '출력 없음'}"
                )
            parsed = json.loads(stdout_s)
            hw_specs = parsed["hw_specs"]
            sw_specs = parsed["sw_specs"]
            active_configs = parsed["active_configs"]

            # Sync newly discovered remote files into Git if changed
            if active_configs:
                commit = self.git_store.commit_files(
                    updates=active_configs,
                    author=triggered_by,
                    message=f"원격 서버({server_id}) 활성 설정 동기화 ({trigger_type})",
                    server_id=server_id,
                    campaign_id=campaign_id,
                )
                self.db.record_config_commit(commit)

        snap = self.db.save_snapshot(
            server_id=server_id,
            trigger_type=trigger_type,
            triggered_by=triggered_by,
            hw_specs=hw_specs,
            sw_specs=sw_specs,
            active_configs=active_configs,
            campaign_id=campaign_id,
            git_hash=self.git_store.get_head_hash(),
        )
        await self._emit({"type": "snapshot_updated", "server_id": server_id, "snapshot": snap})
        return {"snapshot": snap, "benchmark_guard_deferred": False, "guard_message": None}

    async def execute_command(
        self,
        server_id: str,
        actor_id: str,
        command: str,
        purpose: str = "원격 점검/세팅",
        room_id: str | None = None,
        campaign_id: str | None = None,
        timeout: int = 120,
    ) -> dict[str, Any]:
        """Execute a command through the central SSH Gateway with full audit logging."""
        server = self.db.get_server(server_id, include_secret=True)
        if not server:
            raise ValueError(f"서버를 찾을 수 없습니다: {server_id}")

        if (
            server["lock_status"] == "BENCHMARK_RUNNING"
            and server.get("lock_holder") not in (None, actor_id)
        ):
            raise RuntimeError(
                f"서버 '{server_id}'는 현재 '{server['lock_holder']}'가 벤치마크 실행 중이므로 명령 실행이 잠겨 있습니다."
            )

        t0 = time.perf_counter()
        if server["connection_mode"] == "simulation":
            await asyncio.sleep(0.15)
            stdout, stderr, exit_code = self._simulate_ssh_command(server, command)
        else:
            stdout, stderr, exit_code = await self._run_remote(server, command, timeout=timeout)

        duration_ms = int((time.perf_counter() - t0) * 1000)
        audit = self.db.add_ssh_audit_log(
            server_id=server_id,
            actor_id=actor_id,
            command=command,
            purpose=purpose,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
            room_id=room_id,
            campaign_id=campaign_id,
        )
        await self._emit({"type": "ssh_audit", "log": audit})
        return audit

    def _simulate_ssh_command(self, server: dict[str, Any], command: str) -> tuple[str, str, int]:
        cmd_lower = command.lower()
        if "nvidia-smi" in cmd_lower:
            return (
                "GPU 0: NVIDIA H100 80GB HBM3 (UUID: GPU-8f1a...) [TP4 Ready, NVLink 18x Active]\n"
                "GPU 1: NVIDIA H100 80GB HBM3 (UUID: GPU-8f1b...) [TP4 Ready, NVLink 18x Active]\n"
                "GPU 2: NVIDIA H100 80GB HBM3 (UUID: GPU-8f1c...) [TP4 Ready, NVLink 18x Active]\n"
                "GPU 3: NVIDIA H100 80GB HBM3 (UUID: GPU-8f1d...) [TP4 Ready, NVLink 18x Active]",
                "",
                0,
            )
        if "cxl" in cmd_lower or "numactl" in cmd_lower or "dax" in cmd_lower:
            return (
                "available: 3 nodes (0-2)\n"
                "node 0 size: 262144 MB (Host DDR5 DRAM)\n"
                "node 1 size: 262144 MB (Host DDR5 DRAM)\n"
                "node 2 size: 524288 MB (CXL Type-3 Memory Expander /dev/dax2.0, CPU-less NUMA)\n"
                "RDMA mlx5_0 <-> GPU Node: LinkUp 400Gb/s RoCEv2",
                "",
                0,
            )
        if "kubectl" in cmd_lower:
            return (
                "NAME               FRAMEWORK   FRONTEND   WORKER(TP4)   STATUS   AGE\n"
                "vllm-disagg-tp4    vllm        1/1        1/1           Ready    14m",
                "",
                0,
            )
        if "mooncake" in cmd_lower:
            return (
                "[MooncakeStore] Master listening on 0.0.0.0:50051 | RDMA transport: mlx5_0,mlx5_1 | Pool healthy",
                "",
                0,
            )
        return (f"[Simulated SSH on {server['host']}] Executed: {command}\nStatus: OK", "", 0)

    async def update_server_config(
        self,
        server_id: str,
        actor_id: str,
        repo_path: str,
        content: str,
        commit_message: str,
        apply_command: str | None = None,
        room_id: str | None = None,
        campaign_id: str | None = None,
    ) -> dict[str, Any]:
        """Update remote YAML / K8s DGD manifest, commit to Embedded Git, and snapshot state."""
        async with self._get_asyncio_lock(server_id):
            server = self.db.get_server(server_id, include_secret=True)
            if not server:
                raise ValueError(f"서버를 찾을 수 없습니다: {server_id}")
            if (
                server["lock_status"] == "BENCHMARK_RUNNING"
                and server.get("lock_holder") not in (None, actor_id)
            ):
                raise RuntimeError("벤치마크 측정 중인 서버의 설정은 변경할 수 없습니다.")

            clean_repo_path = repo_path.lstrip("/")
            if not clean_repo_path.startswith(f"{server_id}/"):
                clean_repo_path = f"{server_id}/{clean_repo_path}"

            # Apply on remote SSH host if in real SSH mode
            matched_watch = next(
                (w for w in server.get("watched_configs", []) if w.get("repo_path") == clean_repo_path),
                None,
            )
            if server["connection_mode"] != "simulation":
                if matched_watch and matched_watch.get("kind") == "k8s_dgd":
                    ns = server.get("k8s_namespace", "default")
                    _, stderr_s, exit_c = await self._run_remote(
                        server, f"kubectl apply -n {shlex.quote(ns)} -f -", input_text=content
                    )
                    if exit_c != 0:
                        raise RuntimeError(f"kubectl apply 실패: {stderr_s}")
                elif matched_watch and matched_watch.get("remote_path"):
                    rpath = matched_watch["remote_path"]
                    parent = os.path.dirname(rpath)
                    _, stderr_s, exit_c = await self._run_remote(
                        server,
                        f"mkdir -p {shlex.quote(parent)} && cat > {shlex.quote(rpath)}",
                        input_text=content,
                    )
                    if exit_c != 0:
                        raise RuntimeError(f"원격 파일 쓰기 실패 ({rpath}): {stderr_s}")
                if apply_command:
                    await self._run_remote(server, apply_command)
            else:
                self._sim_configs[clean_repo_path] = content
                cmd_desc = (
                    f"kubectl apply -n {server['k8s_namespace']} -f {clean_repo_path}"
                    if "dgd" in clean_repo_path
                    else f"write {clean_repo_path}" + (f" && {apply_command}" if apply_command else "")
                )
                audit = self.db.add_ssh_audit_log(
                    server_id=server_id,
                    actor_id=actor_id,
                    command=cmd_desc,
                    purpose=f"설정 반영: {commit_message}",
                    exit_code=0,
                    stdout=f"Applied {clean_repo_path} successfully on {server['host']}.",
                    stderr="",
                    duration_ms=95,
                    room_id=room_id,
                    campaign_id=campaign_id,
                )
                await self._emit({"type": "ssh_audit", "log": audit})

            commit = self.git_store.commit_files(
                updates={clean_repo_path: content},
                author=actor_id,
                message=commit_message,
                server_id=server_id,
                campaign_id=campaign_id,
            )
            self.db.record_config_commit(commit)
            snap_res = await self.collect_snapshot(
                server_id=server_id,
                trigger_type="CONFIG_CHANGE",
                triggered_by=actor_id,
                force=True,
                campaign_id=campaign_id,
            )
            await self._emit({"type": "config_committed", "commit": commit})
            return {"commit": commit, "snapshot": snap_res["snapshot"]}

    async def rollback_config(
        self,
        target_hash: str,
        actor_id: str,
        server_id: str | None = None,
        room_id: str | None = None,
    ) -> dict[str, Any]:
        """Restore server configs to a historical commit and re-apply over SSH."""
        rb = self.git_store.rollback_to_commit(
            target_hash=target_hash, author=actor_id, server_prefix=server_id
        )
        commit = rb["commit"]
        self.db.record_config_commit(commit)
        restored_files: dict[str, str] = rb["restored_files"]

        affected_servers = {p.split("/", 1)[0] for p in restored_files if "/" in p}
        snapshots = []
        for sid in sorted(affected_servers):
            srv = self.db.get_server(sid, include_secret=True)
            if not srv:
                continue
            if srv["connection_mode"] != "simulation":
                for w in srv.get("watched_configs", []):
                    rpath = w.get("repo_path")
                    if rpath not in restored_files:
                        continue
                    text = restored_files[rpath]
                    if w.get("kind") == "k8s_dgd":
                        ns = srv.get("k8s_namespace", "default")
                        await self._run_remote(
                            srv, f"kubectl apply -n {shlex.quote(ns)} -f -", input_text=text
                        )
                    elif w.get("remote_path"):
                        await self._run_remote(
                            srv, f"cat > {shlex.quote(w['remote_path'])}", input_text=text
                        )
            else:
                for k, v in restored_files.items():
                    if k.startswith(f"{sid}/"):
                        self._sim_configs[k] = v

            audit = self.db.add_ssh_audit_log(
                server_id=sid,
                actor_id=actor_id,
                command=f"rollback_to_commit {target_hash}",
                purpose=f"설정 원클릭 롤백 ({target_hash})",
                exit_code=0,
                stdout=f"Restored {len([k for k in restored_files if k.startswith(sid + '/')])} config file(s) to {target_hash}.",
                stderr="",
                duration_ms=110,
                room_id=room_id,
            )
            await self._emit({"type": "ssh_audit", "log": audit})
            snap_res = await self.collect_snapshot(
                server_id=sid,
                trigger_type="ROLLBACK",
                triggered_by=actor_id,
                force=True,
            )
            snapshots.append(snap_res["snapshot"])

        await self._emit({"type": "config_committed", "commit": commit})
        return {"commit": commit, "snapshots": snapshots}

    async def run_benchmark_step(
        self,
        campaign_id: str,
        server_id: str,
        actor_id: str,
        variant_label: str,
        offload_target: str,
        command: str,
        metrics_override: dict[str, Any] | None = None,
        room_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute a benchmark variant with Pre/Post snapshots and Zero-Noise Server Lock."""
        all_server_ids = [s["id"] for s in self.db.list_servers()]
        for sid in all_server_ids:
            self.db.set_server_lock(
                sid,
                "BENCHMARK_RUNNING",
                holder=actor_id,
                reason=f"{variant_label} ({offload_target})",
            )
        await self._emit({"type": "servers_updated", "servers": self.db.list_servers()})

        try:
            pre_snap = (
                await self.collect_snapshot(
                    server_id=server_id,
                    trigger_type="PRE_BENCHMARK",
                    triggered_by=actor_id,
                    force=True,
                    campaign_id=campaign_id,
                )
            )["snapshot"]

            git_hash = self.git_store.get_head_hash()
            run_rec = self.db.create_benchmark_run(
                campaign_id=campaign_id,
                server_id=server_id,
                variant_label=variant_label,
                offload_target=offload_target,
                command=command,
                git_hash=git_hash,
                pre_snapshot_id=pre_snap["id"],
                executed_by=actor_id,
            )
            await self._emit({"type": "benchmark_started", "run": run_rec})

            server = self.db.get_server(server_id, include_secret=True)
            assert server is not None

            t0 = time.perf_counter()
            if server["connection_mode"] == "simulation":
                await asyncio.sleep(0.35)
                raw_output, sim_metrics = self._simulate_benchmark(variant_label, offload_target, command)
                exit_code = 0
                stderr = ""
            else:
                raw_output, stderr, exit_code = await self._run_remote(
                    server, command, timeout=3600
                )
                sim_metrics = self._try_extract_metrics_from_stdout(raw_output)

            duration_ms = int((time.perf_counter() - t0) * 1000)
            log_file = settings.benchmark_logs_dir / f"{run_rec['id']}.log"
            log_file.write_text(raw_output + ("\n" + stderr if stderr else ""), encoding="utf-8")

            audit = self.db.add_ssh_audit_log(
                server_id=server_id,
                actor_id=actor_id,
                command=command,
                purpose=f"벤치마크 실행: {variant_label} [{offload_target}]",
                exit_code=exit_code,
                stdout=raw_output[:4000],
                stderr=stderr[:1000],
                duration_ms=duration_ms,
                room_id=room_id,
                campaign_id=campaign_id,
            )
            await self._emit({"type": "ssh_audit", "log": audit})

            post_snap = (
                await self.collect_snapshot(
                    server_id=server_id,
                    trigger_type="POST_BENCHMARK",
                    triggered_by=actor_id,
                    force=True,
                    campaign_id=campaign_id,
                )
            )["snapshot"]

            final_metrics = metrics_override if metrics_override else sim_metrics
            finished = self.db.finish_benchmark_run(
                run_id=run_rec["id"],
                status="SUCCESS" if exit_code == 0 else "FAILED",
                post_snapshot_id=post_snap["id"],
                metrics=final_metrics,
                raw_log_path=str(log_file),
            )
            await self._emit({"type": "benchmark_finished", "run": finished})
            return {"run": finished, "raw_output": raw_output}
        finally:
            for sid in all_server_ids:
                self.db.set_server_lock(sid, "FREE", holder=None, reason=None)
            await self._emit({"type": "servers_updated", "servers": self.db.list_servers()})

    @staticmethod
    def _simulate_benchmark(
        variant_label: str, offload_target: str, command: str
    ) -> tuple[str, dict[str, Any]]:
        target_upper = offload_target.upper()
        if "DRAM" in target_upper:
            metrics = {
                "ttft_p50_ms": 38.4,
                "ttft_p99_ms": 64.2,
                "tpot_p50_ms": 11.2,
                "tpot_p99_ms": 16.5,
                "throughput_tok_s": 4820.0,
                "kv_cache_hit_rate_pct": 89.4,
                "offload_bw_gbps": 37.8,
                "effective_pool_capacity_gb": 192,
            }
        elif "CXL" in target_upper:
            metrics = {
                "ttft_p50_ms": 43.1,
                "ttft_p99_ms": 72.8,
                "tpot_p50_ms": 11.8,
                "tpot_p99_ms": 17.9,
                "throughput_tok_s": 4615.0,
                "kv_cache_hit_rate_pct": 94.8,
                "offload_bw_gbps": 33.6,
                "effective_pool_capacity_gb": 512,
            }
        else:  # NVMe SSD
            metrics = {
                "ttft_p50_ms": 118.6,
                "ttft_p99_ms": 246.5,
                "tpot_p50_ms": 15.9,
                "tpot_p99_ms": 29.4,
                "throughput_tok_s": 3190.0,
                "kv_cache_hit_rate_pct": 96.1,
                "offload_bw_gbps": 11.4,
                "effective_pool_capacity_gb": 3840,
            }

        log_text = (
            f"=== LLM Memory Offload Benchmark ({variant_label}) ===\n"
            f"Command: {command}\n"
            f"Model: Llama-3.1-70B-Instruct | Tensor Parallel: TP4 | Backend: LMCache + MooncakeStore\n"
            f"Offload Destination: {offload_target} | Workload: Multi-turn Shared Prefix (16K ctx, concurrency=32)\n"
            f"------------------------------------------------------------\n"
            f"TTFT p50:             {metrics['ttft_p50_ms']} ms\n"
            f"TTFT p99:             {metrics['ttft_p99_ms']} ms\n"
            f"TPOT / ITL p50:       {metrics['tpot_p50_ms']} ms\n"
            f"TPOT / ITL p99:       {metrics['tpot_p99_ms']} ms\n"
            f"Output Throughput:    {metrics['throughput_tok_s']} tok/s\n"
            f"KV Cache Hit Rate:    {metrics['kv_cache_hit_rate_pct']} %\n"
            f"Mooncake Transfer BW: {metrics['offload_bw_gbps']} GB/s\n"
            f"============================================================\n"
        )
        return log_text, metrics

    @staticmethod
    def _try_extract_metrics_from_stdout(stdout: str) -> dict[str, Any]:
        # Try parsing JSON block if benchmark emitted JSON
        for line in reversed(stdout.splitlines()):
            s = line.strip()
            if s.startswith("{") and s.endswith("}"):
                try:
                    data = json.loads(s)
                    if isinstance(data, dict):
                        return data
                except Exception:
                    pass
        return {}
