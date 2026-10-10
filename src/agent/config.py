"""Application configuration.

Loaded from `config/config.yaml` with environment-variable overrides from
`.env`. Secrets (API keys) are intentionally *not* part of the pydantic model —
they are read directly from the environment by the respective providers.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.yaml"


class AgentConfig(BaseModel):
    name: str = "Rujul's Agent"
    dry_run: bool = True


class WhatsAppConfig(BaseModel):
    provider: str = "mock"  # mock | openclaw
    self_chat_id: str = ""
    bridge_url: str = "http://127.0.0.1:18788/wa"


class MonitoringConfig(BaseModel):
    private_chats: bool = True
    group_chats: bool = True
    private_unread_threshold_minutes: int = 10
    private_read_unanswered_threshold_minutes: int = 60


class GroupsConfig(BaseModel):
    monitor: bool = True
    automatic_actions: bool = False


class LLMConfig(BaseModel):
    provider: str = "mock"  # mock | nvidia_nim
    model: str = ""
    confidence_threshold: float = 0.75
    nim_base_url: str = "https://integrate.api.nvidia.com/v1"


class UnreadAckConfig(BaseModel):
    enabled: bool = True
    delay_minutes: int = 10


class ReadUnansweredAckConfig(BaseModel):
    enabled: bool = True
    delay_minutes: int = 60


class AcknowledgementConfig(BaseModel):
    enabled: bool = True
    minimum_confidence: float = 0.75
    unread: UnreadAckConfig = Field(default_factory=UnreadAckConfig)
    read_unanswered: ReadUnansweredAckConfig = Field(default_factory=ReadUnansweredAckConfig)
    categories: dict[str, bool] = Field(
        default_factory=lambda: {
            "DOCUMENT_REQUEST": True,
            "TASK_REQUEST": True,
            "CALLBACK_REQUEST": True,
            "MEETING_REQUEST": True,
            "TIME_SENSITIVE": True,
            "APPROVAL_REQUIRED": True,
            "FOLLOW_UP": True,
            "REMINDER": True,
            "CASUAL": False,
            "INFORMATIONAL": False,
            "UNKNOWN": False,
        }
    )

    def category_enabled(self, intent: str) -> bool:
        return self.categories.get(intent, False)

    @field_validator("categories")
    @classmethod
    def _normalize_keys(cls, value: Mapping[str, bool]) -> dict[str, bool]:
        return {str(k).upper(): bool(v) for k, v in value.items()}


class TasksConfig(BaseModel):
    enabled: bool = True


class MemoryConfig(BaseModel):
    enabled: bool = False


class NotificationsConfig(BaseModel):
    self_chat: bool = True


class RateLimitsConfig(BaseModel):
    max_ack_per_chat_per_day: int = 2
    minimum_ack_interval_minutes: int = 60


class SchedulerConfig(BaseModel):
    poll_interval_seconds: float = 5.0


class BridgeConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8090


class DemoConfig(BaseModel):
    simulate_on_start: bool = False
    message: str = "Hey, can you send me the latest telemetry report?"
    sender_id: str = "demo-contact"


class Settings(BaseModel):
    agent: AgentConfig = Field(default_factory=AgentConfig)
    timezone: str = "Asia/Kolkata"
    whatsapp: WhatsAppConfig = Field(default_factory=WhatsAppConfig)
    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)
    groups: GroupsConfig = Field(default_factory=GroupsConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    acknowledgement: AcknowledgementConfig = Field(default_factory=AcknowledgementConfig)
    tasks: TasksConfig = Field(default_factory=TasksConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    rate_limits: RateLimitsConfig = Field(default_factory=RateLimitsConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    bridge: BridgeConfig = Field(default_factory=BridgeConfig)
    demo: DemoConfig = Field(default_factory=DemoConfig)

    database_path: str = "./data/agent.db"
    log_level: str = "INFO"


def _env_overrides(settings: Settings) -> None:
    """Apply `.env` / environment overrides on top of YAML/ defaults."""
    if path := os.getenv("DATABASE_PATH"):
        settings.database_path = path
    if level := os.getenv("LOG_LEVEL"):
        settings.log_level = level
    if prov := os.getenv("WHATSAPP_PROVIDER"):
        settings.whatsapp.provider = prov
    if prov := os.getenv("LLM_PROVIDER"):
        settings.llm.provider = prov
    if settings.whatsapp.self_chat_id == "" and os.getenv("SELF_CHAT_ID"):
        settings.whatsapp.self_chat_id = os.getenv("SELF_CHAT_ID", "")
    if model := os.getenv("NVIDIA_NIM_MODEL"):
        settings.llm.model = model
    if bridge_url := os.getenv("OPENCLAW_BRIDGE_URL"):
        settings.whatsapp.bridge_url = bridge_url
    if host := os.getenv("BRIDGE_HOST"):
        settings.bridge.host = host
    try:
        if port := int(os.getenv("BRIDGE_PORT", "")):
            settings.bridge.port = port
    except ValueError:
        pass


def load_settings(path: str | Path | None = None) -> Settings:
    """Load settings from YAML + env, returning a validated `Settings`."""
    load_dotenv()
    raw: dict[str, Any] = {}
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    settings = Settings.model_validate(raw)
    _env_overrides(settings)
    return settings