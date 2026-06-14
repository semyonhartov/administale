"""
Модуль для скачивания и инициализации серверных инстансов Hytale.
Адаптирован для кроссплатформенной работы (Windows/Linux) и упаковки PyInstaller.
Структура инстанса:
  instances/example/
  ├── Server/
  │   ├── HytaleServer.aot.config
  │   └── HytaleServer.jar
  ├── Assets.zip
  ├── start.bat
  ├── start.sh
  └── jvm.options
"""
import asyncio
import logging
import os
import platform
import re
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Optional

from core.server_manager import ServerInstance, ServerStatus

logger = logging.getLogger(__name__)
DOWNLOADER_ZIP_URL = "https://cloud.asu.ru/public.php/dav/files/TAY693kcwCaMnSs"


def extract_preferred_auth_url(text: str) -> Optional[str]:
    urls = re.findall(r'https://\S+', text)
    preferred_url = None
    fallback_url = None

    for raw_url in urls:
        url = raw_url.rstrip('.,);]')
        if "oauth2/device/verify?user_code=" in url:
            preferred_url = url
            break
        if not fallback_url and (
            "oauth2/auth" in url or "oauth2/device/verify" in url
        ):
            fallback_url = url

    return preferred_url or fallback_url


def get_resource_path(relative_path: str) -> Path:
    """Получает абсолютный путь к ресурсу, учитывая упаковку PyInstaller."""
    if getattr(sys, 'frozen', False):
        base_path = Path(sys._MEIPASS)
    else:
        base_path = Path(__file__).parent.parent
    return base_path / relative_path


def get_downloader_path() -> Optional[Path]:
    """Возвращает путь к утилите hytale-downloader для текущей ОС."""
    system = platform.system()
    if system == "Windows":
        return get_resource_path("resources/hytale-downloader-windows-amd64.exe")
    elif system == "Linux":
        return get_resource_path("resources/hytale-downloader-linux-amd64")
    return None


def ensure_downloader_available() -> Optional[Path]:
    """Проверяет наличие загрузчика и при необходимости докачивает его."""
    downloader_path = get_downloader_path()
    if downloader_path and downloader_path.exists():
        return downloader_path

    resources_dir = get_resource_path("resources")
    resources_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="administale-downloader-") as temp_dir:
        zip_path = Path(temp_dir) / "hytale-downloader.zip"
        urllib.request.urlretrieve(DOWNLOADER_ZIP_URL, zip_path)
        with zipfile.ZipFile(zip_path, "r") as archive:
            archive.extractall(resources_dir)

    downloader_path = get_downloader_path()
    if downloader_path and downloader_path.exists() and platform.system() == "Linux":
        os.chmod(downloader_path, 0o755)
    return downloader_path if downloader_path and downloader_path.exists() else None


class InstanceBuilder:
    """Управляет процессом создания и инициализации инстанса с нуля."""

    def __init__(self, server: ServerInstance, progress_callback: Optional[Callable[[str], None]] = None):
        self.server = server
        self.progress_callback = progress_callback

    async def build(self, auth_link_callback: Optional[Callable[[str], None]] = None) -> bool:
        """
        Полный асинхронный цикл создания инстанса:
        1. Создание директории.
        2. Проверка и копирование credentials.
        3. Запуск hytale-downloader (асинхронный субпроцесс).
        4. Распаковка скачанных архивов и удаление их.
        5. Обновление jvm.options с учетом кастомного порта.
        """
        try:
            instance_path = Path(self.server.path)
            logger.info(f"Создание директории: {instance_path}")
            instance_path.mkdir(parents=True, exist_ok=True)
            logger.info(f"Директория создана: {instance_path}")

            logger.info("Базовая директория создана")

            downloader_path = ensure_downloader_available()
            logger.info(f"Путь к downloader: {downloader_path}")
            if not downloader_path or not downloader_path.exists():
                logger.error("Ошибка: Утилита hytale-downloader не найдена в resources/")
                self.server.status = ServerStatus.ERROR
                return False

            if platform.system() == "Linux":
                os.chmod(downloader_path, 0o755)
                logger.info("Выданы права 755 на downloader")

            root_creds = get_resource_path(".hytale-downloader-credentials.json")
            local_creds = instance_path / ".hytale-downloader-credentials.json"

            # --- Запуск downloader с обработкой invalid_grant ---
            success = False
            for attempt in range(1, 3):
                logger.info(f"Запуск downloader (попытка {attempt})...")
                if self.progress_callback:
                    try:
                        self.progress_callback("Запуск загрузчика серверных файлов...")
                    except Exception as e:
                        logger.warning(f"progress_callback error: {e}")

                # Перед каждой попыткой копируем актуальные credentials (если есть)
                if root_creds.exists():
                    shutil.copy(str(root_creds), str(local_creds))
                    logger.info("Credentials скопированы в папку инстанса.")
                else:
                    logger.info("Credentials не найдены, downloader запросит авторизацию.")

                proc = await asyncio.create_subprocess_exec(
                    str(downloader_path),
                    cwd=str(instance_path),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                logger.info(f"Downloader PID: {proc.pid}")

                preferred_auth_link = None
                output_lines = []
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    text = line.decode("utf-8", errors="replace").rstrip()
                    output_lines.append(text)
                    if self.progress_callback:
                        try:
                            self.progress_callback(text)
                        except Exception as e:
                            logger.warning(f"progress_callback error: {e}")
                    logger.info(f"[downloader] {text}")

                    if "https://oauth.accounts.hytale.com" in text:
                        url = extract_preferred_auth_url(text)
                        if url and "?user_code=" in url:
                            preferred_auth_link = url
                            logger.info(f"Найдена ссылка авторизации: {url}")
                            if auth_link_callback:
                                auth_link_callback(url)

                await proc.wait()
                logger.info(f"Downloader завершился с кодом: {proc.returncode}")

                if proc.returncode == 0:
                    success = True
                    break

                output_text = "\n".join(output_lines)
                if "invalid_grant" in output_text.lower() or "refresh token" in output_text.lower():
                    logger.warning("Обнаружена ошибка авторизации (invalid_grant), удаление credentials и повторная попытка...")
                    if local_creds.exists():
                        local_creds.unlink(missing_ok=True)
                        logger.info(f"Удалён local credentials: {local_creds}")
                    if root_creds.exists():
                        root_creds.unlink(missing_ok=True)
                        logger.info(f"Удалён root credentials: {root_creds}")
                    preferred_auth_link = None
                    continue
                else:
                    logger.error(f"Ошибка загрузчика: код {proc.returncode}")
                    self.server.status = ServerStatus.ERROR
                    return False

            if not success:
                logger.error("Downloader не смог авторизоваться после повторной попытки")
                self.server.status = ServerStatus.ERROR
                return False

            logger.info("Серверные файлы загружены.")

            # Распаковка всех ZIP-архивов в папке инстанса и удаление их
            zip_files = list(instance_path.glob("*.zip"))
            logger.info(f"Найдено ZIP-архивов: {len(zip_files)}")
            for zip_path in zip_files:
                logger.info(f"Распаковка {zip_path}")
                if self.progress_callback:
                    try:
                        self.progress_callback(f"Распаковка: {zip_path.name}")
                    except Exception as e:
                        logger.warning(f"progress_callback error: {e}")
                try:
                    with zipfile.ZipFile(zip_path, 'r') as zf:
                        zf.extractall(instance_path)
                    logger.info(f"Распаковка завершена: {zip_path.name}")
                except Exception as e:
                    logger.error(f"Ошибка распаковки {zip_path.name}: {e}")
                finally:
                    zip_path.unlink()
                    logger.info(f"Архив удалён: {zip_path.name}")

            # Обновление jvm.options в корне инстанса (start.sh читает ../jvm.options)
            jvm_file = instance_path / "jvm.options"
            jvm_args = []
            if jvm_file.exists():
                logger.info(f"Найден существующий {jvm_file}")
                try:
                    with open(jvm_file, "r", encoding="utf-8") as f:
                        for l in f:
                            l = l.strip()
                            if l and not l.startswith("#"):
                                jvm_args.append(l)
                except Exception as e:
                    logger.warning(f"Ошибка чтения jvm.options: {e}")

            # Записываем обратно (только JVM-аргументы, без --bind)
            with open(jvm_file, "w", encoding="utf-8") as f:
                for arg in jvm_args:
                    f.write(f"{arg}\n")
            logger.info(f"jvm.options записан: {jvm_file}")

            self.server.jvm_args = " ".join(jvm_args)
            self.server.status = ServerStatus.STOPPED
            if self.progress_callback:
                try:
                    self.progress_callback("Инстанс успешно создан и готов к работе.")
                except Exception as e:
                    logger.warning(f"progress_callback error: {e}")
            logger.info("Инстанс успешно создан.")
            return True

        except Exception as e:
            logger.exception(f"Критическая ошибка при создании инстанса: {e}")
            self.server.status = ServerStatus.ERROR
            return False

    def cleanup_failed(self):
        """Удаляет частично созданный инстанс в случае ошибки."""
        instance_path = Path(self.server.path)
        if instance_path.exists():
            logger.info(f"Очистка частичного инстанса: {instance_path}")
            shutil.rmtree(instance_path)
