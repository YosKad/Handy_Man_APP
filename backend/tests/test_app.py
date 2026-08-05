"""Application-level behaviour: envelope, correlation, headers, health, meta.

These run against the real ASGI app with the ``fake`` provider, so they need no
database and make no network calls.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from app.core.config import AIProvider, Environment, Settings, StorageBackend
from app.core.errors import ConfidenceTooLow, NotFound, RateLimited
from app.main import REQUEST_ID_HEADER, create_app


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings)

    # Routes that only exist to exercise the error pipeline.
    probe = APIRouter()

    @probe.get("/boom/app-error")
    async def app_error() -> None:
        raise ConfidenceTooLow(details={"missing_fields": ["wall.material"]})

    @probe.get("/boom/not-found")
    async def not_found() -> None:
        raise NotFound()

    @probe.get("/boom/rate-limited")
    async def rate_limited() -> None:
        raise RateLimited(retry_after_seconds=30)

    @probe.get("/boom/unexpected")
    async def unexpected() -> None:
        raise RuntimeError("a secret internal detail")

    @probe.get("/probe/validated")
    async def validated(count: int) -> dict[str, int]:
        return {"count": count}

    app.include_router(probe, prefix="/v1")
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


# ------------------------------------------------------------------ health


def test_liveness_checks_no_dependencies(client: TestClient) -> None:
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_reports_per_dependency_status(client: TestClient) -> None:
    response = client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["ai_provider"] == "fake"


# ------------------------------------------------------------------ meta


def test_meta_exposes_consent_version_and_media_limits(client: TestClient) -> None:
    """The app reads these instead of hardcoding them, so policy can change
    without an app release."""
    body = client.get("/v1/meta").json()

    assert body["api_version"] == "v1"
    assert body["consent_version"]
    assert body["media"]["max_image_bytes"] > 0
    assert "image/heic" in body["media"]["accepted_image_types"]


# ------------------------------------------------------------------ errors


def test_app_error_is_serialised_as_the_envelope(client: TestClient) -> None:
    response = client.get("/v1/boom/app-error")

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "confidence_too_low"
    assert error["details"] == {"missing_fields": ["wall.material"]}
    assert error["retryable"] is True
    assert error["request_id"]


def test_not_found_uses_the_same_envelope(client: TestClient) -> None:
    error = client.get("/v1/boom/not-found").json()["error"]
    assert error["code"] == "not_found"
    assert error["retryable"] is False


def test_rate_limited_sets_retry_after(client: TestClient) -> None:
    response = client.get("/v1/boom/rate-limited")
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "30"


def test_unexpected_exception_leaks_nothing(client: TestClient) -> None:
    response = client.get("/v1/boom/unexpected")

    assert response.status_code == 500
    body = response.text
    assert "a secret internal detail" not in body
    assert response.json()["error"]["code"] == "internal_error"


def test_unmatched_route_uses_the_envelope(client: TestClient) -> None:
    """Starlette's own 404 shape must not leak: the API has one error shape."""
    response = client.get("/v1/does-not-exist")

    assert response.status_code == 404
    body = response.json()
    assert "detail" not in body
    assert body["error"]["code"] == "not_found"
    assert body["error"]["request_id"]


def test_wrong_method_uses_the_envelope(client: TestClient) -> None:
    response = client.post("/v1/meta")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "not_found"


def test_body_validation_error_uses_the_envelope(client: TestClient) -> None:
    """FastAPI's own 422 shape must not leak past the handler."""
    response = client.get("/v1/probe/validated", params={"count": "not-a-number"})

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"]["fields"][0]["field"] == "count"


# ------------------------------------------------------------------ headers


def test_request_id_is_echoed_when_supplied(client: TestClient) -> None:
    response = client.get("/health/live", headers={REQUEST_ID_HEADER: "caller-supplied-id"})
    assert response.headers[REQUEST_ID_HEADER] == "caller-supplied-id"


def test_request_id_is_generated_when_absent(client: TestClient) -> None:
    response = client.get("/health/live")
    assert response.headers[REQUEST_ID_HEADER]


def test_error_envelope_request_id_matches_the_header(client: TestClient) -> None:
    response = client.get("/v1/boom/not-found", headers={REQUEST_ID_HEADER: "trace-me"})
    assert response.json()["error"]["request_id"] == "trace-me"
    assert response.headers[REQUEST_ID_HEADER] == "trace-me"


def test_api_responses_are_not_cacheable(client: TestClient) -> None:
    response = client.get("/v1/meta")
    assert response.headers["Cache-Control"] == "no-store"


def test_security_headers_are_present(client: TestClient) -> None:
    headers = client.get("/health/live").headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "no-referrer"


# ------------------------------------------------------------------ docs exposure


def test_docs_are_served_outside_production(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        assert client.get("/docs").status_code == 200
        assert client.get("/openapi.json").status_code == 200


def test_docs_are_hidden_in_production() -> None:
    production = Settings(
        environment=Environment.PRODUCTION,
        secret_key="p" * 48,
        ai_provider=AIProvider.OPENAI,
        openai_api_key="key",
        storage_backend=StorageBackend.S3,
        cors_origins=["https://handyai.app"],
    )
    app = create_app(production)
    # Built without entering the lifespan: production startup needs real adapters.
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
