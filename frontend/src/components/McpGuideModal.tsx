import { Check, Copy, Plug, Terminal, X } from 'lucide-react'
import { useState } from 'react'

interface Props {
  open: boolean
  onClose: () => void
}

const STANDBY_PROMPT = `미팅룸 대기 모드로 입장해 줘.
1. wait_for_turn MCP 툴을 호출해 내 차례가 올 때까지 대기해.
2. HEARTBEAT_IDLE 상태가 반환되면 별도 설명 출력 없이 즉시 wait_for_turn을 다시 호출해.
3. YOUR_TURN 상태가 반환되면 instruction 지침과 내 담당 역할(your_assigned_roles)에 맞춰 get_environment_snapshot, ssh_exec, update_server_config, run_benchmark_step, post_message, submit_scenario_proposal, complete_scenario_step, publish_benchmark_report 중 필요한 툴을 실행한 뒤, 작업이 끝나자마자 다시 wait_for_turn을 호출해 대기 상태를 유지해.`

export function McpGuideModal({ open, onClose }: Props) {
  const [selectedAgent, setSelectedAgent] = useState('agent_a')
  const [copiedKey, setCopiedKey] = useState<string | null>(null)

  if (!open) return null

  const origin = window.location.origin
  const mcpUrl = `${origin}/mcp?agent_id=${selectedAgent}`
  const claudeCmd = `MCP_TOOL_TIMEOUT=1800000 claude mcp add --transport http amr-${selectedAgent} "${mcpUrl}"`
  const hermesYaml = `mcp_servers:
  agent_meeting_room:
    transport: streamable_http
    url: "${mcpUrl}"
    timeout: 1800`

  const copyText = (key: string, val: string) => {
    navigator.clipboard.writeText(val)
    setCopiedKey(key)
    setTimeout(() => setCopiedKey(null), 1800)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-xs">
      <div className="max-h-[90vh] w-full max-w-3xl overflow-y-auto rounded-2xl border border-slate-700 bg-slate-900 shadow-2xl">
        <div className="flex items-center justify-between border-b border-slate-800 px-6 py-4">
          <div className="flex items-center gap-2.5">
            <div className="rounded-lg bg-indigo-500/20 p-2 text-indigo-400">
              <Plug className="h-5 w-5" />
            </div>
            <div>
              <h2 className="text-lg font-bold text-white">
                개인 에이전트 (Claude Code · Hermes) MCP 연동 가이드
              </h2>
              <p className="text-xs text-slate-400">
                사내 폐쇄형 LLM과 연동된 각 팀원의 에이전트를 단일 포트 MCP 대기 루프로 연결합니다.
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-800 hover:text-white"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="space-y-5 p-6 text-sm">
          <div>
            <label className="mb-2 block text-xs font-semibold tracking-wider text-slate-400 uppercase">
              1. 연결할 담당 에이전트 선택
            </label>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {[
                { id: 'agent_a', label: 'Agent A · GPU 서버' },
                { id: 'agent_b', label: 'Agent B · 리모트/CXL' },
                { id: 'agent_c', label: 'Agent C · DGD/YAML' },
                { id: 'agent_d', label: 'Agent D · 총괄/벤치마크' },
              ].map((item) => (
                <button
                  key={item.id}
                  onClick={() => setSelectedAgent(item.id)}
                  className={`rounded-xl border px-3 py-2.5 text-left text-xs font-medium transition ${
                    selectedAgent === item.id
                      ? 'border-indigo-500 bg-indigo-500/20 text-indigo-200'
                      : 'border-slate-800 bg-slate-950/60 text-slate-400 hover:border-slate-700'
                  }`}
                >
                  {item.label}
                </button>
              ))}
            </div>
          </div>

          <div>
            <div className="mb-1.5 flex items-center justify-between">
              <span className="text-xs font-semibold text-slate-300">
                2-A. Claude Code 등록 명령어
              </span>
              <button
                onClick={() => copyText('claude', claudeCmd)}
                className="flex items-center gap-1 rounded-md bg-slate-800 px-2.5 py-1 text-xs text-slate-300 hover:bg-slate-700"
              >
                {copiedKey === 'claude' ? (
                  <Check className="h-3.5 w-3.5 text-emerald-400" />
                ) : (
                  <Copy className="h-3.5 w-3.5" />
                )}
                복사
              </button>
            </div>
            <pre className="overflow-x-auto rounded-xl border border-slate-800 bg-slate-950 p-3.5 text-xs text-emerald-300">
              {claudeCmd}
            </pre>
          </div>

          <div>
            <div className="mb-1.5 flex items-center justify-between">
              <span className="text-xs font-semibold text-slate-300">
                2-B. Hermes 설정 (`config.yaml`) 예시
              </span>
              <button
                onClick={() => copyText('hermes', hermesYaml)}
                className="flex items-center gap-1 rounded-md bg-slate-800 px-2.5 py-1 text-xs text-slate-300 hover:bg-slate-700"
              >
                {copiedKey === 'hermes' ? (
                  <Check className="h-3.5 w-3.5 text-emerald-400" />
                ) : (
                  <Copy className="h-3.5 w-3.5" />
                )}
                복사
              </button>
            </div>
            <pre className="overflow-x-auto rounded-xl border border-slate-800 bg-slate-950 p-3.5 text-xs text-sky-300">
              {hermesYaml}
            </pre>
          </div>

          <div>
            <div className="mb-1.5 flex items-center justify-between">
              <span className="flex items-center gap-1.5 text-xs font-semibold text-slate-300">
                <Terminal className="h-3.5 w-3.5 text-amber-400" />
                3. 전용 터미널 탭에서 대기 모드(Standby) 시작 프롬프트
              </span>
              <button
                onClick={() => copyText('prompt', STANDBY_PROMPT)}
                className="flex items-center gap-1 rounded-md bg-slate-800 px-2.5 py-1 text-xs text-slate-300 hover:bg-slate-700"
              >
                {copiedKey === 'prompt' ? (
                  <Check className="h-3.5 w-3.5 text-emerald-400" />
                ) : (
                  <Copy className="h-3.5 w-3.5" />
                )}
                프롬프트 복사
              </button>
            </div>
            <pre className="whitespace-pre-wrap rounded-xl border border-slate-800 bg-slate-950 p-3.5 text-xs leading-relaxed text-slate-300">
              {STANDBY_PROMPT}
            </pre>
            <p className="mt-2 text-xs text-slate-400">
              💡 프로젝트 내 <code className="text-indigo-300">integrations/SKILL.md</code> 파일을 스킬 폴더에 복사해 두면 <code className="text-indigo-300">/join-room</code> 한 줄로 즉시 입장할 수 있습니다.
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
