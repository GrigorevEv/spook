# installation Specification

## Purpose

Установка Spook одной командой: `spook install` ставит инструмент, готовит модели, подключает Скиллы, добавляет значок в меню приложений и включает службу пользователя, которая запускается сама вместе с графическим сеансом. `spook uninstall` убирает всё это, оставляя настройки, модели и Журналы Агента.

## Requirements

### Requirement: `spook install` ставит всё одной командой
Команда `spook install` SHALL по порядку: установить инструмент командой `uv tool install --reinstall <репозиторий>`; подготовить модели командой `spook prepare`; подключить Скиллы; записать значок в меню приложений и обновить базу значков; записать юнит службы и включить её командами `systemctl --user daemon-reload`, `try-restart spook.service` и `enable --now spook.service`.

#### Scenario: Внешние команды установки
- **GIVEN** репозиторий Spook и домашний каталог без следов прежней установки
- **WHEN** выполняется `spook install`
- **THEN** по порядку вызваны: `uv tool install --reinstall <репозиторий>`, `~/.local/bin/spook prepare`, `update-desktop-database ~/.local/share/applications`, `systemctl --user daemon-reload`, `systemctl --user try-restart spook.service`, `systemctl --user enable --now spook.service`

### Requirement: Юнит службы пользователя
Юнит `~/.config/systemd/user/spook.service` SHALL запускать `%h/.local/bin/spook serve` вместе с графическим сеансом пользователя (`WantedBy=graphical-session.target`), перезапускать службу после сбоя (`Restart=on-failure`) и давать ей `PATH`, который начинается с `%h/.local/bin`, чтобы служба находила программы пользователя.

#### Scenario: Содержимое юнита
- **WHEN** выполнен `spook install`
- **THEN** в юните `ExecStart=%h/.local/bin/spook serve`, `Restart=on-failure`, `WantedBy=graphical-session.target`, а `After` называет `graphical-session.target`
- **AND** `PATH` службы начинается с `%h/.local/bin` и содержит `/usr/bin`

#### Scenario: Юнит проходит проверку systemd
- **GIVEN** на машине есть `systemd-analyze`
- **WHEN** юнит проверяется командой `systemd-analyze --user verify`
- **THEN** проверка проходит без замечаний

### Requirement: Значок в меню приложений
Значок `~/.local/share/applications/spook.desktop` с именем «Spook» SHALL открывать вкладку по адресу службы: `xdg-open http://localhost:<порт>/`, где порт — из настроек на момент установки.

#### Scenario: Значок открывает адрес службы
- **GIVEN** в настройках порт 9123
- **WHEN** выполнен `spook install`
- **THEN** в значке `Name=Spook`, `Type=Application` и `Exec=xdg-open http://localhost:9123/`

#### Scenario: Значок проходит проверку
- **GIVEN** на машине есть `desktop-file-validate`
- **WHEN** значок проверяется этой командой
- **THEN** проверка проходит без замечаний

### Requirement: Скиллы подключаются ссылками
Каждый Скилл из папки `skills/` репозитория SHALL подключаться к скиллам пользователя ссылкой `~/.claude/skills/<имя>` на папку Скилла в репозитории. Чужой Скилл с тем же именем SHALL остаться нетронутым, и установка сообщает о нём. Ссылка на исчезнувшую копию Скилла заменяется.

#### Scenario: Скилл из репозитория
- **GIVEN** в репозитории есть Скилл `skills/spook-probe` с файлом `SKILL.md`
- **WHEN** выполнен `spook install`
- **THEN** `~/.claude/skills/spook-probe` — ссылка на `skills/spook-probe` репозитория

#### Scenario: Чужой Скилл с тем же именем
- **GIVEN** `~/.claude/skills/spook-probe` — папка пользователя с собственным `SKILL.md`, а `~/.claude/skills/spook-other` — ссылка в другое место
- **WHEN** выполнен `spook install` из репозитория со Скиллами `spook-probe` и `spook-other`
- **THEN** папка и ссылка пользователя остались прежними
- **AND** вывод установки называет `spook-probe` и `spook-other`

#### Scenario: Ссылка на исчезнувшую копию
- **GIVEN** `~/.claude/skills/spook-probe` — ссылка на папку, которой больше нет
- **WHEN** выполнен `spook install`
- **THEN** ссылка ведёт на `skills/spook-probe` репозитория

### Requirement: Повторная установка безопасна
Повторный `spook install` SHALL оставить юнит, значок и ссылки на Скиллы без изменений и заново выполнить шаги установки, в том числе подготовку моделей и перезапуск службы.

#### Scenario: Вторая установка подряд
- **GIVEN** `spook install` уже выполнен
- **WHEN** он выполняется ещё раз
- **THEN** содержимое юнита и значка то же, ссылка на Скилл на месте
- **AND** снова вызваны `~/.local/bin/spook prepare` и `systemctl --user try-restart spook.service`

### Requirement: `spook uninstall` убирает установленное
Команда `spook uninstall` SHALL выключить и удалить службу, удалить значок, ссылки на Скиллы этого репозитория и инструмент. Настройки, модели, Журналы Агента и чужие Скиллы SHALL остаться.

#### Scenario: Удаление после установки
- **GIVEN** `spook install` выполнен, рядом с подключённым Скиллом лежит чужой — папка `~/.claude/skills/someone-else`
- **WHEN** выполняется `spook uninstall`
- **THEN** по порядку вызваны: `systemctl --user disable --now spook.service`, `systemctl --user daemon-reload`, `update-desktop-database ~/.local/share/applications`, `uv tool uninstall spook`
- **AND** юнита, значка и ссылки `~/.claude/skills/spook-probe` больше нет, папка `someone-else` на месте
