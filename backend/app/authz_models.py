"""授权沙盘的核心数据结构与纯函数。

这里刻意不依赖 FastAPI / store，只描述「授权域」本身的语言：
- 组织树节点（单位）与单桥节点；
- 授权主体（养护单位、检测单位等外部组织）；
- 授权决定（开放 / 关闭，分台账、定检、图纸、联系人、共享待办五类资源）；
- 边权限（某个主体面对某一座桥时最终生效的访问权）。

解析口径（与业务约定一一对应）：
1. 资产移交协议（handover）里的单桥声明优先级最高，不允许在沙盘里改动；
2. 组织链路上，上级组织的决定默认覆盖下级——取最靠近根的那一级；
3. 组织都没声明时，才看拖到单桥节点上的手工授权；
4. 都没有则默认关闭（越权读取必须拒绝）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ResourceType = Literal["ledger", "inspection", "drawing", "contact", "todo"]
NodeType = Literal["org", "bridge"]
GrantSource = Literal["manual", "handover"]

RESOURCE_TYPES: tuple[ResourceType, ...] = (
    "ledger",
    "inspection",
    "drawing",
    "contact",
    "todo",
)
RESOURCE_LABELS: dict[str, str] = {
    "ledger": "桥梁台账",
    "inspection": "定检记录",
    "drawing": "工程图纸",
    "contact": "联系人",
    "todo": "共享待办",
}

# 授权核验结论写入哪张业务表、哪个字段
CONCLUSION_TABLES: dict[ResourceType, tuple[str, str]] = {
    "ledger": ("bridge_info", "授权核验结论"),
    "inspection": ("bridge", "授权核验结论"),
    "todo": ("shared_todo", "授权核验结论"),
}
# 图纸、联系人本身就是被闸门挡住的资源，结论写在各自的访问缓存镜像字段上
RESOURCE_TABLES: dict[ResourceType, str] = {
    "ledger": "bridge_info",
    "inspection": "bridge",
    "drawing": "bridge_drawing",
    "contact": "bridge_contact",
    "todo": "shared_todo",
}


@dataclass(frozen=True)
class OrgNode:
    """组织树节点。"""

    id: str
    name: str
    parent_id: str | None = None


@dataclass(frozen=True)
class BridgeNode:
    """单桥节点：挂在某个组织节点下，关联桥梁档案里的桥梁编号。"""

    id: str
    name: str
    bridge_code: str
    parent_id: str


@dataclass(frozen=True)
class Subject:
    """被授权主体：养护单位、检测单位等外部协作组织。"""

    id: str
    name: str
    kind: str  # 养护单位 / 检测单位 / 设计单位 ...


@dataclass(frozen=True)
class Grant:
    """一条授权决定：subject 对 node 上的 resource 开放或关闭。

    source=handover 表示该决定来自资产移交协议，只读，永远保留快照。
    """

    subject_id: str
    node_type: NodeType
    node_id: str
    resource: ResourceType
    decision: bool
    source: GrantSource = "manual"

    @property
    def key(self) -> tuple[str, str, str, str]:
        return self.subject_id, self.node_type, self.node_id, self.resource


@dataclass(frozen=True)
class Edge:
    """边权限：subject 面对一座桥时，五类资源最终生效的开关与判定依据。"""

    subject_id: str
    bridge_node_id: str
    bridge_code: str
    decisions: dict[str, bool]
    bases: dict[str, str]
    source_grants: dict[str, dict[str, Any]]

    def allows(self, resource: ResourceType) -> bool:
        return bool(self.decisions.get(resource, False))


@dataclass
class AuthzState:
    """一次事务内的授权域完整状态，可整体备份/回滚。"""

    version: int
    orgs: dict[str, OrgNode]
    bridges: dict[str, BridgeNode]
    subjects: dict[str, Subject]
    grants: dict[tuple[str, str, str, str], Grant]
    # 访问缓存：(subject_id, resource, bridge_code, item_id) -> 开放与否
    access_cache: dict[tuple[str, str, str, str], bool] = field(default_factory=dict)
    snapshots: list[dict[str, Any]] = field(default_factory=list)

    def clone(self) -> "AuthzState":
        return AuthzState(
            version=self.version,
            orgs=dict(self.orgs),
            bridges=dict(self.bridges),
            subjects=dict(self.subjects),
            grants=dict(self.grants),
            access_cache=dict(self.access_cache),
            snapshots=[dict(s) for s in self.snapshots],
        )

    def org_chain(self, org_id: str) -> list[str]:
        """从根到当前组织的链路（含自身）。"""
        chain: list[str] = []
        cursor: str | None = org_id
        seen: set[str] = set()
        while cursor and cursor not in seen:
            seen.add(cursor)
            chain.append(cursor)
            node = self.orgs.get(cursor)
            cursor = node.parent_id if node else None
        chain.reverse()
        return chain


def resolve_edge(state: AuthzState, subject_id: str, bridge: BridgeNode) -> Edge:
    """按节点重新计算一条边权限。

    每种资源独立解析，优先级见模块 docstring。
    """
    decisions: dict[str, bool] = {}
    bases: dict[str, str] = {}
    source_grants: dict[str, dict[str, Any]] = {}

    for resource in RESOURCE_TYPES:
        # 1) 资产移交协议的单桥声明：最高优先
        handover = state.grants.get(
            (subject_id, "bridge", bridge.id, resource)
        )
        if handover is not None and handover.source == "handover":
            decisions[resource] = handover.decision
            bases[resource] = f"资产移交协议声明·{bridge.name}"
            source_grants[resource] = _grant_payload(handover)
            continue

        # 2) 组织链路上取最靠近根的一级声明（上级覆盖下级）
        ancestor_decision: Grant | None = None
        ancestor_org: OrgNode | None = None
        for org_id in state.org_chain(bridge.parent_id):
            grant = state.grants.get((subject_id, "org", org_id, resource))
            if grant is not None:
                ancestor_decision = grant
                ancestor_org = state.orgs[org_id]
                break  # 链路由根向叶，第一个即最高级
        if ancestor_decision is not None:
            decisions[resource] = ancestor_decision.decision
            assert ancestor_org is not None
            bases[resource] = f"{ancestor_org.name}（上级组织覆盖）"
            source_grants[resource] = _grant_payload(ancestor_decision)
            continue

        # 3) 单桥节点上的手工声明
        manual = state.grants.get((subject_id, "bridge", bridge.id, resource))
        if manual is not None:
            decisions[resource] = manual.decision
            bases[resource] = f"单桥授权·{bridge.name}"
            source_grants[resource] = _grant_payload(manual)
            continue

        # 4) 默认关闭
        decisions[resource] = False
        bases[resource] = "无授权，默认关闭"
        source_grants[resource] = {}

    return Edge(
        subject_id=subject_id,
        bridge_node_id=bridge.id,
        bridge_code=bridge.bridge_code,
        decisions=decisions,
        bases=bases,
        source_grants=source_grants,
    )


def resolve_all_edges(state: AuthzState) -> list[Edge]:
    """全量按节点重算边权限（提交事务时调用）。"""
    return [
        resolve_edge(state, subject_id, bridge)
        for subject_id in sorted(state.subjects)
        for bridge in sorted(state.bridges.values(), key=lambda b: b.id)
    ]


def apply_changes(state: AuthzState, changes: list[dict[str, Any]]) -> None:
    """把一批暂存决定写入状态（在事务内调用）。"""
    for change in changes:
        grant = Grant(
            subject_id=str(change["subject_id"]),
            node_type=change["node_type"],
            node_id=str(change["node_id"]),
            resource=change["resource"],
            decision=bool(change["decision"]),
            source="manual",
        )
        if grant.subject_id not in state.subjects:
            raise ValueError(f"授权主体 {grant.subject_id} 不存在")
        if grant.node_type == "org" and grant.node_id not in state.orgs:
            raise ValueError(f"组织节点 {grant.node_id} 不存在")
        if grant.node_type == "bridge" and grant.node_id not in state.bridges:
            raise ValueError(f"单桥节点 {grant.node_id} 不存在")
        existing = state.grants.get(grant.key)
        if existing is not None and existing.source == "handover":
            raise ValueError(
                f"{state.bridges[grant.node_id].name} 的「"
                f"{RESOURCE_LABELS[grant.resource]}」以资产移交协议为准，沙盘不能改写"
            )
        state.grants[grant.key] = grant


def _grant_payload(grant: Grant) -> dict[str, Any]:
    return {
        "subject_id": grant.subject_id,
        "node_type": grant.node_type,
        "node_id": grant.node_id,
        "resource": grant.resource,
        "decision": grant.decision,
        "source": grant.source,
    }
