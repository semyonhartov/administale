"""
Диалоговые окна AdminisTale.
"""
import asyncio
import logging
import webbrowser
from datetime import datetime
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QFileDialog, QScrollArea, QWidget,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QHeaderView, QMessageBox, QMenu, QFormLayout
)
from qfluentwidgets import (
    PrimaryPushButton, PushButton, LineEdit, ComboBox, SpinBox,
    BodyLabel, PasswordLineEdit, RadioButton, CheckBox, FluentIcon as FIF,
    SearchLineEdit, SubtitleLabel, CaptionLabel, CardWidget
)

from core.mod_manager import ModManager, CurseForgeAPI
from core.remote_access import REMOTE_PERMISSION_GROUPS


class AuthDialog(QDialog):
    """Диалог авторизации при подключении к инстансу."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Авторизация")
        self.setFixedSize(420, 300)
        self._setup_content()

    def _setup_content(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)

        title = BodyLabel("Введите учётные данные для доступа к инстансу")
        title.setStyleSheet("font-size: 14px; color: #ccc; background: transparent;")
        layout.addWidget(title)

        lbl1 = BodyLabel("Логин:")
        lbl1.setStyleSheet("background: transparent;")
        layout.addWidget(lbl1)
        self.login_edit = LineEdit()
        self.login_edit.setPlaceholderText("admin")
        layout.addWidget(self.login_edit)

        lbl2 = BodyLabel("Пароль:")
        lbl2.setStyleSheet("background: transparent;")
        layout.addWidget(lbl2)
        self.pass_edit = PasswordLineEdit()
        self.pass_edit.setPlaceholderText("••••••••")
        layout.addWidget(self.pass_edit)

        role_layout = QHBoxLayout()
        self.admin_radio = RadioButton("Администратор")
        self.admin_radio.setChecked(True)
        self.mod_radio = RadioButton("Модератор")
        role_layout.addWidget(self.admin_radio)
        role_layout.addWidget(self.mod_radio)
        role_layout.addStretch()
        lbl3 = BodyLabel("Роль:")
        lbl3.setStyleSheet("background: transparent;")
        layout.addWidget(lbl3)
        layout.addLayout(role_layout)

        layout.addStretch()

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.cancel_btn = PushButton("Отмена")
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)

        self.ok_btn = PrimaryPushButton("Войти")
        self.ok_btn.clicked.connect(self.accept)
        btn_layout.addWidget(self.ok_btn)

        layout.addLayout(btn_layout)

    def get_data(self):
        return {
            "login": self.login_edit.text(),
            "password": self.pass_edit.text(),
            "role": "admin" if self.admin_radio.isChecked() else "moderator"
        }


class CreateInstanceDialog(QDialog):
    """Диалог создания нового серверного инстанса Hytale."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Новый инстанс Hytale")
        self.setFixedSize(500, 516)
        self._setup_content()

    def _setup_content(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(24, 24, 24, 24)

        title = BodyLabel("Создание нового серверного инстанса с нуля")
        title.setStyleSheet("font-size: 14px; color: #ccc; background: transparent;")
        layout.addWidget(title)

        lbl1 = BodyLabel("Короткое имя (для папки, без пробелов) *:")
        lbl1.setStyleSheet("background: transparent;")
        layout.addWidget(lbl1)
        self.slug_edit = LineEdit()
        self.slug_edit.setPlaceholderText("my-server")
        layout.addWidget(self.slug_edit)

        lbl2 = BodyLabel("Отображаемое название *:")
        lbl2.setStyleSheet("background: transparent;")
        layout.addWidget(lbl2)
        self.name_edit = LineEdit()
        self.name_edit.setPlaceholderText("My Hytale Server")
        layout.addWidget(self.name_edit)

        lbl3 = BodyLabel("Сообщение дня (MOTD):")
        lbl3.setStyleSheet("background: transparent;")
        layout.addWidget(lbl3)
        self.motd_edit = LineEdit()
        self.motd_edit.setPlaceholderText("Добро пожаловать на сервер!")
        layout.addWidget(self.motd_edit)

        lbl4 = BodyLabel("Порт сервера:")
        lbl4.setStyleSheet("background: transparent;")
        layout.addWidget(lbl4)
        self.port_spin = SpinBox()
        self.port_spin.setRange(1024, 65535)
        self.port_spin.setValue(25565)
        layout.addWidget(self.port_spin)

        lbl5 = BodyLabel("Режим игры по умолчанию:")
        lbl5.setStyleSheet("background: transparent;")
        layout.addWidget(lbl5)
        self.mode_combo = ComboBox()
        self.mode_combo.addItems(["Adventure", "Creative", "Survival"])
        layout.addWidget(self.mode_combo)

        layout.addStretch()

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.cancel_btn = PushButton("Отмена")
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)

        self.ok_btn = PrimaryPushButton("Создать")
        self.ok_btn.clicked.connect(self.accept)
        btn_layout.addWidget(self.ok_btn)

        layout.addLayout(btn_layout)

    def get_data(self):
        slug = self.slug_edit.text().strip().lower().replace(" ", "-") or "server"
        return {
            "slug": slug,
            "name": self.name_edit.text().strip() or "New Server",
            "motd": self.motd_edit.text().strip(),
            "port": self.port_spin.value(),
            "mode": self.mode_combo.currentText()
        }


class ImportDialog(QDialog):
    """Диалог импорта инстанса из файла-шаблона."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Импорт инстанса")
        self.setFixedSize(520, 260)
        self._setup_content()

    def _setup_content(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(24, 24, 24, 24)

        title = BodyLabel("Выберите файл-шаблон (.zip, .json) для импорта")
        title.setStyleSheet("font-size: 14px; color: #ccc; background: transparent;")
        layout.addWidget(title)

        self.file_label = BodyLabel("Файл не выбран")
        self.file_label.setStyleSheet("color: #888; background: transparent;")
        layout.addWidget(self.file_label)

        browse_btn = PushButton(FIF.FOLDER_ADD, "Обзор...")
        browse_btn.clicked.connect(self._browse)
        layout.addWidget(browse_btn)

        self.path = ""

        info = BodyLabel("Поддерживаемые форматы: .zip (полный экспорт), .json (манифест)")
        info.setStyleSheet("color: #666; font-size: 11px; background: transparent;")
        info.setWordWrap(True)
        layout.addWidget(info)

        layout.addStretch()

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.cancel_btn = PushButton("Отмена")
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)

        self.ok_btn = PrimaryPushButton("Импортировать")
        self.ok_btn.clicked.connect(self.accept)
        btn_layout.addWidget(self.ok_btn)

        layout.addLayout(btn_layout)

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Выберите файл шаблона", "",
            "Шаблоны (*.zip *.json);;Все файлы (*.*)"
        )
        if path:
            self.path = path
            self.file_label.setText(f"Выбран: {path}")
            self.file_label.setStyleSheet("color: #4caf50; background: transparent;")

    def get_data(self):
        return {"path": self.path}


class ExportDialog(QDialog):
    """Диалог выбора инстанса и состава экспорта."""

    def __init__(self, servers, parent=None):
        super().__init__(parent)
        self.servers = servers
        self.setWindowTitle("Экспорт инстанса")
        self.setFixedSize(460, 420)
        self._setup_content()

    def _setup_content(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(24, 24, 24, 24)

        title = BodyLabel("Выберите инстанс и данные для экспорта")
        title.setStyleSheet("font-size: 14px; color: #ccc; background: transparent;")
        layout.addWidget(title)

        self.server_combo = ComboBox()
        for server in self.servers:
            self.server_combo.addItem(server.name)
        layout.addWidget(self.server_combo)

        self.include_all = CheckBox("Весь сервер")
        self.include_all.setChecked(True)
        self.include_all.toggled.connect(self._toggle_partial_options)
        layout.addWidget(self.include_all)

        self.include_config = CheckBox("Конфигурация и текстовые файлы")
        self.include_world = CheckBox("Мир (universe/)")
        self.include_mods = CheckBox("Моды")
        self.include_logs = CheckBox("Логи")
        self.include_other = CheckBox("Прочие бинарные файлы")
        self.as_archive = CheckBox("Упаковать в ZIP")
        self.as_archive.setChecked(True)

        for widget in [self.include_config, self.include_world, self.include_mods, self.include_logs, self.include_other, self.as_archive]:
            layout.addWidget(widget)

        self._toggle_partial_options(True)
        layout.addStretch()

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        cancel_btn = PushButton("Отмена")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)
        ok_btn = PrimaryPushButton("Экспортировать")
        ok_btn.clicked.connect(self.accept)
        btn_layout.addWidget(ok_btn)
        layout.addLayout(btn_layout)

    def _toggle_partial_options(self, checked: bool):
        for widget in [self.include_config, self.include_world, self.include_mods, self.include_logs, self.include_other]:
            widget.setEnabled(not checked)

    def get_data(self):
        return {
            "server": self.servers[self.server_combo.currentIndex()],
            "include_all": self.include_all.isChecked(),
            "include_config": self.include_config.isChecked(),
            "include_world": self.include_world.isChecked(),
            "include_mods": self.include_mods.isChecked(),
            "include_logs": self.include_logs.isChecked(),
            "include_other": self.include_other.isChecked(),
            "as_archive": self.as_archive.isChecked(),
        }


class RemoteConnectDialog(QDialog):
    """Подключение к внешнему инстансу владельца."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Подключение к внешнему инстансу")
        self.setFixedSize(460, 500)
        self._setup_content()

    def _setup_content(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(24, 24, 24, 24)

        title = BodyLabel("Введите параметры подключения владельца инстанса")
        title.setStyleSheet("font-size: 14px; color: #ccc; background: transparent;")
        layout.addWidget(title)

        fields = [
            ("Хост:", "127.0.0.1"),
            ("Порт:", "26565"),
            ("ID инстанса:", "srv-1234"),
            ("Логин:", "moderator"),
        ]
        self._edits = {}
        for label, placeholder in fields:
            layout.addWidget(BodyLabel(label))
            edit = LineEdit()
            edit.setPlaceholderText(placeholder)
            layout.addWidget(edit)
            self._edits[label] = edit

        layout.addWidget(BodyLabel("Пароль:"))
        self.password_edit = PasswordLineEdit()
        layout.addWidget(self.password_edit)

        layout.addStretch()

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = PushButton("Отмена")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        ok = PrimaryPushButton("Подключиться")
        ok.clicked.connect(self.accept)
        buttons.addWidget(ok)
        layout.addLayout(buttons)

    def get_data(self):
        return {
            "host": self._edits["Хост:"].text().strip() or "127.0.0.1",
            "port": int(self._edits["Порт:"].text().strip() or "26565"),
            "server_id": self._edits["ID инстанса:"].text().strip(),
            "username": self._edits["Логин:"].text().strip(),
            "password": self.password_edit.text(),
        }


class RemoteUsersDialog(QDialog):
    """Управление внешними пользователями инстанса."""

    def __init__(self, server, remote_service, remote_port: int, parent=None):
        super().__init__(parent)
        self.server = server
        self.remote_service = remote_service
        self.remote_port = remote_port
        self.setWindowTitle("Внешние пользователи")
        self.resize(700, 560)
        self._setup_content()
        self._load_users()

    def _setup_content(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        info = BodyLabel(
            f"Подключение владельца: используйте IP этого ПК и порт {self.remote_port}. ID инстанса: {self.server.id}"
        )
        info.setWordWrap(True)
        info.setStyleSheet("background: transparent;")
        layout.addWidget(info)

        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Логин", "Права", "Создан"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        layout.addWidget(self.table)

        form = CardWidget()
        form.setStyleSheet("background: transparent;")
        form_layout = QFormLayout(form)
        self.username_edit = LineEdit()
        self.password_edit = PasswordLineEdit()
        form_layout.addRow("Логин:", self.username_edit)
        form_layout.addRow("Пароль:", self.password_edit)

        self.permission_checks = {}
        for permission, label in REMOTE_PERMISSION_GROUPS.items():
            check = CheckBox(label)
            check.setStyleSheet("background: transparent;")
            self.permission_checks[permission] = check
            form_layout.addRow(check)
        layout.addWidget(form)

        buttons = QHBoxLayout()
        self.add_btn = PrimaryPushButton("Добавить пользователя")
        self.add_btn.clicked.connect(self._add_user)
        buttons.addWidget(self.add_btn)
        self.delete_btn = PushButton("Удалить выбранного")
        self.delete_btn.clicked.connect(self._delete_selected)
        buttons.addWidget(self.delete_btn)
        buttons.addStretch()
        layout.addLayout(buttons)

    def _load_users(self):
        users = self.remote_service.list_users(self.server.id)
        self.table.setRowCount(0)
        for user in users:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(user["username"]))
            self.table.setItem(row, 1, QTableWidgetItem(", ".join(REMOTE_PERMISSION_GROUPS.get(p, p) for p in user["permissions"])))
            self.table.setItem(row, 2, QTableWidgetItem(str(user["created_at"])))

    def _add_user(self):
        username = self.username_edit.text().strip()
        password = self.password_edit.text()
        permissions = [key for key, check in self.permission_checks.items() if check.isChecked()]
        if not username or not password or not permissions:
            QMessageBox.warning(self, "Недостаточно данных", "Заполните логин, пароль и хотя бы одно право.")
            return
        self.remote_service.add_user(self.server.id, username, password, permissions)
        self.username_edit.clear()
        self.password_edit.clear()
        for check in self.permission_checks.values():
            check.setChecked(False)
        self._load_users()

    def _delete_selected(self):
        row = self.table.currentRow()
        if row < 0:
            return
        username = self.table.item(row, 0).text()
        self.remote_service.delete_user(self.server.id, username)
        self._load_users()


class BackupConfigDialog(QDialog):
    """Диалог конфигурации бэкапа."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройки бэкапа")
        self.setFixedSize(440, 460)
        self._setup_content()

    def set_values(self, config, schedule=None):
        self.include_mods.setChecked(config.include_mods)
        self.include_config.setChecked(config.include_config)
        self.include_world.setChecked(config.include_world)
        self.include_logs.setChecked(config.include_logs)
        self.include_other.setChecked(config.include_other)
        self.include_assets_zip.setChecked(getattr(config, "include_assets_zip", False))
        if schedule is not None:
            self.schedule_enabled.setChecked(schedule.enabled)
            self.interval_spin.setValue(schedule.interval_minutes)
            self.keep_last_spin.setValue(schedule.keep_last)

    def _setup_content(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)

        title = BodyLabel("Выберите, что включить в бэкап:")
        title.setStyleSheet("font-size: 14px; color: #ccc; background: transparent;")
        layout.addWidget(title)

        self.include_mods = CheckBox("Модификации (mods/)")
        self.include_mods.setChecked(True)
        layout.addWidget(self.include_mods)

        self.include_config = CheckBox("Конфигурация (config.json, universe/)")
        self.include_config.setChecked(True)
        layout.addWidget(self.include_config)

        self.include_world = CheckBox("Мир (worlds/)")
        self.include_world.setChecked(True)
        layout.addWidget(self.include_world)

        self.include_logs = CheckBox("Логи (logs/)")
        self.include_logs.setChecked(False)
        layout.addWidget(self.include_logs)

        self.include_other = CheckBox("Прочие файлы")
        self.include_other.setChecked(True)
        layout.addWidget(self.include_other)

        self.include_assets_zip = CheckBox("Assets.zip (крупный файл, обычно не нужен)")
        self.include_assets_zip.setChecked(False)
        layout.addWidget(self.include_assets_zip)

        schedule_card = CardWidget()
        schedule_layout = QFormLayout(schedule_card)
        schedule_layout.setContentsMargins(16, 12, 16, 12)
        schedule_layout.setSpacing(10)

        self.schedule_enabled = CheckBox("Включить автобэкап по расписанию")
        schedule_layout.addRow(self.schedule_enabled)

        self.interval_spin = SpinBox()
        self.interval_spin.setRange(5, 10080)
        self.interval_spin.setSuffix(" мин")
        self.interval_spin.setValue(60)
        schedule_layout.addRow("Интервал:", self.interval_spin)

        self.keep_last_spin = SpinBox()
        self.keep_last_spin.setRange(1, 1000)
        self.keep_last_spin.setValue(10)
        schedule_layout.addRow("Хранить копий:", self.keep_last_spin)

        layout.addWidget(schedule_card)

        layout.addStretch()

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.cancel_btn = PushButton("Отмена")
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)

        self.ok_btn = PrimaryPushButton("Создать бэкап")
        self.ok_btn.clicked.connect(self.accept)
        btn_layout.addWidget(self.ok_btn)

        layout.addLayout(btn_layout)

    def get_config(self):
        from core.server_manager import BackupConfig
        return BackupConfig(
            include_mods=self.include_mods.isChecked(),
            include_config=self.include_config.isChecked(),
            include_world=self.include_world.isChecked(),
            include_logs=self.include_logs.isChecked(),
            include_other=self.include_other.isChecked(),
            include_assets_zip=self.include_assets_zip.isChecked(),
        )

    def get_schedule_data(self):
        return {
            "enabled": self.schedule_enabled.isChecked(),
            "interval_minutes": self.interval_spin.value(),
            "keep_last": self.keep_last_spin.value(),
        }


class ApiKeyDialog(QDialog):
    """Диалог ввода CurseForge API ключа."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("CurseForge API Key")
        self.setFixedSize(520, 260)
        self._setup_content()

    def _setup_content(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(24, 24, 24, 24)

        title = SubtitleLabel("API ключ CurseForge")
        title.setStyleSheet("background: transparent;")
        layout.addWidget(title)

        info = BodyLabel(
            "Для поиска и скачивания модов требуется личный API ключ CurseForge. "
            "Нажмите кнопку ниже, чтобы открыть страницу получения ключа, скопируйте его и вставьте в поле."
        )
        info.setStyleSheet("color: #ccc; font-size: 12px; background: transparent;")
        info.setWordWrap(True)
        layout.addWidget(info)

        open_btn = PushButton(FIF.LINK, "Открыть страницу API ключей")
        open_btn.clicked.connect(self._open_api_page)
        layout.addWidget(open_btn)

        self.key_edit = LineEdit()
        self.key_edit.setPlaceholderText("Вставьте API ключ сюда")
        layout.addWidget(self.key_edit)

        layout.addStretch()

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.cancel_btn = PushButton("Отмена")
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)

        self.ok_btn = PrimaryPushButton("Сохранить")
        self.ok_btn.clicked.connect(self.accept)
        btn_layout.addWidget(self.ok_btn)

        layout.addLayout(btn_layout)

    def _open_api_page(self):
        webbrowser.open("https://console.curseforge.com/?#/api-keys")

    def get_key(self) -> str:
        return self.key_edit.text().strip()


class ModBrowserDialog(QDialog):
    """Диалог поиска и установки модов через CurseForge (подобно Prism Launcher)."""

    mod_installed = Signal(str)
    _populate_results_signal = Signal(list)
    _set_status_signal = Signal(str)
    _enable_search_signal = Signal()
    _logo_loaded_signal = Signal(QPixmap)
    _details_loaded_signal = Signal(dict)
    _open_url_signal = Signal(str)
    _open_mods_folder_signal = Signal()
    _show_deps_signal = Signal(list)
    _set_page_state_signal = Signal(int, int)
    _load_initial_catalog_signal = Signal()

    def __init__(self, mod_manager: ModManager, event_loop, parent=None):
        super().__init__(parent)
        self.mod_manager = mod_manager
        self.event_loop = event_loop
        self._current_mods = []
        self._selected_mod = None
        self._selected_mod_deps = []
        self._categories = []
        self._selected_category_ids = []
        self._current_page = 0
        self._page_size = 50
        self._total_results = 0
        self._browse_mode = "catalog"
        self.setWindowTitle("Поиск модов — CurseForge")
        self.setMinimumSize(900, 600)
        self._setup_ui()

        # Сигналы для безопасного обновления UI из asyncio-потока
        self._populate_results_signal.connect(self._populate_results)
        self._set_status_signal.connect(lambda msg: self.status_label.setText(msg))
        self._enable_search_signal.connect(lambda: self.search_btn.setEnabled(True))
        self._logo_loaded_signal.connect(self._set_logo)
        self._details_loaded_signal.connect(self._update_details)
        self._open_url_signal.connect(webbrowser.open)
        self._open_mods_folder_signal.connect(self._open_mods_folder)
        self._show_deps_signal.connect(self._show_dependency_search)
        self._set_page_state_signal.connect(self._set_page_state)
        self._load_initial_catalog_signal.connect(lambda: self._load_catalog_page(reset_page=True))

        asyncio.run_coroutine_threadsafe(self._initialize_browser(), self.event_loop)

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Поиск
        search_layout = QHBoxLayout()
        self.search_edit = SearchLineEdit()
        self.search_edit.setPlaceholderText("Введите название мода...")
        self.search_edit.returnPressed.connect(self._search)
        search_layout.addWidget(self.search_edit)

        self.categories_btn = PushButton(FIF.FILTER, "Категории: все")
        self.categories_btn.clicked.connect(self._open_categories_menu)
        search_layout.addWidget(self.categories_btn)

        self.search_btn = PrimaryPushButton(FIF.SEARCH, "Поиск")
        self.search_btn.clicked.connect(self._search)
        search_layout.addWidget(self.search_btn)

        self.reset_btn = PushButton(FIF.CANCEL, "Сброс")
        self.reset_btn.clicked.connect(self._reset_search)
        search_layout.addWidget(self.reset_btn)
        layout.addLayout(search_layout)

        # Основной контент: таблица слева, детали справа
        content_layout = QHBoxLayout()
        content_layout.setSpacing(12)

        # Левая панель: таблица
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)

        self.results_table = QTableWidget()
        self.results_table.setColumnCount(3)
        self.results_table.setHorizontalHeaderLabels(["Название", "Автор", "Описание"])
        self.results_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.results_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.results_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.results_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.results_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.results_table.setStyleSheet("""
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
        self.results_table.itemSelectionChanged.connect(self._on_mod_selected)
        left_layout.addWidget(self.results_table)

        self.status_label = BodyLabel("Введите запрос и нажмите Поиск")
        self.status_label.setStyleSheet("color: #888; background: transparent;")
        left_layout.addWidget(self.status_label)

        pagination_layout = QHBoxLayout()
        self.prev_btn = PushButton("Назад")
        self.prev_btn.clicked.connect(self._prev_page)
        pagination_layout.addWidget(self.prev_btn)

        self.page_label = BodyLabel("Страница 1")
        self.page_label.setStyleSheet("color: #aaa; background: transparent;")
        pagination_layout.addWidget(self.page_label)
        pagination_layout.addStretch()

        self.next_btn = PushButton("Вперёд")
        self.next_btn.clicked.connect(self._next_page)
        pagination_layout.addWidget(self.next_btn)
        left_layout.addLayout(pagination_layout)

        content_layout.addWidget(left_widget, stretch=2)

        # Правая панель: детали мода
        right_widget = CardWidget()
        right_widget.setStyleSheet("CardWidget { background-color: #1e1e1e; border: 1px solid #333; border-radius: 8px; }")
        self.details_layout = QVBoxLayout(right_widget)
        self.details_layout.setContentsMargins(16, 16, 16, 16)
        self.details_layout.setSpacing(12)

        self.details_logo = QLabel()
        self.details_logo.setAlignment(Qt.AlignCenter)
        self.details_logo.setFixedSize(128, 128)
        self.details_logo.setStyleSheet("background: transparent;")
        self.details_layout.addWidget(self.details_logo)

        self.details_name = SubtitleLabel("")
        self.details_name.setStyleSheet("background: transparent; font-size: 16px; font-weight: bold;")
        self.details_layout.addWidget(self.details_name)

        self.details_author = BodyLabel("")
        self.details_author.setStyleSheet("color: #aaa; background: transparent;")
        self.details_layout.addWidget(self.details_author)

        self.details_summary = BodyLabel("")
        self.details_summary.setStyleSheet("color: #ccc; background: transparent;")
        self.details_summary.setWordWrap(True)
        self.details_layout.addWidget(self.details_summary)

        self.details_stats = CaptionLabel("")
        self.details_stats.setStyleSheet("color: #888; background: transparent;")
        self.details_layout.addWidget(self.details_stats)

        self.details_deps_btn = PushButton(FIF.LINK, "Зависимости")
        self.details_deps_btn.clicked.connect(self._on_deps_btn_clicked)
        self.details_deps_btn.setVisible(False)
        self.details_layout.addWidget(self.details_deps_btn)

        self.details_install_btn = PrimaryPushButton(FIF.DOWNLOAD, "Установить")
        self.details_install_btn.clicked.connect(self._install_selected_mod)
        self.details_install_btn.setEnabled(False)
        self.details_layout.addWidget(self.details_install_btn)

        self.details_layout.addStretch()
        content_layout.addWidget(right_widget, stretch=1)

        layout.addLayout(content_layout)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        open_folder_btn = PushButton(FIF.FOLDER, "Открыть папку с модами")
        open_folder_btn.clicked.connect(self._open_mods_folder)
        btn_layout.addWidget(open_folder_btn)

        close_btn = PushButton("Закрыть")
        close_btn.clicked.connect(self.reject)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

    def _open_mods_folder(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.mod_manager.mods_dir)))

    def _on_mod_selected(self):
        selected = self.results_table.selectedItems()
        if not selected:
            self._selected_mod = None
            self._selected_mod_deps = []
            self.details_install_btn.setEnabled(False)
            self.details_deps_btn.setVisible(False)
            return
        row = selected[0].row()
        if row < len(self._current_mods):
            mod = self._current_mods[row]
            self._selected_mod = mod
            self._details_loaded_signal.emit(mod)
            self.details_install_btn.setEnabled(True)
            # Загрузка логотипа
            logo_data = mod.get("logo", {})
            url = logo_data.get("url") or logo_data.get("thumbnailUrl")
            if url:
                asyncio.run_coroutine_threadsafe(self._load_logo_async(url), self.event_loop)
            else:
                self._logo_loaded_signal.emit(QPixmap())
            # Загрузка dependencies для первого файла
            asyncio.run_coroutine_threadsafe(self._load_dependencies_async(mod.get("id")), self.event_loop)

    async def _load_dependencies_async(self, mod_id):
        try:
            files = await self.mod_manager.api.get_mod_files(mod_id)
            if not files:
                self._show_deps_signal.emit([])
                return
            details = await self.mod_manager.api.get_mod_file_details(mod_id, files[0]["id"])
            deps = details.get("dependencies", [])
            extra_ids = [d["modId"] for d in deps if d.get("relationType") in (1, 2, 4)]
            self._show_deps_signal.emit(extra_ids)
        except Exception:
            self._show_deps_signal.emit([])

    def _update_details(self, mod):
        self.details_name.setText(mod.get("name", "N/A"))
        authors = mod.get("authors", [])
        author = authors[0].get("name", "Unknown") if authors else "Unknown"
        self.details_author.setText(f"Автор: {author}")
        self.details_summary.setText(mod.get("summary", "Нет описания"))
        downloads = mod.get("downloadCount", 0)
        mod_date = mod.get("dateCreated", "")
        self.details_stats.setText(f"Загрузок: {downloads}\nДата: {mod_date}")

    def _set_logo(self, pixmap):
        if pixmap.isNull():
            self.details_logo.setText("Нет иконки")
        else:
            self.details_logo.setPixmap(pixmap)

    def _on_deps_btn_clicked(self):
        if self._selected_mod_deps:
            self._search_by_mod_ids(self._selected_mod_deps)

    def _show_dependency_search(self, mod_ids):
        self._selected_mod_deps = mod_ids
        self._browse_mode = "dependencies" if mod_ids else "catalog"
        self.details_deps_btn.setVisible(bool(mod_ids))
        if mod_ids:
            self._set_page_state(0, len(mod_ids))

    def _search_by_mod_ids(self, mod_ids):
        self._browse_mode = "dependencies"
        self.status_label.setText("Загрузка зависимостей...")
        self.results_table.setRowCount(0)

        async def do_load():
            mods = []
            for mid in mod_ids:
                try:
                    det = await self.mod_manager.api.get_mod_details(mid)
                    if det:
                        mods.append(det)
                except Exception:
                    pass
            self._populate_results_signal.emit(mods)
            self._set_status_signal.emit(f"Зависимостей найдено: {len(mods)}")

        asyncio.run_coroutine_threadsafe(do_load(), self.event_loop)

    async def _load_logo_async(self, url):
        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, _fetch_image_data, url)
            if data:
                pixmap = QPixmap()
                pixmap.loadFromData(data)
                if not pixmap.isNull():
                    pixmap = pixmap.scaled(128, 128, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    self._logo_loaded_signal.emit(pixmap)
                    return
        except Exception as e:
            logging.warning(f"Ошибка загрузки логотипа: {e}")
        self._logo_loaded_signal.emit(QPixmap())

    def _install_selected_mod(self):
        if self._selected_mod:
            self._install_mod(self._selected_mod)

    def _search(self):
        self._browse_mode = "catalog"
        self._load_catalog_page(reset_page=True)

    def _load_catalog_page(self, reset_page: bool = False):
        if reset_page:
            self._current_page = 0

        query = self.search_edit.text().strip()
        category_ids = self._selected_category_ids.copy()
        self.status_label.setText("Поиск...")
        self.results_table.setRowCount(0)
        self.search_btn.setEnabled(False)
        self.prev_btn.setEnabled(False)
        self.next_btn.setEnabled(False)
        self._selected_mod = None
        self._selected_mod_deps = []
        self.details_install_btn.setEnabled(False)
        self.details_deps_btn.setVisible(False)

        async def do_search():
            try:
                mods, total = await self.mod_manager.api.search_mods(
                    query=query,
                    category_ids=category_ids,
                    page_size=self._page_size,
                    page_index=self._current_page,
                )
                self._total_results = total
                self._populate_results_signal.emit(mods)
                self._set_page_state_signal.emit(self._current_page, total)
            except Exception as e:
                self._set_status_signal.emit(f"Ошибка поиска: {e}")
                self._set_page_state_signal.emit(self._current_page, 0)
            finally:
                self._enable_search_signal.emit()

        asyncio.run_coroutine_threadsafe(do_search(), self.event_loop)

    def _populate_results(self, mods):
        self._current_mods = mods
        self.results_table.setRowCount(0)
        if not mods:
            self.status_label.setText("Моды не найдены")
            return

        for mod in mods:
            row = self.results_table.rowCount()
            self.results_table.insertRow(row)

            name = mod.get("name", "N/A")
            authors = mod.get("authors", [])
            author = authors[0].get("name", "Unknown") if authors else "Unknown"
            summary = mod.get("summary", "")

            self.results_table.setItem(row, 0, QTableWidgetItem(name))
            self.results_table.setItem(row, 1, QTableWidgetItem(author))
            summary_item = QTableWidgetItem(summary)
            summary_item.setToolTip(summary)
            self.results_table.setItem(row, 2, summary_item)

        if self._browse_mode == "dependencies":
            self.status_label.setText(f"Зависимостей найдено: {len(mods)}")
        else:
            self.status_label.setText(f"Найдено: {len(mods)} из {self._total_results}")

    async def _initialize_browser(self):
        try:
            categories = await self.mod_manager.api.get_categories()
            self._categories = [item for item in categories if item.get("classId") == 9137]
        except Exception as e:
            self._set_status_signal.emit(f"Ошибка загрузки категорий: {e}")

        self._load_initial_catalog_signal.emit()

    def _set_page_state(self, page_index: int, total: int):
        if self._browse_mode == "dependencies":
            self.page_label.setText("Режим зависимостей")
            self.prev_btn.setEnabled(False)
            self.next_btn.setEnabled(False)
            return

        total_pages = max(1, (total + self._page_size - 1) // self._page_size)
        self.page_label.setText(f"Страница {page_index + 1} из {total_pages}")
        self.prev_btn.setEnabled(page_index > 0)
        self.next_btn.setEnabled(page_index + 1 < total_pages)

    def _prev_page(self):
        if self._browse_mode != "catalog" or self._current_page == 0:
            return
        self._current_page -= 1
        self._load_catalog_page()

    def _next_page(self):
        if self._browse_mode != "catalog":
            return
        if (self._current_page + 1) * self._page_size >= self._total_results:
            return
        self._current_page += 1
        self._load_catalog_page()

    def _reset_search(self):
        self.search_edit.clear()
        self._selected_category_ids = []
        self.categories_btn.setText("Категории: все")
        self._selected_mod_deps = []
        self._browse_mode = "catalog"
        self._current_page = 0
        self._total_results = 0
        self.results_table.clearSelection()
        self.details_name.setText("")
        self.details_author.setText("")
        self.details_summary.setText("")
        self.details_stats.setText("")
        self._logo_loaded_signal.emit(QPixmap())
        self.details_install_btn.setEnabled(False)
        self.details_deps_btn.setVisible(False)
        self._load_catalog_page(reset_page=True)

    def _open_categories_menu(self):
        menu = QMenu(self)
        all_action = menu.addAction("Все категории")
        all_action.setCheckable(True)
        all_action.setChecked(not self._selected_category_ids)
        menu.addSeparator()

        actions = {}
        for category in sorted(self._categories, key=lambda item: item.get("name", "")):
            action = menu.addAction(category.get("name", "Без названия"))
            action.setCheckable(True)
            action.setChecked(category.get("id") in self._selected_category_ids)
            actions[action] = category.get("id")

        chosen = menu.exec(self.categories_btn.mapToGlobal(self.categories_btn.rect().bottomLeft()))
        if chosen is None:
            return

        if chosen == all_action:
            self._selected_category_ids = []
        elif chosen in actions:
            category_id = actions[chosen]
            if category_id in self._selected_category_ids:
                self._selected_category_ids.remove(category_id)
            else:
                self._selected_category_ids.append(category_id)

        self.categories_btn.setText(
            f"Категории: {len(self._selected_category_ids)}" if self._selected_category_ids else "Категории: все"
        )
        self._search()

    def _install_mod(self, mod):
        mod_id = mod.get("id")
        mod_name = mod.get("name", "Unknown")
        slug = mod.get("slug") or str(mod_id)
        self._set_status_signal.emit(f"Загрузка файлов для '{mod_name}'...")

        async def do_install():
            try:
                files = await self.mod_manager.api.get_mod_files(mod_id)
                if not files:
                    self._set_status_signal.emit("Нет доступных файлов для этого мода")
                    return
                latest = files[0]
                file_id = latest["id"]
                file_name = latest.get("fileName", f"{mod_id}.jar")

                # Получаем dependencies
                file_details = await self.mod_manager.api.get_mod_file_details(mod_id, file_id)
                deps = file_details.get("dependencies", [])

                # Проверка incompatible (5)
                installed_ids = self.mod_manager.get_installed_mod_ids()
                incompatible = [d for d in deps if d.get("relationType") == 5]
                conflict_ids = {d["modId"] for d in incompatible} & installed_ids
                if conflict_ids:
                    conflict_names = []
                    for cid in conflict_ids:
                        d = await self.mod_manager.api.get_mod_details(cid)
                        conflict_names.append(d.get("name", str(cid)) if d else str(cid))
                    self._set_status_signal.emit(
                        f"Конфликт: {mod_name} несовместим с {', '.join(conflict_names)}. "
                        f"Удалите конфликтующие моды перед установкой."
                    )
                    return

                # Скачивание основного мода
                dest = self.mod_manager.mods_dir / file_name
                success, manual_url = await self.mod_manager.api.download_mod(
                    mod_id, file_id, dest, slug=slug
                )
                if not success:
                    self._open_url_signal.emit(manual_url)
                    self._open_mods_folder_signal.emit()
                    self._set_status_signal.emit(
                        f"Не удалось скачать {mod_name} — ссылка открыта в браузере. "
                        f"Скачайте файл и поместите в папку Server/mods."
                    )
                    return

                # Сохраняем метаданные основного мода
                file_date = latest.get("fileDate", 0)
                if isinstance(file_date, str):
                    file_date = int(datetime.fromisoformat(file_date.replace("Z", "+00:00")).timestamp() * 1000)
                self.mod_manager._save_mod_meta(
                    dest, curse_id=mod_id, file_id=file_id, file_date=file_date,
                    slug=slug, name=mod_name
                )

                # Параллельная загрузка required dependencies (3)
                required = [d for d in deps if d.get("relationType") == 3]
                if required:
                    self._set_status_signal.emit(f"Загрузка {len(required)} обязательных зависимостей...")
                    async def install_dep(dep):
                        dep_id = dep["modId"]
                        dep_details = await self.mod_manager.api.get_mod_details(dep_id)
                        if not dep_details:
                            return None
                        dep_name = dep_details.get("name", str(dep_id))
                        dep_slug = dep_details.get("slug", str(dep_id))
                        dep_files = await self.mod_manager.api.get_mod_files(dep_id)
                        if not dep_files:
                            return None
                        dep_latest = dep_files[0]
                        dep_file_id = dep_latest["id"]
                        dep_file_name = dep_latest.get("fileName", f"{dep_id}.jar")
                        dep_dest = self.mod_manager.mods_dir / dep_file_name
                        dep_success, dep_manual = await self.mod_manager.api.download_mod(
                            dep_id, dep_file_id, dep_dest, slug=dep_slug
                        )
                        if dep_success:
                            dep_date = dep_latest.get("fileDate", 0)
                            if isinstance(dep_date, str):
                                dep_date = int(datetime.fromisoformat(dep_date.replace("Z", "+00:00")).timestamp() * 1000)
                            self.mod_manager._save_mod_meta(
                                dep_dest, curse_id=dep_id, file_id=dep_file_id,
                                file_date=dep_date, slug=dep_slug, name=dep_name
                            )
                            return dep_file_name
                        else:
                            self._open_url_signal.emit(dep_manual)
                            return None

                    dep_results = await asyncio.gather(*[install_dep(d) for d in required], return_exceptions=True)
                    installed_deps = [r for r in dep_results if isinstance(r, str)]
                    if installed_deps:
                        self._set_status_signal.emit(f"Установлены зависимости: {', '.join(installed_deps)}")

                self._set_status_signal.emit(f"Установлен: {mod_name}")
                self.mod_installed.emit(file_name)
            except Exception as e:
                self._set_status_signal.emit(f"Ошибка установки: {e}")

        asyncio.run_coroutine_threadsafe(do_install(), self.event_loop)


def _fetch_image_data(url: str) -> bytes:
    try:
        import urllib.request
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.read()
    except Exception:
        return b""
