"""授权沙盘接口。

- ``GET  /api/sandbox/state``     沙盘全貌：权限树、授权声明、边缘权限、联动资源、统计
- ``POST /api/sandbox/preview``   拖动推演（不落库），即时看到三类资源联动结果
- ``POST /api/sandbox/commit``    带版本号串行提交，事务内转换权限树与访问缓存并回写台账
- ``GET  /api/sandbox/tree``      权限树与可授权养护单位
- ``GET  /api/sandbox/todos``     共享待办
- ``GET  /api/sandbox/snapshots`` 历史授权快照
- ``POST /api/sandbox/access``    越权读取核验（无授权返回 403）
- ``POST /api/sandbox/reset``     恢复初始沙盘
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.sandbox import (
    RESOURCES,
    SandboxConflict,
    SandboxReject,
    service,
)

router = APIRouter(prefix="/api/sandbox", tags=["授权沙盘"])


class GrantChange(BaseModel):
    """一条授权决定：把养护单位 grantee 授到 node_id（组织节点或单桥节点）。"""

    node_id: str
    grantee: str
    open: bool = True
    resources: list[str] = Field(default_factory=lambda: list(RESOURCES))
    agreement: str | None = None  # 单桥声明必填的《资产移交协议》编号
    remark: str | None = None


class CommitPayload(BaseModel):
    base_version: int
    changes: list[GrantChange] = Field(default_factory=list)
    removals: list[str] = Field(default_factory=list)
    operator: str = "值班管理员"
    remark: str | None = None


class PreviewPayload(BaseModel):
    changes: list[GrantChange] = Field(default_factory=list)
    removals: list[str] = Field(default_factory=list)


class AccessPayload(BaseModel):
    grantee: str
    bridge_code: str
    resource: str | None = None


def _as_dicts(items: list[GrantChange]) -> list[dict[str, Any]]:
    return [item.model_dump() for item in items]


@router.get("/state")
def get_state() -> dict[str, Any]:
    """沙盘全貌：管理端首屏渲染权限树、边缘权限与联动资源。"""
    return service.get_state()


@router.get("/tree")
def get_tree() -> dict[str, Any]:
    return service.get_tree()


@router.get("/todos")
def list_todos() -> dict[str, Any]:
    return service.list_todos()


@router.get("/snapshots")
def list_snapshots() -> dict[str, Any]:
    return service.list_snapshots()


@router.post("/preview")
def preview(payload: PreviewPayload) -> dict[str, Any]:
    """拖动推演：不落库，只返回新授权集下的边缘权限与联动资源。"""
    try:
        return service.preview(_as_dicts(payload.changes), removals=payload.removals)
    except SandboxReject as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/commit")
def commit(payload: CommitPayload) -> dict[str, Any]:
    """串行提交：版本不符返回 409；核验不过返回 400；成功返回新版本全貌。"""
    try:
        return service.commit(
            base_version=payload.base_version,
            changes=_as_dicts(payload.changes),
            removals=payload.removals,
            operator=payload.operator,
            remark=payload.remark,
        )
    except SandboxConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SandboxReject as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/access")
def access(payload: AccessPayload) -> dict[str, Any]:
    """越权读取核验：无有效授权（或指定资源未开放）一律 403 拒绝。"""
    try:
        return service.read_resource(payload.grantee, payload.bridge_code, payload.resource)
    except SandboxReject as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/reset")
def reset() -> dict[str, Any]:
    return service.reset()
