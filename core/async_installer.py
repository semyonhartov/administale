"""
Асинхронный установщик серверных инстансов.
Работает параллельно с основным приложением через asyncio.
"""
import asyncio
import logging
from pathlib import Path
from typing import Callable, Optional

from core.server_manager import ServerInstance, ServerStatus
from core.instance_builder import InstanceBuilder

logger = logging.getLogger(__name__)


class AsyncInstaller:
    """
    Управляет асинхронной установкой серверных инстансов.
    Может работать параллельно с основным приложением.
    """

    def __init__(self):
        self._active_installs: dict = {}

    async def install(self, server: ServerInstance, progress_callback: Optional[Callable] = None, auth_link_callback: Optional[Callable] = None) -> bool:
        """
        Асинхронная установка серверного инстанса.
        Возвращает True при успехе, False при ошибке.
        """
        logger.info(f"Запуск установки инстанса {server.id} ({server.name})")
        if server.id in self._active_installs:
            logger.warning(f"Установка {server.id} уже активна")
            return False

        self._active_installs[server.id] = {
            "server": server,
            "status": "installing",
            "progress": 0,
        }

        builder = InstanceBuilder(server, progress_callback=progress_callback)

        try:
            result = await builder.build(auth_link_callback=auth_link_callback)
            logger.info(f"Установка {server.id} завершена с результатом: {result}")

            self._active_installs[server.id]["status"] = "completed" if result else "failed"
            self._active_installs[server.id]["progress"] = 100

            return result
        except Exception as e:
            logger.exception(f"Ошибка установки {server.id}: {e}")
            self._active_installs[server.id]["status"] = "failed"
            builder.cleanup_failed()
            return False
        finally:
            del self._active_installs[server.id]

    def get_install_status(self, server_id: str) -> Optional[dict]:
        """Получение статуса установки."""
        return self._active_installs.get(server_id)

    def get_all_active(self) -> dict:
        """Получение всех активных установок."""
        return self._active_installs.copy()


installer = AsyncInstaller()
