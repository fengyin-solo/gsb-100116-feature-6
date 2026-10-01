"""授权沙盘接口。

- GET  /api/authz/sandbox          沙盘全量：权限树、主体、授权决定、边权限、待办、快照
- POST /api/authz/preview          暂存决定试算（不落库），联动开放效果即时预览
- POST /api/authz/commit           带版本号串行提交；权限树与访问缓存同事务转换
- GET  /api/authz/snapshots        历史授权快照
- GET  /api/authz/access           按主体读取受保护资源；越权读取一律 403
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.authz_models import RESOURCE_TABLES
from app.services.authz import (
    HandoverConflictError,
    StaleDecisionError,
    authz_service,
)

router = APIRouter(prefix="/api/authz", tags=["授权沙盘"])


class GrantChange(BaseModel):
    """一条拖拽产生的暂存决定。"""

    subject_id: str
    node_type: Literal["org", "bridge"]
    node_id: str
    resource: Literal["ledger", "inspection", "drawing", "contact", "todo"]
    decision: bool = True


class PreviewPayload(BaseModel):
    changes: list[GrantChange] = Field(default_factory=list)


class CommitPayload(BaseModel):
    changes: list[GrantChange] = Field(default_factory=list)
    expected_version: int
    actor: str = "值班管理员"


def _change_dicts(changes: list[GrantChange]) -> list[dict[str, Any]]:
    return [change.model_dump() for change in changes]


@router.get("/sandbox")
def sandbox(
    actor: str | None = Query(default=None, description="当前操作人，仅用于回显"),
) -> dict[str, Any]:
    """读取沙盘当前版本：拖动前先拿这一份，版本号是后续提交的串行依据。"""
    return authz_service.get_sandbox(actor=actor)


@router.post("/preview")
def preview(payload: PreviewPayload) -> dict[str, Any]:
    """试算暂存决定：定检记录、工程图纸、联系人是否联动开放，当场可见。"""
    try:
        return authz_service.preview(_change_dicts(payload.changes))
    except HandoverConflictError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/commit")
def commit(payload: CommitPayload) -> dict[str, Any]:
    """提交授权决定。

    版本号与服务端不一致返回 409，旧页面必须刷新后重新决定，过期决定不入库；
    权限树与访问缓存在同一事务转换，中途失败整体回滚。
    """
    try:
        return authz_service.commit(
            changes=_change_dicts(payload.changes),
            expected_version=payload.expected_version,
            actor=payload.actor or "值班管理员",
        )
    except StaleDecisionError as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": str(exc), "current_version": exc.current_version},
        )
    except HandoverConflictError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/snapshots")
def snapshots() -> dict[str, Any]:
    """历史授权快照：每一版授权决定与边权限都完整保留，可追溯。"""
    return {"snapshots": authz_service.list_snapshots()}


@router.get("/access")
def access(
    subject_id: str = Query(..., description="访问主体，即养护单位/检测单位 id"),
    resource: str = Query(..., description="ledger/inspection/drawing/contact/todo"),
    item_id: int = Query(..., description="业务记录 id"),
) -> dict[str, Any]:
    """模拟主体读取一条受保护记录：访问缓存未开放则 403 拒绝，绝不放行越权读取。"""
    if resource not in RESOURCE_TABLES:
        raise HTTPException(status_code=400, detail=f"未知资源类型：{resource}")
    try:
        return authz_service.read_resource(subject_id, resource, item_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
