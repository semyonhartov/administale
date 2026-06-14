import base64
from dataclasses import asdict, is_dataclass
import hashlib
import hmac
import json
import secrets
import sqlite3
import socket
import threading
import urllib.error
import urllib.request
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from core.backup_manager import BackupManager
from core.mod_manager import ModManager
from core.process_manager import AsyncProcessManager
from core.server_manager import BASE_DIR, ServerInstance, ServerManager
from core.config import AppConfig

REMOTE_PERMISSION_GROUPS = {
    "tab.home": "Вкладка: Главная",
    "server.start": "Запуск",
    "server.stop": "Остановка",
    "server.restart": "Перезапуск",
    "tab.console": "Вкладка: Консоль",
    "console.command": "Команды",
    "console.macros": "Макросы консоли",
    "tab.mods": "Вкладка: Модификации",
    "mods.install": "Установка модов",
    "mods.toggle": "Включение и отключение модов",
    "mods.delete": "Удаление модов",
    "tab.settings": "Вкладка: Настройки",
    "settings.edit": "Изменение настроек",
    "tab.files": "Вкладка: Файлы",
    "files.view": "Просмотр файлов инстанса",
    "files.write": "Изменение файлов",
    "tab.backups": "Вкладка: Бэкапы",
    "backups.create": "Создание бэкапов",
    "backups.restore": "Восстановление бэкапов",
    "backups.delete": "Удаление бэкапов",
}

REMOTE_DANGEROUS_COMMANDS = {"stop", "shutdown", "restart"}


def _hash_password(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200000).hex()


def _server_audit_log(server: ServerInstance) -> Path:
    return Path(server.path) / "administale-remote-audit.log"


def _json_default(value):
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


class RemoteAccessService:
    _instance = None

    def __new__(cls, db_path: Optional[Path] = None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, db_path: Optional[Path] = None):
        if getattr(self, "_initialized", False):
            return
        self.db_path = db_path or (BASE_DIR / "remote_access.sqlite3")
        self._servers: dict[tuple[str, int], ThreadingHTTPServer] = {}
        self._threads: dict[tuple[str, int], threading.Thread] = {}
        self._instance_ports: dict[str, tuple[str, int]] = {}
        self._token_lock = threading.Lock()
        self._tokens: dict[str, dict] = {}
        self._init_db()
        self._initialized = True

    @staticmethod
    def _pick_available_port(host: str, start_port: int) -> int:
        port = start_port
        while True:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    sock.bind((host, port))
                    return port
                except OSError:
                    port += 1

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS remote_users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    instance_id TEXT NOT NULL,
                    username TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    salt TEXT NOT NULL,
                    permissions_json TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(instance_id, username)
                )
                """
            )
            conn.commit()

    def add_user(self, instance_id: str, username: str, password: str, permissions: list[str]):
        salt = secrets.token_bytes(16)
        password_hash = _hash_password(password, salt)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO remote_users (instance_id, username, password_hash, salt, permissions_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    instance_id,
                    username,
                    password_hash,
                    salt.hex(),
                    json.dumps(sorted(set(permissions)), ensure_ascii=False),
                ),
            )
            conn.commit()

    def delete_user(self, instance_id: str, username: str):
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM remote_users WHERE instance_id = ? AND username = ?",
                (instance_id, username),
            )
            conn.commit()

    def list_users(self, instance_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT username, permissions_json, created_at FROM remote_users WHERE instance_id = ? ORDER BY username",
                (instance_id,),
            ).fetchall()
        return [
            {
                "username": row["username"],
                "permissions": json.loads(row["permissions_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def ensure_server(self, instance_id: str, host: str = "0.0.0.0", port: Optional[int] = None):
        manager = ServerManager()
        server = manager.get_server(instance_id)
        if not server:
            raise RuntimeError("Инстанс не найден")
        existing = self._instance_ports.get(instance_id)
        if existing:
            existing_host, existing_port = existing
            if (existing_host, existing_port) in self._servers:
                return existing_port

        bind_port = port or server.port + 1000
        bind_port = self._pick_available_port(host, bind_port)
        key = (host, bind_port)
        if key in self._servers:
            return bind_port

        service = self

        class Handler(BaseHTTPRequestHandler):
            def _read_json(self):
                length = int(self.headers.get("Content-Length", 0))
                if length <= 0:
                    return {}
                return json.loads(self.rfile.read(length).decode("utf-8"))

            def _send_json(self, payload: dict, status: int = HTTPStatus.OK):
                body = json.dumps(payload, ensure_ascii=False, default=_json_default).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _require_session(self):
                auth = self.headers.get("Authorization", "")
                if not auth.startswith("Bearer "):
                    self._send_json({"error": "missing_token"}, HTTPStatus.UNAUTHORIZED)
                    return None
                session = service._tokens.get(auth.split(" ", 1)[1])
                if not session:
                    self._send_json({"error": "invalid_token"}, HTTPStatus.UNAUTHORIZED)
                    return None
                return session

            def do_GET(self):
                try:
                    if self.path.startswith("/api/users"):
                        session = self._require_session()
                        if not session:
                            return
                        self._send_json({"users": service.list_users(session["instance_id"])})
                        return
                    if self.path.startswith("/api/state"):
                        session = self._require_session()
                        if not session:
                            return
                        self._send_json(service.get_instance_snapshot(session["instance_id"], session["permissions"]))
                        return
                    self.send_error(HTTPStatus.NOT_FOUND)
                except PermissionError as e:
                    self._send_json({"ok": False, "error": "forbidden", "permission": str(e)}, HTTPStatus.FORBIDDEN)
                except Exception as e:
                    self._send_json({"ok": False, "error": "server_error", "details": str(e)}, HTTPStatus.INTERNAL_SERVER_ERROR)

            def do_POST(self):
                try:
                    if self.path == "/auth":
                        payload = self._read_json()
                        result = service._authenticate_local(
                            payload.get("instance_id", ""),
                            payload.get("username", ""),
                            payload.get("password", ""),
                        )
                        if result is None:
                            self._send_json({"error": "auth_failed"}, HTTPStatus.UNAUTHORIZED)
                            return
                        self._send_json(result)
                        return

                    session = self._require_session()
                    if not session:
                        return

                    payload = self._read_json()
                    if self.path == "/api/users/add":
                        service.add_user(session["instance_id"], payload["username"], payload["password"], payload["permissions"])
                        service._write_audit(session["instance_id"], session["username"], f"Добавлен удалённый пользователь {payload['username']}")
                        self._send_json({"ok": True})
                        return
                    if self.path == "/api/users/delete":
                        service.delete_user(session["instance_id"], payload["username"])
                        service._write_audit(session["instance_id"], session["username"], f"Удалён удалённый пользователь {payload['username']}")
                        self._send_json({"ok": True})
                        return
                    if self.path == "/api/action":
                        result = service.handle_action(session, payload)
                        self._send_json(result)
                        return
                    self.send_error(HTTPStatus.NOT_FOUND)
                except PermissionError as e:
                    self._send_json({"ok": False, "error": "forbidden", "permission": str(e)}, HTTPStatus.FORBIDDEN)
                except Exception as e:
                    self._send_json({"ok": False, "error": "server_error", "details": str(e)}, HTTPStatus.INTERNAL_SERVER_ERROR)

            def log_message(self, format, *args):
                return

        httpd = ThreadingHTTPServer((host, bind_port), Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self._servers[key] = httpd
        self._threads[key] = thread
        self._instance_ports[instance_id] = key
        return bind_port

    def _authenticate_local(self, instance_id: str, username: str, password: str) -> Optional[dict]:
        manager = ServerManager()
        server = manager.get_server(instance_id)
        if not server:
            return None

        with self._connect() as conn:
            row = conn.execute(
                "SELECT username, password_hash, salt, permissions_json FROM remote_users WHERE instance_id = ? AND username = ?",
                (instance_id, username),
            ).fetchone()
        if not row:
            return None

        expected = row["password_hash"]
        actual = _hash_password(password, bytes.fromhex(row["salt"]))
        if not hmac.compare_digest(expected, actual):
            return None

        token = secrets.token_urlsafe(32)
        permissions = json.loads(row["permissions_json"])
        with self._token_lock:
            self._tokens[token] = {
                "instance_id": instance_id,
                "username": username,
                "permissions": permissions,
            }
        return {
            "token": token,
            "server_name": server.name,
            "server_id": server.id,
            "instance_id": instance_id,
            "permissions": permissions,
        }

    def get_instance_snapshot(self, instance_id: str, permissions: list[str]) -> dict:
        manager = ServerManager()
        server = manager.get_server(instance_id)
        if not server:
            raise RuntimeError("Инстанс не найден")
        process_manager = AsyncProcessManager()
        proc = process_manager.get_process(instance_id)
        mod_manager = ModManager(server.path)
        backup_manager = BackupManager(server.path, server.slug)
        payload = {
            "server": server.to_dict(),
            "permissions": permissions,
            "state": process_manager.get_state(instance_id).value,
            "usage": process_manager.get_resource_usage(instance_id),
            "macros": list(server.macros),
        }
        if "tab.console" in permissions:
            payload["logs"] = process_manager.get_logs(instance_id, limit=1000) or self._read_recent_console_logs(server, limit=1000)
            payload["commands_dump"] = process_manager.get_commands_dump(instance_id) or self._load_commands_dump(server)
        if "tab.mods" in permissions:
            payload["mods"] = mod_manager.list_all_mods()
        if "tab.backups" in permissions:
            payload["backups"] = backup_manager.list_backups()
        if "tab.files" in permissions or "files.view" in permissions:
            payload["files"] = self._list_files(server, "/")
        if "tab.settings" not in permissions:
            payload["server"].pop("password", None)
        return payload

    def _read_recent_console_logs(self, server: ServerInstance, limit: int = 1000) -> list[str]:
        candidates = [
            Path(server.path) / "Server" / "logs" / "latest.log",
            Path(server.path) / "logs" / "latest.log",
            Path(server.path) / "latest.log",
        ]
        for log_path in candidates:
            if not log_path.exists() or not log_path.is_file():
                continue
            try:
                with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
                    lines = fh.readlines()
                return [line.rstrip("\n\r") for line in lines[-limit:] if line.strip()]
            except Exception:
                continue
        return []

    def _load_commands_dump(self, server: ServerInstance) -> Optional[dict]:
        candidates = [
            Path(server.path) / "Server" / "dumps" / "commands.dump.json",
            Path(server.path) / "dumps" / "commands.dump.json",
        ]
        for dump_path in candidates:
            if not dump_path.exists() or not dump_path.is_file():
                continue
            try:
                with open(dump_path, "r", encoding="utf-8") as fh:
                    return json.load(fh)
            except Exception:
                continue
        return None

    def _resolve_remote_path(self, server: ServerInstance, remote_path: str) -> Path:
        root = Path(server.path).resolve()
        relative = (remote_path or "/").strip()
        if not relative or relative == "/":
            return root
        candidate = (root / relative.lstrip("/")).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as e:
            raise PermissionError("files.view") from e
        return candidate

    def _list_files(self, server: ServerInstance, remote_path: str = "/") -> list[dict]:
        root = Path(server.path).resolve()
        current = self._resolve_remote_path(server, remote_path)
        root = Path(server.path).resolve()
        results = []
        for item in sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            stat = item.stat()
            results.append({
                "name": item.name,
                "path": "/" + item.relative_to(root).as_posix(),
                "is_dir": item.is_dir(),
                "size": stat.st_size,
                "mtime": stat.st_mtime,
            })
        return results

    def _require(self, session: dict, permission: str):
        if permission not in session["permissions"]:
            raise PermissionError(permission)

    def handle_action(self, session: dict, payload: dict) -> dict:
        action = payload.get("action")
        instance_id = session["instance_id"]
        manager = ServerManager()
        server = manager.get_server(instance_id)
        if not server:
            return {"ok": False, "error": "instance_not_found"}

        process_manager = AsyncProcessManager()
        mod_manager = ModManager(server.path, api_key=AppConfig.get_curseforge_api_key())
        backup_manager = BackupManager(server.path, server.slug)

        if action == "server.start":
            self._require(session, "server.start")
            if process_manager._event_loop is None:
                return {"ok": False, "error": "event_loop_unavailable"}
            import asyncio
            asyncio.run_coroutine_threadsafe(process_manager.start_server(server), process_manager._event_loop)
            self._write_audit(instance_id, session["username"], "Удалённый запуск сервера")
            return {"ok": True}

        if action == "server.stop":
            self._require(session, "server.stop")
            import asyncio
            asyncio.run_coroutine_threadsafe(process_manager.stop_server(instance_id), process_manager._event_loop)
            self._write_audit(instance_id, session["username"], "Удалённая остановка сервера")
            return {"ok": True}

        if action == "console.command":
            self._require(session, "console.command")
            command = (payload.get("command", "") or "").strip()
            command_name = command.lstrip("/").split(" ", 1)[0].lower()
            if command_name in REMOTE_DANGEROUS_COMMANDS and "server.stop" not in session["permissions"]:
                return {"ok": False, "error": "dangerous_command_forbidden"}
            import asyncio
            asyncio.run_coroutine_threadsafe(process_manager.send_command(instance_id, command), process_manager._event_loop)
            self._write_audit(instance_id, session["username"], f"Удалённая команда: {command}")
            return {"ok": True}

        if action == "console.macro.add":
            self._require(session, "console.macros")
            manager.add_macro(instance_id, payload["name"], payload["command"])
            self._write_audit(instance_id, session["username"], f"Добавлен макрос {payload['name']}")
            return {"ok": True, "macros": manager.get_server(instance_id).macros}

        if action == "console.macro.delete":
            self._require(session, "console.macros")
            manager.remove_macro(instance_id, int(payload["index"]))
            self._write_audit(instance_id, session["username"], f"Удалён макрос #{payload['index']}")
            return {"ok": True, "macros": manager.get_server(instance_id).macros}

        if action == "mods.toggle":
            self._require(session, "mods.toggle")
            file_name = payload["file_name"]
            enabled = payload.get("enabled", True)
            ok = mod_manager.enable_mod(file_name) if enabled else mod_manager.disable_mod(file_name)
            self._write_audit(instance_id, session["username"], f"Изменён статус мода {file_name}: {'enable' if enabled else 'disable'}")
            return {"ok": ok}

        if action == "mods.delete":
            self._require(session, "mods.delete")
            ok = mod_manager.delete_mod(payload["file_name"])
            self._write_audit(instance_id, session["username"], f"Удалён мод {payload['file_name']}")
            return {"ok": ok}

        if action == "mods.install":
            self._require(session, "mods.install")
            mod_id = int(payload["mod_id"])
            file_id = int(payload["file_id"])
            slug = payload.get("slug")
            mod_name = payload.get("mod_name")
            dest = mod_manager.mods_dir / payload["file_name"]
            import asyncio

            async def install_remote_mod():
                logger.debug(f"mod_id {mod_id}   file_id {file_id}   dest {dest}")
                success, manual_url = await mod_manager.api.download_mod(mod_id, file_id, dest, slug=slug)
                if success:
                    file_date = payload.get("file_date", 0)
                    mod_manager._save_mod_meta(dest, curse_id=mod_id, file_id=file_id, file_date=file_date, slug=slug or "", name=mod_name or dest.stem)
                    return {"ok": True}
                return {"ok": False, "manual_url": manual_url}

            result = asyncio.run_coroutine_threadsafe(install_remote_mod(), process_manager._event_loop).result(timeout=180)
            self._write_audit(instance_id, session["username"], f"Установка мода {payload['file_name']}")
            return result

        if action == "settings.edit":
            self._require(session, "settings.edit")
            updated = payload.get("server", {})
            for field, value in updated.items():
                if hasattr(server, field) and field not in {"id", "path", "slug", "is_remote"}:
                    setattr(server, field, value)
            manager.update_server(server)
            self._write_audit(instance_id, session["username"], "Изменены настройки сервера")
            return {"ok": True}

        if action == "backups.schedule.update":
            self._require(session, "backups.create")
            schedule = payload.get("backup_schedule", {})
            if isinstance(schedule, dict):
                current_schedule = server.backup_schedule
                current_schedule.enabled = bool(schedule.get("enabled", current_schedule.enabled))
                current_schedule.interval_minutes = int(schedule.get("interval_minutes", current_schedule.interval_minutes))
                current_schedule.keep_last = int(schedule.get("keep_last", current_schedule.keep_last))
                config = schedule.get("config", {})
                if isinstance(config, dict):
                    for field, value in config.items():
                        if hasattr(current_schedule.config, field):
                            setattr(current_schedule.config, field, value)
                manager.update_server(server)
            self._write_audit(instance_id, session["username"], "Обновлено расписание бэкапов")
            return {"ok": True, "backup_schedule": server.backup_schedule}

        if action == "backups.create":
            self._require(session, "backups.create")
            config = server.backup_schedule.config
            incoming_config = payload.get("config")
            if isinstance(incoming_config, dict):
                for field, value in incoming_config.items():
                    if hasattr(config, field):
                        setattr(config, field, value)
                manager.update_server(server)
            result = backup_manager.create_backup(config)
            backup_manager.prune_backups(server.backup_schedule.keep_last)
            self._write_audit(instance_id, session["username"], f"Создан бэкап {result['name']}")
            return {"ok": True, "backup": result}

        if action == "backups.restore":
            self._require(session, "backups.restore")
            ok = backup_manager.restore_backup(payload["name"])
            self._write_audit(instance_id, session["username"], f"Восстановлен бэкап {payload['name']}")
            return {"ok": ok}

        if action == "backups.delete":
            self._require(session, "backups.delete")
            ok = backup_manager.delete_backup(payload["name"])
            self._write_audit(instance_id, session["username"], f"Удалён бэкап {payload['name']}")
            return {"ok": ok}

        if action == "files.list":
            self._require(session, "files.view")
            remote_path = payload.get("path", "/")
            return {"ok": True, "path": remote_path, "files": self._list_files(server, remote_path)}

        if action == "files.mkdir":
            self._require(session, "files.write")
            target = self._resolve_remote_path(server, payload.get("path", "/"))
            target.mkdir(parents=False, exist_ok=True)
            self._write_audit(instance_id, session["username"], f"Создана папка {payload.get('path', '/')}")
            return {"ok": True}

        if action == "files.delete":
            self._require(session, "files.write")
            target = self._resolve_remote_path(server, payload.get("path", "/"))
            if target.is_dir():
                import shutil
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
            self._write_audit(instance_id, session["username"], f"Удалён файл {payload.get('path', '/')}")
            return {"ok": True}

        if action == "files.upload":
            self._require(session, "files.write")
            destination = self._resolve_remote_path(server, payload.get("path", "/"))
            content = payload.get("content_base64", "")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(base64.b64decode(content.encode("ascii")))
            self._write_audit(instance_id, session["username"], f"Загружен файл {payload.get('path', '/')}")
            return {"ok": True}

        return {"ok": False, "error": "unsupported_action"}

    def _write_audit(self, instance_id: str, actor: str, message: str):
        manager = ServerManager()
        server = manager.get_server(instance_id)
        if not server:
            return
        audit_file = _server_audit_log(server)
        audit_file.parent.mkdir(parents=True, exist_ok=True)
        with open(audit_file, "a", encoding="utf-8") as fh:
            from datetime import datetime
            fh.write(f"[{datetime.now().isoformat(timespec='seconds')}] [{actor}] {message}\n")

    def authenticate(self, host: str, port: int, instance_id: str, username: str, password: str) -> dict:
        req = urllib.request.Request(
            f"http://{host}:{port}/auth",
            data=json.dumps({"instance_id": instance_id, "username": username, "password": password}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Ошибка авторизации: HTTP {e.code}")

    def api_get(self, host: str, port: int, token: str, path: str) -> dict:
        req = urllib.request.Request(
            f"http://{host}:{port}{path}",
            headers={"Authorization": f"Bearer {token}"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            details = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Ошибка API GET {path}: HTTP {e.code}: {details}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Ошибка API GET {path}: {e.reason}") from e

    def api_get_for_server(self, server: ServerInstance, path: str) -> dict:
        try:
            return self.api_get(server.remote_host, server.remote_port, server.remote_token, path)
        except RuntimeError as e:
            if "invalid_token" not in str(e):
                raise
            self.refresh_remote_token(server)
            return self.api_get(server.remote_host, server.remote_port, server.remote_token, path)

    def api_post(self, host: str, port: int, token: str, path: str, payload: dict) -> dict:
        req = urllib.request.Request(
            f"http://{host}:{port}{path}",
            data=json.dumps(payload, default=_json_default).encode("utf-8"),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            details = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Ошибка API POST {path}: HTTP {e.code}: {details}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Ошибка API POST {path}: {e.reason}") from e

    def api_post_for_server(self, server: ServerInstance, path: str, payload: dict) -> dict:
        try:
            return self.api_post(server.remote_host, server.remote_port, server.remote_token, path, payload)
        except RuntimeError as e:
            if "invalid_token" not in str(e):
                raise
            self.refresh_remote_token(server)
            return self.api_post(server.remote_host, server.remote_port, server.remote_token, path, payload)

    def refresh_remote_token(self, server: ServerInstance) -> str:
        if not server.is_remote:
            raise RuntimeError("Обновление токена доступно только для внешнего инстанса")
        if not server.owner_instance_id or not server.remote_username or not server.remote_password:
            raise RuntimeError("Недостаточно данных для повторной авторизации внешнего инстанса")
        result = self.authenticate(
            server.remote_host,
            server.remote_port,
            server.owner_instance_id,
            server.remote_username,
            server.remote_password,
        )
        server.remote_token = result.get("token")
        server.remote_permissions = list(result.get("permissions", server.remote_permissions or []))
        server.name = result.get("server_name", server.name)
        ServerManager().update_server(server)
        return server.remote_token

    def shutdown(self):
        for key, server in list(self._servers.items()):
            server.shutdown()
            server.server_close()
            thread = self._threads.get(key)
            if thread:
                thread.join(timeout=3)
        self._servers.clear()
        self._threads.clear()
        self._instance_ports.clear()
