/** 授权沙盘接口封装：拖拽推演、版本号串行提交、越权读取核验。 */
import { request } from '@/api/client'

export interface SandboxNode {
  id: string
  type: 'org' | 'bridge'
  name: string
  parent_id: string | null
  bridge_code: string | null
  unit_code: string | null
}

export interface Grantee {
  code: string
  name: string
  node_id: string
}

export interface LinkedResource {
  resource_type: 'inspection' | 'drawing' | 'contact'
  编号: string
  标题: string
  [key: string]: string | undefined
}

export interface Edge {
  grantee: string
  grantee_name: string
  bridge_code: string
  bridge_name: string
  open: boolean
  resources: string[]
  resource_labels: string[]
  basis: 'single' | 'org' | 'none'
  grant_id: string | null
  agreement: string | null
  source_node_id: string | null
  source_node_name: string | null
  reason: string
  linked?: Record<string, LinkedResource[]>
}

export interface Grant {
  id: string
  node_id: string
  grantee: string
  open: boolean
  resources: string[]
  agreement: string | null
  remark: string | null
  operator?: string
  created_at?: string
}

export interface SandboxStats {
  tree_nodes: number
  grants: number
  edge_total: number
  edge_open: number
  edge_closed: number
  single_declarations: number
  pending_todos: number
}

export interface SandboxState {
  version: number
  nodes: SandboxNode[]
  grantees: Grantee[]
  grants: Grant[]
  edges: Edge[]
  stats: SandboxStats
}

export interface GrantChange {
  node_id: string
  grantee: string
  open: boolean
  resources: string[]
  agreement?: string | null
  remark?: string | null
}

export interface Todo {
  id: number
  事项编号: string
  事项类型: string
  桥梁编号: string
  桥梁名称: string
  养护单位编码: string
  养护单位: string
  事项内容: string
  授权版本: number
  经办人: string
  下发时间: string
  状态: string
}

export interface Snapshot {
  version: number
  operator: string
  remark: string
  created_at: string
  grants: Grant[]
  edges: Edge[]
}

export const RESOURCE_TYPES = ['inspection', 'drawing', 'contact'] as const
export const RESOURCE_LABELS: Record<string, string> = {
  inspection: '定检记录',
  drawing: '工程图纸',
  contact: '联系人',
}

async function jsonOrThrow<T>(response: Response): Promise<T> {
  const body = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = (body as { detail?: string }).detail ?? `请求失败（${response.status}）`
    const error = new Error(detail) as Error & { status?: number }
    error.status = response.status
    throw error
  }
  return body as T
}

export function fetchState(): Promise<SandboxState> {
  return request('/api/sandbox/state').then(jsonOrThrow<SandboxState>)
}

export function fetchTodos(): Promise<{ version: number; items: Todo[] }> {
  return request('/api/sandbox/todos').then((r) => jsonOrThrow<{ version: number; items: Todo[] }>(r))
}

export function fetchSnapshots(): Promise<{ version: number; items: Snapshot[] }> {
  return request('/api/sandbox/snapshots').then((r) => jsonOrThrow<{ version: number; items: Snapshot[] }>(r))
}

export function preview(
  changes: GrantChange[],
  removals: string[],
): Promise<{ base_version: number; edges: Edge[]; stats: SandboxStats }> {
  return request('/api/sandbox/preview', {
    method: 'POST',
    body: JSON.stringify({ changes, removals }),
  }).then((r) => jsonOrThrow<{ base_version: number; edges: Edge[]; stats: SandboxStats }>(r))
}

export function commitSandbox(payload: {
  base_version: number
  changes: GrantChange[]
  removals: string[]
  operator: string
  remark: string | null
}): Promise<SandboxState> {
  return request('/api/sandbox/commit', {
    method: 'POST',
    body: JSON.stringify(payload),
  }).then((r) => jsonOrThrow<SandboxState>(r))
}

export function accessResource(payload: {
  grantee: string
  bridge_code: string
  resource?: string | null
}): Promise<{
  ok: boolean
  grantee_name: string
  bridge_code: string
  resources: Record<string, LinkedResource[]>
}> {
  return request('/api/sandbox/access', {
    method: 'POST',
    body: JSON.stringify(payload),
  }).then((r) =>
    jsonOrThrow<{
      ok: boolean
      grantee_name: string
      bridge_code: string
      resources: Record<string, LinkedResource[]>
    }>(r),
  )
}

export function resetSandbox(): Promise<SandboxState> {
  return request('/api/sandbox/reset', { method: 'POST' }).then(jsonOrThrow<SandboxState>)
}
