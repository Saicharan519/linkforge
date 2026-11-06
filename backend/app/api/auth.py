"""/api/v1/auth: register, login, logout, me. Thin router: validate, call the service, respond."""

from fastapi import APIRouter, Depends, Request, Response

from app.api.deps import (
    AppSettings,
    ClientIp,
    DbSession,
    Limiter,
    Now,
    Passwords,
    SessionAuth,
    Sessions,
    origin_guard,
)
from app.core.cookies import SESSION_COOKIE, clear_session_cookies, set_session_cookies
from app.core.sessions import USER_AGENT_MAX
from app.schemas.auth import ChangePasswordRequest, LoginRequest, RegisterRequest, UserOut
from app.services import auth as service

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/register", status_code=201, dependencies=[Depends(origin_guard)])
async def register(body: RegisterRequest, db: DbSession, passwords: Passwords) -> UserOut:
    user = await service.register(db, passwords, body.email, body.password)
    return UserOut(id=user.id, email=user.email, created_at=user.created_at)


@router.post("/login", dependencies=[Depends(origin_guard)])
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: DbSession,
    passwords: Passwords,
    sessions: Sessions,
    limiter: Limiter,
    settings: AppSettings,
    ip: ClientIp,
    now: Now,
) -> UserOut:
    user, sid, csrf_token = await service.login(
        db,
        passwords,
        sessions,
        limiter,
        settings,
        now,
        ip=ip,
        email_raw=body.email,
        password=body.password,
        user_agent=request.headers.get("user-agent", "")[:USER_AGENT_MAX],
        previous_sid=request.cookies.get(SESSION_COOKIE),
    )
    set_session_cookies(response, settings, sid, csrf_token)
    return UserOut(id=user.id, email=user.email, created_at=user.created_at)


@router.post("/logout", status_code=204)
async def logout(ctx: SessionAuth, sessions: Sessions, settings: AppSettings) -> Response:
    await service.logout(sessions, ctx.sid, ctx.user_id)
    response = Response(status_code=204)
    clear_session_cookies(response, settings)
    return response


@router.get("/me")
async def me(ctx: SessionAuth) -> UserOut:
    return UserOut(id=ctx.user_id, email=ctx.email, created_at=ctx.created_at)


@router.post("/change-password", status_code=204)
async def change_password(
    body: ChangePasswordRequest,
    ctx: SessionAuth,
    db: DbSession,
    passwords: Passwords,
    sessions: Sessions,
) -> Response:
    await service.change_password(
        db, passwords, sessions, ctx.user_id, ctx.sid, body.old_password, body.new_password
    )
    return Response(status_code=204)
