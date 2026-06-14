"""
Главное окно AdminisTale — список серверных инстансов.
"""
import asyncio
import logging
import re
import webbrowser

from PySide6.QtCore import Qt, QTimer, Signal, QObject, Slot
from PySide6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea, QFrame, QGridLayout, QMessageBox, QDialog

from qfluentwidgets import (
    PrimaryPushButton, PushButton, InfoBar, InfoBarPosition, FluentIcon as FIF, MessageBox
)

from core.server_manager import ServerManager, ServerInstance, ServerStatus
from core.async_installer import installer
from core.instance_transfer import export_instance, import_instance
from core.process_manager import AsyncProcessManager
from core.config import AppConfig
from core.autostart import AppAutostart
from core.remote_access import RemoteAccessService
from ui.server_card import ServerCard
from ui.dialogs import CreateInstanceDialog, ImportDialog, ExportDialog, RemoteConnectDialog
from ui.server_dashboard import ServerDashboard


class AsyncWorker(QObject):
    """Обёртка для запуска async задач из Qt."""
    finished = Signal(bool, str)
    progress = Signal(str)

    def __init__(self, server):
        super().__init__()
        self.server = server

    async def _run(self):
        def progress_cb(msg):
            self.progress.emit(msg)

        result = await installer.install(self.server, progress_callback=progress_cb)
        return result


class MainWindow(QMainWindow):
    """Главное окно приложения AdminisTale."""

    # Сигналы для безопасного обновления UI из asyncio-потока
    _info_bar_info = Signal(str, str, int)
    _info_bar_success = Signal(str, str, int)
    _info_bar_error = Signal(str, str, int)
    _refresh_signal = Signal()
    _open_url = Signal(str)

    def __init__(self, event_loop: asyncio.AbstractEventLoop):
        super().__init__()
        self.event_loop = event_loop
        self.setWindowTitle("AdminisTale — Панель управления Hytale")
        self.setMinimumSize(1280, 720)
        self.resize(1400, 900)
        self.server_manager = ServerManager()
        self.process_manager = AsyncProcessManager()
        self.remote_access_service = RemoteAccessService()
        self._dashboard_windows = {}
        self.app_auto_start = bool(AppConfig.get("app_auto_start", False)) or AppAutostart.is_enabled()
        self.process_manager.set_event_loop(event_loop)
        self._setup_ui()
        self._apply_dark_theme()
        self._apply_app_autostart()
        self.refresh_servers()

        # Подключение сигналов для UI-обновлений из других потоков
        self._info_bar_info.connect(lambda t, m, d: InfoBar.info(t, m, duration=d, parent=self))
        self._info_bar_success.connect(lambda t, m, d: InfoBar.success(t, m, duration=d, parent=self))
        self._info_bar_error.connect(lambda t, m, d: InfoBar.error(t, m, duration=d, parent=self))
        self._refresh_signal.connect(self.refresh_servers)
        self._open_url.connect(webbrowser.open)
        self.process_manager.add_auth_link_listener(self._open_url.emit)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_servers)
        self.timer.start(5000)

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(20, 20, 20, 20)
        main_layout.setSpacing(16)

        header = QHBoxLayout()

        title = QLabel("AdminisTale")
        title.setStyleSheet("font-size: 28px; font-weight: bold; color: #ffffff; background: transparent;")
        header.addWidget(title)

        header.addStretch()

        self.create_btn = PrimaryPushButton(FIF.ADD, "Создать инстанс")
        self.create_btn.setFixedWidth(180)
        self.create_btn.clicked.connect(lambda *_: self._on_create())
        header.addWidget(self.create_btn)

        self.import_btn = PushButton(FIF.DOWNLOAD, "Импорт")
        self.import_btn.setFixedWidth(120)
        self.import_btn.clicked.connect(lambda *_: self._on_import())
        header.addWidget(self.import_btn)

        self.export_btn = PushButton(FIF.SHARE, "Экспорт")
        self.export_btn.setFixedWidth(120)
        self.export_btn.clicked.connect(lambda *_: self._on_export())
        header.addWidget(self.export_btn)

        self.refresh_btn = PushButton(FIF.SYNC, "Обновить")
        self.refresh_btn.setFixedWidth(120)
        self.refresh_btn.clicked.connect(lambda *_: self.refresh_servers())
        header.addWidget(self.refresh_btn)

        self.remote_btn = PushButton(FIF.GLOBE, "Подключиться")
        self.remote_btn.setFixedWidth(140)
        self.remote_btn.clicked.connect(lambda *_: self._on_remote_connect())
        header.addWidget(self.remote_btn)

        self.autostart_btn = PushButton(FIF.POWER_BUTTON, "Автозапуск: выкл")
        self.autostart_btn.setFixedWidth(160)
        self.autostart_btn.clicked.connect(lambda *_: self._toggle_app_autostart())
        self.autostart_btn.setEnabled(AppAutostart.is_supported())
        header.addWidget(self.autostart_btn)

        self.help_btn = PushButton("?")
        self.help_btn.setFixedWidth(40)
        self.help_btn.clicked.connect(lambda *_: self._show_help())
        header.addWidget(self.help_btn)

        main_layout.addLayout(header)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color: #333; background: transparent;")
        main_layout.addWidget(line)

        section_layout = QHBoxLayout()
        self.servers_label = QLabel("Серверные инстансы")
        self.servers_label.setStyleSheet("font-size: 16px; color: #ccc; background: transparent;")
        section_layout.addWidget(self.servers_label)
        section_layout.addStretch()
        main_layout.addLayout(section_layout)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        self.cards_container = QWidget()
        self.cards_layout = QGridLayout(self.cards_container)
        self.cards_layout.setSpacing(20)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)

        scroll.setWidget(self.cards_container)
        main_layout.addWidget(scroll)

        self.statusBar().showMessage("Готово  •  Автообновление: 5с")
        self._update_autostart_button()

    def _apply_app_autostart(self):
        if self.app_auto_start:
            try:
                AppAutostart.enable()
            except Exception as e:
                logging.warning(f"Не удалось включить автозапуск приложения: {e}")

    def _update_autostart_button(self):
        if not AppAutostart.is_supported():
            self.autostart_btn.setText("Автозапуск: n/a")
            return
        state = "вкл" if self.app_auto_start else "выкл"
        self.autostart_btn.setText(f"Автозапуск: {state}")

    def _toggle_app_autostart(self):
        self.app_auto_start = not self.app_auto_start
        AppConfig.set("app_auto_start", self.app_auto_start)
        try:
            if self.app_auto_start:
                AppAutostart.enable()
            else:
                AppAutostart.disable()
            self._update_autostart_button()
            InfoBar.success("Автозапуск", "Настройка автозапуска приложения обновлена", duration=2500, parent=self)
        except Exception as e:
            self.app_auto_start = not self.app_auto_start
            AppConfig.set("app_auto_start", self.app_auto_start)
            self._update_autostart_button()
            InfoBar.error("Ошибка автозапуска", str(e), duration=4000, parent=self)

    def _show_help(self):
        text = (
            "1. Создайте инстанс кнопкой 'Создать инстанс' и дождитесь установки.\n"
            "2. Откройте 'Управление' у карточки сервера, чтобы запускать, останавливать и смотреть консоль.\n"
            "3. Во вкладке 'Настройки' можно включить автостарт конкретного инстанса при запуске Windows.\n"
            "4. Во вкладке 'Бэкапы' создаются резервные копии и настраивается расписание автобэкапов.\n"
            "5. Перед изменением файлов, модов и мира лучше остановить сервер или дождаться автоматического бэкапа.\n"
            "6. Если закрыть окно, программа сворачивается в трей и продолжает работать в фоне. Выход выполняйте только через трей."
        )
        QMessageBox.information(self, "Краткая справка", text)

    def _apply_dark_theme(self):
        self.setStyleSheet("""
            QMainWindow { background-color: #1a1a1a; }
            QLabel { color: #e0e0e0; background: transparent; }
            QScrollArea { background-color: transparent; border: none; }
            QWidget { background-color: #1a1a1a; }
        """)

    def refresh_servers(self):
        """Обновление сетки карточек серверов."""
        discovered = self.server_manager.discover_servers()
        for srv in discovered:
            InfoBar.info("Новый инстанс", f"Подхвачен каталог: {srv.name}", duration=3000, parent=self)

        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        servers = self.server_manager.get_all_servers()

        if not servers:
            empty_label = QLabel("Нет серверных инстансов. Нажмите «Создать инстанс» для начала.")
            empty_label.setStyleSheet("font-size: 14px; color: #666; padding: 40px; background: transparent;")
            self.cards_layout.addWidget(empty_label, 0, 0)
        else:
            row, col = 0, 0
            max_cols = 3
            for srv in servers:
                card = ServerCard(srv, self.process_manager)
                card.doubleClicked.connect(self._open_dashboard)
                card.deleteRequested.connect(self._on_delete)
                self.cards_layout.addWidget(card, row, col)
                col += 1
                if col >= max_cols:
                    col = 0
                    row += 1

        self.servers_label.setText(f"Серверные инстансы ({len(servers)})")

    def _open_dashboard(self, server_id: str):
        """Открытие панели управления сервером."""
        logging.info("Открытие панели управления сервером.")
        existing = self._dashboard_windows.get(server_id)
        if existing is not None:
            existing.show()
            existing.raise_()
            existing.activateWindow()
            return

        server = self.server_manager.get_server(server_id)
        if not server:
            logging.error("Ошибка открытия панели сервера")
            return

        InfoBar.success(
            "Доступ разрешён",
            f"Открыта панель управления: {server.name}",
            duration=2000,
            parent=self
        )
        dashboard = ServerDashboard(server, self.process_manager, self)
        if not server.is_remote:
            try:
                self.remote_access_service.ensure_server(server.id)
            except Exception as e:
                logging.warning(f"Не удалось поднять сервис удалённого доступа для {server.id}: {e}")
        dashboard.destroyed.connect(lambda *_: self._dashboard_windows.pop(server_id, None))
        self._dashboard_windows[server_id] = dashboard
        dashboard.show()

    def _on_create(self):
        dialog = CreateInstanceDialog(self)
        if not dialog.exec():
            return

        data = dialog.get_data()

        suggested_port = self.server_manager.check_port_conflict(data["port"])
        logging.info("Проверен конфликт порта создаваемого инстанса.")
        if suggested_port is not None:
            msg = MessageBox(
                "Конфликт порта",
                f"Порт {data['port']} уже используется сервером.\n\n"
                f"Предлагаемый свободный порт: {suggested_port}\n\n"
                f"Использовать предложенный порт?",
                self
            )
            if msg.exec():
                data["port"] = suggested_port
            else:
                return

        srv = self.server_manager.create_server(
            slug=data["slug"],
            name=data["name"],
            port=data["port"],
            version="0.5.4",
            game_mode=data["mode"],
            motd=data["motd"]
        )

        logging.info("Обновление списка серверов.")
        self.refresh_servers()

        InfoBar.info("Создание инстанса", f"Запуск загрузчика для {srv.name}...", duration=5000, parent=self)

        asyncio.run_coroutine_threadsafe(self._run_installation(srv), self.event_loop)

    async def _run_installation(self, srv):
        """Асинхронная установка инстанса."""
        logger = logging.getLogger(__name__)
        logger.info(f"Запуск установки инстанса {srv.id}")

        def progress_cb(msg):
            logger.info(f"[install progress] {msg}")
            self._info_bar_info.emit("Прогресс", msg, 3000)

        def auth_link_cb(url):
            logger.info(f"Открытие ссылки авторизации: {url}")
            self._open_url.emit(url)
            self._info_bar_info.emit("Авторизация", f"Ссылка открыта в браузере: {url}", 15000)

        try:
            result = await installer.install(srv, progress_callback=progress_cb, auth_link_callback=auth_link_cb)
            logger.info(f"Результат установки {srv.id}: {result}")

            if result:
                self._info_bar_success.emit("Успех", f"Инстанс {srv.name} успешно создан", 3000)
            else:
                self._info_bar_error.emit("Ошибка", "Не удалось создать инстанс", 5000)
                srv.status = ServerStatus.ERROR
                self.server_manager.update_server(srv)
        except Exception as e:
            logger.exception(f"Критическая ошибка при установке инстанса {srv.id}: {e}")
            self._info_bar_error.emit("Критическая ошибка", str(e), 5000)
            srv.status = ServerStatus.ERROR
            self.server_manager.update_server(srv)

        self._refresh_signal.emit()

    async def _run_export(self, server, options):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, export_instance, server, options)

    def _on_delete(self, server_id: str):
        """Удаление серверного инстанса."""
        srv = self.server_manager.get_server(server_id)
        if not srv:
            return

        msg = MessageBox(
            "Удаление инстанса",
            f"Вы уверены, что хотите удалить инстанс «{srv.name}»?\n\n"
            f"Все файлы сервера, моды и конфигурации будут безвозвратно удалены.",
            self
        )
        msg.yesButton.setText("Удалить")
        msg.cancelButton.setText("Отмена")

        if msg.exec():
            if self.server_manager.delete_server(server_id):
                InfoBar.success("Удалено", f"Инстанс {srv.name} и все его файлы удалены", duration=3000, parent=self)
                self.refresh_servers()
            else:
                InfoBar.error("Ошибка", "Не удалось удалить инстанс", duration=3000, parent=self)

    def _on_import(self):
        dialog = ImportDialog(self)
        if not dialog.exec():
            return

        path = dialog.get_data().get("path")
        if not path:
            InfoBar.error("Ошибка", "Файл для импорта не выбран", duration=3000, parent=self)
            return

        try:
            server = import_instance(path)
            self.refresh_servers()
            if server:
                InfoBar.success("Импорт завершён", f"Создан инстанс: {server.name}", duration=4000, parent=self)
            else:
                InfoBar.info("Импорт завершён", "Файлы импортированы", duration=4000, parent=self)
        except Exception as e:
            InfoBar.error("Ошибка импорта", str(e), duration=5000, parent=self)

    def _on_export(self):
        servers = self.server_manager.get_all_servers()
        if not servers:
            InfoBar.info("Экспорт", "Нет инстансов для экспорта", duration=3000, parent=self)
            return

        dialog = ExportDialog(servers, self)
        if not dialog.exec():
            return

        try:
            data = dialog.get_data()
            InfoBar.info("Экспорт", "Подготовка экспорта...", duration=3000, parent=self)

            async def do_export():
                try:
                    created = await self._run_export(data["server"], data)
                    self._info_bar_success.emit("Экспорт завершён", "\n".join(created), 5000)
                except Exception as e:
                    self._info_bar_error.emit("Ошибка экспорта", str(e), 5000)

            asyncio.run_coroutine_threadsafe(do_export(), self.event_loop)
        except Exception as e:
            InfoBar.error("Ошибка экспорта", str(e), duration=5000, parent=self)

    def _on_remote_connect(self):
        dialog = RemoteConnectDialog(self)
        if not dialog.exec():
            return

        data = dialog.get_data()
        try:
            result = self.remote_access_service.authenticate(
                data["host"], data["port"], data["server_id"], data["username"], data["password"]
            )
            remote = self.server_manager.create_remote_server(
                name=result["server_name"],
                host=data["host"],
                port=data["port"],
                owner_instance_id=data["server_id"],
                username=data["username"],
                password=data["password"],
                token=result.get("token"),
                permissions=result.get("permissions", []),
            )
            self.refresh_servers()
            InfoBar.success("Подключено", f"Добавлен внешний инстанс: {remote.name}", duration=4000, parent=self)
        except Exception as e:
            InfoBar.error("Ошибка подключения", str(e), duration=5000, parent=self)

    def closeEvent(self, event):
        """При закрытии окна сворачиваем в трей вместо завершения."""
        if getattr(self, '_force_exit', False):
            self.timer.stop()
            event.accept()
        else:
            self.hide()
            event.ignore()

    @Slot()
    def tray_exit(self):
        """Полный выход из приложения (через трей)."""
        from PySide6.QtWidgets import QApplication

        self._force_exit = True
        self.timer.stop()
        try:
            future = asyncio.run_coroutine_threadsafe(self.process_manager.stop_all(), self.event_loop)
            future.result(timeout=45)
        except Exception as e:
            logging.warning(f"Ошибка при остановке серверов перед выходом: {e}")
        self.hide()
        self.close()
        QApplication.instance().quit()
