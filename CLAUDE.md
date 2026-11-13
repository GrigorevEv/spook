# Spook

Личный инструмент: слушает звук на компьютере, превращает его в текстовые Заметки, Агент пишет Выжимку. Язык проекта — `GLOSSARY.md`; решения — `docs/adr/`; требования — `openspec/specs/`; спецификация, задачи и артефакты первой версии (тогда проект назывался Tran) — вне репозитория, в архиве истории `~/backups/spook-history-2026-11-08.bundle`, тег `tran-v1` (`git clone <архив> /tmp/spook-old`, затем `git -C /tmp/spook-old show tran-v1:.scratch/tran-v1/spec.md`).

## Соглашения кода

- Python ≥ 3.12 под `uv`; пакет `src/spook/`, тесты `tests/`, шаблоны `src/spook/templates/`, статика `src/spook/static/`. Команда `spook` (`spook serve`, позже `spook topic`, `spook install`).
- Настройки — TOML в `~/.config/spook/`; журналы и служебные сведения — `~/.local/state/spook/`; модель — `~/.local/share/spook/`. В тестах всё это — временные каталоги.
- Имена в коде английские, по словарю: Сессия → Session (kind: meeting, lecture); Источник → Source (system, mic); Реплика → Utterance; Транскрипт → Transcript, абзац → Paragraph; Живой текст → live events; Заметка → Note; Тема → topic; Ссылка → link; Выжимка → Summary; Агент → Agent; Скилл → skill; Словарь терминов → TermsDictionary; Хранилище → Vault; Вики → Wiki; Пауза → pause; автостоп → auto_stop; отставание → backlog.
- Тексты интерфейса и документация — по-русски; сообщения коммитов — по-английски, в повелительном наклонении: `Add…`, `Fix…`. Формат — раздел «Сообщение» в `.claude/skills/commit/SKILL.md` (`/commit` проекта); коммитя без `/commit`, следуй ему же.
- Тесты — только на швах из спецификации (раздел Testing Decisions): HTTP-интерфейс службы с подменами входа звука, распознавателя, внешних программ и часов; контрактные тесты распознавателя; Playwright. Швы согласованы, спрашивать не нужно.
- Быстрые тесты: `uv run pytest -q`. Медленные (модель, PipeWire, браузер) помечены маркерами и по умолчанию не запускаются.
- Звук никогда не пишется на диск (ADR-0001). Устройства пользователя по умолчанию в тестах не трогать; настоящий файл с паролями в `~/Documents/notes` не читать.
- Артефакты изменения (снимки, прототипы, журналы проверок) — `.scratch/<имя изменения OpenSpec>/artifacts/<NN>-<slug>/`, живут до архивации изменения; исследовательские заметки для репозитория — `docs/research/`; скрипты, которые нужны и после влития, — `scripts/`.

## Agent skills

### Порядок работы

Issue → `/grill-with-docs` → `/opsx:propose` → `/opsx:apply` → PR → merge; маршрут зависит от типа issue. См. `docs/agents/workflow.md`.

### Issue tracker

GitHub Issues через `gh` (`/ghub`), без MCP. Тип — одна метка из `bug`, `enhancement`, `chore`, `documentation`; тело — по шаблону из `.github/ISSUE_TEMPLATE/`, без спеки; `proposal.md` ссылается на issue строкой `Issue: #N`, PR закрывает его `Fixes #N`. См. `docs/agents/issue-tracker.md`.

PR: заголовок — по-английски, в повелительном наклонении (станет коммитом в `main`); текст — по-русски, по `.github/pull_request_template.md`.

### OpenSpec

Работа ведётся через OpenSpec (`openspec/config.yaml`). Текущие требования — `openspec/specs/<capability>/spec.md`; любое изменение поведения — изменение в `openspec/changes/<change>/` (`proposal.md`, `design.md`, `tasks.md`, дельты спек): `/opsx:propose` → `/opsx:apply` → `/opsx:archive`. Задачи, замеры и прототипы — пункты `tasks.md`, а не отдельные файлы в `.scratch/`. Базовые спеки для уже написанного кода — `/codely-openspec-spec-create-from-code <capability>`.

### Domain docs

Один `GLOSSARY.md` и `docs/adr/` в корне. Термины — только из словаря, без синонимов. Если решение противоречит ADR, скажи об этом прямо, а не обходи молча.
