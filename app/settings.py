from pydantic import BaseModel, Field, field_validator
from . import database as store
from .security import decrypt, encrypt


class Config(BaseModel):
    daily_goal: int = Field(60, ge=1, le=500)
    min_visits: int = Field(3000, ge=3000, le=1_000_000_000)
    countries: str = "BR"
    traffic_max_age: int = Field(60, ge=1, le=180)
    schedule_enabled: bool = False
    schedule_hour: int = Field(8, ge=0, le=23)
    weekdays_only: bool = True
    max_candidates: int = Field(180, ge=1, le=500)
    max_pages: int = Field(5, ge=1, le=20)
    timezone: str = "America/Sao_Paulo"
    search_topic: str = Field("", max_length=80)
    search_page_limit: int = Field(3, ge=1, le=10)
    search_query_limit: int = Field(24, ge=1, le=60)
    require_crm_check: bool = True

    @field_validator("search_topic")
    @classmethod
    def topic_valid(cls, value):
        from .discovery import normalize_topic
        return normalize_topic(value) if value.strip() else ""

    @field_validator("countries")
    @classmethod
    def countries_valid(cls, value):
        import re
        value = value.upper().replace(" ", "")
        if value and not re.fullmatch(r"[A-Z]{2}(,[A-Z]{2})*", value):
            raise ValueError("Informe códigos de país, por exemplo BR,US,PT.")
        return value

    @field_validator("timezone")
    @classmethod
    def timezone_valid(cls, value):
        from zoneinfo import ZoneInfo
        try:
            ZoneInfo(value)
        except (KeyError, ValueError) as exc:
            raise ValueError("Fuso horário inválido.") from exc
        return value


def config():
    return Config(**store.get_setting("config", {})).model_dump()


def api_key():
    import os
    from .identity import USER_ID
    return (os.getenv("STORELEADS_API_KEY") if USER_ID.get() is None else None) or decrypt(store.get_setting("api_key", ""))


def save_key(value):
    store.set_setting("api_key", encrypt(value.strip()) if value.strip() else "")
