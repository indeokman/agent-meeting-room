export interface UserProfile {
  id: string
  name: string
  role_label: string
  avatar_color: string
}

export interface AgentInfo {
  id: string
  name: string
  owner_user_id: string
  primary_role: string
  specialty_desc: string
  status: 'ONLINE_IDLE' | 'WORKING' | 'OFFLINE' | string
  assigned_roles: string[]
  client_type: string
  last_seen_at: string | null
}

export interface RoomInfo {
  id: string
  name: string
  room_type: 'group' | 'direct'
  target_agent_id: string | null
  workflow_state:
    | 'IDLE'
    | 'DISCUSSION'
    | 'APPROVAL_PENDING'
    | 'EXECUTION'
    | 'REPORTING'
    | 'COMPLETED'
    | string
  current_topic: string | null
  active_campaign_id: string | null
  discussion_round: number
  created_at: string
}

export interface DagStep {
  step_id: string
  title: string
  assigned_agent_id: string
  role_code: string
  depends_on: string[]
  server_id?: string | null
  action_summary: string
  status: 'PENDING' | 'RUNNING' | 'COMPLETED' | string
  result_note?: string | null
}

export interface ScenarioCampaign {
  id: string
  room_id: string
  title: string
  topic_prompt: string
  lead_agent_id: string
  status: string
  role_assignments: Record<string, string>
  steps_dag: DagStep[]
  target_configs_preview: Record<string, string>
  approved_by: string | null
  approved_at: string | null
  feedback_note: string | null
  created_at: string
  completed_at: string | null
}

export interface ChatMessage {
  id: string
  room_id: string
  sender_type: 'user' | 'agent' | 'system' | string
  sender_id: string
  sender_name: string
  role_badge: string | null
  msg_kind: 'chat' | 'system' | 'scenario_proposal' | 'report_published' | string
  content: string
  metadata: Record<string, unknown>
  created_at: string
}

export interface WatchedConfigItem {
  kind: 'k8s_dgd' | 'file'
  name?: string
  remote_path?: string
  repo_path: string
}

export interface ServerInfo {
  id: string
  name: string
  host: string
  ssh_port: number
  ssh_user: string
  ssh_key_path: string
  server_role: 'GPU' | 'REMOTE' | string
  connection_mode: 'simulation' | 'ssh' | string
  k8s_namespace: string
  watched_configs: WatchedConfigItem[]
  lock_status: 'FREE' | 'SETUP_LOCKED' | 'BENCHMARK_RUNNING' | string
  lock_holder: string | null
  lock_reason: string | null
  updated_at: string
}

export interface GpuSpec {
  index: number
  name: string
  memory_total_mb: number
  memory_used_mb: number
  utilization_pct: number
  temp_c: number
  pstate: string
}

export interface NumaNodeSpec {
  node_id: number
  tier: 'DRAM' | 'CXL_MEMORY' | string
  total_gb: number
  used_gb?: number
  cpus?: string
  desc?: string
}

export interface EnvSnapshot {
  id: string
  server_id: string
  trigger_type: string
  triggered_by: string
  campaign_id: string | null
  git_hash: string | null
  hw_specs: {
    hostname?: string
    cpu_model?: string
    cpu_cores?: string
    sockets?: string
    gpus?: GpuSpec[]
    gpu_topology_matrix?: string
    numa_nodes?: NumaNodeSpec[]
    cxl_raw?: string
    storage?: Array<{ name: string; model: string; size: string; mount: string }>
    rdma_status?: string
  }
  sw_specs: {
    os_kernel?: string
    gpu_driver?: string
    cuda_version?: string
    k8s_namespace?: string
    dgd_summary?: {
      name: string
      state: string
      tensor_parallel: string
      kv_connector: string
      active_offload_tier: string
    }
    k8s_pods?: Array<{ name: string; phase: string; node: string }>
    packages?: Record<string, string>
  }
  active_configs: Record<string, string>
  created_at: string
}

export interface ConfigCommit {
  git_hash: string
  parent_hash: string | null
  native_git_hash: string | null
  author: string
  server_id: string | null
  campaign_id: string | null
  summary: string
  changed_files: string[]
  created_at: string
}

export interface SideBySideRow {
  kind: 'equal' | 'insert' | 'delete' | 'replace'
  left_num: number | null
  left_text: string | null
  right_num: number | null
  right_text: string | null
}

export interface FileDiffItem {
  path: string
  status: string
  added_lines: number
  removed_lines: number
  unified_diff: string
  side_by_side: SideBySideRow[]
}

export interface ConfigDiffResponse {
  base_hash: string | null
  target_hash: string | null
  changed_file_count: number
  unified_diff: string
  files: FileDiffItem[]
}

export interface SshAuditLog {
  id: string
  room_id: string | null
  campaign_id: string | null
  server_id: string
  actor_id: string
  command: string
  purpose: string
  exit_code: number
  stdout: string
  stderr: string
  duration_ms: number
  created_at: string
}

export interface BenchmarkMetrics {
  ttft_p50_ms?: number
  ttft_p99_ms?: number
  tpot_p50_ms?: number
  tpot_p99_ms?: number
  throughput_tok_s?: number
  kv_cache_hit_rate_pct?: number
  offload_bw_gbps?: number
  effective_pool_capacity_gb?: number
  [key: string]: unknown
}

export interface BenchmarkRun {
  id: string
  campaign_id: string
  server_id: string
  variant_label: string
  offload_target: string
  command: string
  git_hash: string | null
  pre_snapshot_id: string | null
  post_snapshot_id: string | null
  metrics: BenchmarkMetrics
  raw_log_path: string | null
  status: string
  executed_by: string
  started_at: string
  finished_at: string | null
}

export interface BenchmarkReport {
  id: string
  campaign_id: string
  room_id: string
  title: string
  author_agent_id: string
  summary_markdown: string
  comparison: {
    highlights?: Record<string, string>
    run_count?: number
    commit_hashes?: string[]
  }
  created_at: string
}

export interface BootstrapResponse {
  identity: { actor_id: string; auth_mode: string }
  users: UserProfile[]
  agents: AgentInfo[]
  rooms: RoomInfo[]
  servers: ServerInfo[]
  latest_snapshots: Record<string, EnvSnapshot | null>
  head_git_hash: string | null
  commits: ConfigCommit[]
  campaigns: ScenarioCampaign[]
  runs: BenchmarkRun[]
  reports: BenchmarkReport[]
  ssh_logs: SshAuditLog[]
}
