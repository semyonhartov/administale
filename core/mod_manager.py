"""
Модуль для работы с CurseForge API.
Поиск, скачивание и управление модами Hytale.
"""
import asyncio
import json
import os
import shutil
import logging
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Tuple
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from urllib.parse import quote

CURSEFORGE_API_BASE = "https://api.curseforge.com"
GAME_ID = 70216

logger = logging.getLogger(__name__)


def _humanize_file_stem(file_name: str) -> str:
    stem = Path(file_name).stem.replace("-", " ").replace("_", " ").strip()
    return stem or Path(file_name).stem

class CurseForgeAPI:
    """Клиент для работы с CurseForge API."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key
        self._headers = {
            "Accept": "application/json",
            "User-Agent": "AdminisTale/1.0",
        }
        if api_key:
            self._headers["X-API-Key"] = api_key

    def _request(self, url: str) -> dict:
        """Выполнение GET запроса к API."""
        req = Request(url, headers=self._headers)
        try:
            with urlopen(req, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as e:
            if e.code == 403:
                raise PermissionError(f"CurseForge API 403: {e.reason}")
            raise Exception(f"CurseForge API HTTP {e.code}: {e.reason}")
        except URLError as e:
            raise Exception(f"CurseForge API error: {e}")

    async def _request_async(self, url: str) -> dict:
        """Асинхронная версия запроса."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._request, url)

    async def get_categories(self) -> List[dict]:
        """Получение списка категорий модов для Hytale."""
        url = f"{CURSEFORGE_API_BASE}/v1/categories?gameId={GAME_ID}"
        logger.debug(f"Создание url - {url}")
        data = await self._request_async(url)
        return data.get("data", [])

    async def search_mods(
        self,
        query: str = "",
        category_ids: Optional[List[int]] = None,
        page_size: int = 50,
        page_index: int = 0,
    ) -> Tuple[List[dict], int]:
        """Поиск модов по названию (сортировка по популярности)."""
        params = f"gameId={GAME_ID}&pageSize={page_size}&index={page_index*page_size}&sortField=6&sortOrder=desc"
        if query:
            params += f"&searchFilter={quote(query)}"
        if len(category_ids) == 1:
            params += f"&categoryId={category_ids[0]}"
        elif len(category_ids) > 1 and len(category_ids) <= 10:
            params += "&categoryIds="
            for category_id in category_ids or []:
                params += f"{category_id}%2C"
            params = params[:-3]
        url = f"{CURSEFORGE_API_BASE}/v1/mods/search?{params}"
        logger.debug(f"Создание url - {url}")
        logger.debug("Создание реквеста")
        data = await self._request_async(url)
        logger.debug("Отправлен реквест")
        pagination = data.get("pagination", {})
        total = pagination.get("totalCount", 0)
        return data.get("data", []), total

    async def get_mod_details(self, mod_id: int) -> Optional[dict]:
        """Получение детальной информации о моде."""
        url = f"{CURSEFORGE_API_BASE}/v1/mods/{mod_id}"
        logger.debug(f"Создание url - {url}")
        logger.debug("Создание реквеста")
        data = await self._request_async(url)
        return data.get("data")

    async def get_mod_files(self, mod_id: int) -> List[dict]:
        """Получение списка файлов мода."""
        url = f"{CURSEFORGE_API_BASE}/v1/mods/{mod_id}/files"
        logger.debug(f"Создание url - {url}")
        logger.debug("Создание реквеста")
        data = await self._request_async(url)
        return data.get("data", [])

    async def get_mod_file_details(self, mod_id: int, file_id: int) -> Optional[dict]:
        """Получение деталей конкретного файла мода (включая dependencies)."""
        url = f"{CURSEFORGE_API_BASE}/v1/mods/{mod_id}/files/{file_id}"
        logger.debug(f"Создание url - {url}")
        data = await self._request_async(url)
        return data.get("data")

    async def download_mod(self, mod_id: int, file_id: int, dest_path: Path, slug: Optional[str] = None) -> tuple[bool, Optional[str]]:
        """Скачивание файла мода. Возвращает (success, manual_url_for_fallback)."""
        url = f"{CURSEFORGE_API_BASE}/v1/mods/{mod_id}/files/{file_id}/download-url"
        logger.debug(f"Создание url - {url}")
        logger.debug("Создание реквеста")
        download_url = None
        try:
            data = await self._request_async(url)
            download_url = data.get("data")
        except PermissionError as e:
            logger.warning(f"403 Forbidden при получении ссылки: {e}")
        except Exception as e:
            logger.error(f"Ошибка получения ссылки: {e}")

        # Fallback: пробуем получить прямую ссылку из списка файлов
        if not download_url:
            try:
                files = await self.get_mod_files(mod_id)
                for f in files:
                    if f.get("id") == file_id:
                        download_url = f.get("downloadUrl")
                        break
            except Exception:
                pass

        # Если download_url всё ещё не получен — отправляем пользователя на прямую страницу скачивания
        if not download_url:
            logger.warning(f"Не удалось получить ссылку для скачивания мода {mod_id}, открываем страницу скачивания")
            if slug:
                return False, f"https://www.curseforge.com/hytale/mods/{slug}/download/{file_id}"
            return False, f"https://www.curseforge.com/projects/{mod_id}"

        loop = asyncio.get_event_loop()

        def _download():
            req = Request(download_url, headers={"User-Agent": "AdminisTale/1.0"})
            with urlopen(req, timeout=120) as response:
                with open(dest_path, "wb") as f:
                    shutil.copyfileobj(response, f)
            return True

        try:
            result = await loop.run_in_executor(None, _download)
            return result, None
        except HTTPError as e:
            logger.warning(f"HTTP {e.code} при скачивании файла: {e.reason}")
            if slug:
                return False, f"https://www.curseforge.com/hytale/mods/{slug}/download/{file_id}"
            return False, f"https://www.curseforge.com/projects/{mod_id}"
        except Exception as e:
            logger.error(f"Ошибка скачивания: {e}")
            if slug:
                return False, f"https://www.curseforge.com/hytale/mods/{slug}/download/{file_id}"
            return False, f"https://www.curseforge.com/projects/{mod_id}"


class ModManager:
    """Управление модами серверного инстанса."""

    META_SUFFIX = ".administale.json"

    def __init__(self, instance_path: str, api_key: Optional[str] = None):
        self.instance_path = Path(instance_path)
        # Моды должны храниться в папке Server/mods, а не в корне инстанса
        self.mods_dir = self.instance_path / "Server" / "mods"
        self.disabled_dir = self.instance_path / "Server" / "mods" / "disabled"
        self.api = CurseForgeAPI(api_key)

    def _ensure_dirs(self):
        self.mods_dir.mkdir(parents=True, exist_ok=True)
        self.disabled_dir.mkdir(parents=True, exist_ok=True)

    def _list_mod_files(self, directory: Path) -> List[Path]:
        """Безопасный список файлов модов (.jar/.zip)."""
        if not directory.exists():
            return []
        return [f for f in directory.iterdir() if f.is_file() and f.suffix.lower() in {".jar", ".zip"}]

    def _meta_path(self, jar_path: Path) -> Path:
        """Путь к JSON-метаданным рядом с .jar файлом."""
        return jar_path.parent / (jar_path.name + self.META_SUFFIX)

    def _save_mod_meta(self, jar_path: Path, curse_id: int, file_id: int, file_date: int, slug: str, name: str):
        """Сохранить метаданные мода рядом с .jar."""
        meta = {
            "curse_id": curse_id,
            "file_id": file_id,
            "file_date": file_date,
            "slug": slug,
            "name": name,
            "file_name": jar_path.name,
        }
        meta_path = self._meta_path(jar_path)
        try:
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Не удалось сохранить метаданные мода {jar_path.name}: {e}")

    def _load_mod_meta(self, jar_path: Path) -> Optional[dict]:
        """Загрузить метаданные мода рядом с .jar."""
        meta_path = self._meta_path(jar_path)
        if not meta_path.exists():
            return None
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Не удалось загрузить метаданные мода {jar_path.name}: {e}")
            return None

    def _build_mod_info(self, jar_path: Path, enabled: bool) -> dict:
        """Собрать информацию о моде из файла и метаданных."""
        mtime = datetime.fromtimestamp(jar_path.stat().st_mtime).strftime("%Y-%m-%d")
        info = {
            "name": _humanize_file_stem(jar_path.name),
            "file_name": jar_path.name,
            "path": str(jar_path),
            "enabled": enabled,
            "size": jar_path.stat().st_size,
            "version": mtime,
            "curse_id": None,
            "file_id": None,
            "file_date": 0,
            "slug": None,
        }
        meta = self._load_mod_meta(jar_path)
        if meta:
            info["name"] = meta.get("name") or _humanize_file_stem(jar_path.name)
            info["curse_id"] = meta.get("curse_id")
            info["file_id"] = meta.get("file_id")
            info["file_date"] = meta.get("file_date", 0)
            info["slug"] = meta.get("slug")
        return info

    def list_enabled_mods(self) -> List[dict]:
        """Список включённых модов."""
        self._ensure_dirs()
        return [self._build_mod_info(f, True) for f in self._list_mod_files(self.mods_dir)]

    def list_disabled_mods(self) -> List[dict]:
        """Список отключённых модов."""
        self._ensure_dirs()
        return [self._build_mod_info(f, False) for f in self._list_mod_files(self.disabled_dir)]

    def list_all_mods(self) -> List[dict]:
        """Список всех модов."""
        return self.list_enabled_mods() + self.list_disabled_mods()

    def disable_mod(self, file_name: str) -> bool:
        """Отключить мод (переместить в disabled)."""
        src = self.mods_dir / file_name
        dst = self.disabled_dir / file_name
        if src.exists():
            self._ensure_dirs()
            shutil.move(str(src), str(dst))
            # Переместить метаданные тоже
            meta_src = self._meta_path(src)
            meta_dst = self._meta_path(dst)
            if meta_src.exists():
                shutil.move(str(meta_src), str(meta_dst))
            return True
        return False

    def enable_mod(self, file_name: str) -> bool:
        """Включить мод (переместить из disabled)."""
        src = self.disabled_dir / file_name
        dst = self.mods_dir / file_name
        if src.exists():
            self._ensure_dirs()
            shutil.move(str(src), str(dst))
            meta_src = self._meta_path(src)
            meta_dst = self._meta_path(dst)
            if meta_src.exists():
                shutil.move(str(meta_src), str(meta_dst))
            return True
        return False

    def delete_mod(self, file_name: str) -> bool:
        """Удалить мод."""
        for directory in [self.mods_dir, self.disabled_dir]:
            mod_path = directory / file_name
            if mod_path.exists():
                mod_path.unlink()
                meta_path = self._meta_path(mod_path)
                if meta_path.exists():
                    meta_path.unlink()
                return True
        return False

    def get_installed_mod_ids(self) -> set:
        """Получить множество curse_id установленных модов."""
        ids = set()
        for mod in self.list_all_mods():
            cid = mod.get("curse_id")
            if cid:
                ids.add(cid)
        return ids

    async def update_all_mods(self, progress_callback=None) -> dict:
        """
        Обновление всех модов.
        Возвращает dict с результатами обновлений.
        """
        mods = self.list_all_mods()
        results = {}
        for i, mod in enumerate(mods):
            name = mod.get("name", "Unknown")
            if progress_callback:
                progress_callback(f"Проверка обновления: {name} ({i+1}/{len(mods)})")

            curse_id = mod.get("curse_id")
            if not curse_id:
                results[name] = "skipped_no_id"
                continue

            try:
                files = await self.api.get_mod_files(curse_id)
                if not files:
                    results[name] = "no_files"
                    continue

                latest_file = files[0]
                latest_date = latest_file.get("fileDate", 0)

                if isinstance(latest_date, str):
                    latest_ts = int(datetime.fromisoformat(latest_date.replace("Z", "+00:00")).timestamp() * 1000)
                else:
                    latest_ts = latest_date

                mod_date = mod.get("file_date", 0)

                if latest_ts > mod_date:
                    dest = self.mods_dir / mod["file_name"]
                    slug = mod.get("slug")
                    success, manual_url = await self.api.download_mod(
                        curse_id,
                        latest_file["id"],
                        dest,
                        slug=slug
                    )
                    if success:
                        # Обновить метаданные
                        self._save_mod_meta(
                            dest,
                            curse_id=curse_id,
                            file_id=latest_file["id"],
                            file_date=latest_ts,
                            slug=slug or mod.get("slug", ""),
                            name=mod.get("name", name)
                        )
                        results[name] = "updated"
                    else:
                        results[name] = f"download_failed — ссылка: {manual_url}" if manual_url else "download_failed"
                else:
                    results[name] = "up_to_date"
            except Exception as e:
                results[name] = f"error: {e}"

        return results
