"""Env-driven settings. Mirrors restaurant-manager's `config.py` shape so
the two apps configure the same way.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://menu:menu@db:5432/menu"
    company_name: str = "Alimentos y Servicios"

    # restaurant-manager's read-only JSON API (PLAN.md §5). This app never
    # writes to it -- non-negotiable #8.
    upstream_api_url: str = "http://host.docker.internal:8000/api/v1"
    upstream_timeout_seconds: int = 15

    # Costing defaults (PLAN.md §7.4), overridable per insumo from Ajustes.
    costing_method_default: str = "median_window"
    costing_window_days_default: int = 90

    # LLM-assisted mapping (PLAN.md §8) -- an accelerator, never a
    # dependency. Blank key means the mapping queue falls back to
    # deterministic string similarity; nothing blocks on this.
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-haiku-4-5-20251001"
    anthropic_timeout_seconds: int = 20


settings = Settings()
