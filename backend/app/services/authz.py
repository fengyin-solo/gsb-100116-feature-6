"""授权沙盘服务：事务提交、边权限重算、访问缓存转换与越权读取拦截。

一致性约定：
- 权限树（orgs/bridges/grants）与访问缓存在同一把锁、同一次状态整体替换中转换，
  中间任何一步抛错都会整体回滚，外部读到的永远是「上一版完整状态」；
- 提交必须带 expected_version，与当前版本不一致直接 409（多人串行提交，
  旧页面拿着旧版本号提交会被拒绝，过期决定无法落库）；
- 授权核验结论随事务写入桥梁台账、定检清单、图纸/联系人镜像和共享待办。
"""
from __future__ import annotations

import threading
from collections import defaultdict
from datetime import datetime
from typing import Any

from app.authz_models import (
    RESOURCE_LABELS,
    RESOURCE_TABLES,
    RESOURCE_TYPES,
    AuthzState,
    Edge,
    Grant,
    apply_changes,
    resolve_all_edges,
    resolve_edge,
)
from app.authz_seed import build_initial_state
from app.store import store


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _next_id(rows: list[dict[str, Any]]) -> int:
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


class StaleDecisionError(Exception):
    """版本号过期：别人已经提交过新版本。"""

    def __init__(self, message: str, current_version: int) -> None:
        super().__init__(message)
        self.current_version = current_version


class HandoverConflictError(ValueError):
    """试图改写资产移交协议里的单桥声明。"""


class AuthzService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._state: AuthzState | None = None
        self._bootstrapped = False
        self._access_log: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ 初始化

    def bootstrap(self) -> None:
        """补齐业务表里的授权字段与图纸/联系人/共享待办示例，并生成 v1 基线。

        幂等：只执行一次；后续直接返回当前状态。
        """
        with self._lock:
            if self._bootstrapped:
                return
            self._bootstrapped = True

            self._ensure_resource_tables()
            state = build_initial_state()

            edges = resolve_all_edges(state)
            self._expand_access_cache(state, edges)
            # v1 基线快照：保留初始授权面貌
            state.snapshots.append(self._snapshot_payload(state, edges, [], "系统初始化", 1))
            self._state = state
            # 基线结论写入业务表，并为开放中的共享待办生成首批待办
            self._write_conclusions(state, edges, previous_edges=[])
            self._sync_todos(state, edges, previous_edges=[], actor="系统初始化")

    def _ensure_resource_tables(self) -> None:
        tables = store._tables  # 授权域需要直接准备新表，属于同一数据仓库
        bridge_rows = tables.setdefault("bridge", [])
        for index, row in enumerate(bridge_rows, start=1):
            row.setdefault("桥梁编号", f"BRID-{index:04d}")
            row.setdefault("授权核验结论", "未核验")

        for row in tables.setdefault("bridge_info", []):
            row.setdefault("授权核验结论", "未核验")

        tables["bridge_drawing"] = [
            {"id": 1, "status": "在档", "pending": False, "abnormal": False,
             "图纸编号": "DRAW-0001", "桥梁编号": "BRID-0001",
             "图纸名称": "桥梁档案样例1 总体布置图", "图号": "QL-01-00",
             "授权核验结论": "未核验"},
            {"id": 2, "status": "在档", "pending": False, "abnormal": False,
             "图纸编号": "DRAW-0002", "桥梁编号": "BRID-0002",
             "图纸名称": "桥梁档案样例2 结构配筋图", "图号": "QL-02-00",
             "授权核验结论": "未核验"},
            {"id": 3, "status": "在档", "pending": False, "abnormal": False,
             "图纸编号": "DRAW-0003", "桥梁编号": "BRID-0003",
             "图纸名称": "桥梁档案样例3 加固设计图", "图号": "QL-03-00",
             "授权核验结论": "未核验"},
        ]
        tables["bridge_contact"] = [
            {"id": 1, "status": "在册", "pending": False, "abnormal": False,
             "联系人编号": "CONT-0001", "桥梁编号": "BRID-0001",
             "姓名": "王工", "角色": "养护负责人", "电话": "138****0001",
             "授权核验结论": "未核验"},
            {"id": 2, "status": "在册", "pending": False, "abnormal": False,
             "联系人编号": "CONT-0002", "桥梁编号": "BRID-0002",
             "姓名": "李工", "角色": "定检联系人", "电话": "138****0002",
             "授权核验结论": "未核验"},
            {"id": 3, "status": "在册", "pending": False, "abnormal": False,
             "联系人编号": "CONT-0003", "桥梁编号": "BRID-0003",
             "姓名": "赵工", "角色": "资产移交对接人", "电话": "138****0003",
             "授权核验结论": "未核验"},
        ]
        tables["shared_todo"] = []

    # ------------------------------------------------------------------ 沙盘读取

    def get_sandbox(self, actor: str | None = None) -> dict[str, Any]:
        self.bootstrap()
        assert self._state is not None
        with self._lock:
            state = self._state
            edges = resolve_all_edges(state)
            return {
                "version": state.version,
                "actor": actor or "值班管理员",
                "tree": self._tree_payload(state),
                "subjects": [
                    {"id": s.id, "name": s.name, "kind": s.kind}
                    for s in state.subjects.values()
                ],
                "grants": [self._grant_payload(g) for g in state.grants.values()],
                "edges": [self._edge_payload(e) for e in edges],
                "resource_labels": RESOURCE_LABELS,
                "drawings": list(store.rows("bridge_drawing")),
                "contacts": list(store.rows("bridge_contact")),
                "todos": list(store.rows("shared_todo")),
                "snapshots": [
                    {k: v for k, v in snap.items() if k != "grants"}
                    for snap in state.snapshots
                ],
                "access_log": list(self._access_log[-20:]),
            }

    def list_snapshots(self) -> list[dict[str, Any]]:
        self.bootstrap()
        assert self._state is not None
        with self._lock:
            return [dict(snap) for snap in self._state.snapshots]

    # ------------------------------------------------------------------ 预览

    def preview(self, changes: list[dict[str, Any]]) -> dict[str, Any]:
        """把暂存决定叠到当前授权树上试算，只返回试算后的边权限，不落库。"""
        self.bootstrap()
        assert self._state is not None
        with self._lock:
            state = self._state
            trial = state.clone()
            self._apply_changes_checked(trial, changes)
            edges = resolve_all_edges(trial)
            current = resolve_all_edges(state)
            return {
                "base_version": state.version,
                "edges": [self._edge_payload(e) for e in edges],
                "changed_edges": [
                    self._edge_payload(new)
                    for old, new in zip(current, edges)
                    if old.decisions != new.decisions or old.bases != new.bases
                ],
            }

    # ------------------------------------------------------------------ 提交事务

    def commit(
        self,
        changes: list[dict[str, Any]],
        expected_version: int,
        actor: str,
    ) -> dict[str, Any]:
        self.bootstrap()
        assert self._state is not None
        with self._lock:
            # 1) 版本号串行：旧页面的过期决定直接拒绝
            if expected_version != self._state.version:
                raise StaleDecisionError(
                    f"授权树已到 v{self._state.version}，"
                    f"你手里还是 v{expected_version}，请刷新后重新决定",
                    current_version=self._state.version,
                )

            old_state = self._state
            previous_edges = resolve_all_edges(old_state)

            # 2) 在副本上完成「权限树改动 → 边权限重算 → 访问缓存重建」全链路，
            #    任一步失败都不碰当前状态与业务表，事务整体回滚。
            new_state = old_state.clone()
            self._apply_changes_checked(new_state, changes)
            new_edges = resolve_all_edges(new_state)
            self._expand_access_cache(new_state, new_edges)
            new_state.version = old_state.version + 1
            new_state.snapshots.append(
                self._snapshot_payload(new_state, new_edges, changes, actor, new_state.version)
            )

            # 3) 准备业务表写入计划；先在影子数据上跑一遍，全部成功才整体生效。
            mutations, todo_appends = self._plan_table_writes(
                new_state, new_edges, previous_edges, actor
            )

            # 4) 事务提交点：授权树与访问缓存作为一个整体替换引用，
            #    随后落地核验结论与共享待办，全程持锁，外部看不到中间态。
            self._state = new_state
            for table, row_id, field, value in mutations:
                row = store.find(table, row_id)
                if row is not None:
                    row[field] = value
            todo_rows = store.rows("shared_todo")
            todo_rows.extend(todo_appends)

            return {
                "ok": True,
                "version": new_state.version,
                "message": f"授权决定已提交（v{new_state.version}），"
                           f"联动更新 {len(mutations)} 条核验结论、"
                           f"{len(todo_appends)} 条共享待办",
                "edges": [self._edge_payload(e) for e in new_edges],
                "todos": list(todo_rows),
            }

    # ------------------------------------------------------------------ 越权读取

    def read_resource(
        self,
        subject_id: str,
        resource: str,
        item_id: int,
    ) -> dict[str, Any]:
        """按访问缓存读取受保护资源；未开放一律拒绝（403 由 router 抛出）。"""
        self.bootstrap()
        assert self._state is not None
        if resource not in RESOURCE_TABLES:
            raise ValueError(f"未知资源类型：{resource}")
        with self._lock:
            state = self._state
            subject = state.subjects.get(subject_id)
            if subject is None:
                self._audit(subject_id, resource, item_id, None, False, "主体不存在")
                raise PermissionError("授权主体不存在，拒绝读取")

            table = RESOURCE_TABLES[resource]
            row = store.find(table, item_id)
            if row is None:
                raise LookupError(f"{RESOURCE_LABELS[resource]} {item_id} 不存在")
            bridge_code = str(row.get("桥梁编号", ""))
            bridge = next(
                (b for b in state.bridges.values() if b.bridge_code == bridge_code),
                None,
            )
            if bridge is None:
                self._audit(subject_id, resource, item_id, bridge_code, False, "桥不在授权树上")
                raise PermissionError(f"{bridge_code} 不在授权树管辖范围内，拒绝读取")

            edge = resolve_edge(state, subject_id, bridge)
            allowed = edge.allows(resource)  # type: ignore[arg-type]
            basis = edge.bases[resource]
            self._audit(subject_id, resource, item_id, bridge_code, allowed, basis)
            if not allowed:
                raise PermissionError(
                    f"{subject.name} 无权读取 {bridge_code} 的{RESOURCE_LABELS[resource]}：{basis}"
                )
            return {"row": row, "basis": basis, "version": state.version}

    def _audit(
        self,
        subject_id: str,
        resource: str,
        item_id: int,
        bridge_code: str | None,
        allowed: bool,
        basis: str,
    ) -> None:
        self._access_log.append({
            "time": _now(),
            "subject_id": subject_id,
            "resource": resource,
            "resource_label": RESOURCE_LABELS.get(resource, resource),
            "item_id": item_id,
            "bridge_code": bridge_code,
            "result": "放行" if allowed else "拒绝",
            "basis": basis,
        })

    # ------------------------------------------------------------------ 内部规则

    def _apply_changes_checked(
        self, state: AuthzState, changes: list[dict[str, Any]]
    ) -> None:
        """同一份校验在预览与提交中复用；移交协议声明只读。"""
        normalized: list[dict[str, Any]] = []
        for raw in changes:
            change = {
                "subject_id": str(raw.get("subject_id", "")),
                "node_type": str(raw.get("node_type", "")),
                "node_id": str(raw.get("node_id", "")),
                "resource": str(raw.get("resource", "")),
                "decision": bool(raw.get("decision")),
            }
            if change["resource"] not in RESOURCE_TYPES:
                raise ValueError(f"未知资源类型：{change['resource']}")
            key = (
                change["subject_id"], change["node_type"],
                change["node_id"], change["resource"],
            )
            existing = state.grants.get(key)
            if existing is not None and existing.source == "handover":
                bridge = state.bridges.get(change["node_id"])
                name = bridge.name if bridge else change["node_id"]
                raise HandoverConflictError(
                    f"{name} 的「{RESOURCE_LABELS[change['resource']]}」"
                    f"已在资产移交协议中声明，以协议为准，沙盘不能改写"
                )
            normalized.append(change)
        apply_changes(state, normalized)

    def _expand_access_cache(self, state: AuthzState, edges: list[Edge]) -> None:
        """边权限展开成逐行访问缓存：每条业务记录一个缓存位。"""
        state.access_cache = {}
        for edge in edges:
            for resource in RESOURCE_TYPES:
                table = RESOURCE_TABLES[resource]
                rows = store.rows(table)
                targets = [
                    row for row in rows
                    if str(row.get("桥梁编号", "")) == edge.bridge_code
                ]
                if not targets:
                    targets = [{"id": "*"}]
                for row in targets:
                    state.access_cache[
                        (edge.subject_id, resource, edge.bridge_code, str(row["id"]))
                    ] = edge.allows(resource)  # type: ignore[arg-type]

    def _resource_groups(
        self, edges: list[Edge]
    ) -> dict[str, list[tuple[Edge, str, list[dict[str, Any]]]]]:
        """按资源类型归集 (边, 表名, 命中的业务记录)。"""
        groups: dict[str, list[tuple[Edge, str, list[dict[str, Any]]]]] = defaultdict(list)
        for edge in edges:
            for resource in RESOURCE_TYPES:
                table = RESOURCE_TABLES[resource]
                rows = [
                    row for row in store.rows(table)
                    if str(row.get("桥梁编号", "")) == edge.bridge_code
                ]
                groups[resource].append((edge, table, rows))
        return groups

    def _conclusion_text(self, state: AuthzState, edge: Edge, resource: str) -> str:
        allowed = edge.allows(resource)  # type: ignore[arg-type]
        subject = state.subjects[edge.subject_id]
        verb = "开放" if allowed else "关闭"
        return (
            f"[{subject.name}] {verb}｜依据：{edge.bases[resource]}｜v{state.version}"
        )

    def _plan_table_writes(
        self,
        state: AuthzState,
        edges: list[Edge],
        previous_edges: list[Edge],
        actor: str,
    ) -> tuple[list[tuple[str, int, str, str]], list[dict[str, Any]]]:
        """生成事务内的表写入计划：核验结论 + 共享待办追加。"""
        mutations: list[tuple[str, int, str, str]] = []
        # 每条业务记录上，按主体汇总核验结论
        grouped: dict[str, dict[int, list[str]]] = {
            resource: defaultdict(list) for resource in RESOURCE_TYPES
        }
        for resource, items in self._resource_groups(edges).items():
            for edge, _table, rows in items:
                text = self._conclusion_text(state, edge, resource)
                for row in rows:
                    grouped[resource][int(row["id"])].append(text)

        for resource, by_row in grouped.items():
            table = RESOURCE_TABLES[resource]
            for row_id, texts in by_row.items():
                mutations.append((
                    table, row_id, "授权核验结论",
                    "；".join(sorted(texts)),
                ))

        todo_appends = self._plan_todos(state, edges, previous_edges, actor)
        return mutations, todo_appends

    def _plan_todos(
        self,
        state: AuthzState,
        edges: list[Edge],
        previous_edges: list[Edge],
        actor: str,
    ) -> list[dict[str, Any]]:
        """共享待办：边权限发生变化的（主体 × 桥）生成联动待办。"""
        old_map = {
            (e.subject_id, e.bridge_node_id): e for e in previous_edges
        }
        appends: list[dict[str, Any]] = []
        todo_rows = store.rows("shared_todo")
        base_id = _next_id(todo_rows)
        for edge in edges:
            old = old_map.get((edge.subject_id, edge.bridge_node_id))
            if old is None:
                changed_resources = [
                    r for r in RESOURCE_TYPES if edge.allows(r)  # type: ignore[arg-type]
                ]
            else:
                changed_resources = [
                    r for r in RESOURCE_TYPES
                    if edge.decisions[r] != old.decisions[r]
                    or edge.bases[r] != old.bases[r]
                ]
            if not changed_resources:
                continue
            bridge = state.bridges[edge.bridge_node_id]
            subject = state.subjects[edge.subject_id]
            for resource in changed_resources:
                todo_id = base_id + len(appends)
                verb = "开放" if edge.allows(resource) else "关闭"  # type: ignore[arg-type]
                appends.append({
                    "id": todo_id,
                    "status": "待共享" if edge.allows(resource) else "已收回",  # type: ignore[arg-type]
                    "pending": edge.allows(resource),  # type: ignore[arg-type]
                    "abnormal": False,
                    "待办编号": f"TODO-{todo_id:04d}",
                    "桥梁编号": bridge.bridge_code,
                    "桥梁名称": bridge.name,
                    "授权主体": subject.name,
                    "内容": (
                        f"{RESOURCE_LABELS[resource]}联动：{subject.name} 对 "
                        f"{bridge.name}（{bridge.bridge_code}）{verb}"
                    ),
                    "操作人": actor,
                    "授权核验结论": self._conclusion_text(state, edge, resource),
                    "version": state.version,
                })
        return appends

    def _write_conclusions(
        self, state: AuthzState, edges: list[Edge], previous_edges: list[Edge]
    ) -> None:
        """初始化时直接落地一次核验结论。"""
        mutations, _ = self._plan_table_writes(state, edges, previous_edges, "系统初始化")
        for table, row_id, field, value in mutations:
            row = store.find(table, row_id)
            if row is not None:
                row[field] = value

    def _sync_todos(
        self,
        state: AuthzState,
        edges: list[Edge],
        previous_edges: list[Edge],
        actor: str,
    ) -> None:
        store.rows("shared_todo").extend(
            self._plan_todos(state, edges, previous_edges, actor)
        )

    # ------------------------------------------------------------------ 序列化

    def _tree_payload(self, state: AuthzState) -> list[dict[str, Any]]:
        children: dict[str | None, list[str]] = defaultdict(list)
        for org in state.orgs.values():
            children[org.parent_id].append(org.id)

        def render_org(org_id: str) -> dict[str, Any]:
            return {
                "id": org_id,
                "type": "org",
                "name": state.orgs[org_id].name,
                "children": [
                    *[render_org(child) for child in sorted(children.get(org_id, []))],
                    *[
                        {
                            "id": b.id,
                            "type": "bridge",
                            "name": b.name,
                            "bridge_code": b.bridge_code,
                            "children": [],
                        }
                        for b in state.bridges.values()
                        if b.parent_id == org_id
                    ],
                ],
            }

        return [render_org(root) for root in sorted(children.get(None, []))]

    def _edge_payload(self, edge: Edge) -> dict[str, Any]:
        return {
            "subject_id": edge.subject_id,
            "bridge_node_id": edge.bridge_node_id,
            "bridge_code": edge.bridge_code,
            "decisions": dict(edge.decisions),
            "bases": dict(edge.bases),
            "source_grants": dict(edge.source_grants),
        }

    def _grant_payload(self, grant: Grant) -> dict[str, Any]:
        return {
            "subject_id": grant.subject_id,
            "node_type": grant.node_type,
            "node_id": grant.node_id,
            "resource": grant.resource,
            "decision": grant.decision,
            "source": grant.source,
        }

    def _snapshot_payload(
        self,
        state: AuthzState,
        edges: list[Edge],
        changes: list[dict[str, Any]],
        actor: str,
        version: int,
    ) -> dict[str, Any]:
        return {
            "version": version,
            "actor": actor,
            "time": _now(),
            "changes": [dict(c) for c in changes],
            "grants": [self._grant_payload(g) for g in state.grants.values()],
            "edges": [self._edge_payload(e) for e in edges],
        }


authz_service = AuthzService()
