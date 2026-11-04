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

async function requestJson<T>(
  url: string,
  userProfile: string,
  options?: RequestInit,
): Promise<T> {
  const res = await fetch(url, {
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

  simulateMeeting: (userProfile: string, roomId: string, topic?: string) =>
    requestJson<{ status: string }>(
      `/api/rooms/${roomId}/simulate`,
      userProfile,
      {
        method: 'POST',
        body: JSON.stringify({ topic, user_id: userProfile }),
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

  upsertServer: (userProfile: string, payload: Record<string, unknown>) =>
    requestJson<{ server: ServerInfo }>('/api/servers', userProfile, {
      method: 'POST',
      body: JSON.stringify(payload),
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
