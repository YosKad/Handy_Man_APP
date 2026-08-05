"""FastAPI application factory.

The only module that knows FastAPI exists, besides ``app/api/``. It wires
middleware, exception handlers and lifespan, and keeps the error envelope from
``docs/04-api-design.md`` §2 as the single response shape for every failure.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.container import Container
from app.core.errors import (
    AppError,
    Forbidden,
    InternalError,
    NotFound,
    RateLimited,
    Unauthenticated,
    ValidationError,
)
from app.core.logging import (
    clear_request_context,
    configure_logging,
    get_logger,
    new_request_id,
    set_request_context,
)

logger = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"

#: Starlette raises bare HTTPExceptions for routing failures. Map the ones a
#: client can actually provoke onto our error vocabulary so there is exactly one
#: error shape in the API.
_STATUS_TO_ERROR: dict[int, type[AppError]] = {
    401: Unauthenticated,
    403: Forbidden,
    404: NotFound,
    405: NotFound,
    422: ValidationError,
    500: InternalError,
}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    configure_logging(settings.log_level, json_output=not settings.is_testing)
    container = Container.build(settings)
    app.state.container = container
    logger.info(
        "application started",
        extra={
            "environment": settings.environment.value,
            "ai_provider": settings.ai_provider.value,
            "storage_backend": settings.storage_backend.value,
        },
    )
    try:
        yield
    finally:
        await container.aclose()
        logger.info("application stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()

    app = FastAPI(
        title="HandyAI API",
        version="0.1.0",
        description="AI-powered handyman assistant.",
        docs_url=resolved.docs_url,
        redoc_url=None,
        openapi_url=resolved.openapi_url,
        lifespan=lifespan,
    )
    app.state.settings = resolved

    if resolved.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=resolved.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type", REQUEST_ID_HEADER],
        )

    _register_middleware(app)
    _register_exception_handlers(app)

    app.include_router(api_router, prefix="/v1")
    _register_health_routes(app)
    return app


def _register_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def correlate_requests(
        request: Request, call_next: Callable[[Request], Awaitable[Any]]
    ) -> Any:
        """Accept or mint a request id and attach it to logs and the response."""
        request_id = request.headers.get(REQUEST_ID_HEADER) or new_request_id()
        set_request_context(request_id)
        request.state.request_id = request_id
        try:
            response = await call_next(request)
        finally:
            clear_request_context()
        response.headers[REQUEST_ID_HEADER] = request_id
        if request.url.path.startswith("/v1") and "cache-control" not in response.headers:
            # Authenticated payloads must not be cached by intermediaries.
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        log = logger.warning if exc.status_code < 500 else logger.error
        log(
            "request failed",
            extra={
                "error_code": exc.code.value,
                "status_code": exc.status_code,
                "path": request.url.path,
                # Diagnostics stay in the log; the client sees only the envelope.
                "detail": exc.log_detail,
            },
        )
        headers = (
            {"Retry-After": str(exc.retry_after_seconds)} if isinstance(exc, RateLimited) else None
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_envelope(request_id),
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Translate FastAPI's validation errors into our envelope."""
        fields = [
            {
                "field": ".".join(str(part) for part in error["loc"][1:]) or "body",
                "issue": error["msg"],
            }
            for error in exc.errors()
        ]
        app_error = ValidationError(details={"fields": fields})
        return JSONResponse(
            status_code=app_error.status_code,
            content=app_error.to_envelope(getattr(request.state, "request_id", None)),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        """Starlette's own errors — unmatched routes, wrong method — use the envelope too.

        Without this, a 404 or 405 would return ``{"detail": "..."}`` and the app
        would have two error shapes to handle. See docs/04 §2.
        """
        app_error = _STATUS_TO_ERROR.get(exc.status_code)
        error = app_error() if app_error is not None else InternalError()
        if app_error is None and exc.status_code < 500:
            # An HTTP status we have not mapped: keep the status, use a generic code.
            error = ValidationError(details={"status_code": exc.status_code})
        return JSONResponse(
            status_code=exc.status_code,
            content=error.to_envelope(getattr(request.state, "request_id", None)),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        """Last line of defence: nothing internal ever reaches the client."""
        logger.exception("unhandled exception", extra={"path": request.url.path})
        app_error = InternalError(log_detail=str(exc))
        return JSONResponse(
            status_code=app_error.status_code,
            content=app_error.to_envelope(getattr(request.state, "request_id", None)),
        )


def _register_health_routes(app: FastAPI) -> None:
    @app.get("/health/live", tags=["health"], summary="Liveness probe")
    async def live() -> dict[str, str]:
        """Process is up. Deliberately checks no dependencies."""
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"], summary="Readiness probe")
    async def ready(request: Request) -> dict[str, object]:
        """Dependency status, per dependency, so a partial outage is visible."""
        settings: Settings = request.app.state.settings
        container: Container | None = getattr(request.app.state, "container", None)
        checks = {
            "config": "ok",
            "ai_provider": settings.ai_provider.value,
            "container": "ok" if container is not None else "starting",
        }
        return {"status": "ok" if container is not None else "starting", "checks": checks}


app = create_app()
