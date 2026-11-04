import {
  Cpu,
  HardDrive,
  Lock,
  Network,
  Plus,
  RefreshCw,
  Server,
  Settings2,
  ShieldAlert,
  Unlock,
  X,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../api'
import type { EnvSnapshot, ServerInfo } from '../types'

interface Props {
  userProfile: string
  servers: ServerInfo[]
  snapshots: Record<string, EnvSnapshot | null>
  onSnapshotsUpdated: (next: Record<string, EnvSnapshot | null>) => void
  onRefreshBootstrap: () => void
}

export function EnvironmentView({
  userProfile,
  servers,
  snapshots,
  onSnapshotsUpdated,
  onRefreshBootstrap,
}: Props) {
  const [refreshing, setRefreshing] = useState(false)
  const [guardMessages, setGuardMessages] = useState<string[]>([])
  const [editingServer, setEditingServer] = useState<Partial<ServerInfo> | null>(
    null,
  )
  const [sshPassword, setSshPassword] = useState('')

  const triggerOnDemandRefresh = async (force = false) => {
    setRefreshing(true)
    try {
      const res = await api.refreshAllServers(userProfile, force)
      const nextMap = { ...snapshots }
      const guards: string[] = []
      for (const [sid, item] of Object.entries(res.results)) {
        nextMap[sid] = item.snapshot
        if (item.benchmark_guard_deferred && item.guard_message) {
          guards.push(item.guard_message)
        }
      }
      setGuardMessages(guards)
      onSnapshotsUpdated(nextMap)
    } catch (err) {
      console.error('On-demand SSH refresh error:', err)
    } finally {
      setRefreshing(false)
    }
  }

  // Trigger on-demand SSH read when user enters the Environment page (as designed in Q6!)
  useEffect(() => {
    void triggerOnDemandRefresh(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleSaveServer = async () => {
    if (!editingServer?.name || !editingServer?.host) return
    await api.upsertServer(userProfile, {
      ...editingServer,
      ssh_password: sshPassword || undefined,
    })
    setEditingServer(null)
    setSshPassword('')
    onRefreshBootstrap()
  }

  return (
    <div className="mx-auto max-w-7xl space-y-6 p-6">
      {/* Header Control Bar */}
      <div className="flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-slate-800 bg-slate-900/70 p-5">
        <div>
          <h1 className="text-lg font-bold text-white">
            테스트 인프라 환경 & 하드웨어·소프트웨어 스펙 (SSH 무설치 수집)
          </h1>
          <p className="mt-1 text-xs text-slate-400">
            메모리 벤치마크(CXL·DRAM·SSD) 간섭을 원천 차단하기 위해 상시 백그라운드 폴링을 하지 않고,{' '}
            <strong className="text-slate-200">
              페이지 진입·새로고침 요청 시점 및 설정 변경·테스트 전후
            </strong>
            에만 SSH로 상태를 읽어와 보관합니다.
          </p>
        </div>

        <div className="flex items-center gap-2.5">
          <button
            onClick={() =>
              setEditingServer({
                name: '추가 테스트 노드',
                host: '10.20.30.105',
                ssh_port: 22,
                ssh_user: 'ubuntu',
                ssh_key_path: '~/.ssh/id_ed25519',
                server_role: 'GPU',
                connection_mode: 'ssh',
                k8s_namespace: 'dynamo-llm',
                watched_configs: [],
              })
            }
            className="flex items-center gap-1.5 rounded-xl border border-slate-700 bg-slate-800 px-3.5 py-2 text-xs font-semibold text-slate-200 hover:bg-slate-700"
          >
            <Plus className="h-4 w-4" />
            서버 추가
          </button>
          <button
            onClick={() => triggerOnDemandRefresh(false)}
            disabled={refreshing}
            className="flex items-center gap-1.5 rounded-xl bg-indigo-600 px-4 py-2 text-xs font-bold text-white shadow-sm transition hover:bg-indigo-500 disabled:opacity-50"
          >
            <RefreshCw
              className={`h-4 w-4 ${refreshing ? 'animate-spin' : ''}`}
            />
            지금 SSH로 새로고침
          </button>
        </div>
      </div>

      {/* Zero-Noise Benchmark Guard Banner */}
      {guardMessages.length > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-amber-500/40 bg-amber-500/10 p-4 text-xs text-amber-200">
          <div className="flex items-start gap-2.5">
            <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-amber-400" />
            <div className="space-y-1">
              {guardMessages.map((gm, i) => (
                <div key={i}>{gm}</div>
              ))}
            </div>
          </div>
          <button
            onClick={() => triggerOnDemandRefresh(true)}
            className="rounded-lg border border-amber-400/50 bg-amber-500/20 px-3 py-1.5 font-semibold text-amber-100 hover:bg-amber-500/30"
          >
            강제 실시간 SSH 조회
          </button>
        </div>
      )}

      {/* Server Grid */}
      <div className="grid gap-6 lg:grid-cols-2">
        {servers.map((srv) => {
          const snap = snapshots[srv.id]
          const hw = snap?.hw_specs ?? {}
          const sw = snap?.sw_specs ?? {}
          const isLocked = srv.lock_status !== 'FREE'

          return (
            <div
              key={srv.id}
              className="flex flex-col overflow-hidden rounded-2xl border border-slate-800 bg-slate-900/80 shadow-lg"
            >
              {/* Server Card Header */}
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 bg-slate-950/60 px-5 py-4">
                <div className="flex items-center gap-3">
                  <div
                    className={`rounded-xl p-2.5 ${
                      srv.server_role === 'GPU'
                        ? 'bg-indigo-500/20 text-indigo-300'
                        : 'bg-emerald-500/20 text-emerald-300'
                    }`}
                  >
                    <Server className="h-5 w-5" />
                  </div>
                  <div>
                    <div className="flex items-center gap-2">
                      <h2 className="text-sm font-bold text-white">{srv.name}</h2>
                      <span
                        className={`rounded-md px-2 py-0.5 text-[10px] font-bold uppercase ${
                          srv.connection_mode !== 'simulation'
                            ? 'bg-emerald-500/20 text-emerald-300'
                            : 'bg-amber-500/20 text-amber-300'
                        }`}
                      >
                        {srv.connection_mode === 'ssh'
                          ? 'LIVE SSH (Pool)'
                          : srv.connection_mode === 'ssh_cli'
                            ? 'LIVE SSH (CLI)'
                            : 'SIMULATION'}
                      </span>
                    </div>
                    <div className="mt-0.5 text-xs text-slate-400">
                      <code>
                        {srv.ssh_user}@{srv.host}:{srv.ssh_port}
                      </code>
                      {snap && (
                        <span className="ml-2 text-[11px] text-slate-500">
                          · 수집: {new Date(snap.created_at).toLocaleTimeString('ko-KR')} (
                          {snap.trigger_type})
                        </span>
                      )}
                    </div>
                  </div>
                </div>

                <div className="flex items-center gap-2">
                  <span
                    className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-semibold ${
                      isLocked
                        ? 'bg-rose-500/20 text-rose-300'
                        : 'bg-emerald-500/15 text-emerald-300'
                    }`}
                  >
                    {isLocked ? (
                      <Lock className="h-3 w-3" />
                    ) : (
                      <Unlock className="h-3 w-3" />
                    )}
                    {srv.lock_status}
                  </span>
                  <button
                    onClick={() => setEditingServer(srv)}
                    className="rounded-lg border border-slate-700 bg-slate-800 p-1.5 text-slate-300 hover:bg-slate-700 hover:text-white"
                    title="SSH 접속 설정 편집"
                  >
                    <Settings2 className="h-4 w-4" />
                  </button>
                </div>
              </div>

              <div className="space-y-5 p-5 text-xs">
                {/* CPU & Host Summary */}
                <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-3.5">
                  <div className="mb-1 flex items-center gap-1.5 font-semibold text-slate-300">
                    <Cpu className="h-3.5 w-3.5 text-indigo-400" />
                    호스트 & 프로세서
                  </div>
                  <div className="text-slate-200">{hw.cpu_model ?? '-'}</div>
                  <div className="mt-1 text-[11px] text-slate-400">
                    Hostname: <code>{hw.hostname ?? '-'}</code> · Kernel:{' '}
                    <code>{sw.os_kernel ?? '-'}</code>
                  </div>
                </div>

                {/* GPUs + TP4 Topology (if GPU server) */}
                {hw.gpus && hw.gpus.length > 0 && (
                  <div>
                    <div className="mb-2 font-semibold text-slate-300">
                      GPU 가속기 구성 ({hw.gpus.length}기 · Driver {sw.gpu_driver} ·{' '}
                      {sw.cuda_version})
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      {hw.gpus.map((g) => {
                        const pct = Math.round(
                          (g.memory_used_mb / Math.max(1, g.memory_total_mb)) * 100,
                        )
                        return (
                          <div
                            key={g.index}
                            className="rounded-xl border border-slate-800 bg-slate-950 p-2.5"
                          >
                            <div className="flex items-center justify-between font-semibold text-indigo-200">
                              <span>
                                GPU {g.index} · {g.name.replace('NVIDIA ', '')}
                              </span>
                              <span className="text-[10px] text-slate-400">
                                {g.temp_c}°C · {g.pstate}
                              </span>
                            </div>
                            <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
                              <div
                                className="h-full bg-indigo-500"
                                style={{ width: `${pct}%` }}
                              />
                            </div>
                            <div className="mt-1 flex justify-between text-[10px] text-slate-400">
                              <span>
                                VRAM {(g.memory_used_mb / 1024).toFixed(1)} /{' '}
                                {(g.memory_total_mb / 1024).toFixed(0)} GB
                              </span>
                              <span>Util {g.utilization_pct}%</span>
                            </div>
                          </div>
                        )
                      })}
                    </div>
                    {hw.gpu_topology_matrix && (
                      <pre className="mt-2 overflow-x-auto rounded-xl border border-slate-800 bg-slate-950 p-3 text-[11px] leading-relaxed text-emerald-300">
                        {hw.gpu_topology_matrix}
                      </pre>
                    )}
                  </div>
                )}

                {/* NUMA Memory Map (Host DRAM vs CXL Memory Tier!) */}
                {hw.numa_nodes && hw.numa_nodes.length > 0 && (
                  <div>
                    <div className="mb-2 font-semibold text-slate-300">
                      NUMA 메모리 계층 토폴로지 (Host DRAM & CXL Memory Pool)
                    </div>
                    <div className="space-y-2">
                      {hw.numa_nodes.map((n) => {
                        const isCxl = n.tier.includes('CXL')
                        const used = n.used_gb ?? 0
                        const pct = Math.min(
                          100,
                          Math.round((used / Math.max(1, n.total_gb)) * 100),
                        )
                        return (
                          <div
                            key={n.node_id}
                            className={`rounded-xl border p-3 ${
                              isCxl
                                ? 'border-emerald-500/40 bg-emerald-950/20'
                                : 'border-slate-800 bg-slate-950'
                            }`}
                          >
                            <div className="flex items-center justify-between">
                              <div className="flex items-center gap-2">
                                <span className="font-bold text-white">
                                  NUMA Node {n.node_id}
                                </span>
                                <span
                                  className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${
                                    isCxl
                                      ? 'bg-emerald-500/25 text-emerald-300'
                                      : 'bg-sky-500/20 text-sky-300'
                                  }`}
                                >
                                  {n.tier}
                                </span>
                                {n.cpus && (
                                  <span className="text-[11px] text-slate-400">
                                    CPUs: {n.cpus}
                                  </span>
                                )}
                              </div>
                              <span className="font-mono text-slate-200">
                                {used ? `${used.toFixed(1)} / ` : ''}
                                {n.total_gb} GB
                              </span>
                            </div>
                            {n.desc && (
                              <div className="mt-0.5 text-[11px] text-slate-400">
                                {n.desc}
                              </div>
                            )}
                            {used > 0 && (
                              <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
                                <div
                                  className={`h-full ${
                                    isCxl ? 'bg-emerald-400' : 'bg-sky-400'
                                  }`}
                                  style={{ width: `${pct}%` }}
                                />
                              </div>
                            )}
                          </div>
                        )
                      })}
                    </div>
                  </div>
                )}

                {/* Storage & RDMA Network */}
                <div className="grid gap-3 sm:grid-cols-2">
                  <div className="rounded-xl border border-slate-800 bg-slate-950 p-3">
                    <div className="mb-1.5 flex items-center gap-1.5 font-semibold text-slate-300">
                      <HardDrive className="h-3.5 w-3.5 text-amber-400" />
                      NVMe 스토리지
                    </div>
                    <div className="space-y-1 text-[11px] text-slate-300">
                      {(hw.storage ?? []).map((st) => (
                        <div key={st.name}>
                          <span className="font-mono text-amber-300">{st.name}</span>:{' '}
                          {st.model} ({st.size}) {st.mount && `→ ${st.mount}`}
                        </div>
                      ))}
                    </div>
                  </div>
                  <div className="rounded-xl border border-slate-800 bg-slate-950 p-3">
                    <div className="mb-1.5 flex items-center gap-1.5 font-semibold text-slate-300">
                      <Network className="h-3.5 w-3.5 text-sky-400" />
                      RDMA 인터커넥트
                    </div>
                    <div className="font-mono text-[11px] text-sky-300">
                      {hw.rdma_status ?? 'N/A'}
                    </div>
                  </div>
                </div>

                {/* Software Stack & Active DGD / Mooncake Status */}
                <div className="rounded-xl border border-slate-800 bg-slate-950 p-3.5">
                  <div className="mb-2 flex items-center justify-between">
                    <span className="font-semibold text-slate-300">
                      소프트웨어 스택 & K8s Dynamo DGD / 데몬 상태
                    </span>
                    {sw.dgd_summary?.active_offload_tier && (
                      <span className="rounded-md bg-indigo-500/20 px-2 py-0.5 text-[10px] font-bold text-indigo-300">
                        Active Tier: {sw.dgd_summary.active_offload_tier}
                      </span>
                    )}
                  </div>
                  {sw.dgd_summary && (
                    <div className="mb-2.5 rounded-lg bg-slate-900 p-2.5 text-[11px] text-slate-300">
                      <div>
                        <strong className="text-white">{sw.dgd_summary.name}</strong>:{' '}
                        <span className="text-emerald-400">{sw.dgd_summary.state}</span>
                      </div>
                      <div className="mt-0.5 text-slate-400">
                        Connector: {sw.dgd_summary.kv_connector}
                      </div>
                    </div>
                  )}
                  <div className="flex flex-wrap gap-1.5">
                    {Object.entries(sw.packages ?? {}).map(([pkg, ver]) => (
                      <span
                        key={pkg}
                        className="rounded-lg border border-slate-800 bg-slate-900 px-2 py-1 font-mono text-[11px] text-slate-300"
                      >
                        {pkg}: <strong className="text-indigo-300">{ver}</strong>
                      </span>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          )
        })}
      </div>

      {/* Add / Edit SSH Server Modal */}
      {editingServer && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-xs">
          <div className="w-full max-w-lg rounded-2xl border border-slate-700 bg-slate-900 shadow-2xl">
            <div className="flex items-center justify-between border-b border-slate-800 px-5 py-4">
              <h3 className="text-sm font-bold text-white">
                테스트 서버 SSH 연결 설정 ({editingServer.id ?? '신규 서버'})
              </h3>
              <button
                onClick={() => setEditingServer(null)}
                className="text-slate-400 hover:text-white"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="space-y-3 p-5 text-xs">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="mb-1 block text-slate-400">서버 표시 이름</label>
                  <input
                    value={editingServer.name ?? ''}
                    onChange={(e) =>
                      setEditingServer({ ...editingServer, name: e.target.value })
                    }
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-slate-400">연결 모드</label>
                  <select
                    value={editingServer.connection_mode ?? 'ssh'}
                    onChange={(e) =>
                      setEditingServer({
                        ...editingServer,
                        connection_mode: e.target.value,
                      })
                    }
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white"
                  >
                    <option value="simulation">
                      Simulation (로컬 가상 랩 테스트)
                    </option>
                    <option value="ssh">
                      Live SSH — asyncssh 연결 풀 (권장)
                    </option>
                    <option value="ssh_cli">
                      OpenSSH CLI — 리눅스 기본 ssh 명령어 / ~/.ssh/config 활용
                    </option>
                  </select>
                </div>
              </div>

              <div className="grid grid-cols-3 gap-3">
                <div className="col-span-2">
                  <label className="mb-1 block text-slate-400">호스트 IP / 도메인</label>
                  <input
                    value={editingServer.host ?? ''}
                    onChange={(e) =>
                      setEditingServer({ ...editingServer, host: e.target.value })
                    }
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-slate-400">SSH 포트</label>
                  <input
                    type="number"
                    value={editingServer.ssh_port ?? 22}
                    onChange={(e) =>
                      setEditingServer({
                        ...editingServer,
                        ssh_port: Number(e.target.value),
                      })
                    }
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white"
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="mb-1 block text-slate-400">SSH 계정명</label>
                  <input
                    value={editingServer.ssh_user ?? 'ubuntu'}
                    onChange={(e) =>
                      setEditingServer({ ...editingServer, ssh_user: e.target.value })
                    }
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-slate-400">서버 역할</label>
                  <select
                    value={editingServer.server_role ?? 'GPU'}
                    onChange={(e) =>
                      setEditingServer({
                        ...editingServer,
                        server_role: e.target.value,
                      })
                    }
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white"
                  >
                    <option value="GPU">GPU 서버 (K8s DGD / 서빙)</option>
                    <option value="REMOTE">리모트 서버 (Mooncake / CXL / SSD)</option>
                  </select>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="mb-1 block text-slate-400">
                    SSH 개인키 경로 (웹 서버 기준)
                  </label>
                  <input
                    value={editingServer.ssh_key_path ?? ''}
                    onChange={(e) =>
                      setEditingServer({
                        ...editingServer,
                        ssh_key_path: e.target.value,
                      })
                    }
                    placeholder="~/.ssh/id_ed25519"
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-slate-400">
                    K8s 네임스페이스 (해당 시)
                  </label>
                  <input
                    value={editingServer.k8s_namespace ?? 'dynamo-llm'}
                    onChange={(e) =>
                      setEditingServer({
                        ...editingServer,
                        k8s_namespace: e.target.value,
                      })
                    }
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white"
                  />
                </div>
              </div>

              <div>
                <label className="mb-1 block text-slate-400">
                  SSH 패스워드 (키 인증 미사용 시 선택 입력)
                </label>
                <input
                  type="password"
                  value={sshPassword}
                  onChange={(e) => setSshPassword(e.target.value)}
                  placeholder="비워 두면 기존 설정/SSH 키 사용"
                  className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white"
                />
              </div>

              <div className="flex justify-end gap-2 pt-2">
                <button
                  onClick={() => setEditingServer(null)}
                  className="rounded-xl border border-slate-700 px-4 py-2 font-semibold text-slate-300 hover:bg-slate-800"
                >
                  취소
                </button>
                <button
                  onClick={handleSaveServer}
                  className="rounded-xl bg-indigo-600 px-4 py-2 font-bold text-white hover:bg-indigo-500"
                >
                  저장 및 연결 반영
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
