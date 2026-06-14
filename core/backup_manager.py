from datetime import datetime
import tarfile
from pathlib import Path
from typing import Optional

from core.server_manager import BackupConfig
from core.instance_transfer import TEXT_SUFFIXES


class BackupManager:
    """Управление бэкапами серверного инстанса."""

    def __init__(self, instance_path: str, slug: str, backups_base: Optional[Path] = None):
        self.instance_path = Path(instance_path)
        self.slug = slug
        self.backups_base = backups_base or (Path(__file__).parent.parent / "backups" / slug)
        self.backups_base.mkdir(parents=True, exist_ok=True)

    def create_backup(self, config: BackupConfig, name: Optional[str] = None) -> dict:
        """
        Создание бэкапа с выбранной конфигурацией.
        Возвращает информацию о созданном бэкапе.
        """
        if not name:
            now = datetime.now()
            name = f"backup_{now.strftime('%Y-%m-%d_%H-%M-%S')}.tar.gz"

        backup_path = self.backups_base / name

        included = []
        total_size = 0

        with tarfile.open(backup_path, "w:gz") as tar:
            if config.include_config:
                self._add_text_files_to_archive(tar)
                included.append("config")

            if config.include_mods:
                self._add_dir_to_archive(tar, self.instance_path / "mods", "mods")
                self._add_dir_to_archive(tar, self.instance_path / "Server" / "mods", "Server/mods")
                included.append("mods")

            if config.include_world:
                self._add_dir_to_archive(tar, self.instance_path / "universe", "universe")
                included.append("world")

            if config.include_logs:
                self._add_dir_to_archive(tar, self.instance_path / "logs", "logs")
                self._add_dir_to_archive(tar, self.instance_path / "Server" / "logs", "Server/logs")
                included.append("logs")

            if config.include_other:
                for item in self.instance_path.iterdir():
                    if item.name in ("mods", "logs", "universe", "administale.json"):
                        continue
                    if item.name.lower() == "assets.zip" and not config.include_assets_zip:
                        continue
                    if item.is_file() and item.suffix.lower() not in TEXT_SUFFIXES:
                        tar.add(str(item), arcname=item.name)
                    elif item.is_dir() and item.name != "Server":
                        self._add_dir_to_archive(tar, item, item.name)
                included.append("other")

            if config.include_assets_zip:
                self._add_to_archive(tar, self.instance_path / "Assets.zip", "Assets.zip")
                included.append("assets.zip")

        size_bytes = backup_path.stat().st_size
        total_size = self._format_size(size_bytes)

        return {
            "name": name,
            "date": datetime.now(),
            "size": total_size,
            "included": included,
            "path": str(backup_path),
        }

    def restore_backup(self, backup_name: str, config: Optional[BackupConfig] = None) -> bool:
        """
        Восстановление бэкапа.
        Если config не указан, восстанавливает всё.
        """
        backup_path = self.backups_base / backup_name
        if not backup_path.exists():
            return False

        try:
            with tarfile.open(backup_path, "r:gz") as tar:
                members = tar.getmembers()

                for member in members:
                    name = member.name

                    if config:
                        if config.include_config and (
                            name.startswith("config") or name.startswith("universe") or Path(name).suffix.lower() in TEXT_SUFFIXES
                        ):
                            tar.extract(member, str(self.instance_path))
                        elif config.include_mods and (name.startswith("mods") or name.startswith("Server/mods")):
                            tar.extract(member, str(self.instance_path))
                        elif config.include_world and name.startswith("universe"):
                            tar.extract(member, str(self.instance_path))
                        elif config.include_logs and (name.startswith("logs") or name.startswith("Server/logs")):
                            tar.extract(member, str(self.instance_path))
                        elif getattr(config, "include_assets_zip", False) and name == "Assets.zip":
                            tar.extract(member, str(self.instance_path))
                        elif config.include_other:
                            tar.extract(member, str(self.instance_path))
                    else:
                        tar.extract(member, str(self.instance_path))

            return True
        except Exception:
            return False

    def delete_backup(self, backup_name: str) -> bool:
        """Удаление бэкапа."""
        backup_path = self.backups_base / backup_name
        if backup_path.exists():
            backup_path.unlink()
            return True
        return False

    def list_backups(self) -> list:
        """Список всех бэкапов."""
        backups = []
        for f in self.backups_base.glob("backup_*"):
            if f.is_file():
                stat = f.stat()
                backups.append({
                    "name": f.name,
                    "date": datetime.fromtimestamp(stat.st_mtime),
                    "size": self._format_size(stat.st_size),
                    "path": str(f),
                })
        return sorted(backups, key=lambda x: x["date"], reverse=True)

    def prune_backups(self, keep_last: int):
        if keep_last <= 0:
            return
        backups = self.list_backups()
        for backup in backups[keep_last:]:
            path = Path(backup["path"])
            if path.exists():
                path.unlink()

    def _add_to_archive(self, tar, path: Path, arcname: str):
        """Добавление файла в архив, если он существует."""
        if path.exists():
            tar.add(str(path), arcname=arcname)

    def _add_dir_to_archive(self, tar, path: Path, arcname: str):
        """Добавление директории в архив, если она существует."""
        if path.exists() and path.is_dir():
            tar.add(str(path), arcname=arcname)

    def _add_text_files_to_archive(self, tar):
        for path in self.instance_path.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(self.instance_path)
            if rel.parts == ("administale.json",):
                continue
            if path.suffix.lower() in TEXT_SUFFIXES:
                tar.add(str(path), arcname=str(rel).replace("\\", "/"))

    def _format_size(self, size_bytes: int) -> str:
        """Форматирование размера файла."""
        for unit in ["B", "KB", "MB", "GB"]:
            if size_bytes < 1024:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024
        return f"{size_bytes:.1f} TB"
