import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"


def _default_preview_origin_regex() -> str:
    """Allow local preview hosts + the PUBLIC_BASE_URL host (pilot)."""
    public = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
    host = urlparse(public).hostname or "localhost"
    host_re = re.escape(host)
    return (
        r"^(https://[a-z0-9-]+\.vercel\.app|"
        r"http://(localhost|127\.0\.0\.1)(:[0-9]+)?|"
        rf"https?://{host_re}(:[0-9]+)?)$"
    )


@dataclass(frozen=True)
class Settings:
    app_host: str = os.getenv("APP_HOST", "0.0.0.0")
    app_port: int = int(os.getenv("APP_PORT", "8000"))
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./pipeline.db")
    public_base_url: str = field(
        default_factory=lambda: os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
    )
    preview_origin_regex: str = field(
        default_factory=lambda: os.getenv("PREVIEW_ORIGIN_REGEX") or _default_preview_origin_regex()
    )

    inbound_webhook_secret: str = os.getenv("INBOUND_WEBHOOK_SECRET", "change_this_secret")
    admin_token: str = os.getenv("ADMIN_TOKEN", "change_this_token")

    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    chat_model: str = os.getenv("CHAT_MODEL", "gpt-4o-mini")

    vercel_token: str = os.getenv("VERCEL_TOKEN", "")
    vercel_team_id: str = os.getenv("VERCEL_TEAM_ID", "")
    vercel_project_id: str = os.getenv("VERCEL_PROJECT_ID", "")
    vercel_github_repo_id: str = os.getenv("VERCEL_GITHUB_REPO_ID", "")
    vercel_git_ref: str = os.getenv("VERCEL_GIT_REF", "main")

    per_lead_token_budget: float = float(os.getenv("PER_LEAD_TOKEN_BUDGET", "2.0"))


settings = Settings()
