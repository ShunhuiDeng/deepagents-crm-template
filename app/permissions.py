"""Role-based authorization helpers shared by API routes and agent tools."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final
from uuid import UUID

from fastapi import HTTPException, status


class Role(StrEnum):
    ADMIN = "admin"
    MANAGER = "manager"
    SALES = "sales"
    VIEWER = "viewer"


class Permission(StrEnum):
    CUSTOMER_READ = "customer:read"
    CUSTOMER_CREATE = "customer:create"
    CUSTOMER_UPDATE = "customer:update"
    CUSTOMER_DELETE = "customer:delete"
    USERS_MANAGE = "users:manage"


class CustomerVisibility(StrEnum):
    ALL = "all"
    OWNED = "owned"


@dataclass(frozen=True, slots=True)
class CurrentUser:
    id: UUID
    username: str
    email: str
    display_name: str
    role: Role
    is_active: bool


_ALL_PERMISSIONS = frozenset(Permission)
ROLE_PERMISSIONS: Final[Mapping[Role, frozenset[Permission]]] = MappingProxyType(
    {
        Role.ADMIN: _ALL_PERMISSIONS,
        Role.MANAGER: frozenset(
            {
                Permission.CUSTOMER_READ,
                Permission.CUSTOMER_CREATE,
                Permission.CUSTOMER_UPDATE,
                Permission.CUSTOMER_DELETE,
            }
        ),
        Role.SALES: frozenset(
            {
                Permission.CUSTOMER_READ,
                Permission.CUSTOMER_CREATE,
                Permission.CUSTOMER_UPDATE,
            }
        ),
        Role.VIEWER: frozenset({Permission.CUSTOMER_READ}),
    }
)

_CUSTOMER_VISIBILITY: Final[Mapping[Role, CustomerVisibility]] = MappingProxyType(
    {
        Role.ADMIN: CustomerVisibility.ALL,
        Role.MANAGER: CustomerVisibility.ALL,
        Role.SALES: CustomerVisibility.OWNED,
        Role.VIEWER: CustomerVisibility.ALL,
    }
)


def _as_role(role: Role | str) -> Role | None:
    try:
        return Role(role)
    except ValueError:
        return None


def _as_permission(permission: Permission | str) -> Permission | None:
    try:
        return Permission(permission)
    except ValueError:
        return None


def permissions_for_role(role: Role | str) -> frozenset[Permission]:
    """Return a role's permissions, or an empty set for an unknown role."""
    normalized_role = _as_role(role)
    if normalized_role is None:
        return frozenset()
    return ROLE_PERMISSIONS[normalized_role]


def has_permission(current_user: CurrentUser, permission: Permission | str) -> bool:
    """Check an active user's permission without raising an HTTP exception."""
    normalized_permission = _as_permission(permission)
    return bool(
        current_user.is_active
        and normalized_permission is not None
        and normalized_permission in permissions_for_role(current_user.role)
    )


def require_permission(
    current_user: CurrentUser,
    permission: Permission | str,
) -> CurrentUser:
    """Return the user when authorized; otherwise raise FastAPI HTTP 403."""
    if not has_permission(current_user, permission):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission denied",
        )
    return current_user


def customer_visibility_for(current_user: CurrentUser) -> CustomerVisibility:
    """Return whether an active user can see all customers or only owned ones."""
    normalized_role = _as_role(current_user.role)
    if not current_user.is_active or normalized_role is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission denied",
        )
    return _CUSTOMER_VISIBILITY[normalized_role]


def can_access_customer(current_user: CurrentUser, owner_user_id: UUID) -> bool:
    """Check customer row visibility, including the read permission."""
    if not has_permission(current_user, Permission.CUSTOMER_READ):
        return False
    visibility = customer_visibility_for(current_user)
    return visibility is CustomerVisibility.ALL or owner_user_id == current_user.id
