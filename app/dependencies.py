from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status

from app.database import CRMRepository
from app.permissions import CurrentUser
from app.security import digest_session_token


def get_repo(request: Request) -> CRMRepository:
    return request.app.state.repository


async def get_current_user(
    request: Request,
    repository: CRMRepository = Depends(get_repo),
) -> CurrentUser:
    cookie_name: str = request.app.state.settings.session_cookie_name
    token = request.cookies.get(cookie_name, "")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="请先登录",
        )
    user = await repository.get_user_by_session(digest_session_token(token))
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登录已过期，请重新登录",
        )
    return user
