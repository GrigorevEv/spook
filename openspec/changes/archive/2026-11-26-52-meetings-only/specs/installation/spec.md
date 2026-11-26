## MODIFIED Requirements

### Requirement: `spook install` ставит всё одной командой
Команда `spook install` SHALL по порядку: установить инструмент командой `uv tool install --reinstall <репозиторий>`; подготовить модели командой `spook prepare`; подключить Скилл Встречи; записать значок в меню приложений и обновить базу значков; записать юнит службы и включить её командами `systemctl --user daemon-reload`, `try-restart spook.service` и `enable --now spook.service`.

#### Scenario: Внешние команды установки
- **GIVEN** репозиторий Spook и домашний каталог без следов прежней установки
- **WHEN** выполняется `spook install`
- **THEN** по порядку вызваны: `uv tool install --reinstall <репозиторий>`, `~/.local/bin/spook prepare`, `update-desktop-database ~/.local/share/applications`, `systemctl --user daemon-reload`, `systemctl --user try-restart spook.service`, `systemctl --user enable --now spook.service`

### Requirement: Скиллы подключаются ссылками
Spook SHALL поставлять Скилл `spook-meeting`. Каждый Скилл из папки `skills/` репозитория подключается ссылкой `~/.claude/skills/<имя>`. Если место занято чужим Скиллом, установка SHALL сохранить его и сообщить об этом. Ссылка на исчезнувшую копию подключаемого Скилла заменяется. При обновлении установка SHALL убрать прежнюю ссылку `spook-lecture` только тогда, когда она указывает на Скилл Лекции этого репозитория.

#### Scenario: Скилл из репозитория
- **GIVEN** в репозитории есть Скилл `skills/spook-meeting` с файлом `SKILL.md`
- **WHEN** выполнен `spook install`
- **THEN** `~/.claude/skills/spook-meeting` — ссылка на `skills/spook-meeting` репозитория

#### Scenario: Чужой Скилл с тем же именем
- **GIVEN** `~/.claude/skills/spook-meeting` — папка пользователя с собственным `SKILL.md`, а `~/.claude/skills/spook-other` — ссылка в другое место
- **WHEN** выполнен `spook install` из репозитория со Скиллами `spook-meeting` и `spook-other`
- **THEN** папка и ссылка пользователя остались прежними
- **AND** вывод установки называет `spook-meeting` и `spook-other`

#### Scenario: Ссылка на исчезнувшую копию
- **GIVEN** `~/.claude/skills/spook-meeting` — ссылка на папку, которой больше нет
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
- **GIVEN** `spook install` выполнен, рядом с подключённым Скиллом лежит чужой — папка `~/.claude/skills/someone-else`
- **WHEN** выполняется `spook uninstall`
- **THEN** по порядку вызваны: `systemctl --user disable --now spook.service`, `systemctl --user daemon-reload`, `update-desktop-database ~/.local/share/applications`, `uv tool uninstall spook`
- **AND** юнита, значка и ссылки `~/.claude/skills/spook-meeting` больше нет, папка `someone-else` на месте
