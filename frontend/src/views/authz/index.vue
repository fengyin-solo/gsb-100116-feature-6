<template>
  <section class="page sandbox" data-module="authz">
    <header class="page-head">
      <div>
        <h2>授权沙盘</h2>
        <p class="page-desc">
          把养护单位或单桥节点拖到对方身上即可调整授权：定检记录、工程图纸、联系人会联动预览开放效果；
          提交后核验结论写入桥梁台账、定检清单与共享待办，并按节点重算边缘权限。
        </p>
      </div>
      <div class="page-actions">
        <span class="version-badge" :class="{ stale: stale }">
          授权树版本 v{{ version }}<template v-if="staging.length"> · 页面基线 v{{ baseVersion }}</template>
        </span>
        <button class="btn" type="button" @click="loadSandbox">刷新沙盘</button>
        <button
          class="btn primary"
          type="button"
          :disabled="!changeCount || submitting || stale"
          @click="commitChanges"
        >
          提交授权决定（{{ changeCount }}）
        </button>
      </div>
    </header>

    <div v-if="stale" class="stale-banner">
      授权树已被其他人提交到 v{{ version }}，本页暂存基于旧版本，不能再提交。请
      <button class="link" type="button" @click="rebaseStaging">刷新并保留暂存</button>
      重新核对后提交。
    </div>
    <div v-if="message" class="message-bar" :class="messageKind">{{ message }}</div>

    <div class="sandbox-grid">
      <!-- 左：可拖动主体 -->
      <article class="panel">
        <h3>授权主体（可拖动）</h3>
        <p class="panel-hint">把单位拖到中间的组织或单桥节点上，即可为该节点编辑五类资源开关。</p>
        <div
          v-for="s in sandbox?.subjects ?? []"
          :key="s.id"
          class="subject-chip"
          draggable="true"
          @dragstart="onSubjectDragStart($event, s.id)"
          @dragover.prevent="subjectOverId = s.id"
          @dragleave="subjectOverId = ''"
          @drop.prevent="onDropSubject($event, s.id)"
          :class="{ over: subjectOverId === s.id }"
        >
          <span class="chip-kind">{{ s.kind }}</span>
          <span>{{ s.name }}</span>
          <em class="chip-hint">可拖出 / 可接收节点</em>
        </div>

        <h3 class="mt">越权读取核验</h3>
        <div class="tester">
          <label>
            <span>主体</span>
            <select v-model="tester.subjectId">
              <option v-for="s in sandbox?.subjects ?? []" :key="s.id" :value="s.id">{{ s.name }}</option>
            </select>
          </label>
          <label>
            <span>资源</span>
            <select v-model="tester.resource">
              <option value="ledger">桥梁台账</option>
              <option value="inspection">定检记录</option>
              <option value="drawing">工程图纸</option>
              <option value="contact">联系人</option>
              <option value="todo">共享待办</option>
            </select>
          </label>
          <label>
            <span>记录</span>
            <select v-model.number="tester.itemId">
              <option v-for="item in testerItems" :key="item.id" :value="item.id">
                {{ item.label }}
              </option>
            </select>
          </label>
          <button class="btn" type="button" @click="runAccessTest">尝试读取</button>
        </div>
        <div v-if="tester.result" class="tester-result" :class="tester.result.ok ? 'allow' : 'deny'">
          <strong>{{ tester.result.ok ? '✅ 放行' : '⛔ 拒绝' }}</strong>
          <span>{{ tester.result.detail }}</span>
        </div>
      </article>

      <!-- 中：权限树（节点可拖到主体、可接收主体） -->
      <article class="panel tree-panel">
        <h3>权限树：组织 / 单桥节点（可双向拖动）</h3>
        <p class="panel-hint">上级组织默认覆盖下级；单桥声明以资产移交协议为准（协议节点标🔒，沙盘只读）。</p>
        <template v-for="node in sandbox?.tree ?? []" :key="node.id">
          <div class="tree-node">
            <button
              class="node org root"
              draggable="true"
              @dragstart="onNodeDragStart($event, node)"
              @dragover.prevent="dragOverKey = keyOf(node)"
              @dragleave="dragOverKey = ''"
              @drop.prevent="onDropNode($event, node)"
              :class="{ over: dragOverKey === keyOf(node) }"
            >
              🏛 {{ node.name }}
            </button>
            <div class="tree-children">
              <template v-for="child in node.children" :key="child.id">
                <div v-if="child.type === 'org'" class="tree-node">
                  <button
                    class="node org"
                    draggable="true"
                    @dragstart="onNodeDragStart($event, child)"
                    @dragover.prevent="dragOverKey = keyOf(child)"
                    @dragleave="dragOverKey = ''"
                    @drop.prevent="onDropNode($event, child)"
                    :class="{ over: dragOverKey === keyOf(child) }"
                  >
                    🏛 {{ child.name }}
                  </button>
                  <div class="tree-children">
                    <div v-for="leaf in child.children" :key="leaf.id" class="tree-node">
                      <button
                        class="node bridge"
                        draggable="true"
                        @dragstart="onNodeDragStart($event, leaf)"
                        @dragover.prevent="dragOverKey = keyOf(leaf)"
                        @dragleave="dragOverKey = ''"
                        @drop.prevent="onDropNode($event, leaf)"
                        :class="{ over: dragOverKey === keyOf(leaf) }"
                      >
                        🌉 {{ leaf.name }} <em>{{ leaf.bridge_code }}</em>
                      </button>
                    </div>
                  </div>
                </div>
                <div v-else class="tree-node">
                  <button
                    class="node bridge"
                    draggable="true"
                    @dragstart="onNodeDragStart($event, child)"
                    @dragover.prevent="dragOverKey = keyOf(child)"
                    @dragleave="dragOverKey = ''"
                    @drop.prevent="onDropNode($event, child)"
                    :class="{ over: dragOverKey === keyOf(child) }"
                  >
                    🌉 {{ child.name }} <em>{{ child.bridge_code }}</em>
                  </button>
                </div>
              </template>
            </div>
          </div>
        </template>
      </article>

      <!-- 右：暂存决定 -->
      <article class="panel">
        <h3>暂存决定</h3>
        <p class="panel-hint">先试算不落库；提交时按版本号串行，旧页面无法提交过期决定。</p>
        <div v-if="!staging.length" class="empty-inline">从左侧拖一个主体，或从中间拖一个节点开始。</div>
        <div v-for="item in staging" :key="item.changeKey" class="staging-card">
          <div class="staging-head">
            <strong>{{ subjectName(item.subjectId) }}</strong>
            <span class="staging-target">→ {{ nodeLabel(item.nodeType, item.nodeId) }}</span>
            <button class="link danger" type="button" @click="removeStaging(item.changeKey)">移除</button>
          </div>
          <div class="resource-row">
            <button
              v-for="r in resourceOrder"
              :key="r"
              class="res-chip"
              type="button"
              :class="resChipClass(item, r)"
              :disabled="isHandover(item.subjectId, item.nodeType, item.nodeId, r)"
              @click="toggleResource(item, r)"
            >
              {{ resourceLabel(r) }}
              <em v-if="isHandover(item.subjectId, item.nodeType, item.nodeId, r)">🔒协议</em>
            </button>
          </div>
        </div>
      </article>
    </div>

    <!-- 联动预览矩阵 -->
    <article class="panel matrix-panel">
      <h3>联动预览：边权限（按节点实时重算）</h3>
      <p class="panel-hint">
        行＝主体 × 单桥，列＝资源。蓝为开放、灰为关闭、黄边表示该格相对当前版本会发生变化；
        下方小字是判定依据（上级覆盖 / 移交协议 / 单桥授权 / 默认关闭）。
      </p>
      <table class="matrix">
        <thead>
          <tr>
            <th>主体</th><th>桥梁</th>
            <th v-for="r in resourceOrder" :key="r">{{ resourceLabel(r) }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="edge in previewEdges" :key="edge.subjectId + edge.bridgeNodeId">
            <td>{{ subjectName(edge.subjectId) }}</td>
            <td>{{ bridgeLabel(edge.bridgeCode) }}</td>
            <td v-for="r in resourceOrder" :key="r">
              <span
                class="edge-cell"
                :class="[edge.decisions[r] ? 'open' : 'closed', { changed: isChanged(edge, r) }]"
              >
                {{ edge.decisions[r] ? '开放' : '关闭' }}
              </span>
              <small>{{ edge.bases[r] }}</small>
            </td>
          </tr>
        </tbody>
      </table>
    </article>

    <!-- 共享待办 -->
    <article class="panel">
      <h3>共享待办（提交后联动生成）</h3>
      <table class="data-table compact">
        <thead>
          <tr><th>待办编号</th><th>桥梁</th><th>授权主体</th><th>联动内容</th><th>状态</th><th>版本</th><th>核验结论</th></tr>
        </thead>
        <tbody>
          <tr v-for="t in (sandbox?.todos ?? [])" :key="String(t.id)">
            <td>{{ t['待办编号'] }}</td>
            <td>{{ t['桥梁编号'] }}</td>
            <td>{{ t['授权主体'] }}</td>
            <td>{{ t['内容'] }}</td>
            <td>{{ t.status }}</td>
            <td>v{{ t.version }}</td>
            <td class="conclusion">{{ t['授权核验结论'] }}</td>
          </tr>
        </tbody>
      </table>
    </article>

    <!-- 历史授权快照 -->
    <article class="panel">
      <h3>历史授权快照</h3>
      <table class="data-table compact">
        <thead><tr><th>版本</th><th>时间</th><th>操作人</th><th>决定数</th></tr></thead>
        <tbody>
          <tr v-for="s in (sandbox?.snapshots ?? [])" :key="s.version">
            <td>v{{ s.version }}</td>
            <td>{{ s.time }}</td>
            <td>{{ s.actor }}</td>
            <td>{{ (s.changes ?? []).length }} 条</td>
          </tr>
        </tbody>
      </table>
    </article>

    <!-- 越权访问日志 -->
    <article class="panel">
      <h3>最近访问核验日志</h3>
      <table class="data-table compact">
        <thead><tr><th>时间</th><th>主体</th><th>资源</th><th>桥梁</th><th>结论</th><th>依据</th></tr></thead>
        <tbody>
          <tr v-for="(log, i) in (sandbox?.access_log ?? [])" :key="i">
            <td>{{ log.time }}</td>
            <td>{{ subjectName(log.subject_id) }}</td>
            <td>{{ log.resource_label }}</td>
            <td>{{ log.bridge_code ?? '—' }}</td>
            <td :class="log.result === '放行' ? 'allow-text' : 'deny-text'">{{ log.result }}</td>
            <td>{{ log.basis }}</td>
          </tr>
        </tbody>
      </table>
    </article>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'
import { useSessionStore } from '@/stores/session'

type ResourceKey = 'ledger' | 'inspection' | 'drawing' | 'contact' | 'todo'
type NodeType = 'org' | 'bridge'

interface TreeNode {
  id: string
  type: NodeType
  name: string
  bridge_code?: string
  children: TreeNode[]
}
interface Subject { id: string; name: string; kind: string }
interface EdgeView {
  subjectId: string
  bridgeNodeId: string
  bridgeCode: string
  decisions: Record<ResourceKey, boolean>
  bases: Record<ResourceKey, string>
}
interface GrantView {
  subject_id: string
  node_type: NodeType
  node_id: string
  resource: ResourceKey
  decision: boolean
  source: 'manual' | 'handover'
}
interface Sandbox {
  version: number
  tree: TreeNode[]
  subjects: Subject[]
  grants: GrantView[]
  edges: Array<{
    subject_id: string
    bridge_node_id: string
    bridge_code: string
    decisions: Record<ResourceKey, boolean>
    bases: Record<ResourceKey, string>
  }>
  resource_labels: Record<string, string>
  drawings: Array<Record<string, string | number>>
  contacts: Array<Record<string, string | number>>
  todos: Array<Record<string, any>>
  snapshots: Array<Record<string, any>>
  access_log: Array<Record<string, any>>
}

interface StagingItem {
  changeKey: string
  subjectId: string
  nodeType: NodeType
  nodeId: string
  resources: Partial<Record<ResourceKey, boolean>>
}

const session = useSessionStore()
const resourceOrder: ResourceKey[] = ['ledger', 'inspection', 'drawing', 'contact', 'todo']

const sandbox = ref<Sandbox | null>(null)
const version = ref(0)
const baseVersion = ref(0)
const staging = ref<StagingItem[]>([])
const previewEdges = ref<EdgeView[]>([])
const dragOverKey = ref('')
const subjectOverId = ref('')
const submitting = ref(false)
const stale = ref(false)
const message = ref('')
const messageKind = ref<'ok' | 'err'>('ok')
const dragPayload = ref<{ kind: 'subject'; subjectId: string } | { kind: 'node'; nodeType: NodeType; nodeId: string } | null>(null)

const tester = reactive({
  subjectId: '',
  resource: 'ledger' as ResourceKey,
  itemId: 0,
  result: null as null | { ok: boolean; detail: string },
})

const flatNodes = computed(() => {
  const out: Array<TreeNode & { depth: number }> = []
  const walk = (nodes: TreeNode[], depth: number) => {
    for (const n of nodes) {
      out.push({ ...n, depth })
      walk(n.children, depth + 1)
    }
  }
  walk(sandbox.value?.tree ?? [], 0)
  return out
})

const testerItems = computed(() => {
  const s = sandbox.value
  if (!s) return []
  const rowsOf = (table: Record<string, any>[]): Array<Record<string, any>> => table
  switch (tester.resource) {
    case 'drawing':
      return rowsOf(s.drawings).map((r) => ({ id: Number(r.id), label: `${r['桥梁编号']} · ${r['图纸名称']}` }))
    case 'contact':
      return rowsOf(s.contacts).map((r) => ({ id: Number(r.id), label: `${r['桥梁编号']} · ${r['姓名']}（${r['角色']}）` }))
    case 'ledger':
      return [{ id: 1, label: 'BRID-0001 · 桥梁档案样例1' }, { id: 2, label: 'BRID-0002 · 桥梁档案样例2' }, { id: 3, label: 'BRID-0003 · 桥梁档案样例3' }]
    case 'inspection':
      return [{ id: 1, label: 'BRID-0001 · 定检样例1' }, { id: 2, label: 'BRID-0002 · 定检样例2' }, { id: 3, label: 'BRID-0003 · 定检样例3' }]
    case 'todo': {
      const todos = s.todos.filter((t) => t.pending)
      return todos.length
        ? todos.map((t) => ({ id: Number(t.id), label: `${t['桥梁编号']} · ${t['内容']}` }))
        : [{ id: -1, label: '（暂无开放中的共享待办）' }]
    }
  }
})

function resourceLabel(r: ResourceKey) {
  return sandbox.value?.resource_labels[r] ?? r
}
function subjectName(id: string) {
  return sandbox.value?.subjects.find((x) => x.id === id)?.name ?? id
}
function nodeLabel(type: NodeType, id: string) {
  const node = flatNodes.value.find((n) => n.id === id)
  if (!node) return id
  return `${type === 'org' ? '组织' : '单桥'}·${node.name}`
}
function bridgeLabel(code: string) {
  const node = flatNodes.value.find((n) => n.bridge_code === code)
  return node ? `${node.name}（${code}）` : code
}
function keyOf(node: TreeNode) {
  return `${node.type}:${node.id}`
}

function findGrant(subjectId: string, nodeType: NodeType, nodeId: string, resource: ResourceKey): GrantView | undefined {
  return sandbox.value?.grants.find(
    (g) => g.subject_id === subjectId && g.node_type === nodeType && g.node_id === nodeId && g.resource === resource,
  )
}
function isHandover(subjectId: string, nodeType: NodeType, nodeId: string, resource: ResourceKey) {
  return findGrant(subjectId, nodeType, nodeId, resource)?.source === 'handover'
}

function onSubjectDragStart(event: DragEvent, subjectId: string) {
  dragPayload.value = { kind: 'subject', subjectId }
  event.dataTransfer?.setData('text/plain', JSON.stringify(dragPayload.value))
}
function onNodeDragStart(event: DragEvent, node: TreeNode) {
  dragPayload.value = { kind: 'node', nodeType: node.type, nodeId: node.id }
  event.dataTransfer?.setData('text/plain', JSON.stringify(dragPayload.value))
}

function onDropNode(_event: DragEvent, node: TreeNode) {
  dragOverKey.value = ''
  const payload = dragPayload.value
  if (!payload) return
  // 只接收「主体 → 节点」；节点拖节点不构成授权
  if (payload.kind !== 'subject') return
  openStaging(payload.subjectId, node.type, node.id)
}

function onDropSubject(_event: DragEvent, subjectId: string) {
  subjectOverId.value = ''
  const payload = dragPayload.value
  if (!payload) return
  // 「节点 → 主体」：以被拖动的节点和落点主体成单
  if (payload.kind !== 'node') return
  openStaging(subjectId, payload.nodeType, payload.nodeId)
}

function ensureStaging(subjectId: string, nodeType: NodeType, nodeId: string): StagingItem {
  const changeKey = `${subjectId}|${nodeType}|${nodeId}`
  let item = staging.value.find((x) => x.changeKey === changeKey)
  if (!item) {
    const resources: Partial<Record<ResourceKey, boolean>> = {}
    for (const r of resourceOrder) {
      const grant = findGrant(subjectId, nodeType, nodeId, r)
      if (grant && grant.source !== 'handover') resources[r] = grant.decision
    }
    item = { changeKey, subjectId, nodeType, nodeId, resources }
    staging.value.push(item)
  }
  return item
}
function openStaging(subjectId: string, nodeType: NodeType, nodeId: string) {
  ensureStaging(subjectId, nodeType, nodeId)
  void schedulePreview()
}
function removeStaging(changeKey: string) {
  staging.value = staging.value.filter((x) => x.changeKey !== changeKey)
  void schedulePreview()
}
function toggleResource(item: StagingItem, resource: ResourceKey) {
  if (isHandover(item.subjectId, item.nodeType, item.nodeId, resource)) return
  const current = item.resources[resource]
  const grant = findGrant(item.subjectId, item.nodeType, item.nodeId, resource)
  const baseline = grant ? grant.decision : false
  if (current === undefined) {
    item.resources[resource] = !baseline
  } else if (current === !baseline) {
    delete item.resources[resource]
  } else {
    item.resources[resource] = !baseline
  }
  void schedulePreview()
}
function resChipClass(item: StagingItem, r: ResourceKey) {
  const grant = findGrant(item.subjectId, item.nodeType, item.nodeId, r)
  const value = item.resources[r] ?? (grant ? grant.decision : false)
  const changed = item.resources[r] !== undefined
  const handover = grant?.source === 'handover'
  return {
    on: value,
    changed,
    locked: handover,
    off: !value,
  }
}

const changesPayload = computed(() => {
  const out: Array<Record<string, unknown>> = []
  for (const item of staging.value) {
    for (const r of resourceOrder) {
      if (item.resources[r] !== undefined) {
        out.push({
          subject_id: item.subjectId,
          node_type: item.nodeType,
          node_id: item.nodeId,
          resource: r,
          decision: item.resources[r],
        })
      }
    }
  }
  return out
})
const changeCount = computed(() => changesPayload.value.length)

let previewTimer: ReturnType<typeof setTimeout> | undefined
function schedulePreview() {
  if (previewTimer) clearTimeout(previewTimer)
  previewTimer = setTimeout(() => void runPreview(), 120)
}

function currentEdges(): EdgeView[] {
  return (sandbox.value?.edges ?? []).map((e) => ({
    subjectId: e.subject_id,
    bridgeNodeId: e.bridge_node_id,
    bridgeCode: e.bridge_code,
    decisions: { ...e.decisions },
    bases: { ...e.bases },
  }))
}

async function runPreview() {
  if (!changesPayload.value.length) {
    previewEdges.value = currentEdges()
    return
  }
  try {
    const response = await request('/api/authz/preview', {
      method: 'POST',
      body: JSON.stringify({ changes: changesPayload.value }),
    })
    if (response.status === 200) {
      const payload = await response.json()
      previewEdges.value = payload.edges.map((e: any) => ({
        subjectId: e.subject_id,
        bridgeNodeId: e.bridge_node_id,
        bridgeCode: e.bridge_code,
        decisions: e.decisions,
        bases: e.bases,
      }))
    } else {
      const detail = (await response.json().catch(() => ({}))).detail
      flash(typeof detail === 'string' ? detail : '试算失败', 'err')
    }
  } catch (error) {
    flash(error instanceof Error ? error.message : '试算失败', 'err')
  }
}

function isChanged(edge: EdgeView, r: ResourceKey) {
  const base = sandbox.value?.edges.find(
    (e) => e.subject_id === edge.subjectId && e.bridge_node_id === edge.bridgeNodeId,
  )
  return !!base && (base.decisions[r] !== edge.decisions[r] || base.bases[r] !== edge.bases[r])
}

async function commitChanges() {
  if (stale.value) return
  submitting.value = true
  message.value = ''
  try {
    const response = await request('/api/authz/commit', {
      method: 'POST',
      body: JSON.stringify({
        changes: changesPayload.value,
        expected_version: baseVersion.value,
        actor: session.operator,
      }),
    })
    const payload = await response.json().catch(() => ({}))
    if (response.status === 200) {
      flash(payload.message ?? '授权决定已提交', 'ok')
      staging.value = []
      await loadSandbox()
    } else if (response.status === 409) {
      stale.value = true
      version.value = payload.detail?.current_version ?? version.value
      flash(payload.detail?.message ?? payload.detail ?? '版本冲突，请刷新后重新决定', 'err')
    } else {
      flash(payload.detail ?? '提交失败', 'err')
    }
  } catch (error) {
    flash(error instanceof Error ? error.message : '提交失败', 'err')
  } finally {
    submitting.value = false
  }
}

async function loadSandbox() {
  try {
    const response = await request('/api/authz/sandbox')
    if (response.status !== 200) throw new Error('沙盘读取失败')
    const data: Sandbox = await response.json()
    sandbox.value = data
    version.value = data.version
    baseVersion.value = data.version
    stale.value = false
    if (!tester.subjectId && data.subjects[0]) tester.subjectId = data.subjects[0].id
    tester.itemId = 1
    await runPreview()
  } catch (error) {
    flash(error instanceof Error ? error.message : '沙盘读取失败', 'err')
  }
}

async function rebaseStaging() {
  await loadSandbox()
  // 刷新后保留暂存选择，让管理员基于新版本重新核对
  const kept = staging.value.map((x) => ({ ...x, resources: { ...x.resources } }))
  staging.value = kept
  await runPreview()
}

async function runAccessTest() {
  tester.result = null
  if (tester.itemId < 0) {
    tester.result = { ok: false, detail: '没有可核验的记录' }
    return
  }
  const params = new URLSearchParams({
    subject_id: tester.subjectId,
    resource: tester.resource,
    item_id: String(tester.itemId),
  })
  try {
    const response = await request(`/api/authz/access?${params}`)
    const payload = await response.json().catch(() => ({}))
    if (response.status === 200) {
      tester.result = { ok: true, detail: `放行：${payload.basis}` }
    } else {
      tester.result = { ok: false, detail: payload.detail ?? `HTTP ${response.status}` }
    }
  } catch (error) {
    tester.result = { ok: false, detail: error instanceof Error ? error.message : '请求失败' }
  }
  // 拉取最新访问日志
  const fresh = await request('/api/authz/sandbox')
  if (fresh.status === 200) {
    const data = await fresh.json()
    sandbox.value = data
    version.value = data.version
  }
}

function flash(text: string, kind: 'ok' | 'err') {
  message.value = text
  messageKind.value = kind
}

onMounted(loadSandbox)
</script>

<style scoped>
.sandbox-grid {
  display: grid;
  grid-template-columns: 280px 1fr 340px;
  gap: 12px;
  margin-bottom: 12px;
}
.panel {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 14px;
  margin-bottom: 12px;
}
.panel h3 { margin: 0 0 6px; font-size: 14px; }
.panel h3.mt { margin-top: 16px; }
.panel-hint { color: var(--muted); font-size: 12px; margin: 0 0 10px; }
.version-badge {
  font-size: 12px;
  background: #eef4ff;
  border: 1px solid #b9d2ff;
  color: #1d4ed8;
  border-radius: 999px;
  padding: 4px 10px;
}
.version-badge.stale { background: #fef3c7; border-color: #f59e0b; color: #92400e; }
.stale-banner,
.message-bar {
  border-radius: 6px;
  padding: 8px 12px;
  font-size: 13px;
  margin-bottom: 10px;
}
.stale-banner { background: #fffbeb; border: 1px solid #f59e0b; color: #92400e; }
.message-bar.ok { background: #ecfdf3; border: 1px solid #6ce9a6; color: #027a48; }
.message-bar.err { background: #fef3f2; border: 1px solid #fda29b; color: #b42318; }

.subject-chip {
  display: flex;
  align-items: center;
  gap: 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 8px 10px;
  margin-bottom: 8px;
  font-size: 13px;
  cursor: grab;
  background: #f8fafc;
}
.subject-chip:active { cursor: grabbing; }
.subject-chip.over { outline: 2px solid var(--brand); outline-offset: 1px; }
.chip-hint { font-style: normal; margin-left: auto; font-size: 10px; color: var(--muted); }
.chip-kind {
  font-size: 11px;
  background: var(--brand);
  color: #fff;
  border-radius: 4px;
  padding: 1px 6px;
}

.tester { display: grid; gap: 8px; }
.tester label span { display: block; font-size: 12px; color: var(--muted); }
.tester select { width: 100%; padding: 5px 6px; border: 1px solid var(--border); border-radius: 6px; }
.tester-result { margin-top: 8px; font-size: 12px; border-radius: 6px; padding: 8px; display: grid; gap: 4px; }
.tester-result.allow { background: #ecfdf3; color: #027a48; }
.tester-result.deny { background: #fef3f2; color: #b42318; }

.tree-panel { min-height: 300px; }
.tree-children { margin-left: 18px; border-left: 1px dashed var(--border); padding-left: 10px; }
.node {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: #f8fafc;
  padding: 6px 10px;
  margin: 4px 0;
  font-size: 13px;
  cursor: grab;
}
.node.bridge { background: #f5f3ff; border-color: #ddd6fe; }
.node.root { font-weight: 600; }
.node.over { outline: 2px solid var(--brand); outline-offset: 1px; }
.node em { font-style: normal; color: var(--muted); font-size: 11px; }

.empty-inline { color: var(--muted); font-size: 12px; padding: 8px 0; }
.staging-card { border: 1px solid var(--border); border-radius: 6px; padding: 8px 10px; margin-bottom: 8px; }
.staging-head { display: flex; align-items: center; gap: 8px; font-size: 13px; margin-bottom: 6px; }
.staging-target { color: var(--muted); font-size: 12px; flex: 1; }
.link.danger { color: #b42318; }
.resource-row { display: flex; flex-wrap: wrap; gap: 6px; }
.res-chip {
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 3px 10px;
  font-size: 12px;
  cursor: pointer;
  background: #f1f5f9;
  color: #475569;
}
.res-chip.on { background: var(--brand); border-color: var(--brand); color: #fff; }
.res-chip.changed { outline: 2px solid #f59e0b; outline-offset: 1px; }
.res-chip.locked { opacity: .65; cursor: not-allowed; }
.res-chip em { font-style: normal; font-size: 10px; }

.matrix-panel { overflow-x: auto; }
.matrix { width: 100%; border-collapse: collapse; }
.matrix th, .matrix td { border: 1px solid var(--border); padding: 8px; font-size: 12px; text-align: left; vertical-align: top; }
.matrix small { display: block; color: var(--muted); margin-top: 3px; }
.edge-cell { display: inline-block; border-radius: 4px; padding: 2px 8px; font-size: 12px; }
.edge-cell.open { background: #dbeafe; color: #1d4ed8; }
.edge-cell.closed { background: #f1f5f9; color: #64748b; }
.edge-cell.changed { outline: 2px solid #f59e0b; outline-offset: 0; }

.data-table.compact th, .data-table.compact td { font-size: 12px; padding: 6px 8px; }
.data-table .conclusion { color: var(--muted); max-width: 360px; }
.allow-text { color: #027a48; font-weight: 600; }
.deny-text { color: #b42318; font-weight: 600; }
</style>
