import {
  FileCode2,
  GitBranch,
  GitCommitHorizontal,
  RotateCcw,
  Save,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../api'
import type { ConfigCommit, ConfigDiffResponse } from '../types'

interface Props {
  userProfile: string
  commits: ConfigCommit[]
  focusCommitHash?: string | null
  onRefreshBootstrap: () => void
}

export function ConfigGitView({
  userProfile,
  commits,
  focusCommitHash,
  onRefreshBootstrap,
}: Props) {
  const [subTab, setSubTab] = useState<'explorer' | 'diff'>('explorer')
  const [selectedCommit, setSelectedCommit] = useState<string>('')
  const [filesMap, setFilesMap] = useState<Record<string, string>>({})
  const [selectedFile, setSelectedFile] = useState<string>('')
  const [editorText, setEditorText] = useState<string>('')
  const [commitMsg, setCommitMsg] = useState<string>('')
  const [saving, setSaving] = useState(false)

  const [baseHash, setBaseHash] = useState<string>('')
  const [targetHash, setTargetHash] = useState<string>('')
  const [diffData, setDiffData] = useState<ConfigDiffResponse | null>(null)

  const loadTree = async (hash?: string) => {
    const res = await api.getConfigTree(userProfile, hash || undefined)
    setFilesMap(res.files)
    const keys = Object.keys(res.files)
    const nextFile =
      selectedFile && res.files[selectedFile] !== undefined
        ? selectedFile
        : (keys[0] ?? '')
    setSelectedFile(nextFile)
    setEditorText(nextFile ? res.files[nextFile] : '')
  }

  useEffect(() => {
    if (focusCommitHash) {
      setSelectedCommit(focusCommitHash)
      void loadTree(focusCommitHash)
    } else {
      void loadTree(selectedCommit || undefined)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusCommitHash, commits.length])

  useEffect(() => {
    if (commits.length >= 1 && !targetHash) {
      setTargetHash(commits[0].git_hash)
    }
    if (commits.length >= 2 && !baseHash) {
      setBaseHash(commits[1].git_hash)
    }
  }, [commits, baseHash, targetHash])

  useEffect(() => {
    if (subTab === 'diff' && targetHash) {
      void api
        .getConfigDiff(userProfile, baseHash || undefined, targetHash)
        .then(setDiffData)
    }
  }, [subTab, baseHash, targetHash, userProfile, commits.length])

  const handleSelectFile = (path: string) => {
    setSelectedFile(path)
    setEditorText(filesMap[path] ?? '')
  }

  const handleSaveAndCommit = async () => {
    if (!selectedFile) return
    const serverId = selectedFile.split('/')[0]
    setSaving(true)
    try {
      await api.updateConfig(
        userProfile,
        serverId,
        selectedFile,
        editorText,
        commitMsg.trim() || `웹 대시보드에서 ${selectedFile} 수동 업데이트`,
      )
      setCommitMsg('')
      setSelectedCommit('')
      onRefreshBootstrap()
      await loadTree()
    } finally {
      setSaving(false)
    }
  }

  const handleRollback = async (hash: string) => {
    if (
      !confirm(
        `커밋 [${hash}] 시점의 DGD / YAML 설정으로 복원하고 SSH로 서버에 즉시 재배포할까요?`,
      )
    ) {
      return
    }
    await api.rollbackConfig(userProfile, hash)
    setSelectedCommit('')
    onRefreshBootstrap()
    await loadTree()
  }

  return (
    <div className="mx-auto max-w-7xl space-y-5 p-6">
      {/* Top Mode Switcher */}
      <div className="flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-slate-800 bg-slate-900/70 p-4">
        <div className="flex items-center gap-2">
          <button
            onClick={() => setSubTab('explorer')}
            className={`flex items-center gap-1.5 rounded-xl px-4 py-2 text-xs font-bold transition ${
              subTab === 'explorer'
                ? 'bg-indigo-600 text-white'
                : 'bg-slate-800 text-slate-300 hover:bg-slate-700'
            }`}
          >
            <FileCode2 className="h-4 w-4" />
            활성 DGD · YAML 파일 탐색 및 배포
          </button>
          <button
            onClick={() => setSubTab('diff')}
            className={`flex items-center gap-1.5 rounded-xl px-4 py-2 text-xs font-bold transition ${
              subTab === 'diff'
                ? 'bg-indigo-600 text-white'
                : 'bg-slate-800 text-slate-300 hover:bg-slate-700'
            }`}
          >
            <GitBranch className="h-4 w-4" />
            내장 Git 타임라인 & Side-by-Side Diff 비교 ({commits.length})
          </button>
        </div>

        <div className="flex items-center gap-2 text-xs">
          <span className="text-slate-400">조회 기준 커밋:</span>
          <select
            value={selectedCommit}
            onChange={(e) => {
              setSelectedCommit(e.target.value)
              void loadTree(e.target.value || undefined)
            }}
            className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-1.5 text-xs text-white"
          >
            <option value="">최신 활성 상태 (HEAD)</option>
            {commits.map((c) => (
              <option key={c.git_hash} value={c.git_hash}>
                [{c.git_hash}] {c.summary.slice(0, 42)} ({c.author})
              </option>
            ))}
          </select>
        </div>
      </div>

      {subTab === 'explorer' ? (
        <div className="grid gap-5 lg:grid-cols-12">
          {/* File Tree + Recent Commits */}
          <div className="space-y-4 lg:col-span-4">
            <div className="rounded-2xl border border-slate-800 bg-slate-900/80 p-4">
              <div className="mb-2.5 text-xs font-bold text-slate-300">
                감시 중인 서버 설정 파일 목록
              </div>
              <div className="space-y-1">
                {Object.keys(filesMap).length === 0 ? (
                  <div className="py-4 text-center text-xs text-slate-500">
                    아직 동기화된 설정 파일이 없습니다. 서버 연결 후 새로고침하거나 에이전트가 설정을 배포하면 자동 기록됩니다.
                  </div>
                ) : (
                  Object.keys(filesMap).map((path) => (
                    <button
                      key={path}
                      onClick={() => handleSelectFile(path)}
                      className={`w-full rounded-xl px-3 py-2 text-left font-mono text-xs transition ${
                        selectedFile === path
                          ? 'bg-indigo-600/25 font-semibold text-indigo-200 ring-1 ring-indigo-500/40'
                          : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200'
                      }`}
                    >
                      {path}
                    </button>
                  ))
                )}
              </div>
            </div>

            <div className="rounded-2xl border border-slate-800 bg-slate-900/80 p-4">
              <div className="mb-2.5 text-xs font-bold text-slate-300">
                최근 설정 커밋 히스토리 (원클릭 복원)
              </div>
              <div className="max-h-96 space-y-2.5 overflow-y-auto pr-1">
                {commits.map((c, idx) => (
                  <div
                    key={c.git_hash}
                    className="rounded-xl border border-slate-800 bg-slate-950 p-3 text-xs"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="inline-flex items-center gap-1 font-mono font-bold text-indigo-300">
                        <GitCommitHorizontal className="h-3.5 w-3.5" />
                        {c.git_hash}
                        {idx === 0 && (
                          <span className="ml-1 rounded bg-emerald-500/20 px-1.5 py-0.5 text-[10px] text-emerald-300">
                            HEAD
                          </span>
                        )}
                      </span>
                      <button
                        onClick={() => handleRollback(c.git_hash)}
                        className="inline-flex items-center gap-1 rounded-lg border border-slate-700 bg-slate-800 px-2 py-1 text-[10px] font-semibold text-amber-300 hover:bg-slate-700"
                      >
                        <RotateCcw className="h-3 w-3" />이 버전으로 복원
                      </button>
                    </div>
                    <div className="mt-1 font-medium text-slate-200">{c.summary}</div>
                    <div className="mt-1 text-[11px] text-slate-500">
                      작성: {c.author} · {new Date(c.created_at).toLocaleString('ko-KR')}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* Code Viewer / Direct Commit Editor */}
          <div className="flex flex-col rounded-2xl border border-slate-800 bg-slate-900/80 lg:col-span-8">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800 px-4 py-3">
              <span className="font-mono text-xs font-bold text-amber-300">
                {selectedFile || '파일을 선택하세요'}
              </span>
              {selectedCommit && (
                <span className="rounded bg-amber-500/20 px-2 py-0.5 text-[11px] text-amber-300">
                  과거 스냅샷 조회 중 ({selectedCommit})
                </span>
              )}
            </div>
            <textarea
              value={editorText}
              onChange={(e) => setEditorText(e.target.value)}
              spellCheck={false}
              className="min-h-[460px] flex-1 resize-y bg-slate-950 p-4 font-mono text-xs leading-relaxed text-slate-100 focus:outline-none"
            />
            <div className="flex flex-wrap items-center gap-2 border-t border-slate-800 p-3">
              <input
                value={commitMsg}
                onChange={(e) => setCommitMsg(e.target.value)}
                placeholder="변경 커밋 메시지 입력 (예: LMCache chunk_size 512로 조정)..."
                className="flex-1 rounded-xl border border-slate-700 bg-slate-950 px-3 py-2 text-xs text-white"
              />
              <button
                onClick={handleSaveAndCommit}
                disabled={saving || !selectedFile}
                className="flex items-center gap-1.5 rounded-xl bg-indigo-600 px-4 py-2 text-xs font-bold text-white hover:bg-indigo-500 disabled:opacity-40"
              >
                <Save className="h-3.5 w-3.5" />
                SSH 반영 및 Git 커밋
              </button>
            </div>
          </div>
        </div>
      ) : (
        /* Side-by-Side Diff Viewer */
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-3 rounded-2xl border border-slate-800 bg-slate-900/80 p-4 text-xs">
            <div className="flex items-center gap-2">
              <span className="font-semibold text-slate-400">기준(Base) 커밋:</span>
              <select
                value={baseHash}
                onChange={(e) => setBaseHash(e.target.value)}
                className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-1.5 text-white"
              >
                {commits.map((c) => (
                  <option key={c.git_hash} value={c.git_hash}>
                    [{c.git_hash}] {c.summary} ({c.author})
                  </option>
                ))}
              </select>
            </div>

            <span className="text-slate-500">→</span>

            <div className="flex items-center gap-2">
              <span className="font-semibold text-slate-400">비교(Target) 커밋:</span>
              <select
                value={targetHash}
                onChange={(e) => setTargetHash(e.target.value)}
                className="rounded-xl border border-slate-700 bg-slate-950 px-3 py-1.5 text-white"
              >
                {commits.map((c) => (
                  <option key={c.git_hash} value={c.git_hash}>
                    [{c.git_hash}] {c.summary} ({c.author})
                  </option>
                ))}
              </select>
            </div>

            {targetHash && (
              <button
                onClick={() => handleRollback(targetHash)}
                className="ml-auto flex items-center gap-1.5 rounded-xl border border-amber-500/40 bg-amber-500/15 px-3 py-1.5 font-semibold text-amber-200 hover:bg-amber-500/25"
              >
                <RotateCcw className="h-3.5 w-3.5" />
                Target({targetHash}) 설정으로 복원
              </button>
            )}
          </div>

          {!diffData || diffData.files.length === 0 ? (
            <div className="rounded-2xl border border-slate-800 bg-slate-900/60 py-14 text-center text-xs text-slate-400">
              선택한 두 커밋 시점 사이에 변경된 DGD / YAML 파일이 없습니다. (시나리오 실행 후 여러 커밋이 쌓이면 줄 단위 변경점이 표시됩니다.)
            </div>
          ) : (
            diffData.files.map((f) => (
              <div
                key={f.path}
                className="overflow-hidden rounded-2xl border border-slate-800 bg-slate-900/90"
              >
                <div className="flex items-center justify-between border-b border-slate-800 bg-slate-950 px-4 py-2.5 text-xs">
                  <span className="font-mono font-bold text-amber-300">{f.path}</span>
                  <div className="flex items-center gap-2 font-mono">
                    <span className="text-emerald-400">+{f.added_lines}</span>
                    <span className="text-rose-400">-{f.removed_lines}</span>
                  </div>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full border-collapse font-mono text-[11px]">
                    <tbody>
                      {f.side_by_side.map((row, rIdx) => {
                        const leftBg =
                          row.kind === 'delete' || row.kind === 'replace'
                            ? 'bg-rose-950/45 text-rose-200'
                            : 'text-slate-300'
                        const rightBg =
                          row.kind === 'insert' || row.kind === 'replace'
                            ? 'bg-emerald-950/45 text-emerald-200'
                            : 'text-slate-300'
                        return (
                          <tr
                            key={rIdx}
                            className="border-b border-slate-800/40 align-top"
                          >
                            <td className="w-10 select-none bg-slate-950/80 px-2 py-0.5 text-right text-slate-600">
                              {row.left_num ?? ''}
                            </td>
                            <td className={`w-1/2 whitespace-pre px-3 py-0.5 ${leftBg}`}>
                              {row.left_text ?? ''}
                            </td>
                            <td className="w-10 select-none border-l border-slate-800 bg-slate-950/80 px-2 py-0.5 text-right text-slate-600">
                              {row.right_num ?? ''}
                            </td>
                            <td className={`w-1/2 whitespace-pre px-3 py-0.5 ${rightBg}`}>
                              {row.right_text ?? ''}
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  )
}
