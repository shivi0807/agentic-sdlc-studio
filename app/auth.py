from __future__ import annotations

from typing import Any, cast

from fastapi import HTTPException, Request, status

SESSION_COOKIE = "sdlc_session"


def current_user(request: Request) -> dict[str, Any]:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in required")
    user = cast(dict[str, Any] | None, request.app.state.repository.user_for_session(token))
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    return user
