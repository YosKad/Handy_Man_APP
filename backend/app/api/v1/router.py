"""v1 router aggregation.

Feature routers are mounted here as they land, so there is one place to see the
public surface of the API. Route modules arrive with the persistence layer in the
next Phase 2 commits (auth, media, projects, chat); until then this router
carries only the metadata endpoint, which is genuinely useful to the client for
consent and version negotiation.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.core.config import Settings

api_router = APIRouter()


@api_router.get("/meta", tags=["meta"], summary="Client bootstrap metadata")
async def meta(request: Request) -> dict[str, object]:
    """Values the app needs before a user is signed in.

    Lets the client learn the current consent version and media limits rather
    than hardcoding them, so a policy change does not require an app release.
    """
    settings: Settings = request.app.state.settings
    return {
        "api_version": "v1",
        "consent_version": settings.consent_version,
        "media": {
            "max_image_bytes": settings.media_max_image_bytes,
            "max_video_bytes": settings.media_max_video_bytes,
            "max_images_per_analysis": settings.ai_vision_max_images,
            "accepted_image_types": [
                "image/jpeg",
                "image/png",
                "image/webp",
                "image/heic",
            ],
        },
        "account_deletion_grace_days": settings.account_deletion_grace_days,
    }
