# installation Specification

## Purpose

`spook install` ставит Spook, готовит модели, подключает Скилл Встречи, добавляет значок в меню приложений и включает службу пользователя. Служба запускается вместе с графическим сеансом. `spook uninstall` удаляет установленное, сохраняя настройки, модели и Журналы Агента.

## Requirements

### Requirement: `spook install` ставит всё одной командой
Команда `spook install` SHALL по порядку: установить инструмент командой `uv tool install --reinstall <репозиторий>`; подготовить модели командой `spook prepare`; подключить Скилл Встречи; записать значок в меню приложений и обновить базу значков; записать юнит службы и включить её командами `systemctl --user daemon-reload`, `try-restart spook.service` и `enable --now spook.service`.

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
Spook SHALL поставлять Скилл `spook-meeting`. Каждый Скилл из папки `skills/` репозитория подключается ссылкой `~/.agents/skills/<имя>`. Если место занято чужим Скиллом, установка SHALL сохранить его и сообщить об этом. Ссылка на исчезнувшую копию подключаемого Скилла заменяется. При обновлении Spook SHALL убрать прежние ссылки своего репозитория в `~/.claude/skills`.

#### Scenario: Скилл из репозитория
- **GIVEN** в репозитории есть Скилл `skills/spook-meeting` с файлом `SKILL.md`
- **WHEN** выполнен `spook install`
- **THEN** `~/.agents/skills/spook-meeting` — ссылка на `skills/spook-meeting` репозитория

#### Scenario: Чужой Скилл с тем же именем
- **GIVEN** `~/.agents/skills/spook-meeting` — папка пользователя с собственным `SKILL.md`, а `~/.agents/skills/spook-other` — ссылка в другое место
- **WHEN** выполнен `spook install` из репозитория со Скиллами `spook-meeting` и `spook-other`
- **THEN** папка и ссылка пользователя остались прежними
- **AND** вывод установки называет `spook-meeting` и `spook-other`

#### Scenario: Ссылка на исчезнувшую копию
- **GIVEN** `~/.agents/skills/spook-meeting` — ссылка на папку, которой больше нет
- **WHEN** выполнен `spook install`
- **THEN** ссылка ведёт на `skills/spook-meeting` репозитория

#### Scenario: Обновление со Скиллом Лекции
- **GIVEN** `~/.claude/skills/spook-lecture` — ссылка на прежний `skills/spook-lecture` этого репозитория
- **WHEN** выполняется `spook install` после обновления
- **THEN** ссылка `spook-lecture` удалена, подключён `spook-meeting`

#### Scenario: На месте прежнего Скилла лежит Скилл пользователя
- **GIVEN** `~/.claude/skills/spook-lecture` — папка пользователя или ссылка на его собственный Скилл
- **WHEN** выполняется `spook install`
- **THEN** папка или ссылка пользователя сохраняется, Скилл Встречи подключён по обычным правилам

#### Scenario: Переход с установки Claude Code
- **GIVEN** в `~/.claude/skills` сохранились ссылки на Скиллы этого репозитория и отдельный Скилл пользователя
- **WHEN** выполнен `spook install`
- **THEN** прежние ссылки репозитория убраны, Скилл пользователя сохранён, `spook-meeting` подключён в `~/.agents/skills`

### Requirement: Повторная установка безопасна
Повторный `spook install` SHALL оставить юнит, значок и ссылку на Скилл Встречи без изменений и заново выполнить шаги установки, в том числе подготовку моделей и перезапуск службы.

#### Scenario: Вторая установка подряд
- **GIVEN** `spook install` уже выполнен
- **WHEN** он выполняется ещё раз
- **THEN** содержимое юнита и значка то же, ссылка на Скилл на месте
- **AND** снова вызваны `~/.local/bin/spook prepare` и `systemctl --user try-restart spook.service`

### Requirement: `spook uninstall` убирает установленное
Команда `spook uninstall` SHALL выключить и удалить службу, удалить значок, ссылки на Скиллы этого репозитория и инструмент. Настройки, модели, Журналы Агента и чужие Скиллы SHALL остаться.

#### Scenario: Удаление после установки
- **GIVEN** `spook install` выполнен, рядом с подключённым Скиллом лежит чужой — папка `~/.agents/skills/someone-else`
- **WHEN** выполняется `spook uninstall`
- **THEN** по порядку вызваны: `systemctl --user disable --now spook.service`, `systemctl --user daemon-reload`, `update-desktop-database ~/.local/share/applications`, `uv tool uninstall spook`
- **AND** юнита, значка и ссылки `~/.agents/skills/spook-meeting` больше нет, папка `someone-else` на месте
