"""Глобальная конфигурация приложения AdminisTale."""
import json
from pathlib import Path
from typing import Optional

CONFIG_PATH = Path(__file__).parent.parent / "config.json"


class AppConfig:
    """Простое JSON-хранилище настроек приложения."""

    @staticmethod
    def load() -> dict:
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    @staticmethod
    def save(data: dict):
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    @staticmethod
    def get(key: str, default=None):
        return AppConfig.load().get(key, default)

    @staticmethod
    def set(key: str, value):
        data = AppConfig.load()
        data[key] = value
        AppConfig.save(data)

    @staticmethod
    def get_curseforge_api_key() -> str:
        return AppConfig.get("curseforge_api_key", "")

    @staticmethod
    def set_curseforge_api_key(key: str):
        AppConfig.set("curseforge_api_key", key)
