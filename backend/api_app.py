"""Dashboard API application.

Phase 5 only needs a liveness stub; Phase 6 adds the token-protected routes and
WebSocket.
"""

from __future__ import annotations

from fastapi import FastAPI

from backend.config import Settings

VERSION = "0.1.0"


def create_api_app(settings: Settings) -> FastAPI:
    """Build the human-facing API application."""
    app = FastAPI(
        title="RepoShield Dashboard API",
        version=VERSION,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        """Liveness and version."""
        return {"status": "ok", "version": VERSION}

    return app
