"""
Асинхронный менеджер процессов серверов.
Управляет запуском, остановкой и мониторингом серверных инстансов.
Поддерживает работу в фоне (серверы продолжают работать после закрытия UI).
"""
import asyncio
import json
import os
import platform
import re
import signal
import subprocess
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Dict, List
from enum import Enum

import psutil

from core.server_manager import ServerManager, ServerInstance, ServerStatus
from core.instance_builder import extract_preferred_auth_url
from core.async_installer import installer


class ProcessState(Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


class ServerProcess:
    """Обёртка над запущенным процессом сервера."""

    MAX_LOG_BUFFER = 100000
    ANSI_ESCAPE_RE = re.compile(rb'\x1B\[[0-?]*[ -/]*[@-~]')
    BROKEN_ANSI_RE = re.compile(r'(?:\uFFFD|\?)\[[0-9;?]*[ -/]*[@-~]')

    def __init__(self, server: ServerInstance):
        self.server = server
        self.process: Optional[asyncio.subprocess.Process] = None
        self.state = ProcessState.STOPPED
        self.start_time: Optional[float] = None
        self._log_buffer: List[str] = []
        self._log_listeners: List[Callable] = []
        self._auth_link_listeners: List[Callable] = []
        self._read_task: Optional[asyncio.Task] = None
        self._metrics_task: Optional[asyncio.Task] = None
        self._commands_dump: Optional[dict] = None
        self._auth_browser_requested = False
        self._cpu_percent = 0.0
        self._memory_mb = 0.0
        self._log_revision = 0
        self._resource_processes: List[psutil.Process] = []
        self._opened_auth_urls: set[str] = set()
        self._graceful_shutdown_requested = False
        self.last_error_analysis: Optional[dict] = None
        self._shutdown_triggered = False
        self._shutdown_reason_line: Optional[str] = None

    @property
    def uptime(self) -> str:
        if not self.start_time:
            return "0м"
        elapsed = time.time() - self.start_time
        hours = int(elapsed // 3600)
        minutes = int((elapsed % 3600) // 60)
        if hours > 0:
            return f"{hours}ч {minutes}м"
        return f"{minutes}м"

    def on_log(self, callback: Callable):
        self._log_listeners.append(callback)

    def on_auth_link(self, callback: Callable):
        self._auth_link_listeners.append(callback)

    def _emit_log(self, line: str):
        self._log_buffer.append(line)
        # Обрезка старых логов при превышении лимита
        if len(self._log_buffer) > self.MAX_LOG_BUFFER:
            self._log_buffer = self._log_buffer[-self.MAX_LOG_BUFFER:]
        for cb in self._log_listeners:
            cb(line)

    def get_logs(self, limit: int = 100000) -> List[str]:
        return self._log_buffer[-limit:]

    def clear_logs(self):
        self._log_buffer.clear()
        self._log_revision += 1

    def get_commands_dump(self) -> Optional[dict]:
        return self._commands_dump

    def get_resource_usage(self) -> dict:
        return {
            "cpu_percent": round(self._cpu_percent, 1),
            "memory_mb": round(self._memory_mb, 1),
        }

    def get_log_revision(self) -> int:
        return self._log_revision

    def _sync_status(self):
        """Синхронизация статуса ServerInstance с ProcessState."""
        try:
            mgr = ServerManager()
            state_to_status = {
                ProcessState.RUNNING: ServerStatus.RUNNING,
                ProcessState.STOPPED: ServerStatus.STOPPED,
                ProcessState.STARTING: ServerStatus.STARTING,
                ProcessState.STOPPING: ServerStatus.STOPPED,
                ProcessState.ERROR: ServerStatus.ERROR,
            }
            new_status = state_to_status.get(self.state)
            if new_status and self.server.status != new_status:
                self.server.status = new_status
                mgr.update_server(self.server)
        except Exception:
            pass

    async def start(self, log_callback: Optional[Callable] = None, auth_link_callback: Optional[Callable] = None):
        """Запуск серверного процесса."""
        if self.state in (ProcessState.RUNNING, ProcessState.STARTING):
            return

        # Сброс ERROR → STOPPED чтобы можно было перезапустить с кнопки
        if self.state == ProcessState.ERROR:
            self.state = ProcessState.STOPPED
            self.server.status = ServerStatus.STOPPED

        self.state = ProcessState.STARTING
        self.server.status = ServerStatus.STARTING
        self.clear_logs()
        self._auth_browser_requested = False
        self._opened_auth_urls.clear()
        self._graceful_shutdown_requested = False
        self.last_error_analysis = None
        self._shutdown_triggered = False
        self._shutdown_reason_line = None
        self._cpu_percent = 0.0
        self._memory_mb = 0.0
        self._sync_status()

        if self.server.is_remote:
            self._emit_log("[ERROR] Прямой локальный запуск недоступен для внешнего инстанса")
            self.state = ProcessState.ERROR
            self.server.status = ServerStatus.ERROR
            self._sync_status()
            return

        instance_path = Path(self.server.path)
        server_dir = instance_path / "Server"

        server_args = [f"--bind", f"0.0.0.0:{self.server.port}"]

        # Определяем команду запуска: предпочтительно start.sh / start.bat, иначе java напрямую
        cmd = None
        if platform.system() == "Linux":
            start_script = instance_path / "start.sh"
            if start_script.exists():
                cmd = ["bash", str(start_script)] + server_args
                cwd = str(instance_path)
        elif platform.system() == "Windows":
            start_script = instance_path / "start.bat"
            if start_script.exists():
                cmd = [str(start_script)] + server_args
                cwd = str(instance_path)

        if cmd is None:
            # Fallback: запускаем java напрямую с jvm.options
            if not server_dir.exists():
                server_dir = instance_path
            jar_path = server_dir / "HytaleServer.jar"
            if not jar_path.exists():
                self._emit_log("[WARN] HytaleServer.jar не найден, начинаю докачку серверных файлов")
                installed = await installer.install(
                    self.server,
                    progress_callback=self._emit_log,
                    auth_link_callback=auth_link_callback,
                )
                if not installed:
                    self._emit_log("[ERROR] Не удалось докачать серверные файлы")
                    self.state = ProcessState.ERROR
                    self.server.status = ServerStatus.ERROR
                    self._sync_status()
                    return
                server_dir = instance_path / "Server"
                if not server_dir.exists():
                    server_dir = instance_path
                jar_path = server_dir / "HytaleServer.jar"
                if not jar_path.exists():
                    self._emit_log("[ERROR] HytaleServer.jar не найден после докачки")
                    self.state = ProcessState.ERROR
                    self.server.status = ServerStatus.ERROR
                    self._sync_status()
                    return
            jvm_args = self._load_jvm_args(instance_path)
            cmd = ["java"] + jvm_args + ["-jar", str(jar_path)] + server_args
            cwd = str(server_dir)

        self._emit_log(f"[INFO] Запуск: {' '.join(cmd)}")

        # Регистрация колбэков
        if log_callback:
            self._log_listeners.append(log_callback)
        if auth_link_callback:
            self.on_auth_link(auth_link_callback)

        try:
            self.process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=cwd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            self.start_time = time.time()
            self._prime_resource_monitors()
            self.state = ProcessState.RUNNING
            self.server.status = ServerStatus.RUNNING
            self.server.last_restart = datetime.now()
            self._sync_status()

            self._emit_log("[INFO] Сервер запущен")

            self._read_task = asyncio.create_task(self._read_output())
            self._metrics_task = asyncio.create_task(self._monitor_resources())

            await asyncio.sleep(5)
            await self._request_commands_dump()

        except FileNotFoundError:
            self._emit_log("[ERROR] Java не найдена")
            self.state = ProcessState.ERROR
            self.server.status = ServerStatus.ERROR
            self._sync_status()
        except Exception as e:
            self._emit_log(f"[ERROR] {e}")
            self.state = ProcessState.ERROR
            self.server.status = ServerStatus.ERROR
            self._sync_status()

    async def stop(self):
        """Остановка серверного процесса."""
        if self.state != ProcessState.RUNNING:
            self.state = ProcessState.STOPPED
            self.server.status = ServerStatus.STOPPED
            self._sync_status()
            return

        self.state = ProcessState.STOPPING
        self._sync_status()
        self._emit_log("[INFO] Остановка сервера...")

        try:
            if self.process and self.process.stdin:
                self._graceful_shutdown_requested = True
                self.process.stdin.write(b"stop\n")
                await self.process.stdin.drain()

            try:
                await asyncio.wait_for(self.process.wait(), timeout=30)
            except asyncio.TimeoutError:
                self._emit_log("[WARN] Сервер не остановился за 30с, принудительное завершение")
                if platform.system() == "Windows":
                    self.process.kill()
                else:
                    self.process.send_signal(signal.SIGTERM)
                    await asyncio.sleep(2)
                    if self.process.returncode is None:
                        self.process.kill()
        except Exception as e:
            self._emit_log(f"[ERROR] Ошибка при остановке: {e}")

        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass

        if self._metrics_task:
            self._metrics_task.cancel()
            try:
                await self._metrics_task
            except asyncio.CancelledError:
                pass

        self.process = None
        self.start_time = None
        self.state = ProcessState.STOPPED
        self.server.status = ServerStatus.STOPPED
        self._cpu_percent = 0.0
        self._memory_mb = 0.0
        self._sync_status()
        self._emit_log("[INFO] Сервер остановлен")

    async def send_command(self, command: str):
        """Отправка команды в сервер."""
        if self.state != ProcessState.RUNNING or not self.process or not self.process.stdin:
            self._emit_log("[ERROR] Сервер не запущен")
            return

        self._emit_log(f">>> {command}")
        try:
            self.process.stdin.write(f"{command}\n".encode())
            await self.process.stdin.drain()
        except Exception as e:
            self._emit_log(f"[ERROR] Ошибка отправки команды: {e}")

        # Если пользователь вручную отправил stop, отмечаем graceful shutdown
        if command.strip().lower() == "stop":
            self._graceful_shutdown_requested = True
            self.state = ProcessState.STOPPING
            self.server.status = ServerStatus.STOPPED
            self._sync_status()

    async def _read_output(self):
        """Чтение stdout процесса."""
        if not self.process or not self.process.stdout:
            return

        try:
            while True:
                line = await self.process.stdout.readline()
                if not line:
                    break
                text = self._decode_output_line(line)
                if not text:
                    continue
                self._emit_log(text)

                lowered = text.lower()
                if "shutdown triggered!!!" in lowered:
                    self._shutdown_triggered = True
                    self._shutdown_reason_line = None
                elif self._shutdown_triggered and "shutting down..." in lowered:
                    self._shutdown_reason_line = text

                if not self._auth_browser_requested and "Use /auth login to authenticate." in text:
                    self._auth_browser_requested = True
                    await self.send_command("/auth login browser")

                # Автоматическая аутентификация: ищем ссылку на Hytale OAuth
                if "https://oauth.accounts.hytale.com/oauth2/auth" in text or \
                   "https://oauth.accounts.hytale.com/ouath2/auth" in text or \
                   "https://oauth.accounts.hytale.com/oauth2/device/verify" in text:
                    url = extract_preferred_auth_url(text)
                    if url and url not in self._opened_auth_urls:
                        self._opened_auth_urls.add(url)
                        for cb in self._auth_link_listeners:
                            try:
                                cb(url)
                            except Exception:
                                pass
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self._emit_log(f"[ERROR] Ошибка чтения: {e}")

        returncode = None
        if self.process:
            try:
                returncode = self.process.returncode
                if returncode is None:
                    await self.process.wait()
                    returncode = self.process.returncode
            except Exception:
                pass

        # Graceful exit (returncode 0) или уже отмечен STOPPING — не считаем ошибкой
        if self.state == ProcessState.RUNNING:
            if returncode == 0:
                if self._shutdown_triggered and not self._graceful_shutdown_requested:
                    self._emit_log("[WARN] Сервер аварийно завершился")
                    self.last_error_analysis = self._analyze_failure()
                    self.state = ProcessState.ERROR
                    self.server.status = ServerStatus.ERROR
                    self._sync_status()
                else:
                    self._emit_log("[INFO] Сервер завершил работу")
                    self.state = ProcessState.STOPPED
                    self.server.status = ServerStatus.STOPPED
                    self._sync_status()
            else:
                self._emit_log("[WARN] Сервер неожиданно завершился")
                self.last_error_analysis = self._analyze_failure()
                self.state = ProcessState.ERROR
                self.server.status = ServerStatus.ERROR
                self._sync_status()

        if self.state == ProcessState.STOPPING and self._graceful_shutdown_requested:
            self.state = ProcessState.STOPPED
            self.server.status = ServerStatus.STOPPED
            self._sync_status()

    def _analyze_failure(self) -> Optional[dict]:
        recent_logs = "\n".join(self._log_buffer[-300:]).lower()
        if not recent_logs:
            return None

        shutdown_reason = (self._shutdown_reason_line or "").lower()

        patterns = [
            {
                "code": "missing_jar",
                "match": ["hytaleserver.jar не найден", "unable to access jarfile"],
                "title": "Отсутствуют серверные файлы",
                "message": "У сервера отсутствует основной исполняемый файл. Попробуйте повторно скачать серверные файлы.",
                "suggestion": "Остановите инстанс и повторите запуск: приложение автоматически попытается докачать сервер.",
            },
            {
                "code": "mod_conflict",
                "match": ["mod conflict", "duplicate mod", "failed to load mod", "incompatible mod", "dependency"],
                "title": "Возможен конфликт модов",
                "message": "Похоже, сервер упал из-за несовместимых или отсутствующих зависимостей модов.",
                "suggestion": "Отключите последний добавленный мод или поочерёдно отключите конфликтующие моды во вкладке 'Модификации'.",
            },
            {
                "code": "assets_failed",
                "match": ["missingassets.failedtoload", "failedtoload", "missing or invalid manifest.json"],
                "title": "Проблема с ресурсами или мод-паком",
                "message": "Сервер аварийно завершился из-за некорректного ресурспака или мода без валидного manifest.json.",
                "suggestion": "Проверьте последние добавленные `.zip`/`.jar` моды и отключите проблемный пак. Для `disabled`-каталогов удалите некорректные архивы.",
            },
            {
                "code": "port_in_use",
                "match": ["address already in use", "bind failed", "failed to bind"],
                "title": "Порт уже занят",
                "message": "Сервер не смог занять сетевой порт.",
                "suggestion": "Измените порт инстанса во вкладке 'Настройки' или завершите процесс, который уже использует этот порт.",
            },
            {
                "code": "java_missing",
                "match": ["java не найдена", "no such file or directory: 'java'", "could not find java"],
                "title": "Не найдена Java",
                "message": "Для запуска Hytale Server требуется установленная Java и доступная команда `java`.",
                "suggestion": "Установите Java и проверьте, что она доступна в PATH.",
            },
            {
                "code": "memory",
                "match": ["outofmemoryerror", "could not reserve enough space", "insufficient memory"],
                "title": "Недостаточно памяти",
                "message": "Серверу не хватило доступной оперативной памяти.",
                "suggestion": "Уменьшите JVM-параметры `-Xms/-Xmx` во вкладке 'Настройки' или освободите память на ПК.",
            },
        ]

        for pattern in patterns:
            if any(token in recent_logs or token in shutdown_reason for token in pattern["match"]):
                return pattern
        return {
            "code": "unknown",
            "title": "Не удалось точно определить причину",
            "message": self._shutdown_reason_line or "Сервер завершился с ошибкой, но сигнатура проблемы не распознана.",
            "suggestion": "Откройте вкладку 'Консоль' и проверьте последние строки лога перед аварийным завершением.",
        }

    def _decode_output_line(self, line: bytes) -> str:
        cleaned = self.ANSI_ESCAPE_RE.sub(b"", line)
        text = ""

        encodings = ["utf-8"]
        if platform.system() == "Windows":
            encodings.extend(["cp1251", "cp866"])

        for encoding in encodings:
            try:
                text = cleaned.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = cleaned.decode("utf-8", errors="replace")

        text = text.rstrip()
        text = self.BROKEN_ANSI_RE.sub("", text)
        return text.strip()

    def _get_monitored_processes(self) -> List[psutil.Process]:
        alive = []
        for proc in self._resource_processes:
            try:
                if proc.is_running():
                    alive.append(proc)
            except (psutil.Error, ProcessLookupError):
                continue

        if alive:
            return alive

        self._prime_resource_monitors()
        return [proc for proc in self._resource_processes if proc.is_running()]

    def _prime_resource_monitors(self):
        self._resource_processes = []
        if not self.process or self.process.pid is None:
            return

        time.sleep(1)

        try:
            root = psutil.Process(self.process.pid)
            processes = [root]
            processes.extend(root.children(recursive=True))
            for proc in processes:
                try:
                    if proc.is_running():
                        proc.cpu_percent(interval=None)
                        self._resource_processes.append(proc)
                except (psutil.Error, ProcessLookupError):
                        continue
        except (psutil.Error, ProcessLookupError):
            self._resource_processes = []

    def _refresh_resource_processes(self):
        if not self.process or self.process.pid is None:
            return

        try:
            root = psutil.Process(self.process.pid)
            tracked_pids = {proc.pid for proc in self._resource_processes if proc.is_running()}
            candidates = [root] + root.children(recursive=True)
            for proc in candidates:
                try:
                    if proc.is_running() and proc.pid not in tracked_pids:
                        proc.cpu_percent(interval=None)
                        self._resource_processes.append(proc)
                except (psutil.Error, ProcessLookupError):
                    continue
        except (psutil.Error, ProcessLookupError):
            pass

    async def _monitor_resources(self):
        while self.state in (ProcessState.STARTING, ProcessState.RUNNING, ProcessState.STOPPING):
            try:
                self._refresh_resource_processes()
                processes = self._get_monitored_processes()
                cpu = 0.0
                memory = 0.0
                for proc in processes:
                    try:
                        cpu += proc.cpu_percent(interval=None)
                        memory += proc.memory_info().rss / (1024 * 1024)
                    except (psutil.Error, ProcessLookupError):
                        continue

                self._cpu_percent = cpu
                self._memory_mb = memory
            except Exception:
                self._cpu_percent = 0.0
                self._memory_mb = 0.0

            await asyncio.sleep(2)

    async def _request_commands_dump(self):
        """Запрос /commands dump для получения списка команд."""
        if self.state != ProcessState.RUNNING:
            return
        await self.send_command("/commands dump")

    def _load_jvm_args(self, instance_path: Path) -> List[str]:
        """Загрузка JVM аргументов из jvm.options (корень инстанса) или из настроек сервера."""
        # start.sh читает ../jvm.options относительно Server/, т.е. jvm.options в корне инстанса
        jvm_file = instance_path / "jvm.options"
        if jvm_file.exists():
            try:
                args = []
                with open(jvm_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            args.append(line)
                return args
            except Exception:
                pass

        # Fallback на старый путь внутри Server/
        server_jvm = instance_path / "Server" / "jvm.options"
        if server_jvm.exists():
            try:
                args = []
                with open(server_jvm, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            args.append(line)
                return args
            except Exception:
                pass

        if self.server.jvm_args:
            return self.server.jvm_args.split()

        return ["-Xms4G", "-Xmx6G", "-XX:+UseG1GC"]


class AsyncProcessManager:
    """
    Менеджер всех серверных процессов.
    singleton, управляет множеством ServerProcess.
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._processes: Dict[str, ServerProcess] = {}
            cls._instance._event_loop: Optional[asyncio.AbstractEventLoop] = None
            cls._instance._auth_link_listeners: List[Callable[[str], None]] = []
        return cls._instance

    def set_event_loop(self, loop: asyncio.AbstractEventLoop):
        self._event_loop = loop

    def add_auth_link_listener(self, callback: Callable[[str], None]):
        self._auth_link_listeners.append(callback)

    def _broadcast_auth_link(self, url: str):
        for callback in list(self._auth_link_listeners):
            try:
                callback(url)
            except Exception:
                pass

    def get_process(self, server_id: str) -> Optional[ServerProcess]:
        return self._processes.get(server_id)

    def get_all_processes(self) -> Dict[str, ServerProcess]:
        return self._processes.copy()

    async def start_server(self, server: ServerInstance, log_callback: Optional[Callable] = None, auth_link_callback: Optional[Callable] = None):
        """Запуск сервера в async процессе."""
        if server.id in self._processes:
            proc = self._processes[server.id]
            if proc.state == ProcessState.RUNNING:
                return
            callback = auth_link_callback or self._broadcast_auth_link
            await proc.start(log_callback=log_callback, auth_link_callback=callback)
        else:
            proc = ServerProcess(server)
            if log_callback:
                proc.on_log(log_callback)
            self._processes[server.id] = proc
            callback = auth_link_callback or self._broadcast_auth_link
            await proc.start(log_callback=log_callback, auth_link_callback=callback)

    async def stop_server(self, server_id: str):
        """Остановка сервера."""
        proc = self._processes.get(server_id)
        if proc:
            await proc.stop()

    async def send_command(self, server_id: str, command: str):
        """Отправка команды серверу."""
        proc = self._processes.get(server_id)
        if proc:
            await proc.send_command(command)

    def get_logs(self, server_id: str, limit: int = 100000) -> List[str]:
        """Получение логов сервера."""
        proc = self._processes.get(server_id)
        if proc:
            return proc.get_logs(limit)
        return []

    def get_log_revision(self, server_id: str) -> int:
        proc = self._processes.get(server_id)
        if proc:
            return proc.get_log_revision()
        return 0

    def get_uptime(self, server_id: str) -> str:
        """Получение аптайма сервера."""
        proc = self._processes.get(server_id)
        if proc:
            return proc.uptime
        return "0м"

    def get_state(self, server_id: str) -> ProcessState:
        """Получение состояния процесса."""
        proc = self._processes.get(server_id)
        if proc:
            return proc.state
        return ProcessState.STOPPED

    def get_resource_usage(self, server_id: str) -> dict:
        proc = self._processes.get(server_id)
        if proc:
            return proc.get_resource_usage()
        return {"cpu_percent": 0.0, "memory_mb": 0.0}

    async def stop_all(self):
        """Остановка всех серверов."""
        tasks = []
        for server_id in list(self._processes.keys()):
            proc = self._processes[server_id]
            if proc.state == ProcessState.RUNNING:
                tasks.append(proc.stop())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def get_commands_dump(self, server_id: str) -> Optional[dict]:
        """Получение дампа команд сервера."""
        proc = self._processes.get(server_id)
        if proc:
            return proc.get_commands_dump()
        return None

    def get_last_error_analysis(self, server_id: str) -> Optional[dict]:
        proc = self._processes.get(server_id)
        if proc:
            return proc.last_error_analysis
        return None
