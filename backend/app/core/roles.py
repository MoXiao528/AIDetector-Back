"""角色与权限工具。"""

from enum import Enum
from typing import Iterable


class UserRole(str, Enum):
    """系统内置角色枚举。"""

    VISITOR = "VISITOR"
    INDIVIDUAL = "INDIVIDUAL"
    TEAM_ADMIN = "TEAM_ADMIN"
    SYS_ADMIN = "SYS_ADMIN"


ROLE_PRIORITY: dict[UserRole, int] = {
    UserRole.VISITOR: 0,
    UserRole.INDIVIDUAL: 1,
    UserRole.TEAM_ADMIN: 2,
    UserRole.SYS_ADMIN: 3,
}


def normalize_role(value: str | UserRole) -> UserRole:
    """将数据库或请求中的字符串安全地转为 ``UserRole``。"""

    if isinstance(value, UserRole):
        return value
    return UserRole(value)


def has_required_role(user_role: UserRole, allowed_roles: Iterable[UserRole]) -> bool:
    """基于优先级判断当前角色是否满足任一目标角色。"""

    user_priority = ROLE_PRIORITY[user_role]
    return any(user_priority >= ROLE_PRIORITY[target] for target in allowed_roles)
