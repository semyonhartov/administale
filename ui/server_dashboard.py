"""
Панель управления конкретным серверным инстансом.
Содержит вкладки: Главная, Консоль, Модификации, Настройки, Файлы, Бэкапы.
"""
import asyncio
import base64
from collections import deque
import datetime
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal, QEvent
from PySide6.QtGui import QFont, QDesktopServices
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QStackedWidget, QTableWidget, QTableWidgetItem, QTreeWidget,
    QTreeWidgetItem, QHeaderView, QAbstractItemView, QPlainTextEdit,
    QLineEdit, QFormLayout, QGroupBox, QSplitter, QFileDialog, QMessageBox,
    QMenu, QScrollArea, QInputDialog, QDialog, QGridLayout
)
from qfluentwidgets import (
    Pivot, PrimaryPushButton, PushButton, ToggleButton, LineEdit,
    SpinBox, ComboBox, TextEdit, PlainTextEdit, CardWidget, InfoBar,
    InfoBarPosition, FluentIcon as FIF,
    BodyLabel, SubtitleLabel, CaptionLabel, CheckBox,
    ToolButton
)

from core.server_manager import ServerManager, ServerInstance, ServerStatus, WorldConfig, BackupConfig
from core.process_manager import AsyncProcessManager, ProcessState
from core.mod_manager import ModManager
from core.backup_manager import BackupManager
from core.config import AppConfig
from core.remote_access import RemoteAccessService, REMOTE_PERMISSION_GROUPS
from ui.dialogs import BackupConfigDialog, ApiKeyDialog, ModBrowserDialog, RemoteUsersDialog


def _has_remote_permission(server: ServerInstance, permission: str) -> bool:
    return not server.is_remote or permission in set(server.remote_permissions)


def _is_remote_tab_allowed(server: ServerInstance, tab_permission: str, action_permission: Optional[str] = None) -> bool:
    if not server.is_remote:
        return True
    perms = set(server.remote_permissions)
    if tab_permission in perms:
        return True
    if action_permission and action_permission in perms:
        return True
    return False


def _apply_remote_identity(target: ServerInstance, source: ServerInstance) -> ServerInstance:
    source.is_remote = True
    source.remote_host = target.remote_host
    source.remote_port = target.remote_port
    source.remote_token = target.remote_token
    source.remote_permissions = list(target.remote_permissions)
    source.owner_instance_id = target.owner_instance_id
    source.remote_username = target.remote_username
    source.remote_password = target.remote_password
    source.id = target.id
    source.slug = target.slug
    source.path = target.path
    return source


# =============================================================================
# ВКЛАДКА: ГЛАВНАЯ
# =============================================================================
class HomeTab(QWidget):
    """Главная вкладка с основной информацией и быстрыми действиями."""

    def __init__(self, server: ServerInstance, process_manager: AsyncProcessManager, parent=None):
        super().__init__(parent)
        self.server = server
        self.process_manager = process_manager
        self._shown_error_signatures = set()
        self.remote_access_service = RemoteAccessService()
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        status_card = CardWidget()
        status_layout = QHBoxLayout(status_card)
        status_layout.setContentsMargins(20, 16, 20, 16)

        self.status_icon = QLabel("●")
        self.status_icon.setStyleSheet("font-size: 32px; color: #4caf50; background: transparent;")
        status_layout.addWidget(self.status_icon)

        status_info = QVBoxLayout()
        self.status_title = SubtitleLabel(f"Статус: {self.server.status.value}")
        self.status_title.setStyleSheet("font-size: 20px; background: transparent;")
        status_info.addWidget(self.status_title)

        self.uptime_label = BodyLabel(f"Аптайм: {self.server.uptime}")
        self.uptime_label.setStyleSheet("color: #888; background: transparent;")
        status_info.addWidget(self.uptime_label)
        status_layout.addLayout(status_info)

        status_layout.addStretch()

        actions = QHBoxLayout()
        self.start_btn = PrimaryPushButton(FIF.PLAY, "Запустить")
        self.start_btn.clicked.connect(self._toggle_server)
        actions.addWidget(self.start_btn)

        self.restart_btn = PushButton(FIF.SYNC, "Перезапустить")
        self.restart_btn.clicked.connect(self._restart_server)
        actions.addWidget(self.restart_btn)

        self.start_btn.setVisible(_has_remote_permission(self.server, "server.start") or _has_remote_permission(self.server, "server.stop"))
        self.restart_btn.setVisible(_has_remote_permission(self.server, "server.restart"))

        status_layout.addLayout(actions)
        layout.addWidget(status_card)

        stats_card = CardWidget()
        stats_layout = QHBoxLayout(stats_card)
        stats_layout.setContentsMargins(20, 16, 20, 16)
        stats_layout.setSpacing(16)

        stats = [
            ("Игроки", f"{self.server.online_players}/{self.server.max_players}"),
            ("Порт", str(self.server.port)),
            ("Версия", self.server.version),
            ("CPU", "0.0%"),
            ("RAM", "0.0 MB"),
        ]
        self.stat_values = {}
        for title, value in stats:
            col = QVBoxLayout()
            col.setAlignment(Qt.AlignCenter)
            t = CaptionLabel(title)
            t.setStyleSheet("color: #888; font-size: 12px; background: transparent;")
            col.addWidget(t, alignment=Qt.AlignCenter)
            v = BodyLabel(value)
            v.setStyleSheet("font-size: 18px; font-weight: bold; color: #e0e0e0; background: transparent;")
            col.addWidget(v, alignment=Qt.AlignCenter)
            self.stat_values[title] = v
            stats_layout.addLayout(col)

        stats_layout.addStretch()
        layout.addWidget(stats_card)

        world_card = CardWidget()
        world_layout = QFormLayout(world_card)
        world_layout.setContentsMargins(20, 16, 20, 16)
        world_layout.addRow("Мир:", BodyLabel(self.server.default_world))
        world_layout.addRow("Режим:", BodyLabel(self.server.game_mode))
        self.auto_start_label = BodyLabel("Включён" if self.server.auto_start else "Отключён")
        world_layout.addRow("Автостарт:", self.auto_start_label)
        world_layout.addRow("Путь:", BodyLabel(self.server.path or "/instances/unknown"))

        world_card.setStyleSheet("""
            QLabel, BodyLabel {
             background: transparent;
            }
        """)

        layout.addWidget(world_card)

        layout.addStretch()

    def _toggle_server(self):
        import asyncio
        proc = self.process_manager.get_process(self.server.id)
        loop = self.process_manager._event_loop

        if proc and proc.state == ProcessState.RUNNING:
            if self.server.is_remote:
                self.remote_access_service.api_post_for_server(self.server, "/api/action", {"action": "server.stop"})
            else:
                asyncio.run_coroutine_threadsafe(self.process_manager.stop_server(self.server.id), loop)
        else:
            dashboard = self.window()
            auth_cb = getattr(dashboard, "_handle_auth_link", None)
            if self.server.is_remote:
                self.remote_access_service.api_post_for_server(self.server, "/api/action", {"action": "server.start"})
            else:
                asyncio.run_coroutine_threadsafe(self.process_manager.start_server(self.server, auth_link_callback=auth_cb), loop)
        self.refresh()

    def _restart_server(self):
        import asyncio
        loop = self.process_manager._event_loop

        async def restart():
            if self.server.is_remote:
                self.remote_access_service.api_post_for_server(self.server, "/api/action", {"action": "server.stop"})
                await asyncio.sleep(1)
                self.remote_access_service.api_post_for_server(self.server, "/api/action", {"action": "server.start"})
            else:
                await self.process_manager.stop_server(self.server.id)
                await asyncio.sleep(1)
                dashboard = self.window()
                auth_cb = getattr(dashboard, "_handle_auth_link", None)
                await self.process_manager.start_server(self.server, auth_link_callback=auth_cb)

        asyncio.run_coroutine_threadsafe(restart(), loop)
        self.refresh()

    def _stop_server(self):
        import asyncio
        loop = self.process_manager._event_loop
        if self.server.is_remote:
            self.remote_access_service.api_post_for_server(self.server, "/api/action", {"action": "server.stop"})
        else:
            asyncio.run_coroutine_threadsafe(self.process_manager.stop_server(self.server.id), loop)
        self.refresh()

    def refresh(self):
        srv = ServerManager().get_server(self.server.id)
        if srv:
            self.server = srv

        proc = self.process_manager.get_process(self.server.id)
        usage = self.process_manager.get_resource_usage(self.server.id)
        if self.server.is_remote:
            dashboard = self.window()
            snapshot = getattr(dashboard, "_remote_snapshot_cache", None) if dashboard else None
            if snapshot:
                usage = snapshot.get("usage", usage)
        if proc:
            is_running = proc.state == ProcessState.RUNNING
            self.status_title.setText(f"Статус: {srv.status.value}")
            self.status_icon.setStyleSheet(
                "font-size: 32px; color: #4caf50; background: transparent;" if is_running
                else "font-size: 32px; color: #f44336; background: transparent;"
            )
            self.uptime_label.setText(f"Аптайм: {proc.uptime}")
            self.start_btn.setText("Остановить" if is_running else "Запустить")
        else:
            is_running = srv.status == ServerStatus.RUNNING
            self.status_title.setText(f"Статус: {srv.status.value}")
            self.status_icon.setStyleSheet(
                "font-size: 32px; color: #4caf50; background: transparent;" if is_running
                else "font-size: 32px; color: #f44336; background: transparent;"
            )
            self.uptime_label.setText(f"Аптайм: {srv.uptime}")
            self.start_btn.setText("Остановить" if is_running else "Запустить")

        self.stat_values["Игроки"].setText(f"{self.server.online_players}/{self.server.max_players}")
        self.stat_values["Порт"].setText(str(self.server.port))
        self.stat_values["Версия"].setText(self.server.version)
        self.stat_values["CPU"].setText(f"{usage['cpu_percent']:.1f}%")
        self.stat_values["RAM"].setText(f"{usage['memory_mb']:.1f} MB")
        self.auto_start_label.setText("Включён" if self.server.auto_start else "Отключён")

        analysis = self.process_manager.get_last_error_analysis(self.server.id)
        if srv and srv.status == ServerStatus.ERROR and analysis:
            signature = (analysis.get("code"), analysis.get("message"))
            if signature in self._shown_error_signatures:
                return
            self._shown_error_signatures.add(signature)
            QMessageBox.warning(
                self,
                analysis["title"],
                f"{analysis['message']}\n\nРекомендация:\n{analysis['suggestion']}",
            )
            proc = self.process_manager.get_process(self.server.id)
            if proc:
                proc.last_error_analysis = None
        elif srv and srv.status != ServerStatus.ERROR:
            self._shown_error_signatures.clear()


# =============================================================================
# ВКЛАДКА: КОНСОЛЬ
# =============================================================================
class ConsoleTab(QWidget):
    """Вкладка консоли сервера с логами, фильтрами, макросами и вводом команд."""

    MAX_LOG_LINES = 5000

    CATEGORIES = {
        "all": ("Все", "#e0e0e0"),
        "error": ("Ошибки", "#f44336"),
        "warn": ("Предупреждения", "#ff9800"),
        "info": ("Инфо", "#2196f3"),
        "debug": ("Отладка", "#9e9e9e"),
    }

    LOG_PATTERNS = {
        "error": [r'ERROR', r'Exception', r'Failed', r'Crash'],
        "warn": [r'WARN', r'WARNING'],
        "info": [r'INFO'],
        "debug": [r'FINE', r'DEBUG', r'TRACE'],
    }

    def __init__(self, server: ServerInstance, process_manager: AsyncProcessManager, parent=None):
        super().__init__(parent)
        self.server = server
        self.process_manager = process_manager
        self.remote_access_service = RemoteAccessService()
        self._suggestions_visible = False
        self.current_filter = "all"
        self._log_buffer = deque(maxlen=100000)
        self._processed_log_count = 0
        self._last_remote_log_signature = None
        self._log_revision = 0
        self._command_paths = []
        self._command_cache_path = Path(server.path) / ".remote_commands_dump.json"
        self._remote_poll_ts = 0.0
        self._remote_poll_cache = None
        self.macros_bar_widget = None
        self._setup_ui()
        self._start_log_reader()
        self._load_command_suggestions()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        filter_bar = QWidget()
        filter_bar.setFixedHeight(44)
        filter_bar.setStyleSheet("background-color: #252525; border-bottom: 1px solid #333;")
        filter_layout = QHBoxLayout(filter_bar)
        filter_layout.setContentsMargins(12, 6, 12, 6)
        filter_layout.setSpacing(8)

        filter_label = BodyLabel("Фильтр:")
        filter_label.setStyleSheet("color: #888; background: transparent;")
        filter_layout.addWidget(filter_label)

        self.filter_chips = {}
        for key, (name, color) in self.CATEGORIES.items():
            chip = ToggleButton(name)
            chip.setChecked(key == "all")
            chip.clicked.connect(lambda checked, k=key: self._set_filter(k))
            self.filter_chips[key] = chip
            filter_layout.addWidget(chip)

        filter_layout.addStretch()

        layout.addWidget(filter_bar)

        self.log_view = PlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setStyleSheet("""
            QPlainTextEdit {
                background-color: #121212;
                color: #e0e0e0;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 13px;
                border: none;
                padding: 8px;
            }
        """)
        layout.addWidget(self.log_view)

        macros_bar = QWidget()
        macros_bar.setObjectName("macros_bar")
        macros_bar.setStyleSheet("background-color: #1e1e1e; border-top: 1px solid #333;")
        macros_layout = QVBoxLayout(macros_bar)
        macros_layout.setContentsMargins(8, 4, 8, 4)
        macros_layout.setSpacing(6)

        macros_label = CaptionLabel("Макросы:")
        macros_label.setStyleSheet("color: #888; background: transparent;")
        macros_layout.addWidget(macros_label)

        self.macro_buttons = []
        self.macro_grid = QGridLayout()
        self.macro_grid.setContentsMargins(0, 0, 0, 0)
        self.macro_grid.setSpacing(6)
        macros_layout.addLayout(self.macro_grid)
        self.macros_bar_widget = macros_bar
        self._refresh_macro_buttons()

        self.add_macro_btn = ToolButton(FIF.ADD)
        self.add_macro_btn.setFixedSize(30,30)
        self.add_macro_btn.setToolTip("Добавить макрос")
        self.add_macro_btn.clicked.connect(self._add_macro)
        macros_layout.addWidget(self.add_macro_btn, alignment=Qt.AlignLeft)

        layout.addWidget(macros_bar)

        input_bar = QWidget()
        input_bar.setFixedHeight(50)
        input_bar.setStyleSheet("background-color: #1e1e1e; border-top: 1px solid #333;")
        input_layout = QHBoxLayout(input_bar)
        input_layout.setContentsMargins(12, 8, 12, 8)
        input_layout.setSpacing(10)

        self.cmd_input = QLineEdit()
        self.cmd_input.setPlaceholderText("Введите команду...")
        self.cmd_input.setClearButtonEnabled(True)
        self.cmd_input.returnPressed.connect(self._send_command)
        self.cmd_input.textChanged.connect(self._on_command_text_changed)
        self.cmd_input.installEventFilter(self)
        self.cmd_input.setStyleSheet(
            "background-color: #121212; color: #e0e0e0; border: 1px solid #333; border-radius: 4px; padding: 6px 8px;"
        )
        input_layout.addWidget(self.cmd_input)

        self.suggestion_hint = CaptionLabel("")
        self.suggestion_hint.setStyleSheet("color: #666; background: transparent;")
        self.suggestion_hint.setFixedWidth(180)
        input_layout.addWidget(self.suggestion_hint)

        self._suggestion_menu = None

        self.send_btn = PrimaryPushButton(FIF.SEND, "Отправить")
        self.send_btn.setFixedWidth(120)
        self.send_btn.clicked.connect(self._send_command)
        input_layout.addWidget(self.send_btn)

        if self.server.is_remote and not _has_remote_permission(self.server, "console.command"):
            self.cmd_input.setEnabled(False)
            self.send_btn.setVisible(False)
            self.add_macro_btn.setVisible(False)

        layout.addWidget(input_bar)

    def _refresh_macro_buttons(self):
        if not self.server:
            return
        for btn in self.macro_buttons:
            self.macro_grid.removeWidget(btn)
            btn.deleteLater()
        self.macro_buttons.clear()

        for i, macro in enumerate(getattr(self.server, "macros", []) or []):
            btn = PushButton(macro["name"])
            btn.clicked.connect(lambda checked, cmd=macro["command"]: self._execute_macro(cmd))
            btn.setContextMenuPolicy(Qt.CustomContextMenu)
            btn.customContextMenuRequested.connect(lambda pos, index=i, widget=btn: self._show_macro_menu(index, widget.mapToGlobal(pos)))
            self.macro_grid.addWidget(btn, i // 3, i % 3)
            self.macro_buttons.append(btn)

    def _load_macros(self):
        self._refresh_macro_buttons()

    def _add_macro(self):
        name, ok = QInputDialog.getText(self, "Новый макрос", "Название:")
        if not ok or not name.strip():
            return

        cmd, ok2 = QInputDialog.getText(self, "Новый макрос", "Команда:")
        if not ok2 or not cmd.strip():
            return

        if self.server.is_remote:
            response = self.remote_access_service.api_post_for_server(self.server, "/api/action", {"action": "console.macro.add", "name": name.strip(), "command": cmd.strip()})
            self.server.macros = response.get("macros", [])
        else:
            mgr = ServerManager()
            mgr.add_macro(self.server.id, name.strip(), cmd.strip())
            self.server = mgr.get_server(self.server.id) or self.server
        self._refresh_macro_buttons()

        InfoBar.success("Макрос добавлен", f"{name} -> {cmd}", duration=2000, parent=self)

    def _show_macro_menu(self, index: int, global_pos):
        menu = QMenu(self)
        delete_action = menu.addAction("Удалить макрос")
        action = menu.exec(global_pos)
        if action == delete_action:
            if self.server.is_remote:
                response = self.remote_access_service.api_post_for_server(self.server, "/api/action", {"action": "console.macro.delete", "index": index})
                self.server.macros = response.get("macros", [])
            else:
                mgr = ServerManager()
                mgr.remove_macro(self.server.id, index)
                self.server = mgr.get_server(self.server.id) or self.server
            self._refresh_macro_buttons()

    def _execute_macro(self, command: str):
        self.cmd_input.setText(command)
        self._send_command()

    def _start_log_reader(self):
        self.log_timer = QTimer(self)
        self.log_timer.timeout.connect(self._read_logs_from_process)
        self.log_timer.start(500)

    def _read_logs_from_process(self):
        if not self.server:
            return
        if self.server.is_remote:
            try:
                import time

                if not self._remote_poll_cache or time.time() - self._remote_poll_ts >= 1:
                    self._remote_poll_cache = self.remote_access_service.api_get_for_server(self.server, "/api/state")
                    self._remote_poll_ts = time.time()
                snapshot = self._remote_poll_cache
                remote_server = snapshot.get("server")
                if remote_server:
                    self.server = _apply_remote_identity(self.server, ServerInstance.from_dict(remote_server))
                self.server.macros = snapshot.get("macros", getattr(self.server, "macros", []))
                commands_dump = snapshot.get("commands_dump") or self._load_cached_remote_commands_dump()
                if snapshot.get("commands_dump"):
                    self._store_cached_remote_commands_dump(snapshot.get("commands_dump"))
                self._command_paths = sorted({item.get("name", "").strip() for item in (commands_dump or {}).get("modern", []) if item.get("name")})
                logs = snapshot.get("logs", [])
                log_signature = (len(logs), logs[-1] if logs else "", logs[0] if logs else "")
                if log_signature != self._last_remote_log_signature:
                    self._clear_logs(clear_process_buffer=False)
                    for line in logs:
                        category = self._categorize_log(line)
                        self._append_log(line, category, render=False)
                    self._processed_log_count = len(logs)
                    self._last_remote_log_signature = log_signature
                    self._refresh_macro_buttons()
                    self._render_logs()
            except Exception:
                return
            return

        revision = self.process_manager.get_log_revision(self.server.id)
        if revision != self._log_revision:
            self._clear_logs(clear_process_buffer=False)
            self._log_revision = revision

        logs = self.process_manager.get_logs(self.server.id, limit=100000)
        if not logs:
            return
        if len(logs) < self._processed_log_count:
            self._clear_logs(clear_process_buffer=False)
        if len(logs) > self._processed_log_count:
            new_logs = logs[self._processed_log_count:]
            for line in new_logs:
                category = self._categorize_log(line)
                self._append_log(line, category, render=False)
            self._processed_log_count = len(logs)
            self._render_logs()

    def _categorize_log(self, line: str) -> str:
        for category, patterns in self.LOG_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, line, re.IGNORECASE):
                    return category
        return "info"

    def _append_log(self, message: str, category: str, render: bool = True):
        self._log_buffer.append((message, category))
        if render:
            self._render_logs()

    def _render_logs(self):
        lines = [msg for msg, cat in self._log_buffer if self.current_filter == "all" or self.current_filter == cat]
        self.log_view.setPlainText("\n".join(lines[-self.MAX_LOG_LINES:]))
        scrollbar = self.log_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _set_filter(self, category: str):
        self.current_filter = category
        for k, chip in self.filter_chips.items():
            chip.setChecked(k == category)

        self._render_logs()

    def _clear_logs(self, clear_process_buffer: bool = True):
        self.log_view.clear()
        self._log_buffer.clear()
        self._processed_log_count = 0
        self._last_remote_log_signature = None
        self._remote_poll_cache = None
        self._remote_poll_ts = 0.0
        if clear_process_buffer:
            proc = self.process_manager.get_process(self.server.id)
            if proc:
                proc.clear_logs()

        self._hide_suggestion_menu()

    def _send_command(self):
        cmd = self.cmd_input.text().strip()
        if not cmd:
            return

        dangerous = cmd.lstrip("/").split(" ", 1)[0].lower() in {"stop", "shutdown", "restart"}
        if self.server.is_remote and dangerous and not _has_remote_permission(self.server, "server.stop"):
            InfoBar.error("Команда запрещена", "У вас нет прав на остановку или перезапуск сервера", duration=2500, parent=self)
            return

        self.cmd_input.clear()
        self._hide_suggestion_menu()

        import asyncio
        if self.server.is_remote:
            self.remote_access_service.api_post_for_server(self.server, "/api/action", {"action": "console.command", "command": cmd})
        else:
            loop = self.process_manager._event_loop
            asyncio.run_coroutine_threadsafe(self.process_manager.send_command(self.server.id, cmd), loop)

    def _on_command_text_changed(self, text: str):
        suggestions = self._get_command_suggestions(text)
        self.suggestion_hint.setText("Tab: подсказки" if suggestions and text.startswith("/") else "")
        if self._suggestions_visible and suggestions and text.startswith("/"):
            self._show_suggestion_menu(suggestions)
        else:
            self._hide_suggestion_menu()

    def _apply_completion(self, completion: str):
        has_children = any(path.startswith(f"{completion} ") for path in self._command_paths)
        self.cmd_input.setText(f"{completion} " if has_children else completion)
        self.cmd_input.setFocus()
        self._hide_suggestion_menu()

    def _show_suggestion_menu(self, suggestions: list[str]):
        self._hide_suggestion_menu()
        menu = QMenu(self)
        for suggestion in suggestions[:20]:
            action = menu.addAction(suggestion)
            action.triggered.connect(lambda checked=False, text=suggestion: self._apply_completion(text))

        popup_pos = self.cmd_input.mapToGlobal(self.cmd_input.rect().bottomLeft())
        menu.popup(popup_pos)
        self._suggestion_menu = menu
        self._suggestions_visible = True

    def _hide_suggestion_menu(self):
        if self._suggestion_menu is not None:
            self._suggestion_menu.close()
            self._suggestion_menu.deleteLater()
            self._suggestion_menu = None
        self._suggestions_visible = False

    def eventFilter(self, obj, event):
        if obj is self.cmd_input and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Down, Qt.Key_Tab):
                suggestions = self._get_command_suggestions(self.cmd_input.text())
                if suggestions:
                    self._show_suggestion_menu(suggestions)
                    return True
            if event.key() == Qt.Key_Escape:
                self._hide_suggestion_menu()
        return super().eventFilter(obj, event)

    def _load_command_suggestions(self):
        if self.server.is_remote:
            dump = self._load_cached_remote_commands_dump()
            if dump:
                self._command_paths = sorted({item.get("name", "").strip() for item in dump.get("modern", []) if item.get("name")})
            return
        dump_path = Path(self.server.path) / "Server" / "dumps" / "commands.dump.json"
        if not dump_path.exists():
            dump_path = Path(self.server.path) / "dumps" / "commands.dump.json"

        if dump_path.exists():
            try:
                with open(dump_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._command_paths = sorted({item.get("name", "").strip() for item in data.get("modern", []) if item.get("name")})
            except Exception:
                self._command_paths = []

    def _get_command_suggestions(self, text: str):
        if not text.startswith("/"):
            return []

        if not self._command_paths:
            self._load_command_suggestions()

        if text == "/":
            return [path for path in self._command_paths if path.count(" ") == 0][:50]

        normalized = text.rstrip()
        parts = normalized.split()
        if text.endswith(" "):
            return [path for path in self._command_paths if path.startswith(f"{normalized} ")][:50]

        parent = " ".join(parts[:-1])
        last_part = parts[-1].lower()
        results = []
        for path in self._command_paths:
            path_parts = path.split()
            if len(path_parts) < len(parts):
                continue
            if parent and " ".join(path_parts[:len(parts) - 1]) != parent:
                continue
            if path_parts[len(parts) - 1].lower().startswith(last_part):
                results.append(" ".join(path_parts[:len(parts)]))
        return sorted(set(results))[:50]

    def _store_cached_remote_commands_dump(self, data: dict):
        try:
            self._command_cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._command_cache_path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _load_cached_remote_commands_dump(self) -> Optional[dict]:
        if not self._command_cache_path.exists():
            return None
        try:
            with open(self._command_cache_path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return None


# =============================================================================
# ВКЛАДКА: МОДИФИКАЦИИ
# =============================================================================
class ModsTab(QWidget):
    """Вкладка управления модификациями сервера."""

    _update_result_signal = Signal(str)
    _load_mods_signal = Signal()

    def __init__(self, server: ServerInstance, process_manager: AsyncProcessManager, parent=None):
        super().__init__(parent)
        self.server = server
        self.process_manager = process_manager
        self.mod_manager = ModManager(server.path, api_key=AppConfig.get_curseforge_api_key())
        self.remote_access_service = RemoteAccessService()
        self._setup_ui()
        self._load_mods()

        self._update_result_signal.connect(
            lambda results: InfoBar.success("Обновление завершено", results, duration=5000, parent=self)
        )
        self._load_mods_signal.connect(self._load_mods)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        toolbar = QHBoxLayout()

        title = SubtitleLabel("Модификации")
        toolbar.addWidget(title)
        toolbar.addStretch()

        self.add_btn = PrimaryPushButton(FIF.ADD, "Добавить")
        self.add_btn.clicked.connect(self._open_mod_browser)
        toolbar.addWidget(self.add_btn)

        self.update_btn = PushButton(FIF.SYNC, "Обновить все")
        self.update_btn.clicked.connect(self._update_all_mods)
        toolbar.addWidget(self.update_btn)

        self.refresh_btn = PushButton(FIF.SYNC, "Обновить список")
        self.refresh_btn.clicked.connect(self._load_mods)
        toolbar.addWidget(self.refresh_btn)

        if self.server.is_remote:
            self.add_btn.setVisible(_has_remote_permission(self.server, "mods.install"))
            self.update_btn.setVisible(False)

        layout.addLayout(toolbar)

        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["", "Название", "Файл", "Дата", "Действия"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 40)
        self.table.setColumnWidth(3, 100)
        self.table.setColumnWidth(4, 180)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setStyleSheet("""
            QTableWidget {
                background-color: #1a1a1a;
                color: #e0e0e0;
                border: 1px solid #333;
                gridline-color: #333;
            }
            QTableWidget::item:selected {
                background-color: #2a4a6a;
            }
            QHeaderView::section {
                background-color: #252525;
                color: #ccc;
                padding: 8px;
                border: 1px solid #333;
            }
        """)
        layout.addWidget(self.table)

        info = CaptionLabel("Моды хранятся в папке mods/. Отключённые моды перемещаются в mods/disabled/.")
        if self.server.is_remote:
            info.setText("Для внешнего инстанса действия применяются на стороне владельца сервера.")
        info.setStyleSheet("color: #666; font-size: 11px; background: transparent;")
        info.setWordWrap(True)
        layout.addWidget(info)

    def _load_mods(self):
        self.table.setRowCount(0)

        if self.server.is_remote:
            snapshot = self.remote_access_service.api_get_for_server(self.server, "/api/state")
            remote_mods = snapshot.get("mods", [])
            enabled_mods = [mod for mod in remote_mods if mod.get("enabled", True)]
            disabled_mods = [mod for mod in remote_mods if not mod.get("enabled", True)]
        else:
            enabled_mods = self.mod_manager.list_enabled_mods()
            disabled_mods = self.mod_manager.list_disabled_mods()

        all_mods = [(m, True) for m in enabled_mods] + [(m, False) for m in disabled_mods]

        for mod_info, is_enabled in all_mods:
            row = self.table.rowCount()
            self.table.insertRow(row)

            indicator = QTableWidgetItem()
            indicator.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            if is_enabled:
                indicator.setText("●")
                indicator.setForeground(Qt.green)
            else:
                indicator.setText("●")
                indicator.setForeground(Qt.red)
            self.table.setItem(row, 0, indicator)

            name_item = QTableWidgetItem(mod_info["name"])
            name_item.setFont(QFont("Segoe UI", 10, QFont.Bold))
            self.table.setItem(row, 1, name_item)

            file_item = QTableWidgetItem(mod_info["file_name"])
            file_item.setForeground(Qt.gray)
            self.table.setItem(row, 2, file_item)

            date_item = QTableWidgetItem(mod_info.get("version", "N/A"))
            date_item.setFont(QFont("Consolas", 9))
            date_item.setForeground(Qt.gray)
            self.table.setItem(row, 3, date_item)

            actions_widget = QWidget()
            actions_layout = QHBoxLayout(actions_widget)
            actions_layout.setContentsMargins(4, 2, 4, 2)
            actions_layout.setSpacing(4)

            if is_enabled:
                if _has_remote_permission(self.server, "mods.toggle"):
                    disable_btn = PushButton("Отключить")
                    disable_btn.clicked.connect(lambda checked, fn=mod_info["file_name"]: self._disable_mod(fn))
                    actions_layout.addWidget(disable_btn)
            else:
                if _has_remote_permission(self.server, "mods.toggle"):
                    enable_btn = PushButton("Включить")
                    enable_btn.clicked.connect(lambda checked, fn=mod_info["file_name"]: self._enable_mod(fn))
                    actions_layout.addWidget(enable_btn)

            if _has_remote_permission(self.server, "mods.delete"):
                delete_btn = ToolButton(FIF.DELETE)
                delete_btn.setFixedSize(28, 28)
                delete_btn.clicked.connect(lambda checked, fn=mod_info["file_name"]: self._delete_mod(fn))
                actions_layout.addWidget(delete_btn)

            self.table.setCellWidget(row, 4, actions_widget)

    def _disable_mod(self, file_name: str):
        ok = self.remote_access_service.api_post_for_server(self.server, "/api/action", {"action": "mods.toggle", "file_name": file_name, "enabled": False}).get("ok") if self.server.is_remote else self.mod_manager.disable_mod(file_name)
        if ok:
            InfoBar.success("Мод отключён", file_name, duration=2000, parent=self)
            self._load_mods()
        else:
            InfoBar.error("Ошибка", "Не удалось отключить мод", duration=2000, parent=self)

    def _enable_mod(self, file_name: str):
        ok = self.remote_access_service.api_post_for_server(self.server, "/api/action", {"action": "mods.toggle", "file_name": file_name, "enabled": True}).get("ok") if self.server.is_remote else self.mod_manager.enable_mod(file_name)
        if ok:
            InfoBar.success("Мод включён", file_name, duration=2000, parent=self)
            self._load_mods()
        else:
            InfoBar.error("Ошибка", "Не удалось включить мод", duration=2000, parent=self)

    def _delete_mod(self, file_name: str):
        msg = QMessageBox(self)
        msg.setWindowTitle("Удаление мода")
        msg.setText(f"Удалить мод {file_name}?")
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        if msg.exec() == QMessageBox.Yes:
            ok = self.remote_access_service.api_post_for_server(self.server, "/api/action", {"action": "mods.delete", "file_name": file_name}).get("ok") if self.server.is_remote else self.mod_manager.delete_mod(file_name)
            if ok:
                InfoBar.success("Удалено", file_name, duration=2000, parent=self)
                self._load_mods()

    def _open_mod_browser(self):
        api_key = AppConfig.get_curseforge_api_key()
        if not api_key:
            dialog = ApiKeyDialog(self)
            if dialog.exec() == QDialog.Accepted:
                api_key = dialog.get_key()
                AppConfig.set_curseforge_api_key(api_key)
                self.mod_manager.api.api_key = api_key
                self.mod_manager.api._headers["X-API-Key"] = api_key
            else:
                return

        if self.server.is_remote:
            browser = ModBrowserDialog(self.mod_manager, self.process_manager._event_loop, self)
            original_install = browser._install_mod

            def remote_install(mod):
                mod_id = mod.get("id")
                mod_name = mod.get("name", "Unknown")
                slug = mod.get("slug") or str(mod_id)

                async def do_remote_install():
                    files = await self.mod_manager.api.get_mod_files(mod_id)
                    if not files:
                        browser._set_status_signal.emit("Нет доступных файлов для этого мода")
                        return
                    latest = files[0]
                    file_id = latest["id"]
                    file_name = latest.get("fileName", f"{mod_id}.jar")
                    file_date = latest.get("fileDate", 0)
                    if isinstance(file_date, str):
                        file_date = int(datetime.datetime.fromisoformat(file_date.replace("Z", "+00:00")).timestamp() * 1000)

                    result = self.remote_access_service.api_post_for_server(self.server, "/api/action", {
                        "action": "mods.install",
                        "mod_id": mod_id,
                        "file_id": file_id,
                        "file_name": file_name,
                        "file_date": file_date,
                        "slug": slug,
                        "mod_name": mod_name,
                    })
                    if result.get("ok"):
                        browser.mod_installed.emit(file_name)
                        browser._set_status_signal.emit(f"Установлен: {mod_name}")
                    else:
                        browser._set_status_signal.emit("Не удалось установить мод на стороне сервера")

                asyncio.run_coroutine_threadsafe(do_remote_install(), self.process_manager._event_loop)

            browser._install_mod = remote_install
            browser._open_mods_folder_signal.disconnect()
            browser._open_mods_folder_signal.connect(lambda: InfoBar.info("Недоступно", "Для внешнего инстанса локальная папка модов владельца не открывается", duration=3000, parent=self))
            for button in browser.findChildren(PushButton):
                if button.text() == "Открыть папку с модами":
                    button.setVisible(False)
            browser.mod_installed.connect(self._load_mods)
            browser.exec()
            return

        browser = ModBrowserDialog(self.mod_manager, self.process_manager._event_loop, self)
        browser.mod_installed.connect(self._load_mods)
        browser.exec()

    def _update_all_mods(self):
        import asyncio
        loop = self.process_manager._event_loop

        async def update():
            results = await self.mod_manager.update_all_mods()
            self._update_result_signal.emit(str(results))
            self._load_mods_signal.emit()

        asyncio.run_coroutine_threadsafe(update(), loop)


# =============================================================================
# ВКЛАДКА: НАСТРОЙКИ
# =============================================================================
class SettingsTab(QWidget):
    """Вкладка настроек серверного инстанса Hytale."""

    def __init__(self, server: ServerInstance, parent=None):
        super().__init__(parent)
        self.server = server
        self.remote_access_service = RemoteAccessService()
        self._setup_ui()

    def _setup_ui(self):
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.setStyleSheet("""
            QLabel {
                background: transparent;
            }
        """)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(20)

        server_card = CardWidget()
        server_layout = QFormLayout(server_card)
        server_layout.setContentsMargins(20, 16, 20, 16)
        server_layout.setSpacing(12)
        server_layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        server_layout.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        server_title = SubtitleLabel("Настройки сервера (config.json)")
        server_title.setStyleSheet("background: transparent;")
        server_layout.addRow(server_title)

        self.server_name_edit = self._create_labeled_field("Название сервера:", self.server.server_name, server_layout)
        self.motd_edit = self._create_labeled_field("MOTD:", self.server.motd, server_layout)
        self.password_edit = self._create_labeled_field("Пароль:", self.server.password, server_layout)

        self.port_spin = SpinBox()
        self.port_spin.setRange(1024, 65535)
        self.port_spin.setValue(self.server.port)
        self.port_spin.setFixedHeight(36)
        server_layout.addRow("Порт:", self.port_spin)

        self.max_players_spin = SpinBox()
        self.max_players_spin.setRange(1, 1000)
        self.max_players_spin.setValue(self.server.max_players)
        self.max_players_spin.setFixedHeight(36)
        server_layout.addRow("Макс. игроков:", self.max_players_spin)

        self.view_radius_spin = SpinBox()
        self.view_radius_spin.setRange(8, 64)
        self.view_radius_spin.setValue(self.server.max_view_radius)
        self.view_radius_spin.setFixedHeight(36)
        server_layout.addRow("Радиус обзора:", self.view_radius_spin)

        self.world_edit = self._create_labeled_field("Мир по умолчанию:", self.server.default_world, server_layout)

        self.mode_combo = ComboBox()
        self.mode_combo.addItems(["Adventure", "Creative", "Survival"])
        self.mode_combo.setCurrentText(self.server.game_mode)
        self.mode_combo.setFixedHeight(36)
        server_layout.addRow("Режим игры:", self.mode_combo)

        self.tmp_tags_check = CheckBox("Отображать временные теги в строках")
        self.tmp_tags_check.setChecked(self.server.display_tmp_tags)
        self.tmp_tags_check.setStyleSheet("background: transparent;")
        server_layout.addRow(self.tmp_tags_check)

        self.auto_start_check = CheckBox("Автостарт при загрузке ОС")
        self.auto_start_check.setChecked(self.server.auto_start)
        self.auto_start_check.setStyleSheet("background: transparent;")
        server_layout.addRow(self.auto_start_check)

        layout.addWidget(server_card)

        jvm_card = CardWidget()
        jvm_layout = QFormLayout(jvm_card)
        jvm_layout.setContentsMargins(20, 16, 20, 16)
        jvm_layout.setSpacing(12)
        jvm_layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        jvm_layout.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        jvm_title = SubtitleLabel("Параметры запуска (jvm.options)")
        jvm_title.setStyleSheet("background: transparent;")
        jvm_layout.addRow(jvm_title)

        self.xms_spin = SpinBox()
        self.xms_spin.setRange(1, 32)
        self.xms_spin.setSuffix(" GB")
        self.xms_spin.setFixedHeight(36)
        jvm_layout.addRow("Мин. память (Xms):", self.xms_spin)

        self.xmx_spin = SpinBox()
        self.xmx_spin.setRange(1, 32)
        self.xmx_spin.setSuffix(" GB")
        self.xmx_spin.setFixedHeight(36)
        jvm_layout.addRow("Макс. память (Xmx):", self.xmx_spin)

        self.gc_combo = ComboBox()
        self.gc_combo.addItems(["G1GC", "ZGC", "Parallel", "Serial", "Default"])
        self.gc_combo.setFixedHeight(36)
        jvm_layout.addRow("Сборщик мусора (GC):", self.gc_combo)

        self.extra_jvm_edit = LineEdit()
        self.extra_jvm_edit.setPlaceholderText("Доп. аргументы, например: -XX:+UseStringDeduplication")
        self.extra_jvm_edit.setFixedHeight(36)
        jvm_layout.addRow("Доп. JVM аргументы:", self.extra_jvm_edit)

        self._apply_jvm_args(self.server.jvm_args)

        layout.addWidget(jvm_card)

        world_card = CardWidget()
        world_layout = QFormLayout(world_card)
        world_layout.setContentsMargins(20, 16, 20, 16)
        world_layout.setSpacing(12)
        world_layout.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        world_layout.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        world_title = SubtitleLabel("Настройки мира")
        world_title.setStyleSheet("background: transparent;")
        world_layout.addRow(world_title)

        wc = self.server.world_config

        self.pvp_check = self._create_check_field("PvP", wc.is_pvp_enabled, world_layout)
        self.fall_damage_check = self._create_check_field("Урон от падения", wc.is_fall_damage_enabled, world_layout)
        self.npc_spawning_check = self._create_check_field("Спавн NPC", wc.is_spawning_npc, world_layout)
        self.spawn_markers_check = self._create_check_field("Маркеры спавна", wc.is_spawn_markers_enabled, world_layout)
        self.npc_frozen_check = self._create_check_field("Заморозка NPC", wc.is_all_npc_frozen, world_layout)
        self.compass_check = self._create_check_field("Обновление компаса", wc.is_compass_updating, world_layout)
        self.save_players_check = self._create_check_field("Сохранение игроков", wc.is_saving_players, world_layout)
        self.save_chunks_check = self._create_check_field("Сохранение чанков", wc.is_saving_chunks, world_layout)
        self.unload_chunks_check = self._create_check_field("Выгрузка чанков", wc.is_unloading_chunks, world_layout)
        self.objective_markers_check = self._create_check_field("Маркеры заданий", wc.is_objective_markers_enabled, world_layout)

        self.gameplay_combo = ComboBox()
        self.gameplay_combo.addItems(["Default", "Peaceful", "Hardcore"])
        self.gameplay_combo.setCurrentText(wc.gameplay_config)
        self.gameplay_combo.setFixedHeight(36)
        world_layout.addRow("Геймплей:", self.gameplay_combo)

        layout.addWidget(world_card)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.save_btn = PrimaryPushButton(FIF.SAVE, "Сохранить настройки")
        self.save_btn.setFixedHeight(36)
        self.save_btn.clicked.connect(self._save)
        btn_layout.addWidget(self.save_btn)

        self.reset_btn = PushButton(FIF.RETURN, "Сбросить")
        self.reset_btn.setFixedHeight(36)
        self.reset_btn.clicked.connect(self._reset)
        btn_layout.addWidget(self.reset_btn)

        if self.server.is_remote and not _has_remote_permission(self.server, "settings.edit"):
            self.save_btn.setVisible(False)

        layout.addLayout(btn_layout)
        layout.addStretch()

        scroll.setWidget(container)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll)

    def _apply_jvm_args(self, args_text: str):
        args = args_text.split()
        xms = 4
        xmx = 6
        gc = "G1GC"
        extra = []
        for arg in args:
            if arg.startswith("-Xms"):
                try:
                    xms = int(arg.replace("-Xms", "").replace("G", "").replace("g", ""))
                except Exception:
                    pass
            elif arg.startswith("-Xmx"):
                try:
                    xmx = int(arg.replace("-Xmx", "").replace("G", "").replace("g", ""))
                except Exception:
                    pass
            elif arg == "-XX:+UseG1GC":
                gc = "G1GC"
            elif arg == "-XX:+UseZGC":
                gc = "ZGC"
            elif arg == "-XX:+UseParallelGC":
                gc = "Parallel"
            elif arg == "-XX:+UseSerialGC":
                gc = "Serial"
            elif arg not in ("-XX:+UnlockExperimentalVMOptions",):
                extra.append(arg)
        self.xms_spin.setValue(xms)
        self.xmx_spin.setValue(xmx)
        self.gc_combo.setCurrentText(gc)
        self.extra_jvm_edit.setText(" ".join(extra))

    def _create_labeled_field(self, label: str, value: str, layout: QFormLayout) -> LineEdit:
        edit = LineEdit()
        edit.setText(value)
        edit.setFixedHeight(36)
        layout.addRow(label, edit)
        return edit

    def _create_check_field(self, label: str, checked: bool, layout: QFormLayout) -> CheckBox:
        check = CheckBox(label)
        check.setChecked(checked)
        check.setStyleSheet("background: transparent;")
        layout.addRow(check)
        return check

    def _save(self):
        self.server.server_name = self.server_name_edit.text()
        self.server.motd = self.motd_edit.text()
        self.server.password = self.password_edit.text()
        self.server.port = self.port_spin.value()
        self.server.max_players = self.max_players_spin.value()
        self.server.max_view_radius = self.view_radius_spin.value()
        self.server.default_world = self.world_edit.text()
        self.server.game_mode = self.mode_combo.currentText()
        self.server.display_tmp_tags = self.tmp_tags_check.isChecked()
        self.server.auto_start = self.auto_start_check.isChecked()

        jvm_args = []
        if self.xms_spin.value() > 0:
            jvm_args.append(f"-Xms{self.xms_spin.value()}G")
        if self.xmx_spin.value() > 0:
            jvm_args.append(f"-Xmx{self.xmx_spin.value()}G")
        gc = self.gc_combo.currentText()
        if gc == "G1GC":
            jvm_args.append("-XX:+UseG1GC")
        elif gc == "ZGC":
            jvm_args.append("-XX:+UnlockExperimentalVMOptions")
            jvm_args.append("-XX:+UseZGC")
        elif gc == "Parallel":
            jvm_args.append("-XX:+UseParallelGC")
        elif gc == "Serial":
            jvm_args.append("-XX:+UseSerialGC")
        extra = self.extra_jvm_edit.text().strip()
        if extra:
            jvm_args.extend(extra.split())
        self.server.jvm_args = " ".join(jvm_args)

        wc = self.server.world_config
        wc.is_pvp_enabled = self.pvp_check.isChecked()
        wc.is_fall_damage_enabled = self.fall_damage_check.isChecked()
        wc.is_spawning_npc = self.npc_spawning_check.isChecked()
        wc.is_spawn_markers_enabled = self.spawn_markers_check.isChecked()
        wc.is_all_npc_frozen = self.npc_frozen_check.isChecked()
        wc.is_compass_updating = self.compass_check.isChecked()
        wc.is_saving_players = self.save_players_check.isChecked()
        wc.is_saving_chunks = self.save_chunks_check.isChecked()
        wc.is_unloading_chunks = self.unload_chunks_check.isChecked()
        wc.is_objective_markers_enabled = self.objective_markers_check.isChecked()
        wc.gameplay_config = self.gameplay_combo.currentText()

        if self.server.is_remote:
            self.remote_access_service.api_post_for_server(self.server, "/api/action", {"action": "settings.edit", "server": self.server.to_dict()})
        else:
            ServerManager().update_server(self.server)

            jvm_file = Path(self.server.path) / "jvm.options"
            if jvm_file.parent.exists():
                with open(jvm_file, "w", encoding="utf-8") as f:
                    for arg in self.server.jvm_args.split():
                        f.write(f"{arg}\n")

        InfoBar.success("Сохранено", "Настройки инстанса обновлены", duration=2000, parent=self)
        if not self.server.is_remote:
            self.refresh_from_server(ServerManager().get_server(self.server.id))

    def _reset(self):
        self.refresh_from_server(ServerManager().get_server(self.server.id) or self.server)

    def refresh_from_server(self, server: Optional[ServerInstance]):
        if not server:
            return
        self.server = server
        self.server_name_edit.setText(self.server.server_name)
        self.motd_edit.setText(self.server.motd)
        self.password_edit.setText(self.server.password)
        self.port_spin.setValue(self.server.port)
        self.max_players_spin.setValue(self.server.max_players)
        self.view_radius_spin.setValue(self.server.max_view_radius)
        self.world_edit.setText(self.server.default_world)
        self.mode_combo.setCurrentText(self.server.game_mode)
        self.tmp_tags_check.setChecked(self.server.display_tmp_tags)
        self.auto_start_check.setChecked(self.server.auto_start)
        self._apply_jvm_args(self.server.jvm_args)

        wc = self.server.world_config
        self.pvp_check.setChecked(wc.is_pvp_enabled)
        self.fall_damage_check.setChecked(wc.is_fall_damage_enabled)
        self.npc_spawning_check.setChecked(wc.is_spawning_npc)
        self.spawn_markers_check.setChecked(wc.is_spawn_markers_enabled)
        self.npc_frozen_check.setChecked(wc.is_all_npc_frozen)
        self.compass_check.setChecked(wc.is_compass_updating)
        self.save_players_check.setChecked(wc.is_saving_players)
        self.save_chunks_check.setChecked(wc.is_saving_chunks)
        self.unload_chunks_check.setChecked(wc.is_unloading_chunks)
        self.objective_markers_check.setChecked(wc.is_objective_markers_enabled)
        self.gameplay_combo.setCurrentText(wc.gameplay_config)


# =============================================================================
# ВКЛАДКА: ФАЙЛЫ
# =============================================================================
class FilesTab(QWidget):
    """Файловый менеджер инстанса."""

    def __init__(self, server: ServerInstance, parent=None):
        super().__init__(parent)
        self.server = server
        self.remote_access_service = RemoteAccessService()
        self.root_path = Path(server.path).resolve()
        self.current_path = self.root_path
        self._setup_ui()
        self._load_directory()

    def _is_allowed_path(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self.root_path)
            return True
        except ValueError:
            return False
        except Exception:
            return False

    def _safe_join_current(self, name: str) -> Path | None:
        candidate = (self.current_path / name).resolve()
        if self._is_allowed_path(candidate):
            return candidate
        return None

    def _display_path(self, path: Path) -> str:
        if path == self.root_path:
            return "/"
        try:
            return "/" + path.relative_to(self.root_path).as_posix()
        except Exception:
            return "/"

    def _current_remote_path(self) -> str:
        return self.path_label.text() or "/"

    def _remote_item_path(self, item: QTreeWidgetItem) -> str:
        return item.data(0, Qt.UserRole) or "/"

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        toolbar = QWidget()
        toolbar.setFixedHeight(44)
        toolbar.setStyleSheet("background-color: #252525; border-bottom: 1px solid #333;")
        tb_layout = QHBoxLayout(toolbar)
        tb_layout.setContentsMargins(12, 6, 12, 6)
        tb_layout.setSpacing(8)

        self.up_btn = PushButton(FIF.UP, "Вверх")
        self.up_btn.setFixedWidth(90)
        self.up_btn.clicked.connect(self._go_up)
        tb_layout.addWidget(self.up_btn)

        self.path_label = BodyLabel(self._display_path(self.current_path))
        self.path_label.setStyleSheet("color: #aaa; font-family: monospace; background: transparent;")
        tb_layout.addWidget(self.path_label)

        tb_layout.addStretch()

        self.new_folder_btn = PushButton(FIF.FOLDER_ADD, "Папка")
        self.new_folder_btn.setFixedWidth(90)
        self.new_folder_btn.clicked.connect(self._new_folder)
        tb_layout.addWidget(self.new_folder_btn)

        self.upload_btn = PrimaryPushButton(FIF.UP, "Загрузить")
        self.upload_btn.setFixedWidth(120)
        self.upload_btn.clicked.connect(self._upload)
        tb_layout.addWidget(self.upload_btn)

        self.delete_btn = PushButton(FIF.DELETE, "Удалить")
        self.delete_btn.setFixedWidth(100)
        self.delete_btn.clicked.connect(self._delete_selected)
        tb_layout.addWidget(self.delete_btn)

        if self.server.is_remote:
            can_write = _has_remote_permission(self.server, "files.write")
            self.new_folder_btn.setVisible(can_write)
            self.upload_btn.setVisible(can_write)
            self.delete_btn.setVisible(can_write)

        layout.addWidget(toolbar)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Имя", "Размер", "Тип", "Изменён"])
        self.tree.setColumnWidth(0, 350)
        self.tree.setColumnWidth(1, 100)
        self.tree.setColumnWidth(2, 100)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.tree.setStyleSheet("""
            QTreeWidget {
                background-color: #1a1a1a;
                color: #e0e0e0;
                border: none;
                outline: none;
                font-size: 14px;
            }
            QTreeWidget::item:selected {
                background-color: #2a4a6a;
                color: #fff;
            }
            QHeaderView::section {
                background-color: #252525;
                color: #ccc;
                padding: 6px;
                border: 1px solid #333;
                font-size: 14px;
            }
        """)
        layout.addWidget(self.tree)

    def _load_directory(self):
        self.tree.clear()
        if self.server.is_remote:
            try:
                response = self.remote_access_service.api_post_for_server(self.server, "/api/action", {"action": "files.list", "path": self._current_remote_path()})
                current_path = response.get("path") or "/"
                self.path_label.setText(current_path)
                self.up_btn.setEnabled(current_path != "/")
                for item in response.get("files", []):
                    size = self._format_size(item.get("size", 0)) if not item.get("is_dir") else "—"
                    mtime = datetime.datetime.fromtimestamp(item.get("mtime", 0)).strftime("%d.%m.%Y %H:%M")
                    item_type = "Папка" if item.get("is_dir") else self._get_file_type(Path(item.get("name", "")).suffix)
                    tree_item = QTreeWidgetItem(self.tree, [item.get("name", ""), size, item_type, mtime])
                    tree_item.setData(0, Qt.UserRole, item.get("path", "/"))
                return
            except Exception as e:
                self.up_btn.setEnabled(False)
                InfoBar.error("Ошибка файлов", str(e), duration=3500, parent=self)
                return

        self.current_path = self.current_path.resolve()
        if not self._is_allowed_path(self.current_path):
            self.current_path = self.root_path
        self.path_label.setText(self._display_path(self.current_path))
        self.up_btn.setEnabled(self.current_path != self.root_path)

        try:
            items = sorted(self.current_path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))

            for item in items:
                if not self._is_allowed_path(item):
                    continue
                stat = item.stat()
                size = self._format_size(stat.st_size) if item.is_file() else "—"
                mtime = datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%d.%m.%Y %H:%M")
                item_type = "Папка" if item.is_dir() else self._get_file_type(item.suffix)

                tree_item = QTreeWidgetItem(self.tree, [item.name, size, item_type, mtime])
                tree_item.setData(0, Qt.UserRole, str(item))

                #if item.is_dir():
                #    tree_item.setIcon(0, self.style().standardIcon(self.style().SP_FileIcon))
                #else:
                #    tree_item.setIcon(0, self.style().standardIcon(self.style().SP_FileIcon))
        except PermissionError:
            pass

    def _go_up(self):
        if self.server.is_remote:
            current = Path(self._current_remote_path())
            parent = current.parent.as_posix()
            self.path_label.setText(parent if parent.startswith("/") else f"/{parent}" if parent != "." else "/")
            self._load_directory()
            return
        parent = self.current_path.parent
        if parent != self.current_path and self._is_allowed_path(parent):
            self.current_path = parent
            self._load_directory()

    def _on_item_double_clicked(self, item: QTreeWidgetItem, column: int):
        if self.server.is_remote:
            if item.text(2) == "Папка":
                self.path_label.setText(self._remote_item_path(item))
                self._load_directory()
            return
        path = Path(item.data(0, Qt.UserRole)).resolve()
        if self._is_allowed_path(path) and path.is_dir():
            self.current_path = path
            self._load_directory()

    def _show_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if not item:
            return

        path = self._remote_item_path(item) if self.server.is_remote else Path(item.data(0, Qt.UserRole)).resolve()
        if not self.server.is_remote and not self._is_allowed_path(path):
            InfoBar.error("Доступ запрещён", "Операция за пределами инстанса запрещена", duration=2500, parent=self)
            return

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background-color: #2a2a2a; color: #e0e0e0; border: 1px solid #444; }
            QMenu::item:selected { background-color: #3a3a3a; }
        """)

        if not self.server.is_remote:
            open_action = menu.addAction("Открыть")
            open_action.triggered.connect(lambda: self._open_in_explorer(path))

        if (self.server.is_remote and item.text(2) == "Папка") or (not self.server.is_remote and path.is_dir()):
            enter_action = menu.addAction("Открыть папку")
            enter_action.triggered.connect(lambda: self._enter_directory(path))

        if not self.server.is_remote or _has_remote_permission(self.server, "files.write"):
            menu.addSeparator()
            delete_action = menu.addAction("Удалить")
            delete_action.triggered.connect(lambda: self._delete_item(path))

        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _open_in_explorer(self, path: Path):
        InfoBar.info("Ограничение безопасности", "Открытие внешнего проводника отключено для защиты файловой системы", duration=3500, parent=self)

    def _enter_directory(self, path: Path):
        if self.server.is_remote:
            self.path_label.setText(str(path))
            self._load_directory()
            return
        if self._is_allowed_path(path) and path.is_dir():
            self.current_path = path
            self._load_directory()

    def _delete_item(self, path: Path):
        if self.server.is_remote:
            msg = QMessageBox(self)
            msg.setWindowTitle("Удаление")
            msg.setText(f"Удалить {Path(str(path)).name}?")
            msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            if msg.exec() == QMessageBox.Yes:
                self.remote_access_service.api_post_for_server(self.server, "/api/action", {"action": "files.delete", "path": str(path)})
                self._load_directory()
                InfoBar.success("Удалено", Path(str(path)).name, duration=2000, parent=self)
            return
        path = path.resolve()
        if not self._is_allowed_path(path):
            InfoBar.error("Доступ запрещён", "Удаление за пределами инстанса запрещено", duration=2500, parent=self)
            return
        msg = QMessageBox(self)
        msg.setWindowTitle("Удаление")
        msg.setText(f"Удалить {path.name}?")
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        if msg.exec() == QMessageBox.Yes:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            self._load_directory()
            InfoBar.success("Удалено", path.name, duration=2000, parent=self)

    def _delete_selected(self):
        items = self.tree.selectedItems()
        if not items:
            InfoBar.warning("Ничего не выбрано", "Выберите файл или папку для удаления", duration=2000, parent=self)
            return
        for item in items:
            path = self._remote_item_path(item) if self.server.is_remote else Path(item.data(0, Qt.UserRole)).resolve()
            self._delete_item(path)

    def _new_folder(self):
        name, ok = QInputDialog.getText(self, "Новая папка", "Имя папки:")
        if ok and name:
            if self.server.is_remote:
                current = self._current_remote_path().rstrip("/") or "/"
                remote_path = f"{current}/{name}" if current != "/" else f"/{name}"
                self.remote_access_service.api_post_for_server(self.server, "/api/action", {"action": "files.mkdir", "path": remote_path})
                self._load_directory()
                InfoBar.success("Создано", name, duration=2000, parent=self)
                return
            new_path = self._safe_join_current(name)
            if new_path is None:
                InfoBar.error("Доступ запрещён", "Нельзя создавать папки вне директории инстанса", duration=2500, parent=self)
                return
            new_path.mkdir(exist_ok=True)
            self._load_directory()
            InfoBar.success("Создано", name, duration=2000, parent=self)

    def _upload(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Загрузить файлы", "", "Все файлы (*.*)")
        if paths:
            if self.server.is_remote:
                current = self._current_remote_path().rstrip("/") or "/"
                for path in paths:
                    file_name = Path(path).name
                    remote_path = f"{current}/{file_name}" if current != "/" else f"/{file_name}"
                    with open(path, "rb") as fh:
                        encoded = base64.b64encode(fh.read()).decode("ascii")
                    self.remote_access_service.api_post_for_server(self.server, "/api/action", {"action": "files.upload", "path": remote_path, "content_base64": encoded})
                self._load_directory()
                InfoBar.success("Загрузка завершена", f"Загружено {len(paths)} файл(ов)", duration=2000, parent=self)
                return
            for path in paths:
                dest = self._safe_join_current(Path(path).name)
                if dest is None:
                    InfoBar.error("Доступ запрещён", "Нельзя загружать файлы вне директории инстанса", duration=2500, parent=self)
                    return
                shutil.copy(path, str(dest))
            self._load_directory()
            InfoBar.success("Загрузка завершена", f"Загружено {len(paths)} файл(ов)", duration=2000, parent=self)

    def _format_size(self, size_bytes: int) -> str:
        for unit in ["B", "KB", "MB", "GB"]:
            if size_bytes < 1024:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024
        return f"{size_bytes:.1f} TB"

    def _get_file_type(self, suffix: str) -> str:
        types = {
            ".jar": "JAR",
            ".json": "JSON",
            ".log": "Лог",
            ".txt": "Текст",
            ".zip": "Архив",
            ".gz": "Архив",
            ".bat": "Скрипт",
            ".sh": "Скрипт",
            ".cfg": "Конфиг",
            ".properties": "Конфиг",
        }
        return types.get(suffix.lower(), "Файл")


# =============================================================================
# ВКЛАДКА: БЭКАПЫ
# =============================================================================
class BackupsTab(QWidget):
    """Управление резервными копиями инстанса."""

    def __init__(self, server: ServerInstance, parent=None):
        super().__init__(parent)
        self.server = server
        self.backup_manager = BackupManager(server.path, server.slug)
        self.process_manager = AsyncProcessManager()
        self.remote_access_service = RemoteAccessService()
        self._setup_ui()
        self._load_backups()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        toolbar = QHBoxLayout()

        title = SubtitleLabel("Резервные копии")
        toolbar.addWidget(title)
        toolbar.addStretch()

        self.create_btn = PrimaryPushButton(FIF.SAVE, "Создать бэкап")
        self.create_btn.clicked.connect(self._create_backup)
        toolbar.addWidget(self.create_btn)

        self.schedule_btn = PushButton("Расписание")
        self.schedule_btn.clicked.connect(self._edit_schedule)
        toolbar.addWidget(self.schedule_btn)

        self.restore_btn = PushButton(FIF.SYNC, "Восстановить")
        self.restore_btn.clicked.connect(self._restore_backup)
        toolbar.addWidget(self.restore_btn)

        self.delete_btn = PushButton(FIF.DELETE, "Удалить")
        self.delete_btn.clicked.connect(self._delete_backup)
        toolbar.addWidget(self.delete_btn)

        if self.server.is_remote:
            self.create_btn.setVisible(_has_remote_permission(self.server, "backups.create"))
            self.restore_btn.setVisible(_has_remote_permission(self.server, "backups.restore"))
            self.delete_btn.setVisible(_has_remote_permission(self.server, "backups.delete"))

        layout.addLayout(toolbar)

        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Выбор", "Имя файла", "Дата создания", "Размер", "Содержимое"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setStyleSheet("""
            QTableWidget {
                background-color: #1a1a1a;
                color: #e0e0e0;
                border: 1px solid #333;
                gridline-color: #333;
            }
            QTableWidget::item:selected {
                background-color: #2a4a6a;
            }
            QHeaderView::section {
                background-color: #252525;
                color: #ccc;
                padding: 8px;
                border: 1px solid #333;
            }
        """)
        layout.addWidget(self.table)

        info = CaptionLabel("Бэкапы создаются атомарно. Восстановление перезапишет текущее состояние миров и конфигураций.")
        info.setStyleSheet("color: #666; font-size: 11px; background: transparent;")
        info.setWordWrap(True)
        layout.addWidget(info)

    def _edit_schedule(self):
        dialog = BackupConfigDialog(self)
        dialog.set_values(self.server.backup_schedule.config, self.server.backup_schedule)
        if not dialog.exec():
            return

        self.server.backup_schedule.enabled = dialog.get_schedule_data()["enabled"]
        self.server.backup_schedule.interval_minutes = dialog.get_schedule_data()["interval_minutes"]
        self.server.backup_schedule.keep_last = dialog.get_schedule_data()["keep_last"]
        self.server.backup_schedule.config = dialog.get_config()
        if self.server.is_remote:
            self.remote_access_service.api_post_for_server(self.server, "/api/action", {"action": "backups.schedule.update", "backup_schedule": self.server.backup_schedule})
        else:
            ServerManager().update_server(self.server)
        InfoBar.success("Расписание сохранено", "Настройки автобэкапа обновлены", duration=2500, parent=self)

    def _load_backups(self):
        self.table.setRowCount(0)
        if self.server.is_remote:
            snapshot = self.remote_access_service.api_get_for_server(self.server, "/api/state")
            backups = snapshot.get("backups", [])
        else:
            backups = self.backup_manager.list_backups()

        for backup in backups:
            row = self.table.rowCount()
            self.table.insertRow(row)

            chk = QTableWidgetItem()
            chk.setCheckState(Qt.Unchecked)
            self.table.setItem(row, 0, chk)

            self.table.setItem(row, 1, QTableWidgetItem(backup["name"]))
            date_str = backup["date"].strftime("%d.%m.%Y %H:%M") if isinstance(backup["date"], datetime.datetime) else str(backup["date"])
            self.table.setItem(row, 2, QTableWidgetItem(date_str))
            self.table.setItem(row, 3, QTableWidgetItem(backup["size"]))
            self.table.setItem(row, 4, QTableWidgetItem(", ".join(backup.get("included", ["все"]))))

    def _get_selected_row(self) -> int:
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).checkState() == Qt.Checked:
                return row
        return -1

    def _create_backup(self):
        dialog = BackupConfigDialog(self)
        dialog.set_values(self.server.backup_schedule.config, self.server.backup_schedule)
        if not dialog.exec():
            return

        config = dialog.get_config()
        schedule = self.server.backup_schedule
        schedule_data = dialog.get_schedule_data()
        schedule.enabled = schedule_data["enabled"]
        schedule.interval_minutes = schedule_data["interval_minutes"]
        schedule.keep_last = schedule_data["keep_last"]
        schedule.config = config

        proc = self.process_manager.get_process(self.server.id)
        was_running = proc and proc.state == ProcessState.RUNNING

        if was_running:
            loop = self.process_manager._event_loop
            asyncio.run_coroutine_threadsafe(self.process_manager.stop_server(self.server.id), loop).result(timeout=60)

        if self.server.is_remote:
            result = self.remote_access_service.api_post_for_server(self.server, "/api/action", {"action": "backups.create", "config": config})["backup"]
        else:
            result = self.backup_manager.create_backup(config)
            self.backup_manager.prune_backups(schedule.keep_last)

        self.server.backups.insert(0, {
            "name": result["name"],
            "date": result["date"],
            "size": result["size"],
            "included": result.get("included", []),
        })
        self.server.last_backup = result["date"]
        if not self.server.is_remote:
            ServerManager().update_server(self.server)

        if was_running:
            loop = self.process_manager._event_loop
            asyncio.run_coroutine_threadsafe(self.process_manager.start_server(self.server), loop)

        self._load_backups()
        InfoBar.success("Бэкап создан", f"{result['name']} ({result['size']})", duration=3000, parent=self)

    def _restore_backup(self):
        row = self._get_selected_row()
        if row < 0:
            InfoBar.warning("Выберите бэкап", "Отметьте галочкой нужную резервную копию", duration=2000, parent=self)
            return

        name = self.table.item(row, 1).text()

        msg = QMessageBox(self)
        msg.setWindowTitle("Восстановление бэкапа")
        msg.setText(f"Восстановить бэкап {name}?\n\nТекущее состояние будет перезаписано.")
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg.setDefaultButton(QMessageBox.No)

        if msg.exec() == QMessageBox.Yes:
            proc = self.process_manager.get_process(self.server.id)
            was_running = proc and proc.state == ProcessState.RUNNING
            if was_running:
                loop = self.process_manager._event_loop
                asyncio.run_coroutine_threadsafe(self.process_manager.stop_server(self.server.id), loop).result(timeout=60)
            success = self.remote_access_service.api_post_for_server(self.server, "/api/action", {"action": "backups.restore", "name": name}).get("ok") if self.server.is_remote else self.backup_manager.restore_backup(name)
            if was_running:
                loop = self.process_manager._event_loop
                asyncio.run_coroutine_threadsafe(self.process_manager.start_server(self.server), loop)
            if success:
                InfoBar.success("Восстановлено", f"Бэкап {name} восстановлен", duration=3000, parent=self)
            else:
                InfoBar.error("Ошибка", "Не удалось восстановить бэкап", duration=3000, parent=self)

    def _delete_backup(self):
        row = self._get_selected_row()
        if row < 0:
            InfoBar.warning("Выберите бэкап", "Отметьте галочкой бэкап для удаления", duration=2000, parent=self)
            return

        name = self.table.item(row, 1).text()

        msg = QMessageBox(self)
        msg.setWindowTitle("Удаление бэкапа")
        msg.setText(f"Удалить бэкап {name}?")
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)

        if msg.exec() == QMessageBox.Yes:
            ok = self.remote_access_service.api_post_for_server(self.server, "/api/action", {"action": "backups.delete", "name": name}).get("ok") if self.server.is_remote else self.backup_manager.delete_backup(name)
            if ok:
                self.table.removeRow(row)
                InfoBar.success("Удалено", f"Бэкап {name} удалён", duration=2000, parent=self)


# =============================================================================
# ОСНОВНОЕ ОКНО: ПАНЕЛЬ УПРАВЛЕНИЯ СЕРВЕРОМ
# =============================================================================
class ServerDashboard(QWidget):
    """Окно управления конкретным серверным инстансом."""

    _open_url_signal = Signal(str)

    def __init__(self, server: ServerInstance, process_manager: AsyncProcessManager, parent=None):
        super().__init__(None)
        self.server = server
        self.process_manager = process_manager
        self._shown_error_signatures = set()
        self.remote_access_service = RemoteAccessService()
        self._remote_snapshot_cache = None
        self._remote_snapshot_cache_ts = 0.0
        self.setWindowTitle(f"AdminisTale — {server.name}")
        self.setMinimumSize(1100, 750)
        self.resize(1300, 850)

        self.setWindowFlags(Qt.Window | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self._tab_instances = {}

        self._setup_ui()
        self._apply_dark_theme()
        self._open_url_signal.connect(self._open_auth_url)

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        header = QWidget()
        header.setFixedHeight(56)
        header.setStyleSheet("background-color: #1e1e1e; border-bottom: 1px solid #333;")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 8, 16, 8)
        header_layout.setSpacing(12)

        self.back_btn = PushButton(FIF.RETURN, "К списку")
        self.back_btn.setFixedWidth(110)
        self.back_btn.clicked.connect(self.close)
        header_layout.addWidget(self.back_btn)

        if not self.server.is_remote:
            self.remote_users_btn = PushButton(FIF.PEOPLE, "Пользователи")
            self.remote_users_btn.setFixedWidth(140)
            self.remote_users_btn.clicked.connect(self._manage_remote_users)
            header_layout.addWidget(self.remote_users_btn)

        title = SubtitleLabel(self.server.name)
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #fff; background: transparent;")
        header_layout.addWidget(title)

        header_layout.addStretch()

        if self.server.is_remote:
            remote_badge = CaptionLabel("Внешний инстанс")
            remote_badge.setStyleSheet("color: #7cb7ff; background: transparent;")
            header_layout.addWidget(remote_badge)

        self.status_badge = BodyLabel(f"● {self.server.status.value}")
        self._update_status_badge()
        header_layout.addWidget(self.status_badge)

        main_layout.addWidget(header)

        pivot_container = QWidget()
        pivot_container.setFixedHeight(40)
        pivot_container.setStyleSheet("background-color: #1e1e1e;")
        pivot_layout = QHBoxLayout(pivot_container)
        pivot_layout.setContentsMargins(16, 0, 16, 0)

        self.pivot = Pivot(self)
        self.pivot.setFixedHeight(36)

        self.stacked_widget = QStackedWidget()
        self.home_tab = HomeTab(self.server, self.process_manager)
        self.console_tab = None
        self.mods_tab = None
        self.settings_tab = None
        self.files_tab = None
        self.backups_tab = None
        self._tab_instances[0] = self.home_tab
        self.stacked_widget.addWidget(self.home_tab)
        for _ in range(5):
            self.stacked_widget.addWidget(QWidget())

        tabs = [
            ("home", " Главная", 0, "tab.home", None),
            ("console", " Консоль", 1, "tab.console", "console.command"),
            ("mods", " Модификации", 2, "tab.mods", "mods.install"),
            ("settings", " Настройки", 3, "tab.settings", "settings.edit"),
            ("files", " Файлы", 4, "tab.files", "files.view"),
            ("backups", " Бэкапы", 5, "tab.backups", "backups.create"),
        ]
        for route_key, text, index, permission, fallback_permission in tabs:
            if _is_remote_tab_allowed(self.server, permission, fallback_permission):
                self.pivot.addItem(routeKey=route_key, text=text, onClick=lambda checked=False, idx=index: self._switch_tab(idx))

        self.pivot.setCurrentItem("home")
        pivot_layout.addWidget(self.pivot)
        pivot_layout.addStretch()

        main_layout.addWidget(pivot_container)

        main_layout.addWidget(self.stacked_widget)

        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self._refresh_status)
        self.status_timer.start(3000)

    def _handle_auth_link(self, url: str):
        self._open_url_signal.emit(url)

    def _open_auth_url(self, url: str):
        from PySide6.QtCore import QUrl

        try:
            QDesktopServices.openUrl(QUrl(url))
            InfoBar.info("Авторизация", f"Открыта ссылка: {url}", duration=10000, parent=self)
        except Exception as e:
            InfoBar.error("Ошибка открытия ссылки", str(e), duration=5000, parent=self)

    def _switch_tab(self, index: int):
        self._ensure_tab(index)
        self.stacked_widget.setCurrentIndex(index)

    def _ensure_tab(self, index: int):
        if index in self._tab_instances:
            return self._tab_instances[index]

        if self.server.is_remote and index == 4 and not _has_remote_permission(self.server, "files.view"):
            widget = QWidget()
            layout = QVBoxLayout(widget)
            layout.setContentsMargins(24, 24, 24, 24)
            layout.addWidget(SubtitleLabel("Доступ ограничён"))
            note = BodyLabel("Для внешних пользователей доступ к файловой системе владельца полностью отключён.")
            note.setWordWrap(True)
            layout.addWidget(note)
            layout.addStretch()
            self.files_tab = widget
        elif index == 1:
            widget = ConsoleTab(self.server, self.process_manager)
            self.console_tab = widget
        elif index == 2:
            widget = ModsTab(self.server, self.process_manager)
            self.mods_tab = widget
        elif index == 3:
            widget = SettingsTab(self.server)
            self.settings_tab = widget
        elif index == 4:
            widget = FilesTab(self.server)
            self.files_tab = widget
        else:
            widget = BackupsTab(self.server)
            self.backups_tab = widget

        placeholder = self.stacked_widget.widget(index)
        self.stacked_widget.removeWidget(placeholder)
        placeholder.deleteLater()
        self.stacked_widget.insertWidget(index, widget)
        self._tab_instances[index] = widget
        return widget

    def _apply_dark_theme(self):
        self.setStyleSheet("""
            QWidget { background-color: #121212; }
        """)

    def _update_status_badge(self):
        proc = self.process_manager.get_process(self.server.id)
        if proc:
            colors = {
                ProcessState.RUNNING: "#4caf50",
                ProcessState.STOPPED: "#f44336",
                ProcessState.STARTING: "#ff9800",
                ProcessState.STOPPING: "#ff9800",
                ProcessState.ERROR: "#ff5722",
            }
            color = colors.get(proc.state, "#888")
        else:
            colors = {
                ServerStatus.RUNNING: "#4caf50",
                ServerStatus.STOPPED: "#f44336",
                ServerStatus.STARTING: "#ff9800",
                ServerStatus.ERROR: "#ff5722",
                ServerStatus.BACKUP: "#2196f3",
            }
            color = colors.get(self.server.status, "#888")

        self.status_badge.setText(f'<span style="color:{color}">●</span> {self.server.status.value}')
        self.status_badge.setStyleSheet("font-size: 13px; background: transparent;")

    def _refresh_status(self):
        if self.server.is_remote and self.server.remote_token:
            try:
                import time

                if not self._remote_snapshot_cache or time.time() - self._remote_snapshot_cache_ts >= 5:
                    self._remote_snapshot_cache = self.remote_access_service.api_get_for_server(self.server, "/api/state")
                    self._remote_snapshot_cache_ts = time.time()
                snapshot = self._remote_snapshot_cache
                remote_server = _apply_remote_identity(self.server, ServerInstance.from_dict(snapshot["server"]))
                self.server = remote_server
                ServerManager().update_server(self.server)
                srv = self.server
            except Exception:
                srv = self.server
        else:
            srv = ServerManager().get_server(self.server.id)
        if srv:
            self.server = srv
            self._update_status_badge()
            self.home_tab.refresh()
            if self.console_tab:
                self.console_tab.server = srv
            if self.mods_tab:
                self.mods_tab.server = srv
            if self.settings_tab:
                self.settings_tab.server = srv
            if self.files_tab:
                self.files_tab.server = srv
            if self.backups_tab:
                self.backups_tab.server = srv

    def _manage_remote_users(self):
        port = self.remote_access_service.ensure_server(self.server.id)
        dialog = RemoteUsersDialog(self.server, self.remote_access_service, port, self)
        dialog.exec()
