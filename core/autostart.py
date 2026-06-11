"""Autostart integration for AdminisTale."""
import os
import locale
import subprocess
import sys
from pathlib import Path

try:
    import winreg
except ImportError:
    winreg = None


APP_NAME = "AdminisTale"
TASK_NAME = "AdminisTale"
RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
BASE_DIR = Path(__file__).resolve().parent.parent


class WindowsAutostart:
    @staticmethod
    def is_supported() -> bool:
        return os.name == "nt"

    @staticmethod
    def _startup_dir() -> Path:
        appdata = os.environ.get("APPDATA")
        if not appdata:
            raise RuntimeError("APPDATA is not available")
        return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"

    @staticmethod
    def _shortcut_path() -> Path:
        return WindowsAutostart._startup_dir() / f"{APP_NAME}.cmd"

    @staticmethod
    def _launch_command() -> str:
        if getattr(sys, "frozen", False):
            executable = Path(sys.executable).resolve()
            return f'"{executable}"'

        pythonw = Path(sys.executable)
        if pythonw.name.lower() == "python.exe":
            candidate = pythonw.with_name("pythonw.exe")
            if candidate.exists():
                pythonw = candidate

        main_script = BASE_DIR / "main.py"
        return f'"{pythonw}" "{main_script}"'

    @classmethod
    def _write_startup_script(cls):
        startup_dir = cls._startup_dir()
        startup_dir.mkdir(parents=True, exist_ok=True)
        cls._shortcut_path().write_text(f"@echo off\r\n{cls._launch_command()}\r\n", encoding="utf-8")

    @classmethod
    def _write_registry_run(cls):
        if winreg is None:
            raise RuntimeError("winreg is not available")
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cls._launch_command())

    @classmethod
    def _remove_registry_run(cls):
        if winreg is None:
            return
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, APP_NAME)
        except FileNotFoundError:
            pass

    @classmethod
    def _has_registry_run(cls) -> bool:
        if winreg is None:
            return False
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_READ) as key:
                value, _ = winreg.QueryValueEx(key, APP_NAME)
                return bool(value)
        except FileNotFoundError:
            return False

    @staticmethod
    def _run_schtasks(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["schtasks", *args],
            capture_output=True,
            text=True,
            encoding=locale.getpreferredencoding(False) or "cp866",
            errors="ignore",
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    @classmethod
    def enable(cls):
        if not cls.is_supported():
            return
        cls._write_registry_run()
        cls._write_startup_script()
        result = cls._run_schtasks(
            "/Create",
            "/TN", TASK_NAME,
            "/SC", "ONLOGON",
            "/TR", cls._launch_command(),
            "/RL", "LIMITED",
            "/F",
        )
        if result.returncode != 0:
            return

    @classmethod
    def disable(cls):
        if not cls.is_supported():
            return
        cls._remove_registry_run()
        cls._run_schtasks("/Delete", "/TN", TASK_NAME, "/F")
        path = cls._shortcut_path()
        if path.exists():
            path.unlink()

    @classmethod
    def is_enabled(cls) -> bool:
        try:
            if cls._has_registry_run():
                return True
            result = cls._run_schtasks("/Query", "/TN", TASK_NAME)
            if result.returncode == 0:
                return True
            return cls._shortcut_path().exists()
        except Exception:
            return cls._has_registry_run() or cls._shortcut_path().exists()


class LinuxAutostart:
    @staticmethod
    def is_supported() -> bool:
        return sys.platform.startswith("linux")

    @staticmethod
    def _autostart_dir() -> Path:
        config_home = os.environ.get("XDG_CONFIG_HOME")
        if config_home:
            return Path(config_home) / "autostart"
        return Path.home() / ".config" / "autostart"

    @classmethod
    def _desktop_file(cls) -> Path:
        return cls._autostart_dir() / f"{APP_NAME}.desktop"

    @staticmethod
    def _launch_command() -> str:
        if getattr(sys, "frozen", False):
            return f'"{Path(sys.executable).resolve()}" --minimized'
        main_script = BASE_DIR / "main.py"
        return f'"{Path(sys.executable).resolve()}" "{main_script}" --minimized'

    @classmethod
    def enable(cls):
        if not cls.is_supported():
            return
        autostart_dir = cls._autostart_dir()
        autostart_dir.mkdir(parents=True, exist_ok=True)
        desktop_file = cls._desktop_file()
        desktop_file.write_text(
            "\n".join([
                "[Desktop Entry]",
                "Type=Application",
                f"Name={APP_NAME}",
                f"Exec={cls._launch_command()}",
                "Terminal=false",
                "X-GNOME-Autostart-enabled=true",
            ]) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def disable(cls):
        desktop_file = cls._desktop_file()
        if desktop_file.exists():
            desktop_file.unlink()

    @classmethod
    def is_enabled(cls) -> bool:
        return cls._desktop_file().exists()


class AppAutostart:
    @staticmethod
    def is_supported() -> bool:
        return WindowsAutostart.is_supported() or LinuxAutostart.is_supported()

    @staticmethod
    def enable():
        if WindowsAutostart.is_supported():
            return WindowsAutostart.enable()
        if LinuxAutostart.is_supported():
            return LinuxAutostart.enable()

    @staticmethod
    def disable():
        if WindowsAutostart.is_supported():
            return WindowsAutostart.disable()
        if LinuxAutostart.is_supported():
            return LinuxAutostart.disable()

    @staticmethod
    def is_enabled() -> bool:
        if WindowsAutostart.is_supported():
            return WindowsAutostart.is_enabled()
        if LinuxAutostart.is_supported():
            return LinuxAutostart.is_enabled()
        return False
