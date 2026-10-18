"""Application settings loaded from RS_ environment variables."""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Every value comes from an environment variable."""

    model_config = SettingsConfigDict(env_prefix="RS_", extra="ignore")

    workspace: Path = Path("./workspace")
    policy_path: Path = Path("./.aishield")
    data_dir: Path = Path("./data")
    profile: str | None = None
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8001
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    approver_token: str = ""
    log_level: str = "INFO"

    def model_post_init(self, _context: object) -> None:
        """Generate an approver token when none was supplied."""
        if not self.approver_token:
            object.__setattr__(self, "approver_token", secrets.token_urlsafe(24))

    @property
    def workspace_abs(self) -> Path:
        """Absolute workspace jail root."""
        return self.workspace.resolve()

    @property
    def data_abs(self) -> Path:
        """Absolute directory holding the audit DB and snapshots."""
        return self.data_dir.resolve()

    @property
    def policy_abs(self) -> Path:
        """Absolute path to the policy file."""
        return self.policy_path.resolve()

    @property
    def db_path(self) -> Path:
        """Absolute path to the audit database."""
        return self.data_abs / "audit.db"


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
