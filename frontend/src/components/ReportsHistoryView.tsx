import {
  BarChart3,
  Download,
  FileText,
  GitCommitHorizontal,
  X,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../api'
import type {
  BenchmarkReport,
  BenchmarkRun,
  ScenarioCampaign,
} from '../types'

interface Props {
  userProfile: string
  campaigns: ScenarioCampaign[]
  runs: BenchmarkRun[]
  reports: BenchmarkReport[]
  focusCampaignId?: string | null
  onJumpToCommit: (commitHash: string) => void
}

function MetricBarCard({
  title,
  unit,
  note,
  items,
}: {
  title: string
  unit: string
  note: string
  items: Array<{ label: string; value: number; subValue?: number; color: string }>
}) {
  const maxVal = Math.max(
    1,
    ...items.map((i) => Math.max(i.value, i.subValue ?? 0)),
  )

  return (
    <div className="rounded-2xl border border-slate-800 bg-slate-950/80 p-4">
      <div className="mb-3 flex items-baseline justify-between">
        <span className="text-xs font-bold text-white">{title}</span>
        <span className="text-[11px] text-slate-400">{note}</span>
      </div>
      <div className="space-y-3">
        {items.map((it) => {
          const pct = Math.max(4, Math.round((it.value / maxVal) * 100))
          const subPct =
            it.subValue !== undefined
              ? Math.max(4, Math.round((it.subValue / maxVal) * 100))
              : null
          return (
            <div key={it.label} className="space-y-1">
              <div className="flex justify-between text-xs">
                <span className="font-medium text-slate-300">{it.label}</span>
                <span className="font-mono font-bold text-slate-100">
                  {it.value} {unit}
                  {it.subValue !== undefined && (
                    <span className="ml-1.5 text-[11px] font-normal text-slate-400">
                      (p99: {it.subValue} {unit})
                    </span>
                  )}
                </span>
              </div>
              <div className="h-2.5 w-full overflow-hidden rounded-full bg-slate-800">
                <div
                  className={`h-full rounded-full ${it.color}`}
                  style={{ width: `${pct}%` }}
                />
              </div>
              {subPct !== null && (
                <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-900">
                  <div
                    className="h-full rounded-full bg-slate-500/70"
                    style={{ width: `${subPct}%` }}
                  />
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

export function ReportsHistoryView({
  userProfile,
  campaigns,
  runs,
  reports,
  focusCampaignId,
  onJumpToCommit,
}: Props) {
  const [selectedCampaignId, setSelectedCampaignId] = useState<string>('')
  const [logModal, setLogModal] = useState<{
    title: string
    text: string
  } | null>(null)

  useEffect(() => {
    if (focusCampaignId) {
      setSelectedCampaignId(focusCampaignId)
    } else if (!selectedCampaignId && campaigns.length > 0) {
      setSelectedCampaignId(campaigns[0].id)
    }
  }, [focusCampaignId, campaigns, selectedCampaignId])

  const activeCamp =
    campaigns.find((c) => c.id === selectedCampaignId) ?? campaigns[0]
  const campRuns = activeCamp
    ? runs.filter((r) => r.campaign_id === activeCamp.id)
    : []
  const campReport = activeCamp
    ? reports.find((rp) => rp.campaign_id === activeCamp.id)
    : undefined

  const tierColor = (target: string) => {
    const t = target.toUpperCase()
    if (t.includes('CXL')) return 'bg-emerald-500'
    if (t.includes('DRAM')) return 'bg-sky-500'
    return 'bg-amber-500'
  }

  const handleOpenRawLog = async (runId: string, label: string) => {
    const res = await api.getRunLog(userProfile, runId)
    setLogModal({ title: label, text: res.raw_log || '원본 로그 없음' })
  }

  return (
    <div className="mx-auto max-w-7xl p-6">
      {campaigns.length === 0 ? (
        <div className="rounded-2xl border border-slate-800 bg-slate-900/70 py-20 text-center">
          <BarChart3 className="mx-auto h-10 w-10 text-slate-600" />
          <h2 className="mt-3 text-sm font-bold text-white">
            아직 실행된 벤치마크 시나리오 히스토리가 없습니다
          </h2>
          <p className="mt-1 text-xs text-slate-400">
            에이전트 미팅룸에서 주제를 발제하고 에이전트들이 논의해 제출한 시나리오를 승인하면 비교 리포트가 자동 생성됩니다.
          </p>
        </div>
      ) : (
        <div className="grid gap-6 lg:grid-cols-12">
          {/* Campaign Sidebar */}
          <div className="space-y-2.5 lg:col-span-4">
            <div className="mb-2 text-xs font-bold tracking-wider text-slate-400 uppercase">
              벤치마크 캠페인 히스토리 ({campaigns.length})
            </div>
            {campaigns.map((c) => {
              const rCount = runs.filter((r) => r.campaign_id === c.id).length
              const selected = c.id === activeCamp?.id
              return (
                <button
                  key={c.id}
                  onClick={() => setSelectedCampaignId(c.id)}
                  className={`w-full rounded-2xl border p-4 text-left transition ${
                    selected
                      ? 'border-indigo-500 bg-indigo-950/30 shadow-md'
                      : 'border-slate-800 bg-slate-900/70 hover:border-slate-700'
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono text-[11px] text-indigo-300">
                      {c.id}
                    </span>
                    <span
                      className={`rounded-full px-2 py-0.5 text-[10px] font-bold ${
                        c.status === 'COMPLETED'
                          ? 'bg-emerald-500/20 text-emerald-300'
                          : 'bg-amber-500/20 text-amber-300'
                      }`}
                    >
                      {c.status}
                    </span>
                  </div>
                  <div className="mt-1.5 text-xs font-bold text-white">
                    {c.title}
                  </div>
                  <div className="mt-1.5 flex items-center justify-between text-[11px] text-slate-400">
                    <span>실험군 {rCount}건</span>
                    <span>{new Date(c.created_at).toLocaleString('ko-KR')}</span>
                  </div>
                </button>
              )
            })}
          </div>

          {/* Report & Charts Main Content */}
          <div className="space-y-5 lg:col-span-8">
            {activeCamp && (
              <>
                <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-slate-800 bg-slate-900/80 p-5">
                  <div>
                    <div className="text-xs font-semibold text-indigo-400">
                      캠페인 리포트 · {activeCamp.id} (승인자:{' '}
                      {activeCamp.approved_by ?? '대기 중'})
                    </div>
                    <h1 className="mt-0.5 text-base font-bold text-white">
                      {campReport?.title ?? activeCamp.title}
                    </h1>
                  </div>
                  {campReport && (
                    <a
                      href={api.reportExportUrl(campReport.id)}
                      download
                      className="flex items-center gap-1.5 rounded-xl bg-emerald-600 px-3.5 py-2 text-xs font-bold text-white hover:bg-emerald-500"
                    >
                      <Download className="h-4 w-4" />
                      Markdown 리포트 다운로드
                    </a>
                  )}
                </div>

                {/* Comparison Charts */}
                {campRuns.length > 0 && (
                  <div className="grid gap-4 sm:grid-cols-2">
                    <MetricBarCard
                      title="TTFT 지연시간 (Time To First Token)"
                      unit="ms"
                      note="낮을수록 우수 (p50 / p99)"
                      items={campRuns.map((r) => ({
                        label: r.offload_target,
                        value: Number(r.metrics.ttft_p50_ms ?? 0),
                        subValue: Number(r.metrics.ttft_p99_ms ?? 0),
                        color: tierColor(r.offload_target),
                      }))}
                    />
                    <MetricBarCard
                      title="출력 토큰 처리량 (Throughput)"
                      unit="tok/s"
                      note="높을수록 우수"
                      items={campRuns.map((r) => ({
                        label: r.offload_target,
                        value: Number(r.metrics.throughput_tok_s ?? 0),
                        color: tierColor(r.offload_target),
                      }))}
                    />
                    <MetricBarCard
                      title="KV 캐시 히트율 (LMCache + Mooncake)"
                      unit="%"
                      note="높을수록 우수"
                      items={campRuns.map((r) => ({
                        label: r.offload_target,
                        value: Number(r.metrics.kv_cache_hit_rate_pct ?? 0),
                        color: tierColor(r.offload_target),
                      }))}
                    />
                    <MetricBarCard
                      title="오프로딩 전송 대역폭 (Mooncake RDMA/IO)"
                      unit="GB/s"
                      note="높을수록 우수"
                      items={campRuns.map((r) => ({
                        label: r.offload_target,
                        value: Number(r.metrics.offload_bw_gbps ?? 0),
                        color: tierColor(r.offload_target),
                      }))}
                    />
                  </div>
                )}

                {/* Runs Quantitative Table */}
                <div className="overflow-hidden rounded-2xl border border-slate-800 bg-slate-900/80">
                  <div className="border-b border-slate-800 px-4 py-3 text-xs font-bold text-white">
                    세부 실행(Run) 정량 지표 및 설정 스냅샷 연결
                  </div>
                  <div className="overflow-x-auto">
                    <table className="w-full text-left text-xs">
                      <thead className="border-b border-slate-800 bg-slate-950/70 text-[11px] text-slate-400">
                        <tr>
                          <th className="px-4 py-2.5">실험군 (Variant)</th>
                          <th className="px-3 py-2.5">오프로딩 타겟</th>
                          <th className="px-3 py-2.5">TTFT p50/p99</th>
                          <th className="px-3 py-2.5">TPOT p50</th>
                          <th className="px-3 py-2.5">처리량</th>
                          <th className="px-3 py-2.5">KV 히트율</th>
                          <th className="px-3 py-2.5">Git 설정</th>
                          <th className="px-3 py-2.5">원본 로그</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-800/70">
                        {campRuns.map((r) => (
                          <tr key={r.id} className="hover:bg-slate-800/40">
                            <td className="px-4 py-2.5 font-semibold text-white">
                              {r.variant_label}
                            </td>
                            <td className="px-3 py-2.5">
                              <span className="rounded bg-slate-800 px-2 py-0.5 font-mono text-[11px] text-emerald-300">
                                {r.offload_target}
                              </span>
                            </td>
                            <td className="px-3 py-2.5 font-mono text-slate-200">
                              {r.metrics.ttft_p50_ms ?? '-'} /{' '}
                              {r.metrics.ttft_p99_ms ?? '-'} ms
                            </td>
                            <td className="px-3 py-2.5 font-mono text-slate-200">
                              {r.metrics.tpot_p50_ms ?? '-'} ms
                            </td>
                            <td className="px-3 py-2.5 font-mono font-bold text-indigo-300">
                              {r.metrics.throughput_tok_s ?? '-'} tok/s
                            </td>
                            <td className="px-3 py-2.5 font-mono text-emerald-300">
                              {r.metrics.kv_cache_hit_rate_pct ?? '-'}%
                            </td>
                            <td className="px-3 py-2.5">
                              {r.git_hash ? (
                                <button
                                  onClick={() => onJumpToCommit(r.git_hash!)}
                                  className="inline-flex items-center gap-1 rounded bg-indigo-500/20 px-2 py-0.5 font-mono text-[11px] text-indigo-300 hover:bg-indigo-500/35"
                                >
                                  <GitCommitHorizontal className="h-3 w-3" />
                                  {r.git_hash}
                                </button>
                              ) : (
                                '-'
                              )}
                            </td>
                            <td className="px-3 py-2.5">
                              <button
                                onClick={() =>
                                  handleOpenRawLog(r.id, r.variant_label)
                                }
                                className="inline-flex items-center gap-1 rounded border border-slate-700 bg-slate-800 px-2 py-1 text-[11px] text-slate-300 hover:bg-slate-700"
                              >
                                <FileText className="h-3 w-3" />
                                로그
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>

                {/* Agent Synthesized Markdown Analysis */}
                {campReport && (
                  <div className="rounded-2xl border border-slate-800 bg-slate-900/80 p-5">
                    <div className="mb-3 flex items-center justify-between border-b border-slate-800 pb-2.5">
                      <span className="text-xs font-bold text-indigo-300">
                        총괄 에이전트({campReport.author_agent_id.toUpperCase()}) 종합 아키텍처 평가 리포트
                      </span>
                      <span className="text-[11px] text-slate-500">
                        {new Date(campReport.created_at).toLocaleString('ko-KR')}
                      </span>
                    </div>
                    <div className="whitespace-pre-wrap text-sm leading-relaxed text-slate-200">
                      {campReport.summary_markdown}
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      )}

      {/* Raw Log Modal */}
      {logModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4">
          <div className="w-full max-w-3xl rounded-2xl border border-slate-700 bg-slate-900 shadow-2xl">
            <div className="flex items-center justify-between border-b border-slate-800 px-5 py-3.5">
              <h3 className="text-sm font-bold text-white">
                벤치마크 원본 실행 로그 · {logModal.title}
              </h3>
              <button
                onClick={() => setLogModal(null)}
                className="text-slate-400 hover:text-white"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <pre className="max-h-[70vh] overflow-auto p-5 font-mono text-xs leading-relaxed text-emerald-300">
              {logModal.text}
            </pre>
          </div>
        </div>
      )}
    </div>
  )
}
