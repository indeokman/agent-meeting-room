import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Cpu,
  HardDrive,
  Loader2,
  Lock,
  Network,
  PlugZap,
  Plus,
  RefreshCw,
  Server,
  Settings2,
  ShieldAlert,
  Trash2,
  Unlock,
  X,
  XCircle,
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
  const [refreshErrors, setRefreshErrors] = useState<Record<string, string>>({})
  const [editingServer, setEditingServer] = useState<Partial<ServerInfo> | null>(
    null,
  )
  const [sshPassword, setSshPassword] = useState('')
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [testingConn, setTestingConn] = useState(false)
  const [savingServer, setSavingServer] = useState(false)
  const [connTestResult, setConnTestResult] = useState<{
    ok: boolean
    text: string
  } | null>(null)

  const triggerOnDemandRefresh = async (force = false) => {
    if (servers.length === 0) return
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
      setRefreshErrors(res.errors ?? {})
      onSnapshotsUpdated(nextMap)
    } catch (err) {
      console.error('On-demand SSH refresh error:', err)
    } finally {
      setRefreshing(false)
    }
  }

  useEffect(() => {
    if (servers.length > 0) {
      void triggerOnDemandRefresh(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [servers.length])

  const openNewServerModal = () => {
    setEditingServer({
      name: '',
      host: '',
      ssh_port: 22,
      ssh_user: 'root',
      server_role: 'GPU',
      k8s_namespace: 'dynamo-llm',
      watched_configs: [],
    })
    setSshPassword('')
    setShowAdvanced(false)
    setConnTestResult(null)
  }

  const openEditServerModal = (srv: ServerInfo) => {
    setEditingServer(srv)
    setSshPassword('')
    setShowAdvanced(false)
    setConnTestResult(null)
  }

  const handleTestConnection = async () => {
    if (!editingServer?.host?.trim()) {
      setConnTestResult({
        ok: false,
        text: '호스트 이름(IP 주소 또는 도메인)을 입력해 주세요.',
      })
      return
    }
    setTestingConn(true)
    setConnTestResult(null)
    try {
      const res = await api.testServerConnection(userProfile, {
        id: editingServer.id,
        host: editingServer.host.trim(),
        ssh_port: Number(editingServer.ssh_port ?? 22),
        ssh_user: (editingServer.ssh_user ?? 'root').trim(),
        ssh_password: sshPassword || undefined,
      })
      if (res.ok) {
        setConnTestResult({
          ok: true,
          text: `${res.banner ?? 'SSH 연결 성공'} (${res.latency_ms ?? 0}ms)`,
        })
      } else {
        setConnTestResult({
          ok: false,
          text: `연결 실패: ${res.error ?? '알 수 없는 오류'}`,
        })
      }
    } catch (err) {
      setConnTestResult({ ok: false, text: `연결 요청 오류: ${err}` })
    } finally {
      setTestingConn(false)
    }
  }

  const handleSaveServer = async () => {
    if (!editingServer?.host?.trim()) {
      setConnTestResult({
        ok: false,
        text: '호스트 이름(IP 주소 또는 도메인)을 입력해 주세요.',
      })
      return
    }
    const finalName =
      editingServer.name?.trim() ||
      `${editingServer.server_role === 'REMOTE' ? '리모트 서버' : 'GPU 서버'} (${editingServer.host.trim()})`

    setSavingServer(true)
    try {
      const res = await api.upsertServer(userProfile, {
        id: editingServer.id,
        name: finalName,
        host: editingServer.host.trim(),
        ssh_port: Number(editingServer.ssh_port ?? 22),
        ssh_user: (editingServer.ssh_user ?? 'root').trim(),
        ssh_password: sshPassword || undefined,
        server_role: editingServer.server_role ?? 'GPU',
        k8s_namespace: editingServer.k8s_namespace ?? 'dynamo-llm',
        watched_configs: editingServer.watched_configs ?? [],
      })
      if (res.collect_warning) {
        setRefreshErrors((prev) => ({
          ...prev,
          [res.server.id]: res.collect_warning!,
        }))
      } else {
        setRefreshErrors((prev) => {
          const next = { ...prev }
          delete next[res.server.id]
          return next
        })
      }
      setEditingServer(null)
      setSshPassword('')
      setConnTestResult(null)
      onRefreshBootstrap()
    } finally {
      setSavingServer(false)
    }
  }

  const handleDeleteServer = async () => {
    if (!editingServer?.id) return
    if (!confirm(`'${editingServer.name}' 서버 연결 정보를 삭제할까요?`)) return
    await api.deleteServer(userProfile, editingServer.id)
    setEditingServer(null)
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
            onClick={openNewServerModal}
            className="flex items-center gap-1.5 rounded-xl border border-slate-700 bg-slate-800 px-3.5 py-2 text-xs font-semibold text-slate-200 hover:bg-slate-700"
          >
            <Plus className="h-4 w-4" />
            서버 추가
          </button>
          <button
            onClick={() => triggerOnDemandRefresh(false)}
            disabled={refreshing || servers.length === 0}
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

      {/* Empty State when no servers registered yet */}
      {servers.length === 0 ? (
        <div className="rounded-2xl border border-slate-800 bg-slate-900/70 py-18 text-center">
          <Server className="mx-auto h-10 w-10 text-slate-600" />
          <h2 className="mt-3 text-sm font-bold text-white">
            등록된 테스트 대상 서버가 없습니다
          </h2>
          <p className="mt-1 text-xs text-slate-400">
            GPU 서빙 서버 및 리모트 메모리(Mooncake · CXL / DRAM / SSD) 서버의 호스트 IP, 포트, 계정, 비밀번호를 등록하세요.
          </p>
          <button
            onClick={openNewServerModal}
            className="mt-4 inline-flex items-center gap-1.5 rounded-xl bg-indigo-600 px-4 py-2 text-xs font-bold text-white hover:bg-indigo-500"
          >
            <Plus className="h-4 w-4" />
            새 SSH 서버 세션 등록
          </button>
        </div>
      ) : (
        /* Server Grid */
        <div className="grid gap-6 lg:grid-cols-2">
          {servers.map((srv) => {
            const snap = snapshots[srv.id]
            const hw = snap?.hw_specs ?? {}
            const sw = snap?.sw_specs ?? {}
            const isLocked = srv.lock_status !== 'FREE'
            const errText = refreshErrors[srv.id]

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
                          className={`rounded-md px-2 py-0.5 text-[10px] font-bold ${
                            srv.server_role === 'GPU'
                              ? 'bg-indigo-500/20 text-indigo-300'
                              : 'bg-emerald-500/20 text-emerald-300'
                          }`}
                        >
                          {srv.server_role === 'GPU' ? 'GPU 서버' : '리모트 서버'}
                        </span>
                      </div>
                      <div className="mt-0.5 text-xs text-slate-400">
                        <code>
                          ssh://{srv.ssh_user}@{srv.host}:{srv.ssh_port}
                        </code>
                        {snap && (
                          <span className="ml-2 text-[11px] text-slate-500">
                            · 수집: {new Date(snap.created_at).toLocaleTimeString('ko-KR')}
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
                      onClick={() => openEditServerModal(srv)}
                      className="rounded-lg border border-slate-700 bg-slate-800 p-1.5 text-slate-300 hover:bg-slate-700 hover:text-white"
                      title="SSH 세션 설정 편집"
                    >
                      <Settings2 className="h-4 w-4" />
                    </button>
                  </div>
                </div>

                {errText && (
                  <div className="border-b border-rose-500/30 bg-rose-950/30 px-5 py-2.5 text-xs text-rose-300">
                    SSH 수집 경고: {errText}
                  </div>
                )}

                <div className="space-y-5 p-5 text-xs">
                  {/* CPU & Host Summary */}
                  <div className="rounded-xl border border-slate-800 bg-slate-950/60 p-3.5">
                    <div className="mb-1 flex items-center gap-1.5 font-semibold text-slate-300">
                      <Cpu className="h-3.5 w-3.5 text-indigo-400" />
                      호스트 & 프로세서
                    </div>
                    <div className="text-slate-200">{hw.cpu_model ?? '아직 수집된 스냅샷이 없습니다'}</div>
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
                        {(hw.storage ?? []).length > 0 ? (
                          (hw.storage ?? []).map((st) => (
                            <div key={st.name}>
                              <span className="font-mono text-amber-300">{st.name}</span>:{' '}
                              {st.model} ({st.size}) {st.mount && `→ ${st.mount}`}
                            </div>
                          ))
                        ) : (
                          <span className="text-slate-500">-</span>
                        )}
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
      )}

      {/* WinSCP-Style Simple SSH Session Modal */}
      {editingServer && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-xs">
          <div className="w-full max-w-md rounded-2xl border border-slate-700 bg-slate-900 shadow-2xl">
            <div className="flex items-center justify-between border-b border-slate-800 px-5 py-4">
              <div>
                <h3 className="text-sm font-bold text-white">
                  서버 SSH 세션 설정 {editingServer.id ? `(${editingServer.id})` : '(새 세션)'}
                </h3>
                <p className="mt-0.5 text-[11px] text-slate-400">
                  호스트 주소, 포트, 계정, 비밀번호만으로 간편하게 연결합니다.
                </p>
              </div>
              <button
                onClick={() => setEditingServer(null)}
                className="text-slate-400 hover:text-white"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <div className="space-y-3.5 p-5 text-xs">
              {/* Session Box (WinSCP Layout: Host + Port, User + Password) */}
              <div className="space-y-3 rounded-xl border border-slate-800 bg-slate-950/60 p-3.5">
                <div className="flex items-center justify-between text-[11px] font-bold text-indigo-300">
                  <span>세션 (SSH 접속 정보)</span>
                  <span className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[10px] text-slate-300">
                    프로토콜: SSH
                  </span>
                </div>

                <div className="grid grid-cols-4 gap-2.5">
                  <div className="col-span-3">
                    <label className="mb-1 block text-slate-400">
                      호스트 이름 (IP 또는 도메인)
                    </label>
                    <input
                      value={editingServer.host ?? ''}
                      onChange={(e) =>
                        setEditingServer({ ...editingServer, host: e.target.value })
                      }
                      placeholder="예: 192.168.10.101"
                      className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-white focus:border-indigo-500 focus:outline-none"
                    />
                  </div>
                  <div>
                    <label className="mb-1 block text-slate-400">포트 번호</label>
                    <input
                      type="number"
                      value={editingServer.ssh_port ?? 22}
                      onChange={(e) =>
                        setEditingServer({
                          ...editingServer,
                          ssh_port: Number(e.target.value),
                        })
                      }
                      className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-white focus:border-indigo-500 focus:outline-none"
                    />
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-2.5">
                  <div>
                    <label className="mb-1 block text-slate-400">사용자 이름</label>
                    <input
                      value={editingServer.ssh_user ?? ''}
                      onChange={(e) =>
                        setEditingServer({
                          ...editingServer,
                          ssh_user: e.target.value,
                        })
                      }
                      placeholder="root 또는 ubuntu"
                      className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-white focus:border-indigo-500 focus:outline-none"
                    />
                  </div>
                  <div>
                    <label className="mb-1 block text-slate-400">비밀번호</label>
                    <input
                      type="password"
                      value={sshPassword}
                      onChange={(e) => setSshPassword(e.target.value)}
                      placeholder={
                        editingServer.has_password
                          ? '저장됨 (변경 시 입력)'
                          : 'SSH 비밀번호 입력'
                      }
                      className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white focus:border-indigo-500 focus:outline-none"
                    />
                  </div>
                </div>
              </div>

              {/* Server Alias & Role */}
              <div className="grid grid-cols-2 gap-2.5">
                <div>
                  <label className="mb-1 block text-slate-400">
                    세션 표시 이름 (선택)
                  </label>
                  <input
                    value={editingServer.name ?? ''}
                    onChange={(e) =>
                      setEditingServer({ ...editingServer, name: e.target.value })
                    }
                    placeholder="예: GPU 서버 #1"
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white focus:border-indigo-500 focus:outline-none"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-slate-400">서버 구분</label>
                  <select
                    value={editingServer.server_role ?? 'GPU'}
                    onChange={(e) =>
                      setEditingServer({
                        ...editingServer,
                        server_role: e.target.value,
                      })
                    }
                    className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-white focus:border-indigo-500 focus:outline-none"
                  >
                    <option value="GPU">GPU 서버 (K8s DGD / 서빙)</option>
                    <option value="REMOTE">리모트 서버 (Mooncake / CXL / SSD)</option>
                  </select>
                </div>
              </div>

              {/* Optional Collapsible Advanced Setting */}
              <div>
                <button
                  type="button"
                  onClick={() => setShowAdvanced((v) => !v)}
                  className="flex items-center gap-1 text-[11px] font-medium text-slate-400 hover:text-slate-200"
                >
                  {showAdvanced ? (
                    <ChevronDown className="h-3.5 w-3.5" />
                  ) : (
                    <ChevronRight className="h-3.5 w-3.5" />
                  )}
                  고급 옵션 (K8s 네임스페이스)
                </button>
                {showAdvanced && (
                  <div className="mt-2 rounded-xl border border-slate-800 bg-slate-950/60 p-3">
                    <label className="mb-1 block text-slate-400">
                      K8s 네임스페이스 (GPU 서버 해당 시)
                    </label>
                    <input
                      value={editingServer.k8s_namespace ?? 'dynamo-llm'}
                      onChange={(e) =>
                        setEditingServer({
                          ...editingServer,
                          k8s_namespace: e.target.value,
                        })
                      }
                      className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-1.5 font-mono text-white focus:border-indigo-500 focus:outline-none"
                    />
                  </div>
                )}
              </div>

              {/* Connection Test Feedback Banner */}
              {connTestResult && (
                <div
                  className={`flex items-start gap-2 rounded-xl border p-2.5 text-[11px] ${
                    connTestResult.ok
                      ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200'
                      : 'border-rose-500/40 bg-rose-500/10 text-rose-200'
                  }`}
                >
                  {connTestResult.ok ? (
                    <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-400" />
                  ) : (
                    <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-rose-400" />
                  )}
                  <span className="break-all">{connTestResult.text}</span>
                </div>
              )}

              {/* Footer Actions */}
              <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-800 pt-3">
                <div className="flex items-center gap-1.5">
                  {editingServer.id && (
                    <button
                      type="button"
                      onClick={handleDeleteServer}
                      className="inline-flex items-center gap-1 rounded-xl border border-rose-500/30 bg-rose-500/10 px-2.5 py-2 text-rose-300 hover:bg-rose-500/20"
                      title="서버 삭제"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                      삭제
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={handleTestConnection}
                    disabled={testingConn}
                    className="inline-flex items-center gap-1.5 rounded-xl border border-slate-700 bg-slate-800 px-3 py-2 font-semibold text-slate-200 hover:bg-slate-700 disabled:opacity-50"
                  >
                    {testingConn ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <PlugZap className="h-3.5 w-3.5 text-amber-400" />
                    )}
                    연결 테스트
                  </button>
                </div>

                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => setEditingServer(null)}
                    className="rounded-xl border border-slate-700 px-3.5 py-2 font-semibold text-slate-300 hover:bg-slate-800"
                  >
                    취소
                  </button>
                  <button
                    type="button"
                    onClick={handleSaveServer}
                    disabled={savingServer}
                    className="rounded-xl bg-indigo-600 px-4 py-2 font-bold text-white hover:bg-indigo-500 disabled:opacity-50"
                  >
                    {savingServer ? '연결 중...' : '저장 및 연결'}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
