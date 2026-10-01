"""授权沙盘初始数据：组织树、单桥节点、授权主体与首批授权决定。

桥梁编号与业务种子数据（bridge_info / bridge）对齐：BRID-0001 ~ BRID-0003。
"""
from __future__ import annotations

from app.authz_models import AuthzState, BridgeNode, Grant, OrgNode, Subject

ORG_TREE = [
    OrgNode(id="org-root", name="市公路事业发展中心", parent_id=None),
    OrgNode(id="org-urban", name="城区管养分中心", parent_id="org-root"),
    OrgNode(id="org-suburb", name="郊区管养分中心", parent_id="org-root"),
    OrgNode(id="org-urban-team1", name="城东养护所", parent_id="org-urban"),
]

BRIDGE_TREE = [
    BridgeNode(id="br-1", name="桥梁档案样例1", bridge_code="BRID-0001", parent_id="org-urban-team1"),
    BridgeNode(id="br-2", name="桥梁档案样例2", bridge_code="BRID-0002", parent_id="org-urban"),
    BridgeNode(id="br-3", name="桥梁档案样例3", bridge_code="BRID-0003", parent_id="org-suburb"),
]

SUBJECTS = [
    Subject(id="sub-chengtou", name="城投养护工程有限公司", kind="养护单位"),
    Subject(id="sub-gongguan", name="公路工程检测中心", kind="检测单位"),
    Subject(id="sub-design", name="市政设计院桥梁所", kind="设计单位"),
]

# 资产移交协议声明：最高优先、沙盘只读、永久保留快照
HANDOVER_GRANTS = [
    # BRID-0002 已协议移交给城投养护：台账/联系人随资产开放，图纸在协议中明确关闭
    Grant("sub-chengtou", "bridge", "br-2", "ledger", True, "handover"),
    Grant("sub-chengtou", "bridge", "br-2", "contact", True, "handover"),
    Grant("sub-chengtou", "bridge", "br-2", "drawing", False, "handover"),
]

# 初始手工授权：上级组织（城区分中心）对下属桥梁批量开放台账与定检
INITIAL_MANUAL_GRANTS = [
    Grant("sub-gongguan", "org", "org-urban", "ledger", True),
    Grant("sub-gongguan", "org", "org-urban", "inspection", True),
    Grant("sub-gongguan", "org", "org-urban", "drawing", False),
    # 城投养护在郊区分中心层级拿到共享待办
    Grant("sub-chengtou", "org", "org-suburb", "todo", True),
]


def build_initial_state() -> AuthzState:
    state = AuthzState(
        version=1,
        orgs={node.id: node for node in ORG_TREE},
        bridges={node.id: node for node in BRIDGE_TREE},
        subjects={node.id: node for node in SUBJECTS},
        grants={},
    )
    for grant in [*HANDOVER_GRANTS, *INITIAL_MANUAL_GRANTS]:
        state.grants[grant.key] = grant
    return state
