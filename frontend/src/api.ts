import type {
  BenchmarkRun,
  BootstrapResponse,
  ChatMessage,
  ConfigCommit,
  ConfigDiffResponse,
  EnvSnapshot,
  RoomInfo,
  ScenarioCampaign,
  ServerInfo,
} from './types'

export const IS_SIM_MODE =
  window.location.pathname === '/sim' ||
  window.location.pathname.startsWith('/sim/')

export const API_PREFIX = IS_SIM_MODE ? '/sim' : ''

async function requestJson<T>(
  url: string,
  userProfile: string,
  options?: RequestInit,
): Promise<T> {
  const fullUrl = url.startsWith('/api/') ? `${API_PREFIX}${url}` : url
  const res = await fetch(fullUrl, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      'X-User-Profile': userProfile,
      ...(options?.headers ?? {}),
    },
  })
  if (!res.ok) {
    const errText = await res.text()
    throw new Error(errText || `HTTP ${res.status}`)
  }
  return res.json() as Promise<T>
}

export const api = {
  wsUrl: () => {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    return `${proto}//${window.location.host}${API_PREFIX}/ws`
  },

  mcpUrl: (agentId: string) =>
    `${window.location.origin}${API_PREFIX}/mcp?agent_id=${encodeURIComponent(agentId)}`,

  reportExportUrl: (reportId: string) =>
    `${API_PREFIX}/api/reports/${encodeURIComponent(reportId)}/export.md`,

  bootstrap: (userProfile: string) =>
    requestJson<BootstrapResponse>('/api/bootstrap', userProfile),

  createRoom: (userProfile: string, name: string) =>
    requestJson<RoomInfo>('/api/rooms', userProfile, {
      method: 'POST',
      body: JSON.stringify({ name, room_type: 'group' }),
    }),

  getRoomMessages: (userProfile: string, roomId: string) =>
    requestJson<{
      room: RoomInfo
      active_campaign: ScenarioCampaign | null
      messages: ChatMessage[]
    }>(`/api/rooms/${roomId}/messages`, userProfile),

  sendMessage: (userProfile: string, roomId: string, content: string) =>
    requestJson<{ message: ChatMessage; room: RoomInfo }>(
      `/api/rooms/${roomId}/messages`,
      userProfile,
      {
        method: 'POST',
        body: JSON.stringify({ content, user_id: userProfile }),
      },
    ),

  approveCampaign: (userProfile: string, campaignId: string) =>
    requestJson<ScenarioCampaign>(
      `/api/campaigns/${campaignId}/approve`,
      userProfile,
      {
        method: 'POST',
        body: JSON.stringify({ user_id: userProfile }),
      },
    ),

  requestChanges: (
    userProfile: string,
    campaignId: string,
    feedback: string,
  ) =>
    requestJson<ScenarioCampaign>(
      `/api/campaigns/${campaignId}/request-changes`,
      userProfile,
      {
        method: 'POST',
        body: JSON.stringify({ user_id: userProfile, feedback }),
      },
    ),

  refreshServer: (userProfile: string, serverId: string, force = false) =>
    requestJson<{
      snapshot: EnvSnapshot
      benchmark_guard_deferred: boolean
      guard_message: string | null
    }>(`/api/servers/${serverId}/refresh`, userProfile, {
      method: 'POST',
      body: JSON.stringify({ force }),
    }),

  refreshAllServers: (userProfile: string, force = false) =>
    requestJson<{
      results: Record<
        string,
        {
          snapshot: EnvSnapshot
          benchmark_guard_deferred: boolean
          guard_message: string | null
        }
      >
      errors: Record<string, string>
    }>('/api/servers/refresh-all', userProfile, {
      method: 'POST',
      body: JSON.stringify({ force }),
    }),

  testServerConnection: (
    userProfile: string,
    payload: {
      id?: string
      host: string
      ssh_port: number
      ssh_user: string
      ssh_password?: string
    },
  ) =>
    requestJson<{
      ok: boolean
      banner?: string
      latency_ms?: number
      error?: string
    }>('/api/servers/test-connection', userProfile, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  upsertServer: (userProfile: string, payload: Record<string, unknown>) =>
    requestJson<{
      server: ServerInfo
      collect_warning?: string
    }>('/api/servers', userProfile, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  deleteServer: (userProfile: string, serverId: string) =>
    requestJson<{ deleted: string }>(`/api/servers/${serverId}`, userProfile, {
      method: 'DELETE',
    }),

  getConfigTree: (userProfile: string, commitHash?: string) =>
    requestJson<{
      head_hash: string | null
      selected_hash: string | null
      files: Record<string, string>
      commits: ConfigCommit[]
    }>(
      `/api/configs/tree${commitHash ? `?commit_hash=${encodeURIComponent(commitHash)}` : ''}`,
      userProfile,
    ),

  getConfigDiff: (
    userProfile: string,
    baseHash?: string,
    targetHash?: string,
    serverId?: string,
  ) => {
    const params = new URLSearchParams()
    if (baseHash) params.set('base_hash', baseHash)
    if (targetHash) params.set('target_hash', targetHash)
    if (serverId) params.set('server_id', serverId)
    return requestJson<ConfigDiffResponse>(
      `/api/configs/diff?${params.toString()}`,
      userProfile,
    )
  },

  updateConfig: (
    userProfile: string,
    serverId: string,
    repoPath: string,
    content: string,
    commitMessage: string,
  ) =>
    requestJson<{ commit: ConfigCommit }>('/api/configs/update', userProfile, {
      method: 'POST',
      body: JSON.stringify({
        server_id: serverId,
        repo_path: repoPath,
        content,
        commit_message: commitMessage,
      }),
    }),

  rollbackConfig: (
    userProfile: string,
    targetHash: string,
    serverId?: string,
  ) =>
    requestJson<{ commit: ConfigCommit }>('/api/configs/rollback', userProfile, {
      method: 'POST',
      body: JSON.stringify({ target_hash: targetHash, server_id: serverId }),
    }),

  getRunLog: (userProfile: string, runId: string) =>
    requestJson<{ run: BenchmarkRun; raw_log: string }>(
      `/api/runs/${runId}/log`,
      userProfile,
    ),
}
