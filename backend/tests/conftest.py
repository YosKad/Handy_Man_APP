from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest

from app.core.config import AIProvider, Environment, Settings, get_settings
from app.features.vision.domain.entities import Analysis, Field, Finding, FindingSource
from app.shared.values import Confidence


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    """Settings are process-cached; clear around each test that touches them."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        environment=Environment.TEST,
        secret_key="test-secret-key-that-is-long-enough-for-hs256",
        ai_provider=AIProvider.FAKE,
    )


@pytest.fixture
def analysis_factory() -> object:
    """Builds an Analysis from field/value pairs with a chosen confidence.

    Keeps safety tests readable: a test says what the world looks like, not how
    to construct four dataclasses.
    """

    def build(
        values: dict[Field, object],
        *,
        confidence: float = 0.95,
        project_id: uuid.UUID | None = None,
    ) -> Analysis:
        analysis = Analysis(id=uuid.uuid4(), project_id=project_id or uuid.uuid4())
        for field_key, value in values.items():
            analysis.add(
                Finding(
                    field=field_key,
                    value=value,
                    confidence=Confidence(confidence),
                    source=FindingSource.VISION,
                )
            )
        return analysis

    return build
