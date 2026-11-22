"""Identity endpoints: register, login, logout and the login page."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.auth import (
    COOKIE_NAME,
    CSRF_COOKIE_NAME,
    AuthError,
    authenticate,
    issue_token,
    new_csrf_token,
    register_user,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])
pages_router = APIRouter(tags=["pages"])
_templates = Jinja2Templates(directory="templates")


class RegisterIn(BaseModel):
    phone: str
    code: str
    referrer: str | None = None


class LoginIn(BaseModel):
    provider: str = "password"
    credential: str
    phone: str | None = None
    email: str | None = None


def _set_auth_cookies(response: Response, web_token: str, csrf_token: str) -> None:
    response.set_cookie(COOKIE_NAME, web_token, httponly=True, samesite="lax", path="/")
    response.set_cookie(CSRF_COOKIE_NAME, csrf_token, httponly=False, samesite="lax", path="/")


@router.post("/register")
def register(payload: RegisterIn, response: Response, db: Session = Depends(get_db)):
    try:
        user = register_user(
            db, phone=payload.phone, code=payload.code, referrer=payload.referrer
        )
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))

    # The body token is for the API/Bearer channel; the cookie token is for
    # the web channel. Both carry sub/channel/referrer_code.
    api_token = issue_token(user, channel="api", referrer_code=payload.referrer)
    web_token = issue_token(user, channel="web", referrer_code=payload.referrer)
    _set_auth_cookies(response, web_token, new_csrf_token())
    return {
        "token": api_token,
        "user_id": user.id,
        "role": user.role,
        "referrer": payload.referrer,
    }


@router.post("/login")
def login(payload: LoginIn, response: Response, db: Session = Depends(get_db)):
    try:
        user = authenticate(
            db,
            payload.provider,
            payload.credential,
            subject=payload.phone or payload.email,
        )
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))

    api_token = issue_token(user, channel="api")
    web_token = issue_token(user, channel="web")
    _set_auth_cookies(response, web_token, new_csrf_token())
    return {"token": api_token, "user_id": user.id, "role": user.role}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    response.delete_cookie(CSRF_COOKIE_NAME, path="/")
    return {"ok": True}


@pages_router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    csrf_token = new_csrf_token()
    response = _templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"csrf_token": csrf_token},
    )
    response.set_cookie(CSRF_COOKIE_NAME, csrf_token, httponly=False, samesite="lax", path="/")
    return response
