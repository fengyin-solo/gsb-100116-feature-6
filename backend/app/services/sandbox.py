"""授权沙盘业务规则。

把"桥梁档案权限"做成一张可以推演的沙盘：

- 权限树 ``sandbox_tree``：组织节点（上级 → 下级）+ 单桥叶子节点；养护单位作为
  被授权主体，可被拖到任意节点上形成一条授权声明。
- 边缘权限：每一条「养护单位 × 单桥」连线都按节点重新计算。组织授权沿祖先链向下
  继承，上级组织默认覆盖下级；单桥声明必须登记《资产移交协议》编号，且效力高于一切
  组织授权。
- 联动开放：边缘一旦开放，定检记录、工程图纸、联系人三类资源一起开放；关闭则一起收回。
- 同事务转换：授权变更、访问缓存重建、写桥梁台账/定检清单/共享待办、落历史授权快照
  在同一把锁、同一组检查点里完成，任一步失败整体回滚。
- 版本号串行：提交必须带页面打开时的版本号；服务端版本已前进则判 409，旧页面提交的
  过期决定一律不落库。

真实项目里这里的内存态会换成数据库 + 行级事务；当前实现用进程内锁模拟事务边界。
"""
from __future__ import annotations

import copy
import threading
from datetime import datetime
from typing import Any

from app.store import store

# ---------------------------------------------------------------------------
# 常量与口径
# ---------------------------------------------------------------------------

MODULE = "sandbox"
TREE_TABLE = "sandbox_tree"
GRANT_TABLE = "sandbox_grant"
TODO_TABLE = "sandbox_todo"
DRAWING_TABLE = "sandbox_drawing"
CONTACT_TABLE = "sandbox_contact"
SNAPSHOT_TABLE = "sandbox_snapshot"

NODE_ORG = "org"
NODE_BRIDGE = "bridge"

RESOURCE_INSPECTION = "inspection"  # 定检记录
RESOURCE_DRAWING = "drawing"        # 工程图纸
RESOURCE_CONTACT = "contact"        # 联系人
RESOURCES = [RESOURCE_INSPECTION, RESOURCE_DRAWING, RESOURCE_CONTACT]
RESOURCE_LABELS = {
    RESOURCE_INSPECTION: "定检记录",
    RESOURCE_DRAWING: "工程图纸",
    RESOURCE_CONTACT: "联系人",
}

# 授权核验结论回写到业务台账时使用的列
LEDGER_CONCLUSION = "授权核验结论"
LEDGER_VERSION = "最近授权版本"
LEDGER_TIME = "最近授权时间"
INSPECTION_CONCLUSION = "授权结论"


class SandboxConflict(Exception):
    """提交所依据的版本号已过期（多人并发调整）。"""


class SandboxReject(Exception):
    """授权核验不通过或越权读取，调用方转成 4xx。"""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# 初始沙盘数据：一棵 组织→单桥 的权限树、若干养护单位和初始授权
# ---------------------------------------------------------------------------

def _seed_tree() -> list[dict[str, Any]]:
    return [
        # 市级 → 片区 → 养护单位（组织节点），叶子为单桥
        {"id": "root", "type": NODE_ORG, "name": "市市政设施管理中心", "parent_id": None, "bridge_code": None, "unit_code": None},
        {"id": "org-central", "type": NODE_ORG, "name": "中心片区养护分部", "parent_id": "root", "bridge_code": None, "unit_code": None},
        {"id": "org-west", "type": NODE_ORG, "name": "西部片区养护分部", "parent_id": "root", "bridge_code": None, "unit_code": None},
        {"id": "unit-center", "type": NODE_ORG, "name": "市市政桥梁隧道中心", "parent_id": "org-central", "bridge_code": None, "unit_code": "U-CENTER"},
        {"id": "unit-east", "type": NODE_ORG, "name": "东区养护中心", "parent_id": "org-central", "bridge_code": None, "unit_code": "U-EAST"},
        {"id": "unit-west", "type": NODE_ORG, "name": "西区养护中心", "parent_id": "org-west", "bridge_code": None, "unit_code": "U-WEST"},
        {"id": "unit-west-dept", "type": NODE_ORG, "name": "西区市政服务部", "parent_id": "org-west", "bridge_code": None, "unit_code": "U-WEST-DEPT"},
        {"id": "bridge-1", "type": NODE_BRIDGE, "name": "桥梁档案样例1", "parent_id": "unit-center", "bridge_code": "BRID-0001", "unit_code": None},
        {"id": "bridge-2", "type": NODE_BRIDGE, "name": "桥梁档案样例2", "parent_id": "unit-east", "bridge_code": "BRID-0002", "unit_code": None},
        {"id": "bridge-3", "type": NODE_BRIDGE, "name": "桥梁档案样例3", "parent_id": "unit-west", "bridge_code": "BRID-0003", "unit_code": None},
    ]


def _seed_grants() -> list[dict[str, Any]]:
    return [
        # 单桥声明：已登记《资产移交协议》，效力高于组织授权
        {"id": "g-init-1", "node_id": "bridge-1", "grantee": "U-CENTER", "open": True,
         "resources": list(RESOURCES), "agreement": "XY-2025-0007", "remark": "资产移交协议授权",
         "operator": "系统初始化", "created_at": "2026-09-10 09:00:00"},
        # 组织授权：中心片区 → 东区养护中心（覆盖其下属单桥）
        {"id": "g-init-2", "node_id": "org-central", "grantee": "U-EAST", "open": True,
         "resources": list(RESOURCES), "agreement": None, "remark": "片区协作授权",
         "operator": "系统初始化", "created_at": "2026-09-11 09:00:00"},
        # 组织授权：西部片区 → 西区养护中心
        {"id": "g-init-3", "node_id": "org-west", "grantee": "U-WEST", "open": True,
         "resources": list(RESOURCES), "agreement": None, "remark": "西部片区年度授权",
         "operator": "系统初始化", "created_at": "2026-09-12 09:00:00"},
    ]


def _seed_drawings() -> list[dict[str, Any]]:
    return [
        {"id": 1, "bridge_code": "BRID-0001", "图纸编号": "DWG-0001-A", "图纸名称": "桥梁档案样例1-上部结构施工图",
         "图纸类型": "施工图", "版本": "A/2025", "密级": "内部", "归档日期": "2025-11-02"},
        {"id": 2, "bridge_code": "BRID-0001", "图纸编号": "DWG-0001-B", "图纸名称": "桥梁档案样例1-下部基础竣工图",
         "图纸类型": "竣工图", "版本": "B/2026", "密级": "内部", "归档日期": "2026-03-18"},
        {"id": 3, "bridge_code": "BRID-0002", "图纸编号": "DWG-0002-A", "图纸名称": "桥梁档案样例2-加固设计图",
         "图纸类型": "设计图", "版本": "A/2026", "密级": "受控", "归档日期": "2026-04-25"},
        {"id": 4, "bridge_code": "BRID-0003", "图纸编号": "DWG-0003-A", "图纸名称": "桥梁档案样例3-定期检查布点图",
         "图纸类型": "检查图", "版本": "A/2025", "密级": "内部", "归档日期": "2025-12-09"},
    ]


def _seed_contacts() -> list[dict[str, Any]]:
    return [
        {"id": 1, "bridge_code": "BRID-0001", "单位名称": "市市政桥梁隧道中心", "联系人": "周工", "职务": "档案管理员", "电话": "0571-88000001"},
        {"id": 2, "bridge_code": "BRID-0002", "单位名称": "东区养护中心", "联系人": "吴敏", "职务": "定检负责人", "电话": "0571-88000002"},
        {"id": 3, "bridge_code": "BRID-0003", "单位名称": "西区养护中心", "联系人": "郑海", "职务": "养护主管", "电话": "0571-88000003"},
        {"id": 4, "bridge_code": "BRID-0003", "单位名称": "西区市政服务部", "联系人": "王倩", "职务": "值班长", "电话": "0571-88000004"},
    ]


# ---------------------------------------------------------------------------
# 沙盘状态：权限树、授权声明、边缘权限、访问缓存、共享待办、历史快照
# ---------------------------------------------------------------------------

class SandboxState:
    """授权沙盘的全部可推演状态。整体被一把锁保护，提交时整份演进。"""

    def __init__(self) -> None:
        self.version = 0
        self.nodes: list[dict[str, Any]] = _seed_tree()
        self.grants: list[dict[str, Any]] = _seed_grants()
        self.edges: list[dict[str, Any]] = []
        self.cache: dict[str, Any] = {}
        self.todos: list[dict[str, Any]] = []
        self.snapshots: list[dict[str, Any]] = []
        self._seq = 0
        recompute_edges(self, attach_resources=False)

    # ---- 基础索引 ----
    def node_map(self) -> dict[str, dict[str, Any]]:
        return {node["id"]: node for node in self.nodes}

    def bridge_nodes(self) -> list[dict[str, Any]]:
        return [node for node in self.nodes if node["type"] == NODE_BRIDGE]

    def grantees(self) -> list[dict[str, str]]:
        """可被拖入沙盘的养护单位（取树上挂了 unit_code 的组织节点）。"""
        seen: set[str] = set()
        units: list[dict[str, str]] = []
        for node in self.nodes:
            code = node.get("unit_code")
            if code and code not in seen:
                seen.add(code)
                units.append({"code": code, "name": node["name"], "node_id": node["id"]})
        return units

    def ancestors(self, node_id: str) -> list[dict[str, Any]]:
        """从节点自身向上到根的祖先链（含自身），顺序为 近 → 远。"""
        mapping = self.node_map()
        chain: list[dict[str, Any]] = []
        current = mapping.get(node_id)
        while current is not None:
            chain.append(current)
            parent = current.get("parent_id")
            current = mapping.get(parent) if parent else None
        return chain

    def next_id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}-{self._seq}"


# ---------------------------------------------------------------------------
# 资源装配：定检记录 / 工程图纸 / 联系人
# ---------------------------------------------------------------------------

def _inspection_resources(bridge_code: str) -> list[dict[str, Any]]:
    """定检记录来自桥梁定检（bridge）台账，按桥梁编号与档案对齐。"""
    result: list[dict[str, Any]] = []
    for row in store.rows("bridge"):
        if str(row.get("桥梁名称", "")) == bridge_code:
            result.append({
                "resource_type": RESOURCE_INSPECTION,
                "编号": row.get("检测编号"),
                "标题": f"{bridge_code} {row.get('检测类型', '')}定检",
                "检测日期": row.get("检测日期"),
                "检测单位": row.get("检测单位"),
                "检测状态": row.get("检测状态"),
                "技术状况评分": row.get("技术状况评分"),
            })
    return result


def _drawing_resources(bridge_code: str) -> list[dict[str, Any]]:
    return [
        {
            "resource_type": RESOURCE_DRAWING,
            "编号": row.get("图纸编号"),
            "标题": row.get("图纸名称"),
            "图纸类型": row.get("图纸类型"),
            "版本": row.get("版本"),
            "密级": row.get("密级"),
            "归档日期": row.get("归档日期"),
        }
        for row in store.rows(DRAWING_TABLE) if row.get("bridge_code") == bridge_code
    ]


def _contact_resources(bridge_code: str) -> list[dict[str, Any]]:
    return [
        {
            "resource_type": RESOURCE_CONTACT,
            "编号": f"联系人-{row.get('id')}",
            "标题": f"{row.get('单位名称')}·{row.get('联系人')}",
            "单位名称": row.get("单位名称"),
            "联系人": row.get("联系人"),
            "职务": row.get("职务"),
            "电话": row.get("电话"),
        }
        for row in store.rows(CONTACT_TABLE) if row.get("bridge_code") == bridge_code
    ]


def _resources_for(bridge_code: str) -> dict[str, list[dict[str, Any]]]:
    return {
        RESOURCE_INSPECTION: _inspection_resources(bridge_code),
        RESOURCE_DRAWING: _drawing_resources(bridge_code),
        RESOURCE_CONTACT: _contact_resources(bridge_code),
    }


# ---------------------------------------------------------------------------
# 边缘权限计算：按节点重算「养护单位 × 单桥」连线
# ---------------------------------------------------------------------------

def _winning_grant(
    state: SandboxState, grantee: str, bridge_node: dict[str, Any]
) -> dict[str, Any] | None:
    """求某单位对某单桥当前生效的授权声明。

    优先级：
    1. 单桥声明（挂在 bridge 节点、且登记《资产移交协议》）——以资产移交协议为准，最高效力；
    2. 祖先链上的组织授权——上级组织默认覆盖下级（近根者优先）。
    """
    # 1) 单桥声明
    for grant in state.grants:
        if (
            grant["grantee"] == grantee
            and grant["node_id"] == bridge_node["id"]
            and grant.get("agreement")
        ):
            return grant

    # 2) 祖先链组织授权：上级组织默认覆盖下级（取最高层级，即离根最近的一条）
    for node in reversed(state.ancestors(bridge_node["id"])):
        if node["type"] != NODE_ORG:
            continue
        for grant in state.grants:
            if (
                grant["grantee"] == grantee
                and grant["node_id"] == node["id"]
                and grant.get("open")
            ):
                return grant
    return None


def recompute_edges(state: SandboxState, *, attach_resources: bool) -> list[dict[str, Any]]:
    """重算全部边缘权限，并同步重建访问缓存。

    返回边缘列表（可选挂上三类联动资源明细，沙盘预览需要完整数据，落库态为节省内存不带）。
    """
    mapping = state.node_map()
    edges: list[dict[str, Any]] = []
    cache: dict[str, Any] = {"unit_bridges": {}, "bridge_units": {}, "resources": {}}

    for unit in state.grantees():
        cache["unit_bridges"].setdefault(unit["code"], [])
        for bridge_node in state.bridge_nodes():
            code = bridge_node["bridge_code"]
            winning = _winning_grant(state, unit["code"], bridge_node)
            open_resources = list(winning["resources"]) if winning and winning.get("open") else []
            is_open = bool(open_resources)
            source_node = mapping.get(winning["node_id"]) if winning else None
            if winning:
                if source_node and source_node["type"] == NODE_BRIDGE:
                    basis = "single"
                    reason = f"依据《资产移交协议》{winning.get('agreement')}的单桥声明"
                else:
                    basis = "org"
                    reason = f"继承组织节点「{source_node['name'] if source_node else ''}」的授权"
            else:
                basis = "none"
                reason = "无有效授权，默认关闭"

            edge: dict[str, Any] = {
                "grantee": unit["code"],
                "grantee_name": unit["name"],
                "bridge_code": code,
                "bridge_name": bridge_node["name"],
                "open": is_open,
                "resources": open_resources,
                "resource_labels": [RESOURCE_LABELS[r] for r in open_resources],
                "basis": basis,
                "grant_id": winning["id"] if winning else None,
                "agreement": winning.get("agreement") if winning else None,
                "source_node_id": winning["node_id"] if winning else None,
                "source_node_name": source_node["name"] if source_node else None,
                "reason": reason,
            }
            if attach_resources:
                edge["linked"] = _resources_for(code) if is_open else {r: [] for r in RESOURCES}
            edges.append(edge)

            # 重建访问缓存：单位 → 开放单桥；单桥 → 开放单位；(单位, 单桥) → 资源
            if is_open:
                cache["unit_bridges"][unit["code"]].append(code)
                cache["bridge_units"].setdefault(code, []).append(unit["code"])
                cache["resources"][f"{unit['code']}@{code}"] = _resources_for(code)

    state.edges = edges
    state.cache = cache
    return edges


# ---------------------------------------------------------------------------
# 授权核验：单桥声明必须有《资产移交协议》
# ---------------------------------------------------------------------------

def _validate_grant(state: SandboxState, item: dict[str, Any]) -> None:
    node = state.node_map().get(str(item.get("node_id") or ""))
    if node is None:
        raise SandboxReject(f"授权节点 {item.get('node_id')} 不在权限树中")
    grantee = str(item.get("grantee") or "").strip()
    if grantee not in {unit["code"] for unit in state.grantees()}:
        raise SandboxReject(f"养护单位 {grantee or '(空)'} 不在可授权名单内")
    agreement = str(item.get("agreement") or "").strip()
    if node["type"] == NODE_BRIDGE and not agreement:
        raise SandboxReject(
            f"单桥「{node['name']}」声明授权必须登记《资产移交协议》编号"
        )
    if node["type"] == NODE_ORG and agreement:
        raise SandboxReject("组织节点授权不得附带资产移交协议，协议仅用于单桥声明")
    resources = item.get("resources") or RESOURCES
    bad = [r for r in resources if r not in RESOURCES]
    if bad:
        raise SandboxReject(f"未知资源类型：{'、'.join(bad)}")


# ---------------------------------------------------------------------------
# 服务入口
# ---------------------------------------------------------------------------

class SandboxService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        # 联动资源表：工程图纸 / 联系人（定检记录直接复用 bridge 台账）
        for name, rows in (
            (DRAWING_TABLE, _seed_drawings()),
            (CONTACT_TABLE, _seed_contacts()),
        ):
            store.rows(name).extend(copy.deepcopy(rows))
        self.state = SandboxState()
        self._bootstrap_ledger()
        self._snapshot("系统初始化", "初始授权基线", version=0)

    # ---- 台账初始化：让既有桥梁档案/定检记录挂上授权结论列 ----
    def _bootstrap_ledger(self) -> None:
        with self._lock:
            for row in store.rows("bridge_info"):
                row.setdefault(LEDGER_CONCLUSION, "")
                row.setdefault(LEDGER_VERSION, 0)
                row.setdefault(LEDGER_TIME, "")
            for row in store.rows("bridge"):
                # 定检清单按桥梁编号与档案对齐
                code = str(row.get("桥梁名称", ""))
                if code.startswith("BRID-"):
                    row.setdefault(INSPECTION_CONCLUSION, "")
            recompute_edges(self.state, attach_resources=False)
            self._write_ledgers(operator="系统初始化")

    # ---- 对外读视图 ----
    def get_state(self) -> dict[str, Any]:
        with self._lock:
            return self._state_payload(include_resources=True)

    def get_tree(self) -> dict[str, Any]:
        with self._lock:
            return {
                "version": self.state.version,
                "nodes": copy.deepcopy(self.state.nodes),
                "grantees": self.state.grantees(),
            }

    def list_todos(self) -> dict[str, Any]:
        with self._lock:
            return {"version": self.state.version, "items": copy.deepcopy(self.state.todos)}

    def list_snapshots(self) -> dict[str, Any]:
        with self._lock:
            return {"version": self.state.version, "items": copy.deepcopy(self.state.snapshots)}

    def _state_payload(self, *, include_resources: bool) -> dict[str, Any]:
        # 给视图临时挂上联动资源，不污染落库态
        if include_resources:
            recompute_edges(self.state, attach_resources=True)
            edges = copy.deepcopy(self.state.edges)
            recompute_edges(self.state, attach_resources=False)
        else:
            edges = copy.deepcopy(self.state.edges)
        return {
            "version": self.state.version,
            "nodes": copy.deepcopy(self.state.nodes),
            "grantees": self.state.grantees(),
            "grants": copy.deepcopy(self.state.grants),
            "edges": edges,
            "stats": self._stats(edges),
        }

    def _stats(self, edges: list[dict[str, Any]]) -> dict[str, int]:
        total = len(edges)
        opened = sum(1 for edge in edges if edge["open"])
        return {
            "tree_nodes": len(self.state.nodes),
            "grants": len(self.state.grants),
            "edge_total": total,
            "edge_open": opened,
            "edge_closed": total - opened,
            "single_declarations": sum(1 for g in self.state.grants if g.get("agreement")),
            "pending_todos": sum(1 for t in self.state.todos if t["状态"] == "待联动"),
        }

    # ---- 沙盘推演（不落库）：管理端拖动时即时看到联动结果 ----
    def preview(
        self,
        changes: list[dict[str, Any]],
        *,
        removals: list[str] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            trial = SandboxState.__new__(SandboxState)
            trial.version = self.state.version
            trial.nodes = copy.deepcopy(self.state.nodes)
            trial.grants = copy.deepcopy(self.state.grants)
            trial.edges = []
            trial.cache = {}
            trial.todos = []
            trial.snapshots = []
            trial._seq = 0
            self._apply_changes(trial, changes, removals or [])
            recompute_edges(trial, attach_resources=True)
            return {
                "base_version": self.state.version,
                "edges": copy.deepcopy(trial.edges),
                "stats": self._stats(trial.edges),
            }

    # ---- 越权读取网关：无有效授权一律拒绝 ----
    def read_resource(
        self, grantee: str, bridge_code: str, resource: str | None = None
    ) -> dict[str, Any]:
        with self._lock:
            labels = {unit["code"]: unit["name"] for unit in self.state.grantees()}
            if grantee not in labels:
                raise SandboxReject(f"养护单位 {grantee or '(空)'} 未登记，拒绝读取")
            edge = next(
                (
                    e
                    for e in self.state.edges
                    if e["grantee"] == grantee and e["bridge_code"] == bridge_code
                ),
                None,
            )
            if edge is None:
                raise SandboxReject(f"越权读取被拒绝：台账中没有单桥 {bridge_code}")
            if not edge["open"]:
                raise SandboxReject(
                    f"越权读取被拒绝：{labels[grantee]} 对 {bridge_code} {edge['reason']}"
                )
            if resource is not None:
                if resource not in RESOURCES:
                    raise SandboxReject(f"未知资源类型 {resource}")
                if resource not in edge["resources"]:
                    raise SandboxReject(
                        f"越权读取被拒绝：{labels[grantee]} 对 {bridge_code} 的"
                        f"{RESOURCE_LABELS[resource]}未开放"
                    )
            opened = self.state.cache["resources"].get(f"{grantee}@{bridge_code}", {})
            return {
                "ok": True,
                "grantee": grantee,
                "grantee_name": labels[grantee],
                "bridge_code": bridge_code,
                "resources": opened,
            }

    # ---- 提交：版本号串行 + 同事务转换 ----
    def commit(
        self,
        *,
        base_version: int,
        changes: list[dict[str, Any]],
        removals: list[str] | None,
        operator: str,
        remark: str | None,
    ) -> dict[str, Any]:
        with self._lock:
            if int(base_version) != self.state.version:
                raise SandboxConflict(
                    f"授权版本已过期（页面基于 v{base_version}，服务端已到 v{self.state.version}），"
                    "请刷新沙盘后再提交"
                )

            # 事务检查点：失败时整体回滚，权限树与访问缓存绝不出现半成品
            checkpoint = self._checkpoint()
            try:
                self._apply_changes(self.state, changes, removals or [], operator=operator)
                # 权限树/授权 与 访问缓存在同一事务里完成转换
                recompute_edges(self.state, attach_resources=False)
                # 版本号先前进：台账、待办、快照都落到新版本上
                self.state.version += 1
                todo_summary = self._write_ledgers(operator=operator)
                snapshot = self._snapshot(operator, remark or "授权调整", self.state.version)
                snapshot["todo_summary"] = todo_summary
            except Exception:
                self._restore(checkpoint)
                raise

            return {
                "ok": True,
                "message": f"授权已提交并联动（v{self.state.version}）",
                **self._state_payload(include_resources=True),
            }

    # ---- 重置沙盘（便于反复演示） ----
    def reset(self) -> dict[str, Any]:
        with self._lock:
            checkpoint = self._checkpoint()
            try:
                # 清空运行期产生的台账表，再重建初始沙盘
                store.reset_table(TODO_TABLE)
                store.reset_table(SNAPSHOT_TABLE)
                self.state = SandboxState()
                self._bootstrap_ledger()
                self._snapshot("系统初始化", "沙盘重置", version=0)
            except Exception:
                self._restore(checkpoint)
                raise
            return {"ok": True, "message": "授权沙盘已恢复初始状态", **self._state_payload(include_resources=True)}

    # ------------------------------------------------------------------
    # 内部：变更应用、台账回写、快照与事务检查点
    # ------------------------------------------------------------------

    def _apply_changes(
        self,
        state: SandboxState,
        changes: list[dict[str, Any]],
        removals: list[str],
        *,
        operator: str = "值班管理员",
    ) -> None:
        for grant_id in removals:
            before = len(state.grants)
            state.grants = [g for g in state.grants if g["id"] != grant_id]
            if len(state.grants) == before:
                raise SandboxReject(f"待撤销授权 {grant_id} 不存在")
        for item in changes:
            _validate_grant(state, item)
            node_id = str(item["node_id"])
            grantee = str(item["grantee"]).strip()
            existing = next(
                (g for g in state.grants if g["node_id"] == node_id and g["grantee"] == grantee),
                None,
            )
            payload = {
                "node_id": node_id,
                "grantee": grantee,
                "open": bool(item.get("open", True)),
                "resources": item.get("resources") or list(RESOURCES),
                "agreement": str(item.get("agreement") or "").strip() or None,
                "remark": str(item.get("remark") or "").strip() or None,
            }
            if existing:
                existing.update(payload)
            else:
                payload["id"] = state.next_id("g")
                payload["operator"] = operator if state is self.state else "推演中"
                payload["created_at"] = _now()
                state.grants.append(payload)

    def _write_ledgers(self, *, operator: str) -> dict[str, Any]:
        """把授权核验结论写入桥梁台账、定检清单与共享待办。"""
        now = _now()
        version = self.state.version
        edges = self.state.edges
        opened_by_bridge: dict[str, list[dict[str, Any]]] = {}
        for edge in edges:
            if edge["open"]:
                opened_by_bridge.setdefault(edge["bridge_code"], []).append(edge)

        # 1) 桥梁台账
        for row in store.rows("bridge_info"):
            code = str(row.get("桥梁编号", ""))
            winners = opened_by_bridge.get(code, [])
            if winners:
                detail = "；".join(
                    f"{e['grantee_name']}({'/'.join(e['resource_labels'])})" for e in winners
                )
                row[LEDGER_CONCLUSION] = f"开放给：{detail}"
            else:
                row[LEDGER_CONCLUSION] = "无有效授权，档案不开放"
            row[LEDGER_VERSION] = version
            row[LEDGER_TIME] = now

        # 2) 定检清单
        for row in store.rows("bridge"):
            code = str(row.get("桥梁名称", ""))
            if code in {b["bridge_code"] for b in self.state.bridge_nodes()}:
                winners = opened_by_bridge.get(code, [])
                row[INSPECTION_CONCLUSION] = (
                    "定检记录联动开放：" + "、".join(e["grantee_name"] for e in winners)
                    if winners else "定检记录随授权收回"
                )

        # 3) 共享待办：上一轮的待办一律结算为已联动；本轮按新边缘重新下发
        for todo in self.state.todos:
            if todo["状态"] == "待联动":
                todo["状态"] = "已联动"
        created = 0
        for edge in edges:
            if edge["open"]:
                self._add_todo(edge, "开放共享", version, operator, now)
                created += 1
            elif self._was_open_before(edge):
                # 上一版本开放、本轮关闭 → 下发收回待办
                self._add_todo(edge, "收回共享", version, operator, now)
                created += 1
        return {"created": created, "open_bridges": len(opened_by_bridge)}

    def _was_open_before(self, edge: dict[str, Any]) -> bool:
        """从最近一次快照判断该边缘上一版本是否开放（用于生成收回待办）。"""
        for snapshot in reversed(self.state.snapshots):
            for old in snapshot.get("edges", []):
                if old["grantee"] == edge["grantee"] and old["bridge_code"] == edge["bridge_code"]:
                    return bool(old["open"])
        return False

    def _add_todo(
        self,
        edge: dict[str, Any],
        kind: str,
        version: int,
        operator: str,
        now: str,
    ) -> None:
        table = store.rows(TODO_TABLE)
        todo_id = max((int(t.get("id", 0)) for t in table), default=0) + 1
        action = "开放定检记录/工程图纸/联系人" if kind == "开放共享" else "收回定检记录/工程图纸/联系人"
        todo = {
            "id": todo_id,
            "事项编号": f"TODO-{version}-{todo_id:03d}",
            "事项类型": kind,
            "桥梁编号": edge["bridge_code"],
            "桥梁名称": edge["bridge_name"],
            "养护单位编码": edge["grantee"],
            "养护单位": edge["grantee_name"],
            "事项内容": f"{action}（{edge['reason']}）",
            "授权版本": version,
            "经办人": operator,
            "下发时间": now,
            "状态": "待联动",
        }
        table.append(todo)
        self.state.todos.append(todo)

    def _snapshot(self, operator: str, remark: str, version: int) -> dict[str, Any]:
        """保留历史授权快照：固化当时的授权声明与边缘权限。"""
        snapshot = {
            "version": version,
            "operator": operator,
            "remark": remark,
            "created_at": _now(),
            "grants": copy.deepcopy(self.state.grants),
            "edges": [
                {k: v for k, v in edge.items() if k != "linked"}
                for edge in copy.deepcopy(self.state.edges)
            ],
        }
        self.state.snapshots.append(snapshot)
        store.rows(SNAPSHOT_TABLE).append(snapshot)
        return snapshot

    def _checkpoint(self) -> dict[str, Any]:
        return {
            "version": self.state.version,
            "nodes": copy.deepcopy(self.state.nodes),
            "grants": copy.deepcopy(self.state.grants),
            "edges": copy.deepcopy(self.state.edges),
            "cache": copy.deepcopy(self.state.cache),
            "todos": copy.deepcopy(self.state.todos),
            "snapshots": copy.deepcopy(self.state.snapshots),
            "tables": {
                "bridge_info": copy.deepcopy(store.rows("bridge_info")),
                "bridge": copy.deepcopy(store.rows("bridge")),
                TODO_TABLE: copy.deepcopy(store.rows(TODO_TABLE)),
                SNAPSHOT_TABLE: copy.deepcopy(store.rows(SNAPSHOT_TABLE)),
            },
        }

    def _restore(self, checkpoint: dict[str, Any]) -> None:
        self.state.version = checkpoint["version"]
        self.state.nodes = checkpoint["nodes"]
        self.state.grants = checkpoint["grants"]
        self.state.edges = checkpoint["edges"]
        self.state.cache = checkpoint["cache"]
        self.state.todos = checkpoint["todos"]
        self.state.snapshots = checkpoint["snapshots"]
        for name, rows in checkpoint["tables"].items():
            store.replace_table(name, rows)


# 单例：路由层共享同一份沙盘
service = SandboxService()
