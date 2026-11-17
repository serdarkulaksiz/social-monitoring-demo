"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .. import __version__
from ..application.errors import MonitorError
from ..application.ports import Clock
from ..config import Settings
from ..container import Container, build_container
from .dependencies import require_api_key
from .errors import install_error_handlers
from .routers import alerts, analysis, collector_runs, dashboard, health, incidents, posts, quality, stats, topics

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent / "dashboard" / "static"
# Dashboard pages load nothing from other origins and run no inline code.
DASHBOARD_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
    "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)


def create_app(
    settings: Settings,
    *,
    clock: Clock | None = None,
    run_background_evaluation: bool = True,
) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        container = build_container(settings, clock)
        app.state.container = container
        try:
            recovered = container.monitoring.recover()
            if recovered:
                logger.warning("classified %d stored posts that had no analysis result", recovered)
            container.monitoring.evaluate()
            task = None
            if run_background_evaluation:
                task = asyncio.create_task(_evaluation_loop(container, settings.evaluation_interval_seconds))
            logger.info("monitor started; data_dir=%s", settings.data_dir.resolve())
            yield
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        finally:
            container.lock.release()

    app = FastAPI(
        title="Social Incident Monitor",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
    )
    install_error_handlers(app)
    # Answer only to the configured host names: keeps a web page from reaching the local
    # dashboard through a DNS-rebinding trick.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.url.path.startswith("/dashboard"):
            response.headers.setdefault("Content-Security-Policy", DASHBOARD_CSP)
            response.headers.setdefault("X-Frame-Options", "DENY")
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    app.include_router(health.router)
    protected = [Depends(require_api_key)]
    for module in (posts, analysis, incidents, alerts, stats, collector_runs, quality, topics):
        app.include_router(module.router, dependencies=protected)
    if settings.dashboard_enabled:
        app.mount("/dashboard/static", StaticFiles(directory=STATIC_DIR), name="dashboard-static")
        app.include_router(dashboard.router)
        app.include_router(dashboard.actions)

        @app.get("/", include_in_schema=False)
        def root() -> RedirectResponse:
            return RedirectResponse("/dashboard")

    return app


async def _evaluation_loop(container: Container, interval_seconds: int) -> None:
    """Periodic evaluation: time-based incident resolution and collector staleness."""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await asyncio.to_thread(container.monitoring.evaluate)
        except MonitorError as exc:
            logger.error("periodic evaluation failed: %s", exc)
        except Exception:
            logger.exception("unexpected error in periodic evaluation")
