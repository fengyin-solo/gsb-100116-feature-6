<template>
  <section class="page sandbox" data-module="sandbox">
    <header class="page-head">
      <div>
        <h2>授权沙盘</h2>
        <p class="page-desc">
          拖动养护单位到组织或单桥节点，即时推演定检记录、工程图纸、联系人的联动开放；
          上级组织默认覆盖下级，单桥声明以《资产移交协议》为准。
        </p>
      </div>
      <div class="page-actions">
        <span class="version-badge" :class="{ stale: versionStale }">
          沙盘版本 v{{ state.version }}<template v-if="previewVersion !== null"> · 推演中</template>
        </span>
        <button class="btn" type="button" @click="reload">刷新沙盘</button>
        <button class="btn ghost" type="button" @click="onReset">重置沙盘</button>
      </div>
    </header>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">权限树节点</span>
        <strong class="stat-value">{{ state.stats.tree_nodes }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">授权声明</span>
        <strong class="stat-value">{{ state.stats.grants }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">开放边缘 / 总边缘</span>
        <strong class="stat-value">{{ activeStats.edge_open }} / {{ activeStats.edge_total }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">单桥协议声明</span>
        <strong class="stat-value">{{ state.stats.single_declarations }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">待联动共享待办</span>
        <strong class="stat-value">{{ state.stats.pending_todos }}</strong>
      </article>
    </div>

    <div class="sandbox-body">
      <!-- 左：养护单位（拖动源） -->
      <aside class="panel unit-panel">
        <h3>养护单位</h3>
        <p class="panel-tip">拖到中间权限树的任意节点授权</p>
        <div
          v-for="unit in state.grantees"
          :key="unit.code"
          class="unit-chip"
          draggable="true"
          @dragstart="onDragUnit($event, unit.code)"
        >
          <span class="unit-name">{{ unit.name }}</span>
          <span class="unit-code">{{ unit.code }}</span>
        </div>
      </aside>

      <!-- 中：权限树（放置目标） -->
      <div class="panel tree-panel">
        <h3>权限树 · 授权落点</h3>
        <div v-for="node in treeRows" :key="node.id">
          <div
            class="tree-node"
            :class="{
              org: node.type === 'org',
              bridge: node.type === 'bridge',
              selected: draft && draft.node_id === node.id,
              dragover: dropTarget === node.id,
            }"
            :style="{ marginLeft: node.depth * 18 + 'px' }"
            @dragover.prevent="dropTarget = node.id"
            @dragleave="dropTarget = ''"
            @drop.prevent="onDropNode($event, node)"
            @click="selectNode(node)"
          >
            <span class="node-icon">{{ node.type === 'org' ? '🏢' : '🌉' }}</span>
            <span class="node-name">{{ node.name }}</span>
            <span v-if="node.bridge_code" class="node-code">{{ node.bridge_code }}</span>
            <span class="node-grants">
              <em
                v-for="g in grantsOnNode(node.id)"
                :key="g.id"
                class="grant-pill"
                :class="{ agreement: !!g.agreement, closed: !g.open }"
                :title="grantTitle(g)"
              >
                {{ granteeName(g.grantee) }}<small v-if="g.agreement">📜{{ g.agreement }}</small>
                <button class="pill-x" type="button" @click.stop="markRemove(g.id)">×</button>
              </em>
            </span>
          </div>
        </div>

        <!-- 授权草稿 -->
        <div v-if="draft" class="draft-card">
          <h4>授权决定（草稿）</h4>
          <div class="draft-row">
            <span>落点：</span><strong>{{ draftNodeName }}</strong>
            <span class="draft-kind">{{ draftNodeType === 'bridge' ? '单桥声明' : '组织授权（向下继承）' }}</span>
          </div>
          <div class="draft-row">
            <span>养护单位：</span><strong>{{ granteeName(draft.grantee) }}</strong>
          </div>
          <div class="draft-row">
            <span>开放资源：</span>
            <label v-for="r in RESOURCE_TYPES" :key="r" class="res-check">
              <input type="checkbox" :value="r" v-model="draft.resources" />
              {{ RESOURCE_LABELS[r] }}
            </label>
          </div>
          <div v-if="draftNodeType === 'bridge'" class="draft-row">
            <span>资产移交协议：</span>
            <input v-model="draft.agreement" placeholder="单桥声明必填，如 XY-2026-0001" />
          </div>
          <div class="draft-row">
            <span>备注：</span>
            <input v-model="draft.remark" placeholder="选填" />
          </div>
          <p v-if="draftError" class="error-text">{{ draftError }}</p>
          <div class="draft-actions">
            <button class="btn ghost" type="button" @click="cancelDraft">取消</button>
            <button class="btn" type="button" @click="stageDraft">加入提交清单</button>
          </div>
        </div>

        <!-- 提交清单 -->
        <div v-if="pendingChanges.length || pendingRemovals.length" class="staged-card">
          <h4>本次提交清单（v{{ state.version }}）</h4>
          <ul>
            <li v-for="(c, i) in pendingChanges" :key="'c' + i">
              {{ granteeName(c.grantee) }} → {{ nodeName(c.node_id) }}
              <small v-if="c.agreement">📜{{ c.agreement }}</small>
              （{{ c.resources.map((r) => RESOURCE_LABELS[r]).join('、') }}）
              <button class="link" type="button" @click="pendingChanges.splice(i, 1)">移除</button>
            </li>
            <li v-for="(rid, i) in pendingRemovals" :key="'r' + rid" class="remove-item">
              撤销授权 {{ rid }}
              <button class="link" type="button" @click="undoRemove(rid)">还原</button>
            </li>
          </ul>
          <div class="commit-bar">
            <input v-model="operator" class="operator-input" placeholder="操作人" />
            <input v-model="commitRemark" class="remark-input" placeholder="提交说明" />
            <button class="btn primary" type="button" :disabled="submitting" @click="onCommit">
              {{ submitting ? '提交中…' : `按版本 v${state.version} 串行提交` }}
            </button>
          </div>
        </div>
      </div>

      <!-- 右：边缘权限与联动资源 -->
      <aside class="panel edge-panel">
        <div class="edge-tabs">
          <button :class="{ active: edgeTab === 'preview' }" type="button" @click="edgeTab = 'preview'">
            联动推演{{ previewVersion !== null ? '●' : '' }}
          </button>
          <button :class="{ active: edgeTab === 'committed' }" type="button" @click="edgeTab = 'committed'">
            当前生效
          </button>
        </div>
        <p v-if="edgeTab === 'preview'" class="panel-tip">
          {{ previewVersion !== null ? `基于提交清单推演（基线 v${previewVersion}）` : '把决定加入提交清单后，此处即时联动' }}
        </p>
        <div class="edge-list">
          <div
            v-for="edge in shownEdges"
            :key="edge.grantee + edge.bridge_code"
            class="edge-item"
            :class="{ open: edge.open, closed: !edge.open }"
          >
            <div class="edge-head" @click="toggleEdge(edge.grantee + edge.bridge_code)">
              <span class="edge-dot">{{ edge.open ? '🟢' : '⚪' }}</span>
              <span class="edge-unit">{{ edge.grantee_name }}</span>
              <span class="edge-arrow">→</span>
              <span class="edge-bridge">{{ edge.bridge_code }}</span>
              <span class="edge-basis" :class="edge.basis">{{ basisLabel(edge.basis) }}</span>
            </div>
            <div class="edge-res">{{ edge.open ? edge.resource_labels.join('、') : '未开放' }}</div>
            <div class="edge-reason">{{ edge.reason }}</div>
            <div v-if="expandedEdges.has(edge.grantee + edge.bridge_code) && edge.open && edge.linked" class="linked-box">
              <div v-for="r in RESOURCE_TYPES" :key="r" class="linked-group">
                <h5>{{ RESOURCE_LABELS[r] }}（{{ edge.linked[r]?.length ?? 0 }}）</h5>
                <ul>
                  <li v-for="res in edge.linked[r]" :key="res.编号">{{ res.标题 }}</li>
                </ul>
                <span v-if="!edge.linked[r]?.length" class="muted">无记录</span>
              </div>
            </div>
          </div>
        </div>
      </aside>
    </div>

    <!-- 底部：共享待办 / 历史快照 / 越权核验 -->
    <div class="bottom-tabs">
      <button :class="{ active: bottomTab === 'todo' }" type="button" @click="switchBottom('todo')">
        共享待办（{{ todos.length }}）
      </button>
      <button :class="{ active: bottomTab === 'snapshot' }" type="button" @click="switchBottom('snapshot')">
        历史授权快照（{{ snapshots.length }}）
      </button>
      <button :class="{ active: bottomTab === 'access' }" type="button" @click="switchBottom('access')">
        越权读取核验
      </button>
    </div>

    <div v-if="bottomTab === 'todo'" class="panel bottom-panel">
      <table class="data-table">
        <thead>
          <tr><th>事项编号</th><th>类型</th><th>桥梁</th><th>养护单位</th><th>事项内容</th><th>版本</th><th>经办人</th><th>状态</th></tr>
        </thead>
        <tbody>
          <tr v-for="t in todos" :key="t.id" :class="{ done: t.状态 !== '待联动' }">
            <td>{{ t.事项编号 }}</td><td>{{ t.事项类型 }}</td><td>{{ t.桥梁编号 }}</td>
            <td>{{ t.养护单位 }}</td><td>{{ t.事项内容 }}</td><td>v{{ t.授权版本 }}</td>
            <td>{{ t.经办人 }}</td><td>{{ t.状态 }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="bottomTab === 'snapshot'" class="panel bottom-panel">
      <div v-for="s in snapshots" :key="s.version" class="snapshot-card">
        <h4>v{{ s.version }} · {{ s.remark }} <small>{{ s.operator }} · {{ s.created_at }}</small></h4>
        <p class="muted">授权 {{ s.grants.length }} 条，开放边缘 {{ s.edges.filter((e) => e.open).length }}/{{ s.edges.length }}</p>
        <details>
          <summary>查看边缘权限快照</summary>
          <ul class="snap-edges">
            <li v-for="e in s.edges.filter((x) => x.open)" :key="e.grantee + e.bridge_code">
              {{ e.grantee_name }} → {{ e.bridge_code }}（{{ e.resource_labels.join('、') }}）· {{ basisLabel(e.basis) }}
            </li>
          </ul>
        </details>
      </div>
    </div>

    <div v-if="bottomTab === 'access'" class="panel bottom-panel access-panel">
      <div class="access-form">
        <label>
          养护单位
          <select v-model="accessGrantee">
            <option v-for="u in state.grantees" :key="u.code" :value="u.code">{{ u.name }}</option>
          </select>
        </label>
        <label>
          单桥
          <select v-model="accessBridge">
            <option v-for="b in bridgeNodes" :key="b.bridge_code ?? b.id" :value="b.bridge_code ?? ''">
              {{ b.bridge_code }} {{ b.name }}
            </option>
          </select>
        </label>
        <label>
          资源（留空=全部）
          <select v-model="accessResourceType">
            <option :value="null">全部</option>
            <option value="inspection">定检记录</option>
            <option value="drawing">工程图纸</option>
            <option value="contact">联系人</option>
          </select>
        </label>
        <button class="btn primary" type="button" @click="onAccess">发起读取</button>
      </div>
      <div v-if="accessResponse" class="access-ok">
        <strong>读取通过：</strong>{{ accessResponse.grantee_name }} 可访问 {{ accessResponse.bridge_code }}
        <div v-for="r in RESOURCE_TYPES" :key="r" class="linked-group">
          <h5>{{ RESOURCE_LABELS[r] }}（{{ accessResponse.resources[r]?.length ?? 0 }}）</h5>
          <ul><li v-for="res in accessResponse.resources[r]" :key="res.编号">{{ res.标题 }}</li></ul>
        </div>
      </div>
      <div v-else-if="accessDenied" class="access-denied">⛔ {{ accessDenied }}</div>
    </div>

    <footer class="page-foot">
      <span>权限树与访问缓存同事务转换 · 提交按版本号串行 · 越权读取一律拒绝</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import {
  RESOURCE_LABELS,
  RESOURCE_TYPES,
  accessResource,
  commitSandbox,
  fetchSnapshots,
  fetchState,
  fetchTodos,
  preview,
  resetSandbox,
  type Edge,
  type Grant,
  type GrantChange,
  type SandboxNode,
  type SandboxState,
  type SandboxStats,
  type Snapshot,
  type Todo,
} from '@/api/sandbox'

interface Draft {
  node_id: string
  grantee: string
  open: boolean
  resources: string[]
  agreement: string
  remark: string
}

const emptyStats: SandboxStats = {
  tree_nodes: 0, grants: 0, edge_total: 0, edge_open: 0, edge_closed: 0,
  single_declarations: 0, pending_todos: 0,
}
const emptyState: SandboxState = {
  version: 0, nodes: [], grantees: [], grants: [], edges: [], stats: emptyStats,
}

const state = ref<SandboxState>({ ...emptyState })
const todos = ref<Todo[]>([])
const snapshots = ref<Snapshot[]>([])

const dropTarget = ref('')
const draft = ref<Draft | null>(null)
const draftError = ref('')
const pendingChanges = ref<GrantChange[]>([])
const pendingRemovals = ref<string[]>([])
const removedGrants = ref<Grant[]>([])

const operator = ref('值班管理员')
const commitRemark = ref('')
const submitting = ref(false)
const errorMessage = ref('')

const edgeTab = ref<'preview' | 'committed'>('committed')
const bottomTab = ref<'todo' | 'snapshot' | 'access'>('todo')
const expandedEdges = ref<Set<string>>(new Set())

const previewEdges = ref<Edge[]>([])
const previewVersion = ref<number | null>(null)
const versionStale = ref(false)

const accessGrantee = ref('')
const accessBridge = ref('')
const accessResourceType = ref<string | null>(null)
const accessResponse = ref<Awaited<ReturnType<typeof accessResource>> | null>(null)
const accessDenied = ref('')

const bridgeNodes = computed(() => state.value.nodes.filter((n) => n.type === 'bridge'))

const treeRows = computed(() => {
  const byParent = new Map<string | null, SandboxNode[]>()
  for (const n of state.value.nodes) {
    const list = byParent.get(n.parent_id) ?? []
    list.push(n)
    byParent.set(n.parent_id, list)
  }
  const rows: Array<SandboxNode & { depth: number }> = []
  const walk = (parent: string | null, depth: number) => {
    for (const n of byParent.get(parent) ?? []) {
      rows.push({ ...n, depth })
      walk(n.id, depth + 1)
    }
  }
  walk(null, 0)
  return rows
})

const activeStats = computed<SandboxStats>(() =>
  previewVersion.value !== null && previewEdges.value.length
    ? { ...state.value.stats, edge_open: previewEdges.value.filter((e) => e.open).length }
    : state.value.stats,
)

const shownEdges = computed(() =>
  edgeTab.value === 'preview' && previewVersion.value !== null ? previewEdges.value : state.value.edges,
)

const draftNode = computed(() => state.value.nodes.find((n) => n.id === draft.value?.node_id) ?? null)
const draftNodeName = computed(() => draftNode.value?.name ?? '')
const draftNodeType = computed(() => draftNode.value?.type ?? 'org')

function granteeName(code: string): string {
  return state.value.grantees.find((g) => g.code === code)?.name ?? code
}
function nodeName(id: string): string {
  return state.value.nodes.find((n) => n.id === id)?.name ?? id
}
function grantsOnNode(nodeId: string): Grant[] {
  return state.value.grants.filter((g) => g.node_id === nodeId && !pendingRemovals.value.includes(g.id))
}
function grantTitle(g: Grant): string {
  return `${granteeName(g.grantee)}：${g.resources.map((r) => RESOURCE_LABELS[r]).join('、')}${g.agreement ? '｜' + g.agreement : ''}`
}
function basisLabel(basis: string): string {
  return basis === 'single' ? '单桥协议' : basis === 'org' ? '组织继承' : '无授权'
}

// ---- 拖拽：养护单位 → 权限树节点 ----
function onDragUnit(event: DragEvent, code: string) {
  event.dataTransfer?.setData('text/grantee', code)
  event.dataTransfer?.setData('text/plain', code)
}

function onDropNode(event: DragEvent, node: SandboxNode) {
  dropTarget.value = ''
  const code = event.dataTransfer?.getData('text/grantee') || event.dataTransfer?.getData('text/plain')
  if (!code) return
  beginDraft(node, code)
}

function selectNode(node: SandboxNode) {
  if (state.value.grantees.length && !draft.value) {
    beginDraft(node, state.value.grantees[0].code)
  }
}

function beginDraft(node: SandboxNode, grantee: string) {
  const existing = pendingChanges.value.find((c) => c.node_id === node.id && c.grantee === grantee)
  draft.value = {
    node_id: node.id,
    grantee,
    open: true,
    resources: existing ? [...existing.resources] : [...RESOURCE_TYPES],
    agreement: existing?.agreement ?? (node.type === 'bridge' ? '' : ''),
    remark: existing?.remark ?? '',
  }
  draftError.value = ''
}

function cancelDraft() {
  draft.value = null
  draftError.value = ''
}

function stageDraft() {
  if (!draft.value) return
  const d = draft.value
  if (!d.resources.length) {
    draftError.value = '至少勾选一类开放资源'
    return
  }
  if (draftNodeType.value === 'bridge' && !d.agreement.trim()) {
    draftError.value = '单桥声明必须填写《资产移交协议》编号，否则核验不会通过'
    return
  }
  if (draftNodeType.value === 'org' && d.agreement.trim()) {
    draftError.value = '组织授权不得登记资产移交协议'
    return
  }
  const change: GrantChange = {
    node_id: d.node_id,
    grantee: d.grantee,
    open: d.open,
    resources: [...d.resources],
    agreement: draftNodeType.value === 'bridge' ? d.agreement.trim() : null,
    remark: d.remark.trim() || null,
  }
  const idx = pendingChanges.value.findIndex((c) => c.node_id === change.node_id && c.grantee === change.grantee)
  if (idx >= 0) pendingChanges.value.splice(idx, 1, change)
  else pendingChanges.value.push(change)
  draft.value = null
  draftError.value = ''
}

function markRemove(grantId: string) {
  const g = state.value.grants.find((x) => x.id === grantId)
  if (g && !pendingRemovals.value.includes(grantId)) {
    pendingRemovals.value.push(grantId)
    removedGrants.value.push(g)
  }
}
function undoRemove(grantId: string) {
  pendingRemovals.value = pendingRemovals.value.filter((id) => id !== grantId)
  removedGrants.value = removedGrants.value.filter((g) => g.id !== grantId)
}

function toggleEdge(key: string) {
  if (expandedEdges.value.has(key)) expandedEdges.value.delete(key)
  else expandedEdges.value.add(key)
}

// ---- 推演：提交清单变化时调用 preview，即时联动 ----
let previewTimer: ReturnType<typeof setTimeout> | undefined
watch([pendingChanges, pendingRemovals], () => {
  clearTimeout(previewTimer)
  if (!pendingChanges.value.length && !pendingRemovals.value.length) {
    previewEdges.value = []
    previewVersion.value = null
    return
  }
  previewTimer = setTimeout(async () => {
    try {
      const result = await preview(pendingChanges.value, pendingRemovals.value)
      previewEdges.value = result.edges
      previewVersion.value = result.base_version
      versionStale.value = false
    } catch (error) {
      // 推演阶段的核验错误（如缺协议）直接提示，不落任何决定
      draftError.value = error instanceof Error ? error.message : '推演失败'
    }
  }, 250)
}, { deep: true })

// ---- 提交：版本号串行；过期则要求刷新 ----
async function onCommit() {
  submitting.value = true
  errorMessage.value = ''
  try {
    const result = await commitSandbox({
      base_version: state.value.version,
      changes: pendingChanges.value,
      removals: pendingRemovals.value,
      operator: operator.value || '值班管理员',
      remark: commitRemark.value || null,
    })
    applyState(result)
    pendingChanges.value = []
    pendingRemovals.value = []
    removedGrants.value = []
    previewEdges.value = []
    previewVersion.value = null
    commitRemark.value = ''
    await loadAux()
  } catch (error) {
    const err = error as Error & { status?: number }
    if (err.status === 409) {
      versionStale.value = true
      errorMessage.value = `${err.message}——已阻止旧页面提交过期决定，请刷新沙盘后重做`
    } else {
      errorMessage.value = err.message || '提交失败'
    }
  } finally {
    submitting.value = false
  }
}

async function onReset() {
  if (!window.confirm('确认把授权沙盘恢复到初始状态？')) return
  const result = await resetSandbox()
  applyState(result)
  pendingChanges.value = []
  pendingRemovals.value = []
  previewEdges.value = []
  previewVersion.value = null
  await loadAux()
}

async function reload() {
  const data = await fetchState()
  applyState(data)
  versionStale.value = false
}

function applyState(data: SandboxState) {
  state.value = data
  if (!accessGrantee.value && data.grantees[0]) accessGrantee.value = data.grantees[0].code
  if (!accessBridge.value && data.nodes.find((n) => n.type === 'bridge')?.bridge_code) {
    accessBridge.value = data.nodes.find((n) => n.type === 'bridge')!.bridge_code!
  }
}

async function loadAux() {
  const [todoData, snapData] = await Promise.all([fetchTodos(), fetchSnapshots()])
  todos.value = todoData.items
  snapshots.value = snapData.items
}

function switchBottom(tab: 'todo' | 'snapshot' | 'access') {
  bottomTab.value = tab
}

async function onAccess() {
  accessResponse.value = null
  accessDenied.value = ''
  try {
    accessResponse.value = await accessResource({
      grantee: accessGrantee.value,
      bridge_code: accessBridge.value,
      resource: accessResourceType.value,
    })
  } catch (error) {
    accessDenied.value = error instanceof Error ? error.message : '读取被拒绝'
  }
}

onMounted(async () => {
  await reload()
  await loadAux()
})
</script>

<style scoped>
.sandbox { display: flex; flex-direction: column; }
.page-actions { display: flex; align-items: center; gap: 8px; }
.version-badge { font-size: 12px; background: #eef4ff; border: 1px solid #b9d2ff; color: #1f6feb; border-radius: 999px; padding: 4px 10px; }
.version-badge.stale { background: #fff1f0; border-color: #ffa39e; color: #cf1322; }

.sandbox-body { display: grid; grid-template-columns: 200px 1fr 380px; gap: 12px; align-items: start; }
.panel { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 12px; }
.panel h3 { margin: 0 0 8px; font-size: 14px; }
.panel-tip { font-size: 12px; color: var(--muted); margin: 0 0 8px; }

.unit-panel { display: flex; flex-direction: column; gap: 8px; }
.unit-chip { border: 1px solid var(--border); border-radius: 6px; padding: 8px 10px; cursor: grab; background: #fafcff; display: flex; flex-direction: column; gap: 2px; }
.unit-chip:active { cursor: grabbing; }
.unit-chip:hover { border-color: var(--brand); }
.unit-name { font-size: 13px; font-weight: 600; }
.unit-code { font-size: 11px; color: var(--muted); }

.tree-node { display: flex; align-items: center; gap: 6px; padding: 7px 8px; border: 1px dashed transparent; border-radius: 6px; margin: 2px 0; font-size: 13px; }
.tree-node.org { background: #f6f8fb; }
.tree-node.bridge { background: #fff; }
.tree-node:hover { border-color: var(--border); }
.tree-node.selected, .tree-node.dragover { border-color: var(--brand); background: #eef4ff; }
.node-icon { font-size: 14px; }
.node-code { font-size: 11px; color: var(--muted); }
.node-grants { margin-left: auto; display: flex; gap: 4px; flex-wrap: wrap; justify-content: flex-end; }
.grant-pill { display: inline-flex; align-items: center; gap: 4px; font-style: normal; font-size: 11px; background: #e6f4ea; color: #1a7f37; border-radius: 999px; padding: 2px 6px; }
.grant-pill.agreement { background: #fff3d6; color: #ad6800; }
.grant-pill.closed { background: #f0f0f0; color: #999; }
.pill-x { border: none; background: none; cursor: pointer; color: inherit; padding: 0 2px; font-size: 12px; line-height: 1; }

.draft-card, .staged-card { margin-top: 12px; border: 1px solid var(--border); border-radius: 8px; padding: 10px; background: #fcfdff; }
.draft-card h4, .staged-card h4 { margin: 0 0 8px; font-size: 13px; }
.draft-row { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; font-size: 13px; flex-wrap: wrap; }
.draft-row input[type='text'], .draft-row input:not([type]), .draft-row input { flex: 1; min-width: 160px; padding: 4px 8px; border: 1px solid var(--border); border-radius: 6px; }
.draft-kind { font-size: 11px; color: var(--brand); }
.res-check { font-size: 12px; display: inline-flex; align-items: center; gap: 3px; }
.draft-actions, .commit-bar { display: flex; gap: 8px; justify-content: flex-end; margin-top: 6px; flex-wrap: wrap; }
.staged-card ul { margin: 0; padding-left: 16px; font-size: 13px; display: flex; flex-direction: column; gap: 4px; }
.staged-card .remove-item { color: #b42318; }
.operator-input, .remark-input { padding: 5px 8px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; }
.operator-input { width: 110px; }
.remark-input { width: 180px; }

.edge-tabs, .bottom-tabs { display: flex; gap: 6px; margin-bottom: 8px; }
.edge-tabs button, .bottom-tabs button { border: 1px solid var(--border); background: #f6f8fb; border-radius: 6px 6px 0 0; padding: 6px 12px; cursor: pointer; font-size: 13px; }
.edge-tabs button.active, .bottom-tabs button.active { background: var(--brand); color: #fff; border-color: var(--brand); }
.edge-list { max-height: 520px; overflow: auto; display: flex; flex-direction: column; gap: 6px; }
.edge-item { border: 1px solid var(--border); border-radius: 6px; padding: 8px; font-size: 12px; }
.edge-item.open { border-left: 3px solid #1a7f37; }
.edge-item.closed { border-left: 3px solid #cbd5e1; opacity: 0.85; }
.edge-head { display: flex; align-items: center; gap: 6px; cursor: pointer; }
.edge-unit { font-weight: 600; }
.edge-arrow { color: var(--muted); }
.edge-basis { margin-left: auto; font-size: 11px; padding: 1px 6px; border-radius: 999px; }
.edge-basis.single { background: #fff3d6; color: #ad6800; }
.edge-basis.org { background: #e6f4ea; color: #1a7f37; }
.edge-basis.none { background: #f0f0f0; color: #999; }
.edge-res { color: #1a7f37; margin: 3px 0; }
.edge-item.closed .edge-res { color: var(--muted); }
.edge-reason { color: var(--muted); font-size: 11px; }
.linked-box { margin-top: 6px; border-top: 1px dashed var(--border); padding-top: 6px; }
.linked-group h5 { margin: 4px 0 2px; font-size: 12px; }
.linked-group ul { margin: 0; padding-left: 16px; }
.muted { color: var(--muted); font-size: 12px; }

.bottom-tabs { margin-top: 14px; }
.bottom-panel { border-radius: 0 8px 8px; }
.bottom-panel table .done { color: #999; }
.snapshot-card { border-bottom: 1px solid var(--border); padding: 8px 0; }
.snapshot-card h4 { margin: 0; font-size: 13px; }
.snapshot-card small { color: var(--muted); font-weight: 400; }
.snap-edges { font-size: 12px; columns: 2; margin: 4px 0; }

.access-form { display: flex; gap: 10px; align-items: flex-end; flex-wrap: wrap; }
.access-form label { font-size: 12px; color: var(--muted); display: flex; flex-direction: column; gap: 4px; }
.access-form select { padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; min-width: 160px; }
.access-ok { margin-top: 12px; font-size: 13px; }
.access-denied { margin-top: 12px; background: #fff1f0; border: 1px solid #ffa39e; color: #cf1322; border-radius: 6px; padding: 10px; font-size: 13px; }
</style>
