"""Application configuration, loaded from environment / .env.

Reuses the existing OPENAI_* keys in .env for the vision extraction, and adds
a few app-level settings (session secret, registration toggle, paths).
"""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- LLM (vision) — existing keys in .env ---
    openai_endpoint: str = ""
    openai_model: str = ""
    openai_apikey: str = ""

    # --- App / auth ---
    session_secret: str = "dev-insecure-secret-change-me"
    registration_open: bool = True
    admin_username: str = ""  # optional: bootstrap this username as admin on startup
    cookie_secure: bool = False  # set True when served over HTTPS
    # ICP 备案号：站点专属信息，留空则底部不显示备案栏（在 .env 中设置）
    beian: str = ""
    # Behind a trusted reverse proxy (Cloudflare Tunnel / Nginx / Caddy) the socket
    # peer is the proxy, so the real client IP arrives in a header. Used for the
    # login/register rate limiter. Keep True for any proxied deploy; set False only
    # if the app is exposed directly (no proxy), where headers would be spoofable.
    trust_proxy_headers: bool = True

    # --- Paths ---
    docs_dir: Path = BASE_DIR / "docs"
    data_dir: Path = BASE_DIR / "data"
    # DB location is separate so it can live on a mounted volume in Docker
    # without shadowing the baked-in data/extracted content. Override via DB_PATH.
    db_path: Path = BASE_DIR / "data" / "app.db"

    @property
    def extracted_dir(self) -> Path:
        return self.data_dir / "extracted"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.db_path}"


settings = Settings()
