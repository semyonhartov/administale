"""
Виджет карточки серверного инстанса для отображения в сетке.
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QWidget, QLabel
from qfluentwidgets import CardWidget, PrimaryPushButton, PushButton, ToolButton, FluentIcon as FIF
from core.server_manager import ServerInstance, ServerStatus
from core.process_manager import AsyncProcessManager, ProcessState


class ServerCard(CardWidget):
    """Карточка сервера в стиле Fluent Design."""

    clicked = Signal(str)
    doubleClicked = Signal(str)
    deleteRequested = Signal(str)

    def __init__(self, server: ServerInstance, process_manager: AsyncProcessManager, parent=None):
        super().__init__(parent)
        self.server = server
        self.process_manager = process_manager
        self.setFixedSize(320, 220)
        self._setup_ui()
        self._update_style()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        top_layout = QHBoxLayout()

        self.status_label = QLabel(self._status_text())
        self.status_label.setStyleSheet("font-weight: bold; font-size: 13px; background: transparent;")
        top_layout.addWidget(self.status_label)

        top_layout.addStretch()

        self.online_label = QLabel(f"{self.server.online_players}/{self.server.max_players}")
        self.online_label.setStyleSheet("color: #888; font-size: 12px; background: transparent;")
        top_layout.addWidget(self.online_label)

        layout.addLayout(top_layout)

        self.name_label = QLabel(self.server.name)
        self.name_label.setStyleSheet("font-size: 18px; font-weight: bold; color: #e0e0e0; background: transparent;")
        self.name_label.setWordWrap(True)
        layout.addWidget(self.name_label)

        if self.server.is_remote:
            remote_label = QLabel("Внешний инстанс")
            remote_label.setStyleSheet("color: #7cb7ff; font-size: 12px; background: transparent;")
            layout.addWidget(remote_label)

        info_text = f"Порт: {self.server.port}  •  Версия: {self.server.version}"
        self.info_label = QLabel(info_text)
        self.info_label.setStyleSheet("color: #999; font-size: 12px; background: transparent;")
        layout.addWidget(self.info_label)

        usage = self.process_manager.get_resource_usage(self.server.id)
        self.resources_label = QLabel(self._resources_text(usage))
        self.resources_label.setStyleSheet("color: #777; font-size: 11px; background: transparent;")
        layout.addWidget(self.resources_label)

        restart_text = self.server.last_restart.strftime("%d.%m %H:%M") if self.server.last_restart else "Нет данных"
        backup_text = self.server.last_backup.strftime("%d.%m %H:%M") if self.server.last_backup else "Нет данных"
        meta_text = f"Рестарт: {restart_text}  |  Бэкап: {backup_text}"
        self.meta_label = QLabel(meta_text)
        self.meta_label.setStyleSheet("color: #777; font-size: 11px; background: transparent;")
        self.meta_label.setWordWrap(True)
        layout.addWidget(self.meta_label)

        layout.addStretch()

        btn_layout = QHBoxLayout()

        self.toggle_btn = PrimaryPushButton("Запустить" if self.server.status != ServerStatus.RUNNING else "Остановить")
        self.toggle_btn.setFixedSize(100, 32)
        self.toggle_btn.clicked.connect(self._on_toggle)
        btn_layout.addWidget(self.toggle_btn)

        btn_layout.addStretch()

        self.edit_btn = PushButton("Управление")
        self.edit_btn.setFixedSize(100, 32)
        self.edit_btn.clicked.connect(lambda: self.doubleClicked.emit(self.server.id))
        btn_layout.addWidget(self.edit_btn)

        self.delete_btn = ToolButton(FIF.DELETE)
        self.delete_btn.setFixedSize(32, 32)
        self.delete_btn.setToolTip("Удалить инстанс")
        self.delete_btn.clicked.connect(lambda: self.deleteRequested.emit(self.server.id))
        btn_layout.addWidget(self.delete_btn)

        layout.addLayout(btn_layout)

    def _status_text(self) -> str:
        proc = self.process_manager.get_process(self.server.id)
        if proc:
            state_map = {
                ProcessState.RUNNING: ("Запущен", "#4caf50"),
                ProcessState.STOPPED: ("Остановлен", "#f44336"),
                ProcessState.STARTING: ("Запускается", "#ff9800"),
                ProcessState.STOPPING: ("Останавливается", "#ff9800"),
                ProcessState.ERROR: ("Ошибка", "#ff5722"),
            }
            text, color = state_map.get(proc.state, ("Неизвестно", "#888"))
        else:
            status_map = {
                ServerStatus.RUNNING: ("Запущен", "#4caf50"),
                ServerStatus.STOPPED: ("Остановлен", "#f44336"),
                ServerStatus.STARTING: ("Запускается", "#ff9800"),
                ServerStatus.ERROR: ("Ошибка", "#ff5722"),
                ServerStatus.BACKUP: ("Бэкап", "#2196f3"),
            }
            text, color = status_map.get(self.server.status, ("Неизвестно", "#888"))
        return f'<span style="color:{color}">●</span> {text}'

    def _update_style(self):
        proc = self.process_manager.get_process(self.server.id)
        is_running = proc and proc.state == ProcessState.RUNNING

        if is_running:
            self.setStyleSheet("ServerCard { background-color: #1e2a1e; border: 1px solid #2e4a2e; border-radius: 8px; }")
        elif self.server.status == ServerStatus.ERROR:
            self.setStyleSheet("ServerCard { background-color: #2a1e1e; border: 1px solid #4a2e2e; border-radius: 8px; }")
        else:
            self.setStyleSheet("ServerCard { background-color: #252525; border: 1px solid #333; border-radius: 8px; }")

    def _resources_text(self, usage: dict) -> str:
        return f"CPU: {usage.get('cpu_percent', 0.0):.1f}%  •  RAM: {usage.get('memory_mb', 0.0):.1f} MB"

    def _on_toggle(self):
        import asyncio
        proc = self.process_manager.get_process(self.server.id)
        loop = self.process_manager._event_loop

        if proc and proc.state == ProcessState.RUNNING:
            asyncio.run_coroutine_threadsafe(self.process_manager.stop_server(self.server.id), loop)
        else:
            auth_cb = None
            parent_window = self.window()
            if hasattr(parent_window, "_handle_auth_link"):
                auth_cb = parent_window._handle_auth_link
            asyncio.run_coroutine_threadsafe(self.process_manager.start_server(self.server, auth_link_callback=auth_cb), loop)

        self.refresh()

    def refresh(self):
        from core.server_manager import ServerManager
        srv = ServerManager().get_server(self.server.id)
        if srv:
            self.server = srv
        self.status_label.setText(self._status_text())
        self.online_label.setText(f"{self.server.online_players}/{self.server.max_players}")
        self.resources_label.setText(self._resources_text(self.process_manager.get_resource_usage(self.server.id)))

        proc = self.process_manager.get_process(self.server.id)
        is_running = proc and proc.state == ProcessState.RUNNING
        self.toggle_btn.setText("Остановить" if is_running else "Запустить")
        self._update_style()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.doubleClicked.emit(self.server.id)
        super().mouseDoubleClickEvent(event)
