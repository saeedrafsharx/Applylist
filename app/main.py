from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

from .bootstrap import seed
from .config import settings
from .db import session_scope
from .deps import AppRedirect, render, set_flash
from .routers import admin, assistant, billing, catalog, contacts, pages, positions
from .routers import auth as auth_router

logging.basicConfig(
    level=logging.INFO if not settings.db_echo else logging.DEBUG,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
log = logging.getLogger("applylist")

# How stale `last_seen_at` may get before we write it again. Without this every
# request would issue a write just to update a timestamp.
LAST_SEEN_INTERVAL = timedelta(minutes=15)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Schema itself is owned by Alembic (`alembic upgrade head`), not by the app.
    # Startup only plants the rows the app can't run without.
    try:
        with session_scope() as db:
            seed(db)
    except Exception:  # noqa: BLE001 - a seeding failure must be visible, not fatal
        log.exception("Startup seeding failed - the app is running, but check the database")

    if settings.is_production and settings.secret_key == "dev-secret-change-me":
        log.error(
            "SECRET_KEY is still the development default in production. "
            "Every session cookie is forgeable until this is set."
        )
    yield


app = FastAPI(
    title="ApplyList",
    description="Contact and application tracker for graduate and research applications.",
    lifespan=lifespan,
    docs_url="/api/docs" if not settings.is_production else None,
    redoc_url=None,
)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie=settings.session_cookie,
    max_age=settings.session_max_age,
    same_site="lax",
    https_only=settings.is_production,
)


class UserStateMiddleware(BaseHTTPMiddleware):
    """
    Put the signed-in user on `request.state` for templates (the nav needs it on
    every page) and keep `last_seen_at` roughly current for the admin panel.

    The object here is detached once the scope closes - read-only. Routes that
    write take their own session-bound user via `Depends(require_user)`.
    """

    async def dispatch(self, request: Request, call_next):
        request.state.user = None
        uid = request.session.get("uid") if "session" in request.scope else None

        if uid:
            try:
                with session_scope() as db:
                    from .models import User

                    user = db.get(User, int(uid))
                    if user is not None and user.is_active:
                        now = datetime.now(timezone.utc)
                        last = user.last_seen_at
                        if last is not None and last.tzinfo is None:
                            last = last.replace(tzinfo=timezone.utc)
                        if last is None or now - last > LAST_SEEN_INTERVAL:
                            user.last_seen_at = now
                        request.state.user = user
            except Exception:  # noqa: BLE001 - never 500 a page over the nav avatar
                log.exception("Could not load the session user")

        return await call_next(request)


app.add_middleware(UserStateMiddleware)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.exception_handler(AppRedirect)
async def _handle_redirect(request: Request, exc: AppRedirect):
    if exc.message:
        set_flash(request, exc.message, exc.level)
    return RedirectResponse(exc.url, status_code=303)


@app.exception_handler(404)
async def _handle_404(request: Request, exc):
    return render(request, "errors/404.html", status_code=404)


@app.exception_handler(500)
async def _handle_500(request: Request, exc):  # pragma: no cover - defensive
    log.exception("Unhandled error on %s", request.url.path)
    return render(request, "errors/500.html", status_code=500)


app.include_router(pages.router)
app.include_router(auth_router.router)
app.include_router(contacts.router)
app.include_router(positions.router)
app.include_router(catalog.router)
app.include_router(assistant.router)
app.include_router(billing.router)
app.include_router(admin.router)
