import {
  BarChart3,
  Bot,
  FileCode2,
  MessageSquare,
  Plug,
  Server,
  UserCircle2,
} from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import { ConfigGitView } from './components/ConfigGitView'
import { EnvironmentView } from './components/EnvironmentView'
import { McpGuideModal } from './components/McpGuideModal'
import { MeetingRoomView } from './components/MeetingRoomView'
import { ReportsHistoryView } from './components/ReportsHistoryView'
import type {
  AgentInfo,
  BenchmarkReport,
  BenchmarkRun,
  ChatMessage,
  ConfigCommit,
  EnvSnapshot,
  RoomInfo,
  ScenarioCampaign,
  ServerInfo,
  SshAuditLog,
  UserProfile,
} from './types'

type MainTab = 'meeting' | 'environment' | 'configs' | 'reports'

export function App() {
  const [activeTab, setActiveTab] = useState<MainTab>('meeting')
  const [userProfile, setUserProfile] = useState<string>(
    () => localStorage.getItem('amr_user_profile') || 'member_a',
  )
  const [users, setUsers] = useState<UserProfile[]>([])
  const [agents, setAgents] = useState<AgentInfo[]>([])
  const [rooms, setRooms] = useState<RoomInfo[]>([])
  const [selectedRoomId, setSelectedRoomId] = useState<string>('room-main')
  const [messagesByRoom, setMessagesByRoom] = useState<
    Record<string, ChatMessage[]>
  >({})
  const [servers, setServers] = useState<ServerInfo[]>([])
  const [snapshots, setSnapshots] = useState<Record<string, EnvSnapshot | null>>(
    {},
  )
  const [commits, setCommits] = useState<ConfigCommit[]>([])
  const [campaigns, setCampaigns] = useState<ScenarioCampaign[]>([])
  const [runs, setRuns] = useState<BenchmarkRun[]>([])
  const [reports, setReports] = useState<BenchmarkReport[]>([])
  const [sshLogs, setSshLogs] = useState<SshAuditLog[]>([])

  const [mcpModalOpen, setMcpModalOpen] = useState(false)
  const [focusCampaignId, setFocusCampaignId] = useState<string | null>(null)
  const [focusCommitHash, setFocusCommitHash] = useState<string | null>(null)

  const loadBootstrap = useCallback(async () => {
    const data = await api.bootstrap(userProfile)
    setUsers(data.users)
    setAgents(data.agents)
    setRooms(data.rooms)
    setServers(data.servers)
    setSnapshots(data.latest_snapshots)
    setCommits(data.commits)
    setCampaigns(data.campaigns)
    setRuns(data.runs)
    setReports(data.reports)
    setSshLogs(data.ssh_logs)
  }, [userProfile])

  const loadRoomMessages = useCallback(
    async (roomId: string) => {
      const res = await api.getRoomMessages(userProfile, roomId)
      setMessagesByRoom((prev) => ({ ...prev, [roomId]: res.messages }))
    },
    [userProfile],
  )

  useEffect(() => {
    void loadBootstrap()
  }, [loadBootstrap])

  useEffect(() => {
    if (selectedRoomId) {
      void loadRoomMessages(selectedRoomId)
    }
  }, [selectedRoomId, loadRoomMessages])

  // Real-time WebSocket Stream
  useEffect(() => {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const wsUrl = `${proto}//${window.location.host}/ws`
    let ws: WebSocket | null = null
    let retryTimer: number | undefined

    const connect = () => {
      ws = new WebSocket(wsUrl)
      ws.onmessage = (ev) => {
        try {
          const event = JSON.parse(ev.data)
          if (event.type === 'message_created') {
            const msg = event.message as ChatMessage
            setMessagesByRoom((prev) => {
              const list = prev[msg.room_id] ?? []
              if (list.some((x) => x.id === msg.id)) return prev
              return { ...prev, [msg.room_id]: [...list, msg] }
            })
          } else if (event.type === 'room_updated') {
            const rm = event.room as RoomInfo
            setRooms((prev) => prev.map((x) => (x.id === rm.id ? rm : x)))
          } else if (event.type === 'room_created') {
            const rm = event.room as RoomInfo
            setRooms((prev) => [...prev, rm])
          } else if (event.type === 'agents_updated') {
            setAgents(event.agents as AgentInfo[])
          } else if (event.type === 'campaign_updated') {
            const camp = event.campaign as ScenarioCampaign
            setCampaigns((prev) => {
              const exists = prev.some((c) => c.id === camp.id)
              return exists
                ? prev.map((c) => (c.id === camp.id ? camp : c))
                : [camp, ...prev]
            })
          } else if (event.type === 'ssh_audit') {
            const log = event.log as SshAuditLog
            setSshLogs((prev) => [log, ...prev.slice(0, 99)])
          } else if (event.type === 'snapshot_updated') {
            const snap = event.snapshot as EnvSnapshot
            setSnapshots((prev) => ({ ...prev, [snap.server_id]: snap }))
          } else if (event.type === 'servers_updated') {
            setServers(event.servers as ServerInfo[])
          } else if (event.type === 'config_committed') {
            void loadBootstrap()
          } else if (
            event.type === 'benchmark_started' ||
            event.type === 'benchmark_finished'
          ) {
            const run = event.run as BenchmarkRun
            setRuns((prev) => {
              const exists = prev.some((r) => r.id === run.id)
              return exists
                ? prev.map((r) => (r.id === run.id ? run : r))
                : [...prev, run]
            })
          } else if (event.type === 'report_created') {
            const rep = event.report as BenchmarkReport
            setReports((prev) => {
              const exists = prev.some((r) => r.id === rep.id)
              return exists
                ? prev.map((r) => (r.id === rep.id ? rep : r))
                : [rep, ...prev]
            })
          }
        } catch (err) {
          console.error('WS parse error:', err)
        }
      }
      ws.onclose = () => {
        retryTimer = window.setTimeout(connect, 2500)
      }
    }

    connect()
    return () => {
      if (retryTimer) clearTimeout(retryTimer)
      ws?.close()
    }
  }, [loadBootstrap])

  const handleProfileChange = (nextId: string) => {
    setUserProfile(nextId)
    localStorage.setItem('amr_user_profile', nextId)
  }

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      {/* Global Top Bar */}
      <header className="sticky top-0 z-40 flex h-15 items-center justify-between border-b border-slate-800 bg-slate-950/90 px-5 backdrop-blur-md">
        <div className="flex items-center gap-6">
          <div className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-indigo-600 text-white shadow-sm">
              <Bot className="h-5 w-5" />
            </div>
            <div>
              <div className="text-sm font-extrabold tracking-tight text-white">
                Agent Meeting Room
              </div>
              <div className="text-[10px] text-slate-400">
                LLM Memory Benchmark & SSH Hub
              </div>
            </div>
          </div>

          <nav className="flex items-center gap-1">
            {[
              { id: 'meeting', label: '에이전트 미팅룸', icon: MessageSquare },
              { id: 'environment', label: '서버 환경 & 스펙', icon: Server },
              { id: 'configs', label: 'DGD · YAML 형상관리', icon: FileCode2 },
              {
                id: 'reports',
                label: `테스트 히스토리 & 리포트 (${reports.length})`,
                icon: BarChart3,
              },
            ].map((tab) => {
              const Icon = tab.icon
              const active = activeTab === tab.id
              return (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id as MainTab)}
                  className={`flex items-center gap-1.5 rounded-xl px-3.5 py-2 text-xs font-bold transition ${
                    active
                      ? 'bg-indigo-600 text-white shadow-xs'
                      : 'text-slate-400 hover:bg-slate-900 hover:text-slate-200'
                  }`}
                >
                  <Icon className="h-3.5 w-3.5" />
                  {tab.label}
                </button>
              )
            })}
          </nav>
        </div>

        <div className="flex items-center gap-2.5">
          <button
            onClick={() => setMcpModalOpen(true)}
            className="flex items-center gap-1.5 rounded-xl border border-indigo-500/40 bg-indigo-500/15 px-3 py-1.5 text-xs font-semibold text-indigo-200 hover:bg-indigo-500/25"
          >
            <Plug className="h-3.5 w-3.5 text-indigo-400" />
            MCP 에이전트 연결 가이드
          </button>

          <div className="flex items-center gap-1.5 rounded-xl border border-slate-800 bg-slate-900 px-2.5 py-1.5 text-xs">
            <UserCircle2 className="h-4 w-4 text-emerald-400" />
            <select
              value={userProfile}
              onChange={(e) => handleProfileChange(e.target.value)}
              className="bg-transparent font-semibold text-slate-200 focus:outline-none"
              title="초기 무인증 프로필 선택 (추후 사내 SSO 확장 가능)"
            >
              {users.map((u) => (
                <option key={u.id} value={u.id} className="bg-slate-900">
                  {u.name}
                </option>
              ))}
            </select>
          </div>
        </div>
      </header>

      {/* Main Content Area */}
      {activeTab === 'meeting' && (
        <MeetingRoomView
          userProfile={userProfile}
          agents={agents}
          rooms={rooms}
          selectedRoomId={selectedRoomId}
          onSelectRoom={setSelectedRoomId}
          messages={messagesByRoom[selectedRoomId] ?? []}
          campaigns={campaigns}
          sshLogs={sshLogs}
          onJumpToReport={(cid) => {
            setFocusCampaignId(cid)
            setActiveTab('reports')
          }}
          onRefreshBootstrap={loadBootstrap}
        />
      )}

      {activeTab === 'environment' && (
        <EnvironmentView
          userProfile={userProfile}
          servers={servers}
          snapshots={snapshots}
          onSnapshotsUpdated={setSnapshots}
          onRefreshBootstrap={loadBootstrap}
        />
      )}

      {activeTab === 'configs' && (
        <ConfigGitView
          userProfile={userProfile}
          commits={commits}
          focusCommitHash={focusCommitHash}
          onRefreshBootstrap={loadBootstrap}
        />
      )}

      {activeTab === 'reports' && (
        <ReportsHistoryView
          userProfile={userProfile}
          campaigns={campaigns}
          runs={runs}
          reports={reports}
          focusCampaignId={focusCampaignId}
          onJumpToCommit={(hash) => {
            setFocusCommitHash(hash)
            setActiveTab('configs')
          }}
        />
      )}

      <McpGuideModal
        open={mcpModalOpen}
        onClose={() => setMcpModalOpen(false)}
      />
    </div>
  )
}
export default App
