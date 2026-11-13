# Порядок работы

От issue до merge. Скиллы вызываются вручную. Как заводить issue и как они связаны со спеками — `docs/agents/issue-tracker.md`.

## Основная цепочка (`enhancement`)

| # | Шаг | Результат |
|---|---|---|
| 1 | `/ghub заведи issue: …` | Issue `#N` с меткой типа, по шаблону |
| 2 | `/grill-with-docs #N` — если есть открытые решения | Решения приняты; `GLOSSARY.md` и ADR обновлены |
| 3 | Ветка `<N>-<change>`, `/opsx:propose <change>` — в той же сессии, без `/clear` | `proposal.md` (первая строка `Issue: #N`), дельты спек, `design.md`, `tasks.md` |
| 4 | Прочитать `proposal.md` и дельты, `/commit` | План согласован до кода |
| 5 | `/clear`, `/tdd`, `/opsx:apply <change>` | Код, отмеченные задачи |
| 6 | `/opsx:verify <change>` | Код соответствует спеке |
| 7 | `/code-review main openspec/changes/<change>` | Замечания исправлены |
| 8 | `/opsx:archive <change>`, `/commit` | Дельты влиты в `openspec/specs/`; изменение в архиве |
| 9 | `/ghub открой PR` | PR по `.github/pull_request_template.md`, с `Fixes #N`; CI зелёный |
| 10 | `/ghub влей PR` | Squash-merge; issue закрыт; ветка удалена |
| 11 | `/retro` — по желанию, до `/clear` | Улучшения окружения |

Архивация идёт последним коммитом того же PR: спеки и код попадают в `main` вместе, второй PR не нужен.

Замеры и прототипы — первые пункты `tasks.md` с указанием, что они блокируют, а не отдельный шаг до `/opsx:propose`.

## Маршруты по типу issue

| Тип | Маршрут |
|---|---|
| `enhancement` | Основная цепочка |
| `bug` | `/diagnosing-bugs` → PR с `Fixes #N`, без OpenSpec. Если оказалось, что в спеке не хватает требования, — основная цепочка |
| `chore` | PR с `Fixes #N`, без OpenSpec |
| `documentation` | PR с `Fixes #N`, без OpenSpec |
| Чужой issue | Сначала `/triage`, потом маршрут его типа |

Большая неясная работа, которая не помещается в одну сессию, — `/wayfinder`; каждый прояснившийся кусок идёт в `/opsx:propose` вместо `/to-spec`. Параллельная работа — одно изменение OpenSpec на ветку в своём worktree.

## Скиллы

| Статус | Скиллы | Почему |
|---|---|---|
| В цепочке | `ghub`, `grill-with-docs`, `opsx:propose`, `opsx:apply`, `opsx:verify`, `opsx:archive`, `tdd`, `code-review`, `commit`, `diagnosing-bugs`, `triage` | Не пересекаются: OpenSpec не интервьюирует и не задаёт TDD |
| Вызываются изнутри | `grilling`, `domain-modeling` | Их читают файлом `grill-with-docs` и `triage`. Все глобальные скиллы ручные; после `npx skills update` запустить `~/.agents/patch-skills.sh` |
| По ситуации | `prototype`, `research`, `retro`, `handoff`, `wayfinder`, `improve-codebase-architecture`, `codebase-design`, `wait-what`, `opsx:update`, `opsx:sync` | Работают рядом с цепочкой; найденная работа становится issue |
| Не используем | `opsx:explore` | Перекрывается с `grill-with-docs` |
| Не используем | `to-spec`, `to-tickets` | Вторая спека и второй список задач рядом с OpenSpec и `tasks.md` |
| Не используем | `implement`, `implement-spec` | Заменены `opsx:apply`; `implement-spec` требует `to-spec` и `to-tickets` |
| Не используем | `grill-me` | То же интервью, но без `GLOSSARY.md` и ADR |
| Не используем | `ask-matt` | Направляет в `to-spec` |
| Не используем | `pr` | Текст PR — по `.github/pull_request_template.md`: один шаблон для агента и для сайта |
| Не нужен | `setup-matt-pocock-skills` | Его результат уже записан вручную: этот файл и `issue-tracker.md` |

## Проверки и merge

- CI (`.github/workflows/ci.yml`) на каждый PR: `uv run pytest -q` и `openspec validate --all --strict`.
- В `main` — только через PR с зелёным CI; merge только squash, ветка удаляется после merge.
- Squash-коммит в `main` — заголовок PR и `(#N)`, без тела. Поэтому заголовок PR пишется как коммит: по-английски, в повелительном наклонении. Текст PR — по-русски.
- Апрув не обязателен: репозиторий ведёт один человек, а GitHub не даёт одобрить свой PR.
- Merge — только по явной команде пользователя.
