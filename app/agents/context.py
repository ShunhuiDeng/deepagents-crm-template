from dataclasses import dataclass
from uuid import UUID

from app.permissions import CurrentUser, Role


@dataclass(frozen=True)
class CRMContext:
    """Server-authenticated identity propagated to Agent tools and the subagent."""

    user_id: UUID
    username: str
    display_name: str
    email: str
    role: str
    permissions: frozenset[str]
    conversation_id: UUID
    request_id: str

    def current_user(self) -> CurrentUser:
        return CurrentUser(
            id=self.user_id,
            username=self.username,
            email=self.email,
            display_name=self.display_name,
            role=Role(self.role),
            is_active=True,
        )
