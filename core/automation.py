"""Background automation for autostarted instances and scheduled backups."""
import asyncio
import logging
from datetime import datetime

from core.backup_manager import BackupManager
from core.process_manager import AsyncProcessManager, ProcessState
from core.server_manager import ServerManager, ServerStatus


class AutomationService:
    def __init__(self, process_manager: AsyncProcessManager):
        self.process_manager = process_manager
        self.server_manager = ServerManager()
        self._loop = None
        self._task = None
        self._started_instances = set()

    def start(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop
        if self._task and not self._task.done():
            return
        self._task = asyncio.run_coroutine_threadsafe(self._run(), loop)

    async def _run(self):
        await asyncio.sleep(3)
        while True:
            try:
                await self._auto_start_instances()
                await self._run_scheduled_backups()
            except Exception:
                logging.exception("Ошибка фоновой автоматизации")
            await asyncio.sleep(30)

    async def _auto_start_instances(self):
        for server in self.server_manager.get_all_servers():
            if not server.auto_start or server.id in self._started_instances:
                continue
            proc = self.process_manager.get_process(server.id)
            if proc and proc.state in (ProcessState.RUNNING, ProcessState.STARTING):
                self._started_instances.add(server.id)
                continue
            await self.process_manager.start_server(server)
            self._started_instances.add(server.id)

    async def _run_scheduled_backups(self):
        now = datetime.now()
        for server in self.server_manager.get_all_servers():
            schedule = getattr(server, "backup_schedule", None)
            if not schedule or not schedule.enabled:
                continue

            if server.last_backup:
                next_run = server.last_backup.timestamp() + max(schedule.interval_minutes, 1) * 60
                if now.timestamp() < next_run:
                    continue

            proc = self.process_manager.get_process(server.id)
            was_running = proc and proc.state == ProcessState.RUNNING
            manager = BackupManager(server.path, server.slug)

            try:
                server.status = ServerStatus.BACKUP
                self.server_manager.update_server(server)

                if was_running:
                    await self.process_manager.stop_server(server.id)
                    await asyncio.sleep(2)

                result = await asyncio.to_thread(manager.create_backup, schedule.config)
                server.backups.insert(0, {
                    "name": result["name"],
                    "date": result["date"],
                    "size": result["size"],
                    "included": result.get("included", []),
                })
                server.last_backup = result["date"]
                manager.prune_backups(schedule.keep_last)
            except Exception:
                logging.exception("Ошибка создания бэкапа для %s", server.name)
                server.status = ServerStatus.ERROR
                self.server_manager.update_server(server)
                continue
            finally:
                if was_running:
                    await self.process_manager.start_server(server)

            server.status = ServerStatus.RUNNING if was_running else ServerStatus.STOPPED
            self.server_manager.update_server(server)
