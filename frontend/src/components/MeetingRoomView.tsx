import {
  Bot,
  CheckCircle2,
  Clock,
  FileBarChart2,
  Hash,
  Loader2,
  MessageSquare,
  Play,
  Plus,
  Send,
  Sparkles,
  Terminal,
  User,
  Wrench,
} from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type {
  AgentInfo,
  ChatMessage,
  RoomInfo,
  ScenarioCampaign,
  SshAuditLog,
} from '../types'

interface Props {
  userProfile: string
  agents: AgentInfo[]
  rooms: RoomInfo[]
  selectedRoomId: string
  onSelectRoom: (roomId: string) => void
  messages: ChatMessage[]
  campaigns: ScenarioCampaign[]
  sshLogs: SshAuditLog[]
  onJumpToReport: (campaignId: string) => void
  onRefreshBootstrap: () => void
}

const ROLE_NAME_MAP: Record<string, string> = {
  ROLE_ORCH: '총괄·벤치마크',
  ROLE_GPU: 'GPU 서버',
  ROLE_REMOTE: '리모트·메모리',
  ROLE_CONFIG: 'DGD·YAML',
}

const WORKFLOW_STEPS = [
  { key: 'IDLE', label: '대기' },
  { key: 'DISCUSSION', label: '에이전트 논의' },
  { key: 'APPROVAL_PENDING', label: '사용자 승인 대기' },
  { key: 'EXECUTION', label: '병렬·순차 실행' },
  { key: 'REPORTING', label: '리포트 생성' },
  { key: 'COMPLETED', label: '완료' },
]

const QUICK_TOPICS = [
  'TP4로 GPU를 구성하고 LMCache 백엔드를 Mooncake Store로 연동한 뒤, KV 캐시 오프로딩 데스티네이션을 CXL 메모리 / DRAM / NVMe SSD 3가지로 나누어 비교 테스트 시나리오를 작성해 줘.',
  '현재 GPU 서버의 Dynamo DGD 설정과 리모트 서버의 CXL NUMA 노드 상태를 점검하고 병목 요인을 논의해 줘.',
]

export function MeetingRoomView({
  userProfile,
  agents,
  rooms,
  selectedRoomId,
  onSelectRoom,
  messages,
  campaigns,
  sshLogs,
  onJumpToReport,
  onRefreshBootstrap,
}: Props) {
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [showDrawer, setShowDrawer] = useState(true)
  const [newRoomName, setNewRoomName] = useState('')
  const [creatingRoom, setCreatingRoom] = useState(false)
  const [feedbackMap, setFeedbackMap] = useState<Record<string, string>>({})
  const [revisionOpenId, setRevisionOpenId] = useState<string | null>(null)
  const chatEndRef = useRef<HTMLDivElement>(null)

  const currentRoom = rooms.find((r) => r.id === selectedRoomId) ?? rooms[0]
  const campaignById = Object.fromEntries(campaigns.map((c) => [c.id, c]))

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages.length, selectedRoomId])

  const handleSend = async (textOverride?: string) => {
    const content = (textOverride ?? input).trim()
    if (!content || !currentRoom || sending) return
    setSending(true)
    try {
      await api.sendMessage(userProfile, currentRoom.id, content)
      if (!textOverride) setInput('')
    } catch (err) {
      alert(`메시지 전송 실패: ${err}`)
    } finally {
      setSending(false)
    }
  }

  const handleSimulate = async () => {
    if (!currentRoom) return
    try {
      await api.simulateMeeting(
        userProfile,
        currentRoom.id,
        input.trim() || undefined,
      )
      setInput('')
    } catch (err) {
      alert(`시뮬레이션 시작 실패: ${err}`)
    }
  }

  const handleCreateRoom = async () => {
    const name = newRoomName.trim()
    if (!name) return
    const created = await api.createRoom(userProfile, name)
    setNewRoomName('')
    setCreatingRoom(false)
    onRefreshBootstrap()
    onSelectRoom(created.id)
  }

  const handleApprove = async (campaignId: string) => {
    await api.approveCampaign(userProfile, campaignId)
  }

  const handleRequestChanges = async (campaignId: string) => {
    const fb = (feedbackMap[campaignId] ?? '').trim()
    if (!fb) return
    await api.requestChanges(userProfile, campaignId, fb)
    setRevisionOpenId(null)
  }

  const groupRooms = rooms.filter((r) => r.room_type === 'group')
  const directRooms = rooms.filter((r) => r.room_type === 'direct')

  return (
    <div className="flex h-[calc(100vh-3.75rem)] overflow-hidden">
      {/* Left Sidebar: Agents Presence + Channels */}
      <aside className="flex w-76 shrink-0 flex-col border-r border-slate-800 bg-slate-900/70">
        <div className="border-b border-slate-800 p-4">
          <div className="mb-2.5 flex items-center justify-between">
            <span className="text-xs font-bold tracking-wider text-slate-400 uppercase">
              에이전트 4인방 접속 & 역할 현황
            </span>
            <span className="rounded-full bg-slate-800 px-2 py-0.5 text-[11px] text-slate-300">
              온라인 {agents.filter((a) => a.status !== 'OFFLINE').length}/4
            </span>
          </div>
          <div className="space-y-2">
            {agents.map((ag) => {
              const isOnline = ag.status !== 'OFFLINE'
              const isWorking = ag.status === 'WORKING'
              return (
                <div
                  key={ag.id}
                  className="rounded-xl border border-slate-800/90 bg-slate-950/70 p-2.5"
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span
                        className={`h-2.5 w-2.5 rounded-full ${
                          isWorking
                            ? 'animate-pulse bg-sky-400'
                            : isOnline
                              ? 'bg-emerald-400'
                              : 'bg-slate-600'
                        }`}
                      />
                      <span className="text-xs font-semibold text-slate-100">
                        {ag.name}
                      </span>
                    </div>
                    <span className="text-[10px] text-slate-500">
                      {isWorking ? '작업 중' : isOnline ? '대기 중' : 'Offline'}
                    </span>
                  </div>
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {ag.assigned_roles.length > 0 ? (
                      ag.assigned_roles.map((rc) => (
                        <span
                          key={rc}
                          className={`rounded-md px-1.5 py-0.5 text-[10px] font-medium ${
                            rc === ag.primary_role
                              ? 'bg-indigo-500/20 text-indigo-300'
                              : 'bg-amber-500/20 text-amber-300'
                          }`}
                        >
                          {ROLE_NAME_MAP[rc] ?? rc}
                          {rc !== ag.primary_role ? ' (겸임)' : ''}
                        </span>
                      ))
                    ) : (
                      <span className="text-[10px] text-slate-500">
                        기본 주특기: {ROLE_NAME_MAP[ag.primary_role] ?? ag.primary_role}
                      </span>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        </div>

        <div className="flex-1 space-y-5 overflow-y-auto p-3">
          <div>
            <div className="mb-1.5 flex items-center justify-between px-2">
              <span className="text-[11px] font-bold tracking-wider text-slate-400 uppercase">
                그룹 미팅룸 (4인 + 4에이전트)
              </span>
              <button
                onClick={() => setCreatingRoom((v) => !v)}
                className="rounded p-0.5 text-slate-400 hover:bg-slate-800 hover:text-white"
                title="새 미팅룸 생성"
              >
                <Plus className="h-3.5 w-3.5" />
              </button>
            </div>
            {creatingRoom && (
              <div className="mb-2 flex gap-1 px-1">
                <input
                  value={newRoomName}
                  onChange={(e) => setNewRoomName(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleCreateRoom()}
                  placeholder="새 회의방 이름..."
                  className="w-full rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-1 text-xs text-white focus:border-indigo-500 focus:outline-none"
                />
                <button
                  onClick={handleCreateRoom}
                  className="rounded-lg bg-indigo-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-indigo-500"
                >
                  생성
                </button>
              </div>
            )}
            <div className="space-y-1">
              {groupRooms.map((r) => (
                <button
                  key={r.id}
                  onClick={() => onSelectRoom(r.id)}
                  className={`flex w-full items-center justify-between rounded-xl px-3 py-2 text-left text-xs transition ${
                    r.id === currentRoom?.id
                      ? 'bg-indigo-600/20 font-semibold text-indigo-200 ring-1 ring-indigo-500/40'
                      : 'text-slate-300 hover:bg-slate-800/70'
                  }`}
                >
                  <span className="flex items-center gap-2 truncate">
                    <Hash className="h-3.5 w-3.5 shrink-0 text-indigo-400" />
                    <span className="truncate">{r.name}</span>
                  </span>
                  {r.workflow_state !== 'IDLE' && (
                    <span className="ml-1 shrink-0 rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-amber-300">
                      {r.workflow_state}
                    </span>
                  )}
                </button>
              ))}
            </div>
          </div>

          <div>
            <div className="mb-1.5 px-2 text-[11px] font-bold tracking-wider text-slate-400 uppercase">
              에이전트 1:1 다이렉트 룸
            </div>
            <div className="space-y-1">
              {directRooms.map((r) => (
                <button
                  key={r.id}
                  onClick={() => onSelectRoom(r.id)}
                  className={`flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-xs transition ${
                    r.id === currentRoom?.id
                      ? 'bg-indigo-600/20 font-semibold text-indigo-200 ring-1 ring-indigo-500/40'
                      : 'text-slate-400 hover:bg-slate-800/70 hover:text-slate-200'
                  }`}
                >
                  <MessageSquare className="h-3.5 w-3.5 shrink-0 text-emerald-400" />
                  <span className="truncate">{r.name}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      </aside>

      {/* Center Messenger Stream */}
      <main className="flex min-w-0 flex-1 flex-col bg-slate-950">
        {/* Room Header + Workflow Stepper */}
        {currentRoom && (
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 bg-slate-900/50 px-5 py-3">
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-sm font-bold text-white">{currentRoom.name}</h1>
                <span className="rounded-md bg-slate-800 px-2 py-0.5 text-[11px] text-slate-400">
                  {currentRoom.room_type === 'group' ? '그룹 미팅룸' : '1:1 채널'}
                </span>
              </div>
              {currentRoom.room_type === 'group' && (
                <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                  {WORKFLOW_STEPS.map((st, idx) => {
                    const active = currentRoom.workflow_state === st.key
                    return (
                      <div key={st.key} className="flex items-center gap-1.5">
                        <span
                          className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${
                            active
                              ? 'bg-indigo-500 text-white shadow-xs'
                              : 'bg-slate-900 text-slate-500'
                          }`}
                        >
                          {st.label}
                        </span>
                        {idx < WORKFLOW_STEPS.length - 1 && (
                          <span className="text-[10px] text-slate-700">→</span>
                        )}
                      </div>
                    )
                  })}
                </div>
              )}
            </div>

            <div className="flex items-center gap-2">
              {currentRoom.room_type === 'group' && (
                <button
                  onClick={handleSimulate}
                  className="flex items-center gap-1.5 rounded-xl border border-amber-500/40 bg-amber-500/15 px-3 py-1.5 text-xs font-semibold text-amber-200 transition hover:bg-amber-500/25"
                  title="실제 터미널 연결 전에도 4개 에이전트의 회의 → 승인카드 → 병렬/순차 실행 → 리포트 전 과정을 즉시 시연합니다"
                >
                  <Sparkles className="h-3.5 w-3.5 text-amber-400" />
                  4개 에이전트 협업 시뮬레이션
                </button>
              )}
              <button
                onClick={() => setShowDrawer((v) => !v)}
                className={`flex items-center gap-1.5 rounded-xl border px-3 py-1.5 text-xs font-medium transition ${
                  showDrawer
                    ? 'border-indigo-500/50 bg-indigo-500/15 text-indigo-200'
                    : 'border-slate-700 bg-slate-900 text-slate-300 hover:bg-slate-800'
                }`}
              >
                <Terminal className="h-3.5 w-3.5" />
                실시간 SSH 로그 ({sshLogs.length})
              </button>
            </div>
          </div>
        )}

        {/* Messages Feed */}
        <div className="flex-1 space-y-4 overflow-y-auto px-5 py-4">
          {messages.map((m) => {
            const isSystem = m.sender_type === 'system'
            const isAgent = m.sender_type === 'agent'
            const campId = m.metadata?.campaign_id as string | undefined
            const liveCampaign = campId ? campaignById[campId] : undefined

            if (isSystem) {
              return (
                <div
                  key={m.id}
                  className="mx-auto max-w-3xl rounded-xl border border-slate-800 bg-slate-900/70 px-4 py-2.5 text-center text-xs text-slate-300"
                >
                  {m.content}
                </div>
              )
            }

            return (
              <div key={m.id} className="flex items-start gap-3">
                <div
                  className={`mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-xl ${
                    isAgent
                      ? 'bg-indigo-500/20 text-indigo-300 ring-1 ring-indigo-500/30'
                      : 'bg-emerald-500/20 text-emerald-300 ring-1 ring-emerald-500/30'
                  }`}
                >
                  {isAgent ? <Bot className="h-4 w-4" /> : <User className="h-4 w-4" />}
                </div>

                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-xs font-bold text-white">
                      {m.sender_name}
                    </span>
                    {m.role_badge && (
                      <span
                        className={`rounded-md px-1.5 py-0.5 text-[10px] font-semibold ${
                          isAgent
                            ? 'bg-indigo-500/20 text-indigo-300'
                            : 'bg-emerald-500/20 text-emerald-300'
                        }`}
                      >
                        {m.role_badge}
                      </span>
                    )}
                    <span className="text-[11px] text-slate-500">
                      {new Date(m.created_at).toLocaleTimeString('ko-KR')}
                    </span>
                  </div>

                  <div className="mt-1 whitespace-pre-wrap rounded-2xl rounded-tl-xs border border-slate-800/90 bg-slate-900/80 px-4 py-3 text-sm leading-relaxed text-slate-200">
                    {m.content}
                  </div>

                  {/* Interactive Scenario Proposal Card */}
                  {m.msg_kind === 'scenario_proposal' && liveCampaign && (
                    <div className="mt-3 overflow-hidden rounded-2xl border border-indigo-500/40 bg-slate-900 shadow-lg">
                      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 bg-indigo-950/40 px-4 py-3">
                        <div>
                          <span className="text-[10px] font-bold tracking-wider text-indigo-400 uppercase">
                            시나리오 제안 및 실행 계획 (Campaign {liveCampaign.id})
                          </span>
                          <h3 className="text-sm font-bold text-white">
                            {liveCampaign.title}
                          </h3>
                        </div>
                        <span
                          className={`rounded-full px-2.5 py-1 text-xs font-semibold ${
                            liveCampaign.status === 'APPROVAL_PENDING'
                              ? 'bg-amber-500/20 text-amber-300'
                              : liveCampaign.status === 'COMPLETED'
                                ? 'bg-emerald-500/20 text-emerald-300'
                                : 'bg-sky-500/20 text-sky-300'
                          }`}
                        >
                          {liveCampaign.status}
                        </span>
                      </div>

                      <div className="space-y-4 p-4 text-xs">
                        {/* Role Assignments */}
                        <div>
                          <div className="mb-1.5 font-semibold text-slate-300">
                            1. 에이전트 역할 분담표
                          </div>
                          <div className="grid gap-2 sm:grid-cols-2">
                            {Object.entries(liveCampaign.role_assignments).map(
                              ([actor, duty]) => (
                                <div
                                  key={actor}
                                  className="rounded-xl border border-slate-800 bg-slate-950/70 p-2.5"
                                >
                                  <div className="font-semibold text-indigo-300">
                                    {actor}
                                  </div>
                                  <div className="mt-0.5 text-slate-400">{duty}</div>
                                </div>
                              ),
                            )}
                          </div>
                        </div>

                        {/* Execution DAG Steps */}
                        <div>
                          <div className="mb-1.5 font-semibold text-slate-300">
                            2. 단계별 실행 DAG (독립 스텝 병렬 · 종속 스텝 순차)
                          </div>
                          <div className="space-y-2">
                            {liveCampaign.steps_dag.map((st) => (
                              <div
                                key={st.step_id}
                                className="flex items-start justify-between gap-3 rounded-xl border border-slate-800 bg-slate-950/70 px-3 py-2.5"
                              >
                                <div className="flex items-start gap-2.5">
                                  {st.status === 'COMPLETED' ? (
                                    <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-400" />
                                  ) : st.status === 'RUNNING' ? (
                                    <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin text-sky-400" />
                                  ) : (
                                    <Clock className="mt-0.5 h-4 w-4 shrink-0 text-slate-500" />
                                  )}
                                  <div>
                                    <div className="font-semibold text-slate-100">
                                      {st.title}{' '}
                                      <span className="ml-1 rounded bg-slate-800 px-1.5 py-0.5 text-[10px] text-indigo-300">
                                        {st.assigned_agent_id.toUpperCase()}
                                      </span>
                                      {st.depends_on.length === 0 && (
                                        <span className="ml-1 rounded bg-emerald-500/15 px-1.5 py-0.5 text-[10px] text-emerald-300">
                                          병렬 시작
                                        </span>
                                      )}
                                    </div>
                                    <div className="mt-0.5 text-slate-400">
                                      {st.action_summary}
                                    </div>
                                    {st.result_note && (
                                      <div className="mt-1 text-[11px] text-emerald-400">
                                        결과: {st.result_note}
                                      </div>
                                    )}
                                  </div>
                                </div>
                                <span className="shrink-0 text-[10px] font-semibold text-slate-400">
                                  {st.status}
                                </span>
                              </div>
                            ))}
                          </div>
                        </div>

                        {/* Target Configs Preview */}
                        {Object.keys(liveCampaign.target_configs_preview).length > 0 && (
                          <div>
                            <div className="mb-1.5 font-semibold text-slate-300">
                              3. 변경 대상 DGD / YAML 설정 요약
                            </div>
                            <div className="rounded-xl border border-slate-800 bg-slate-950 p-2.5 font-mono text-[11px] text-slate-300">
                              {Object.entries(liveCampaign.target_configs_preview).map(
                                ([path, summary]) => (
                                  <div key={path} className="py-0.5">
                                    <span className="text-amber-300">{path}</span>:{' '}
                                    <span className="text-slate-400">{summary}</span>
                                  </div>
                                ),
                              )}
                            </div>
                          </div>
                        )}

                        {/* Approval Action Bar */}
                        {liveCampaign.status === 'APPROVAL_PENDING' && (
                          <div className="border-t border-slate-800 pt-3">
                            <div className="flex flex-wrap items-center gap-2">
                              <button
                                onClick={() => handleApprove(liveCampaign.id)}
                                className="flex items-center gap-1.5 rounded-xl bg-emerald-600 px-4 py-2 text-xs font-bold text-white shadow-sm transition hover:bg-emerald-500"
                              >
                                <Play className="h-3.5 w-3.5" />
                                승인 및 역할별 실행 시작
                              </button>
                              <button
                                onClick={() =>
                                  setRevisionOpenId(
                                    revisionOpenId === liveCampaign.id
                                      ? null
                                      : liveCampaign.id,
                                  )
                                }
                                className="flex items-center gap-1.5 rounded-xl border border-slate-700 bg-slate-800 px-3.5 py-2 text-xs font-semibold text-slate-200 hover:bg-slate-700"
                              >
                                <Wrench className="h-3.5 w-3.5" />
                                시나리오 수정 지시
                              </button>
                            </div>
                            {revisionOpenId === liveCampaign.id && (
                              <div className="mt-2.5 flex gap-2">
                                <input
                                  value={feedbackMap[liveCampaign.id] ?? ''}
                                  onChange={(e) =>
                                    setFeedbackMap((prev) => ({
                                      ...prev,
                                      [liveCampaign.id]: e.target.value,
                                    }))
                                  }
                                  placeholder="예: 동시성(concurrency)을 64로 높이고 SSD 대신 CXL과 DRAM 2종만 먼저 비교해 줘"
                                  className="flex-1 rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-white focus:border-indigo-500 focus:outline-none"
                                />
                                <button
                                  onClick={() => handleRequestChanges(liveCampaign.id)}
                                  className="rounded-xl bg-amber-600 px-3.5 py-2 text-xs font-bold text-white hover:bg-amber-500"
                                >
                                  전송
                                </button>
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    </div>
                  )}

                  {/* Published Report Jump Card */}
                  {m.msg_kind === 'report_published' && campId && (
                    <button
                      onClick={() => onJumpToReport(campId)}
                      className="mt-2.5 inline-flex items-center gap-2 rounded-xl border border-emerald-500/40 bg-emerald-500/15 px-4 py-2 text-xs font-bold text-emerald-300 transition hover:bg-emerald-500/25"
                    >
                      <FileBarChart2 className="h-4 w-4" />
                      생성된 CXL · DRAM · SSD 비교 리포트 바로 보기 →
                    </button>
                  )}
                </div>
              </div>
            )
          })}
          <div ref={chatEndRef} />
        </div>

        {/* Quick Topic Chips + Composer */}
        <div className="border-t border-slate-800 bg-slate-900/60 p-4">
          <div className="mb-2 flex flex-wrap gap-1.5">
            {QUICK_TOPICS.map((qt, i) => (
              <button
                key={i}
                onClick={() => setInput(qt)}
                className="truncate rounded-lg border border-slate-800 bg-slate-900 px-2.5 py-1 text-[11px] text-slate-400 transition hover:border-indigo-500/40 hover:text-indigo-300"
              >
                💡 예시 주제 {i + 1}: {qt.slice(0, 56)}...
              </button>
            ))}
          </div>
          <div className="flex gap-2">
            <textarea
              rows={2}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  handleSend()
                }
              }}
              placeholder="벤치마크 주제나 요청 사항을 입력하세요 (Enter 전송, Shift+Enter 줄바꿈)..."
              className="flex-1 resize-none rounded-xl border border-slate-700 bg-slate-950 px-3.5 py-2.5 text-sm text-white placeholder-slate-500 focus:border-indigo-500 focus:outline-none"
            />
            <button
              onClick={() => handleSend()}
              disabled={sending || !input.trim()}
              className="flex items-center gap-1.5 self-end rounded-xl bg-indigo-600 px-4 py-2.5 text-xs font-bold text-white transition hover:bg-indigo-500 disabled:opacity-40"
            >
              <Send className="h-4 w-4" />
              발제 / 전송
            </button>
          </div>
        </div>
      </main>

      {/* Right Collapsible SSH Execution Audit Drawer */}
      {showDrawer && (
        <aside className="flex w-92 shrink-0 flex-col border-l border-slate-800 bg-slate-900/80">
          <div className="border-b border-slate-800 px-4 py-3">
            <div className="flex items-center gap-2 text-xs font-bold text-white">
              <Terminal className="h-4 w-4 text-emerald-400" />
              중앙 SSH 게이트웨이 실시간 관제 로그
            </div>
            <p className="mt-0.5 text-[11px] text-slate-400">
              에이전트가 실행한 모든 원격 명령어·DGD 배포·벤치마크 로그가 자동 박제됩니다.
            </p>
          </div>
          <div className="flex-1 space-y-2.5 overflow-y-auto p-3">
            {sshLogs.length === 0 ? (
              <div className="py-12 text-center text-xs text-slate-500">
                아직 실행된 원격 SSH 작업이 없습니다.
              </div>
            ) : (
              sshLogs.map((log) => (
                <div
                  key={log.id}
                  className="rounded-xl border border-slate-800 bg-slate-950 p-3 text-xs"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="rounded bg-indigo-500/20 px-1.5 py-0.5 text-[10px] font-semibold text-indigo-300">
                      {log.actor_id.toUpperCase()} → {log.server_id}
                    </span>
                    <span
                      className={`text-[10px] font-semibold ${
                        log.exit_code === 0 ? 'text-emerald-400' : 'text-rose-400'
                      }`}
                    >
                      exit {log.exit_code} · {log.duration_ms}ms
                    </span>
                  </div>
                  <div className="mt-1 text-[11px] font-medium text-slate-300">
                    {log.purpose}
                  </div>
                  <pre className="mt-1.5 overflow-x-auto rounded-lg bg-slate-900 p-2 text-[11px] text-amber-300">
                    $ {log.command}
                  </pre>
                  {log.stdout && (
                    <pre className="mt-1.5 max-h-28 overflow-y-auto rounded-lg bg-black/50 p-2 text-[10px] leading-relaxed text-slate-400">
                      {log.stdout}
                    </pre>
                  )}
                </div>
              ))
            )}
          </div>
        </aside>
      )}
    </div>
  )
}
