from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    app_env: str
    database_path: Path
    storage_root: Path
    output_root: Path
    temp_root: Path
    rag_root: Path
    discord_bot_token: str
    target_channel_ids: tuple[int, ...]
    ai_provider: str
    ai_api_key: str
    ai_model: str
    ai_base_url: str
    repository_backend: str = "sqlite"
    storage_backend: str = "local"
    insforge_database_url: str | None = None
    insforge_project_id: str | None = None
    insforge_storage_namespace: str | None = None
    notification_webhook_url: str | None = None
    notification_webhook_username: str | None = None
    notification_webhook_avatar_url: str | None = None
    notification_slack_webhook_url: str | None = None
    notification_line_api_base_url: str = "https://api.line.me"
    notification_line_data_api_base_url: str = "https://api-data.line.me"
    notification_line_channel_access_token: str | None = None
    notification_line_recipient_ids: tuple[str, ...] = ()
    line_channel_secret: str | None = None
    line_inbox_case_code: str = "LINE-INBOX"
    notification_email_smtp_host: str | None = None
    notification_email_smtp_port: int = 587
    notification_email_smtp_username: str | None = None
    notification_email_smtp_password: str | None = None
    notification_email_use_tls: bool = True
    notification_email_from: str | None = None
    notification_email_recipients: tuple[str, ...] = ()
    notification_email_subject_prefix: str = "O's flow"
    notification_auto_urgent_targets: tuple[str, ...] = ("line", "discord")
    notification_auto_warning_targets: tuple[str, ...] = ("slack", "email")


def _require_env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default or "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def _parse_channel_ids(raw_value: str) -> tuple[int, ...]:
    values = [item.strip() for item in raw_value.split(",") if item.strip()]
    if not values:
        raise ValueError("DISCORD_TARGET_CHANNEL_IDS must contain at least one channel ID.")
    return tuple(int(item) for item in values)


def _parse_bool(raw_value: str | None, default: bool = False) -> bool:
    if raw_value is None:
        return default
    value = raw_value.strip().lower()
    if not value:
        return default
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {raw_value}")


def _parse_csv(raw_value: str | None) -> tuple[str, ...]:
    if raw_value is None:
        return ()
    values = [item.strip() for item in raw_value.split(",") if item.strip()]
    return tuple(values)


def load_settings() -> Settings:
    load_dotenv()

    return Settings(
        app_env=os.getenv("APP_ENV", "development").strip() or "development",
        database_path=Path(os.getenv("DATABASE_PATH", "data/app.db")),
        storage_root=Path(os.getenv("STORAGE_ROOT", "storage")),
        output_root=Path(os.getenv("OUTPUT_ROOT", "output")),
        temp_root=Path(os.getenv("TEMP_ROOT", "temp")),
        rag_root=Path(os.getenv("RAG_ROOT", "storage/rag")),
        discord_bot_token=os.getenv("DISCORD_BOT_TOKEN", "").strip(),
        target_channel_ids=_parse_channel_ids(_require_env("DISCORD_TARGET_CHANNEL_IDS", "123456789012345678")),
        ai_provider=os.getenv("AI_PROVIDER", "openai_compatible").strip() or "openai_compatible",
        ai_api_key=os.getenv("AI_API_KEY", "").strip(),
        ai_model=os.getenv("AI_MODEL", "gpt-4.1").strip() or "gpt-4.1",
        ai_base_url=os.getenv("AI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
        repository_backend=os.getenv("REPOSITORY_BACKEND", "sqlite").strip() or "sqlite",
        storage_backend=os.getenv("STORAGE_BACKEND", "local").strip() or "local",
        insforge_database_url=os.getenv("INSFORGE_DATABASE_URL", "").strip() or None,
        insforge_project_id=os.getenv("INSFORGE_PROJECT_ID", "").strip() or None,
        insforge_storage_namespace=os.getenv("INSFORGE_STORAGE_NAMESPACE", "").strip() or None,
        notification_webhook_url=os.getenv("NOTIFICATION_WEBHOOK_URL", "").strip() or None,
        notification_webhook_username=os.getenv("NOTIFICATION_WEBHOOK_USERNAME", "").strip() or None,
        notification_webhook_avatar_url=os.getenv("NOTIFICATION_WEBHOOK_AVATAR_URL", "").strip() or None,
        notification_slack_webhook_url=os.getenv("NOTIFICATION_SLACK_WEBHOOK_URL", "").strip() or None,
        notification_line_api_base_url=os.getenv("NOTIFICATION_LINE_API_BASE_URL", "https://api.line.me").rstrip("/"),
        notification_line_data_api_base_url=os.getenv("NOTIFICATION_LINE_DATA_API_BASE_URL", "https://api-data.line.me").rstrip("/"),
        notification_line_channel_access_token=os.getenv("NOTIFICATION_LINE_CHANNEL_ACCESS_TOKEN", "").strip() or None,
        notification_line_recipient_ids=_parse_csv(os.getenv("NOTIFICATION_LINE_RECIPIENT_IDS")),
        line_channel_secret=os.getenv("LINE_CHANNEL_SECRET", "").strip() or None,
        line_inbox_case_code=os.getenv("LINE_INBOX_CASE_CODE", "LINE-INBOX").strip() or "LINE-INBOX",
        notification_email_smtp_host=os.getenv("NOTIFICATION_EMAIL_SMTP_HOST", "").strip() or None,
        notification_email_smtp_port=int(os.getenv("NOTIFICATION_EMAIL_SMTP_PORT", "587").strip() or "587"),
        notification_email_smtp_username=os.getenv("NOTIFICATION_EMAIL_SMTP_USERNAME", "").strip() or None,
        notification_email_smtp_password=os.getenv("NOTIFICATION_EMAIL_SMTP_PASSWORD", "").strip() or None,
        notification_email_use_tls=_parse_bool(os.getenv("NOTIFICATION_EMAIL_USE_TLS"), default=True),
        notification_email_from=os.getenv("NOTIFICATION_EMAIL_FROM", "").strip() or None,
        notification_email_recipients=_parse_csv(os.getenv("NOTIFICATION_EMAIL_RECIPIENTS")),
        notification_email_subject_prefix=os.getenv("NOTIFICATION_EMAIL_SUBJECT_PREFIX", "O's flow").strip()
        or "O's flow",
        notification_auto_urgent_targets=_parse_csv(os.getenv("NOTIFICATION_AUTO_URGENT_TARGETS"))
        or ("line", "discord"),
        notification_auto_warning_targets=_parse_csv(os.getenv("NOTIFICATION_AUTO_WARNING_TARGETS"))
        or ("slack", "email"),
    )
