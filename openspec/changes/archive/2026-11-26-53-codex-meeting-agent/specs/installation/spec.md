## MODIFIED Requirements

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

### Requirement: `spook uninstall` убирает установленное
Команда `spook uninstall` SHALL выключить и удалить службу, удалить значок, ссылки на Скиллы этого репозитория и инструмент. Настройки, модели, Журналы Агента и чужие Скиллы SHALL остаться.

#### Scenario: Удаление после установки
- **GIVEN** `spook install` выполнен, рядом с подключённым Скиллом лежит чужой — папка `~/.agents/skills/someone-else`
- **WHEN** выполняется `spook uninstall`
- **THEN** по порядку вызваны: `systemctl --user disable --now spook.service`, `systemctl --user daemon-reload`, `update-desktop-database ~/.local/share/applications`, `uv tool uninstall spook`
- **AND** юнита, значка и ссылки `~/.agents/skills/spook-meeting` больше нет, папка `someone-else` на месте
