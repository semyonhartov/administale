# AdminisTale

Десктопное приложение для управления локальными и внешними инстансами серверов Hytale.
Пока что на русском, к релизу отдельно от курсача будет дополнено. С курсача прошлого достаточно времени, но нам пока что лень что-либо делать.

## Статус проекта

- Название проекта: `AdminisTale`
- Тип: desktop application
- Стек: `Python`, `PySide6`, `qfluentwidgets`
- Актуальная документация для разработчиков: [`docs/development.md`](docs/development.md)

## Что умеет приложение

- управлять локальными инстансами
- подключаться к внешним инстансам по удалённому API
- запускать и останавливать сервер
- работать с консолью и логами
- управлять модами
- редактировать настройки инстанса
- просматривать файлы инстанса
- создавать и восстанавливать бэкапы

## Особенности внешних инстансов

- все действия выполняются на стороне удалённого сервера
- локальная файловая система пользователя не используется для работы с внешним инстансом
- локальные кнопки открытия папок для внешнего инстанса скрываются или отключаются
- данные статуса, консоли, файлов и бэкапов загружаются через удалённый API

## Требования

- Python `3.10+`
- Linux или Windows
- зависимости из `requirements.txt`

## Установка

```bash
pip install -r requirements.txt
```

## Запуск

```bash
python main.py
```

## Инструкция для пользователей

### 1. Локальный инстанс

1. Запустите приложение.
2. Создайте новый инстанс или импортируйте существующий.
3. Откройте карточку инстанса.
4. Используйте вкладки `Главная`, `Консоль`, `Модификации`, `Настройки`, `Файлы`, `Бэкапы`.

### 2. Внешний инстанс

1. На сервере-владельце должен быть создан удалённый пользователь с нужными правами.
2. В главном окне выберите подключение к внешнему инстансу.
3. Укажите `host`, `port`, `server id`, `username`, `password`.
4. После подключения приложение добавит внешний инстанс в список.
5. Доступные вкладки и действия будут зависеть от выданных прав.

### О проекте

- Краткое описание: 
> Десктопное user-friendly приложение, позволяющее запускать сервера Hytale на домашнем ПК без надобности установки Docker и полной настройки всех процессов;
- Целевая аудитория:
> Игроки Hytale, которые хотят поиграть с друзьями через отдельный сервер;
- Поддерживаемые платформы: 
> Windows (проверено на 11) и Linux (проверено на Arch-based).

### Скриншоты

- Главное окно: [`docs/images/main-window.png`](docs/images/main-window.png)
- Панель инстанса: [`docs/images/local-instance.png`](docs/images/local-instance.png)
- Удалённый доступ: [`docs/images/remote-instance.png`](docs/images/remote-instance.png)
- Права доступа: [`docs/images/user-rules.png`](docs/images/user-rules.png)
- Экспорт инстанса: [`docs/images/export.png`](docs/images/export.png)
- Импорт инстанса: [`docs/images/import.png`](docs/images/import.png)
- Консоль: [`docs/images/console.png`](docs/images/console.png)
- Помощник команд: [`docs/images/command-helper.png`](docs/images/command-helper.png)
- Панель модов: [`docs/images/mods-panel.png`](docs/images/mods-panel.png)
- Поиск модов: [`docs/images/curseforge-panel.png`](docs/images/curseforge-panel.png)
- Настройки: [`docs/images/settings-panel.png`](docs/images/settings-panel.png)
- Файлы: [`docs/images/files-panel.png`](docs/images/files-panel.png)
- Бэкапы: [`docs/images/backup-panel.png`](docs/images/backup-panel.png)
- Окно создания бэкапа: [`docs/images/backup-dialog.png`](docs/images/backup-dialog.png)

### Полезные ссылки

- Релизы: [`Последний`](https://github.com/semyonhartov/progeng/releases/latest)
- Документация разработчика: [`docs/development.md`](docs/development.md)

### Команда

- Участник 1: `semyonhartov - scrum, dev`
- Участник 2: `SumHuman111 | ChmoMraz - dev, docs`

## Структура проекта

```text
.
├── main.py
├── README.md
├── requirements.txt
├── core/
├── ui/
├── resources/
└── docs/
```

## Обратная связь 
[@semyonhartov](https://t.me/semyonhartov)  
[mr.sokoloff@inbox.ru](mailto:mr.sokoloff@inbox.ru) 
