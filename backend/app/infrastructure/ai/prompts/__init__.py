"""Versioned prompt loading.

Prompts are files, not string literals, and they are *versioned rather than
edited*: a guide the user is halfway through must remain reproducible, so
changing behaviour means adding ``<purpose>.v2.md`` and pointing the registry at
it (``docs/06-ai-pipeline.md`` §5).

Every prompt is hashed at load time and the version is recorded on every AI call
and every generated artefact, so any output can be traced back to the exact text
that produced it.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

from app.core.errors import InternalError

PROMPT_DIR = Path(__file__).parent

#: Purpose -> active version. Bumping a value here is a deliberate,
#: reviewable behaviour change.
ACTIVE_VERSIONS: dict[str, int] = {
    "vision_system": 1,
    "vision_analyze": 1,
    "guide_system": 1,
    "generate_guide": 1,
    "chat_system": 1,
    "validate_step": 1,
}


@lru_cache(maxsize=32)
def load_prompt(purpose: str) -> str:
    """Return the active prompt text for a purpose.

    Cached: prompts are immutable at runtime, so a file edit requires a restart.
    That is deliberate — it makes the running configuration knowable.
    """
    version = ACTIVE_VERSIONS.get(purpose)
    if version is None:
        raise InternalError(log_detail=f"no active prompt registered for {purpose!r}")

    path = PROMPT_DIR / f"{purpose}.v{version}.md"
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise InternalError(log_detail=f"prompt file missing: {path.name}") from exc


def prompt_version(purpose: str) -> str:
    """Version label recorded alongside every call: ``vision_analyze.v1+a1b2c3d4``."""
    version = ACTIVE_VERSIONS.get(purpose)
    if version is None:
        raise InternalError(log_detail=f"no active prompt registered for {purpose!r}")
    digest = hashlib.sha256(load_prompt(purpose).encode()).hexdigest()[:8]
    return f"{purpose}.v{version}+{digest}"


def all_prompt_versions() -> dict[str, str]:
    """Every active prompt with its hash — logged at startup and exposed to ops."""
    return {purpose: prompt_version(purpose) for purpose in ACTIVE_VERSIONS}
