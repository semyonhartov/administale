"""
Менеджер серверных инстансов AdminisTale.
Содержит модели данных и логику управления файловой системой.
Каждый инстанс хранит свой конфиг в instances/<slug>/administale.json.
"""
import json
import re
import shutil
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import List, Optional, Dict
import random

BASE_DIR = Path(__file__).parent.parent
INSTANCES_DIR = BASE_DIR / "instances"
LEGACY_SERVERS_FILE = BASE_DIR / "servers.json"


class ServerStatus(Enum):
    STOPPED = "Остановлен"
    STARTING = "Запускается..."
    RUNNING = "Запущен"
    ERROR = "Ошибка"
    BACKUP = "Создание бэкапа"


@dataclass
class WorldConfig:
    is_pvp_enabled: bool = False
    is_fall_damage_enabled: bool = True
    is_game_time_paused: bool = False
    is_spawning_npc: bool = True
    is_spawn_markers_enabled: bool = True
    is_all_npc_frozen: bool = False
    is_compass_updating: bool = True
    is_saving_players: bool = True
    is_saving_chunks: bool = True
    is_unloading_chunks: bool = True
    is_objective_markers_enabled: bool = True
    gameplay_config: str = "Default"


@dataclass
class ModInfo:
    name: str
    version: str
    file_name: str
    enabled: bool = True
    curse_id: Optional[int] = None
    file_date: Optional[int] = None
    download_url: Optional[str] = None


@dataclass
class BackupConfig:
    include_mods: bool = True
    include_config: bool = True
    include_world: bool = True
    include_logs: bool = False
    include_other: bool = True
    include_assets_zip: bool = False


@dataclass
class BackupSchedule:
    enabled: bool = False
    interval_minutes: int = 60
    keep_last: int = 10
    config: BackupConfig = field(default_factory=BackupConfig)


@dataclass
class ServerInstance:
    id: str
    slug: str
    name: str
    status: ServerStatus
    port: int
    version: str
    online_players: int = 0
    last_restart: Optional[datetime] = None
    last_backup: Optional[datetime] = None
    uptime: str = "0м"
    path: str = ""
    auto_start: bool = False
    jvm_args: str = "-Xms4G -Xmx6G -XX:+UseG1GC"

    server_name: str = "Hytale Server"
    motd: str = ""
    password: str = ""
    max_players: int = 100
    max_view_radius: int = 32
    default_world: str = "default"
    game_mode: str = "Adventure"
    display_tmp_tags: bool = False
    player_storage_type: str = "Hytale"

    mods: List[Dict] = field(default_factory=list)
    backups: List[Dict] = field(default_factory=list)
    logs: List[Dict] = field(default_factory=list)
    world_config: WorldConfig = field(default_factory=WorldConfig)
    backup_schedule: BackupSchedule = field(default_factory=BackupSchedule)

    macros: List[Dict] = field(default_factory=list)
    is_remote: bool = False
    owner_instance_id: Optional[str] = None
    remote_host: Optional[str] = None
    remote_port: Optional[int] = None
    remote_username: Optional[str] = None
    remote_password: Optional[str] = None
    remote_token: Optional[str] = None
    remote_permissions: List[str] = field(default_factory=list)

    def _config_path(self) -> Path:
        return Path(self.path) / "administale.json"

    def to_dict(self) -> dict:
        data = asdict(self)
        data["status"] = self.status.value
        if self.last_restart:
            data["last_restart"] = self.last_restart.isoformat()
        if self.last_backup:
            data["last_backup"] = self.last_backup.isoformat()
        for backup in data.get("backups", []):
            if isinstance(backup.get("date"), datetime):
                backup["date"] = backup["date"].isoformat()
        for log in data.get("logs", []):
            if isinstance(log.get("time"), datetime):
                log["time"] = log["time"].isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "ServerInstance":
        data = data.copy()
        data["status"] = ServerStatus(data["status"])
        if data.get("last_restart"):
            data["last_restart"] = datetime.fromisoformat(data["last_restart"])
        if data.get("last_backup"):
            data["last_backup"] = datetime.fromisoformat(data["last_backup"])
        if "world_config" in data and isinstance(data["world_config"], dict):
            data["world_config"] = WorldConfig(**data["world_config"])
        if "backup_schedule" in data and isinstance(data["backup_schedule"], dict):
            schedule = data["backup_schedule"]
            schedule_config = schedule.get("config", {})
            schedule["config"] = BackupConfig(**schedule_config)
            data["backup_schedule"] = BackupSchedule(**schedule)
        return cls(**data)

    def save(self):
        """Сохранить конфиг инстанса в свой JSON файл."""
        config_path = self._config_path()
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    @classmethod
    def load_from_dir(cls, path: Path) -> Optional["ServerInstance"]:
        """Загрузить инстанс из директории."""
        config_path = path / "administale.json"
        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                data["path"] = str(path)
                return cls.from_dict(data)
            except Exception:
                pass
        # Fallback: попытка загрузить из legacy servers.json
        return None


class ServerManager:
    """Синглтон-менеджер для управления инстансами."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._servers: Dict[str, ServerInstance] = {}
            cls._instance._load_all()
        return cls._instance

    def _load_all(self):
        """Сканирует instances/ и загружает все administale.json."""
        # Миграция из legacy servers.json
        if LEGACY_SERVERS_FILE.exists():
            try:
                with open(LEGACY_SERVERS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for s in data:
                    srv = ServerInstance.from_dict(s)
                    srv.save()
                # Переименовываем legacy файл, чтобы не мигрировать повторно
                LEGACY_SERVERS_FILE.rename(BASE_DIR / "servers.json.bak")
            except Exception:
                pass

        if not INSTANCES_DIR.exists():
            return

        for subdir in INSTANCES_DIR.iterdir():
            if subdir.is_dir():
                srv = ServerInstance.load_from_dir(subdir)
                if srv:
                    self._servers[srv.id] = srv

    def discover_servers(self):
        """Подхватывает новые инстансы, добавленные вручную в instances/."""
        if not INSTANCES_DIR.exists():
            return []

        known_paths = {Path(server.path).resolve(): server.id for server in self._servers.values() if server.path}
        discovered = []
        for subdir in INSTANCES_DIR.iterdir():
            if not subdir.is_dir():
                continue

            resolved = subdir.resolve()
            if resolved in known_paths:
                continue

            srv = ServerInstance.load_from_dir(subdir)
            if srv:
                if srv.id not in self._servers:
                    self._servers[srv.id] = srv
                    discovered.append(srv)
                continue

            srv = self._bootstrap_server_from_directory(subdir)
            if srv:
                self._servers[srv.id] = srv
                discovered.append(srv)

        return discovered

    def _bootstrap_server_from_directory(self, path: Path) -> Optional[ServerInstance]:
        slug = re.sub(r"[^a-z0-9-]+", "-", path.name.strip().lower()).strip("-") or f"server-{random.randint(1000, 9999)}"
        known_slugs = {server.slug for server in self._servers.values()}
        base_slug = slug
        suffix = 1
        while slug in known_slugs:
            suffix += 1
            slug = f"{base_slug}-{suffix}"

        port = self.check_port_conflict(25565) or 25565
        srv = ServerInstance(
            id=f"srv-{random.randint(1000, 9999)}",
            slug=slug,
            name=path.name,
            status=ServerStatus.STOPPED,
            port=port,
            version="0.5.4",
            path=str(path),
            server_name=path.name,
            default_world="default",
            game_mode="Adventure",
            macros=[{"name": "Остановить", "command": "stop"}],
        )
        srv.save()
        return srv

    def get_all_servers(self) -> List[ServerInstance]:
        return list(self._servers.values())

    def get_server(self, server_id: str) -> Optional[ServerInstance]:
        return self._servers.get(server_id)

    def check_port_conflict(self, port: int, exclude_id: Optional[str] = None) -> Optional[int]:
        """Проверяет конфликт порта. Возвращает suggested_port или None, если порт свободен."""
        for s in self._servers.values():
            if s.id != exclude_id and s.port == port:
                suggested = port + 1
                while self.check_port_conflict(suggested, exclude_id) is not None:
                    suggested += 1
                return suggested
        return None

    def create_server(self, slug: str, name: str, port: int, version: str, game_mode: str = "Adventure", motd: str = "") -> ServerInstance:
        instance_path = INSTANCES_DIR / slug
        instance_path.mkdir(parents=True, exist_ok=True)
        srv = ServerInstance(
            id=f"srv-{random.randint(1000, 9999)}",
            slug=slug,
            name=name,
            status=ServerStatus.STOPPED,
            port=port,
            version=version,
            path=str(instance_path),
            server_name=name,
            motd=motd,
            default_world="default",
            game_mode=game_mode,
            macros=[{"name": "Остановить", "command": "stop"}],
        )
        srv.save()
        self._servers[srv.id] = srv
        return srv

    def create_remote_server(
        self,
        name: str,
        host: str,
        port: int,
        owner_instance_id: str,
        username: str,
        password: Optional[str] = None,
        token: Optional[str] = None,
        permissions: Optional[List[str]] = None,
    ) -> ServerInstance:
        slug_base = re.sub(r"[^a-z0-9-]+", "-", name.strip().lower()).strip("-") or "remote-server"
        slug = f"remote-{slug_base}-{uuid.uuid4().hex[:6]}"
        instance_path = INSTANCES_DIR / slug
        instance_path.mkdir(parents=True, exist_ok=True)
        srv = ServerInstance(
            id=f"remote-{uuid.uuid4().hex[:12]}",
            slug=slug,
            name=name,
            status=ServerStatus.STOPPED,
            port=port,
            version="remote",
            path=str(instance_path),
            server_name=name,
            game_mode="Adventure",
            is_remote=True,
            owner_instance_id=owner_instance_id,
            remote_host=host,
            remote_port=port,
            remote_username=username,
            remote_password=password,
            remote_token=token,
            remote_permissions=list(permissions or []),
        )
        srv.save()
        self._servers[srv.id] = srv
        return srv

    def delete_server(self, server_id: str) -> bool:
        srv = self.get_server(server_id)
        if not srv:
            return False

        if server_id in self._servers:
            del self._servers[server_id]

        srv_path = Path(srv.path)
        if srv_path.exists() and srv_path.is_dir():
            shutil.rmtree(srv_path)

        backup_path = BASE_DIR / "backups" / srv.slug
        if backup_path.exists() and backup_path.is_dir():
            shutil.rmtree(backup_path)

        return True

    def update_server(self, server: ServerInstance):
        if server.id in self._servers:
            self._servers[server.id] = server
            server.save()

    def toggle_server(self, server_id: str):
        srv = self.get_server(server_id)
        if not srv:
            return
        if srv.status == ServerStatus.RUNNING:
            srv.status = ServerStatus.STOPPED
            srv.online_players = 0
            srv.uptime = "0м"
        else:
            srv.status = ServerStatus.RUNNING
            srv.last_restart = datetime.now()
            srv.uptime = "0м"
        self.update_server(srv)

    def add_log(self, server_id: str, message: str, category: str = "system"):
        srv = self.get_server(server_id)
        if srv:
            srv.logs.append({
                "time": datetime.now().strftime("%H:%M:%S"),
                "message": message,
                "category": category
            })
            self.update_server(srv)

    def add_macro(self, server_id: str, name: str, command: str):
        srv = self.get_server(server_id)
        if srv:
            srv.macros.append({"name": name, "command": command})
            self.update_server(srv)

    def remove_macro(self, server_id: str, index: int):
        srv = self.get_server(server_id)
        if srv and 0 <= index < len(srv.macros):
            srv.macros.pop(index)
            self.update_server(srv)
