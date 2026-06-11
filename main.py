import sys
import asyncio
import logging
import threading

from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QIcon, QPixmap, QColor, QAction

from qfluentwidgets import setTheme, Theme

from core.automation import AutomationService


def create_tray_icon(app, window):
    """Создаёт иконку в системном трее через Qt."""
    if not QSystemTrayIcon.isSystemTrayAvailable():
        return None

    try:
        def show_window():
            window.showNormal()
            window.raise_()
            window.activateWindow()

        pixmap = QPixmap(64, 64)
        pixmap.fill(QColor("#1e1e1e"))
        tray = QSystemTrayIcon(QIcon(pixmap), app)
        tray.setToolTip("AdminisTale")

        menu = QMenu()
        open_action = QAction("Открыть", tray)
        open_action.triggered.connect(show_window)
        menu.addAction(open_action)

        exit_action = QAction("Выход", tray)
        exit_action.triggered.connect(window.tray_exit)
        menu.addAction(exit_action)

        tray.setContextMenu(menu)
        tray.activated.connect(lambda reason: show_window() if reason == QSystemTrayIcon.DoubleClick else None)
        tray.show()
        return tray
    except Exception as e:
        logging.error(f"Не удалось создать иконку трея: {e}")
        return None


def main():
    logging.basicConfig(level=logging.DEBUG)

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setFont(QFont("Segoe UI", 10))
    setTheme(Theme.DARK)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def run_asyncio_loop():
        asyncio.set_event_loop(loop)
        loop.run_forever()

    async_thread = threading.Thread(target=run_asyncio_loop, daemon=True)
    async_thread.start()

    from ui.main_window import MainWindow
    win = MainWindow(loop)
    minimized = "--minimized" in sys.argv
    if not minimized:
        win.show()

    tray_icon = create_tray_icon(app, win)
    win.tray_icon = tray_icon

    automation = AutomationService(win.process_manager)
    automation.start(loop)

    exit_code = app.exec()

    if tray_icon:
        tray_icon.hide()

    try:
        win.remote_access_service.shutdown()
    except Exception as e:
        logging.warning(f"Ошибка при остановке сервиса удалённого доступа: {e}")

    try:
        from core.process_manager import AsyncProcessManager
        pm = AsyncProcessManager()
        future = asyncio.run_coroutine_threadsafe(pm.stop_all(), loop)
        future.result(timeout=45)
    except Exception as e:
        logging.warning(f"Ошибка при остановке процессов: {e}")

    # Остановка asyncio loop
    loop.call_soon_threadsafe(loop.stop)
    async_thread.join(timeout=5)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
