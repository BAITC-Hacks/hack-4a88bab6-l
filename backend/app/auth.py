"""Minimal HR-only session for the local demo. Configure HR_PASSWORD on the server."""
from __future__ import annotations

import os
import secrets
import time
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, SecretStr

router = APIRouter(prefix="/api/hr", tags=["HR access"])
COOKIE = "career_quest_hr_session"
LIFETIME = 8 * 60 * 60
_sessions: dict[str, tuple[str, float]] = {}


class HRLogin(BaseModel):
    password: SecretStr


def require_hr(request: Request, response: Response) -> str:
    token = request.cookies.get(COOKIE, "")
    role, expires = _sessions.get(token, ("", 0))
    if role != "hr" or expires <= time.time():
        _sessions.pop(token, None)
        raise HTTPException(status_code=401, detail="Войдите в аккаунт HR")
    response.headers["Cache-Control"] = "no-store"
    return role


@router.post("/login")
def login(payload: HRLogin, request: Request, response: Response) -> dict:
    expected = os.getenv("HR_PASSWORD")
    if not expected:
        raise HTTPException(status_code=503, detail="HR-вход не настроен: задайте HR_PASSWORD перед запуском сервера")
    if not secrets.compare_digest(payload.password.get_secret_value().encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Неверный пароль HR")
    now = time.time()
    for token, (_, expires) in list(_sessions.items()):
        if expires <= now:
            _sessions.pop(token, None)
    _sessions.pop(request.cookies.get(COOKIE, ""), None)
    token = secrets.token_urlsafe(32)
    _sessions[token] = ("hr", now + LIFETIME)
    response.set_cookie(COOKIE, token, max_age=LIFETIME, httponly=True,
                        secure=request.url.scheme == "https", samesite="strict")
    response.headers["Cache-Control"] = "no-store"
    return {"role": "hr"}


@router.get("/session")
def session(role: str = Depends(require_hr)) -> dict:
    return {"role": role}


@router.post("/logout")
def logout(request: Request, response: Response) -> dict:
    _sessions.pop(request.cookies.get(COOKIE, ""), None)
    response.delete_cookie(COOKIE)
    return {"message": "Вы вышли из HR"}
