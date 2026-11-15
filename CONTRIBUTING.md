# Как работать в Spook

Spook ведёт один человек вместе с агентами разработки (Claude Code, opencode, Codex…). Работа идёт от issue до merge, каждый шаг — скилл, который ты вызываешь сам, по имени. Правила для агентов — в `AGENTS.md`, правила кода — в `CODING_STANDARDS.md`, язык проекта — в `GLOSSARY.md`.

## После клона

Файлы агента (`.claude/`, `.opencode/`) в git не хранятся: скиллы OpenSpec каждый создаёт для своего агента.

```bash
openspec config set profile custom
openspec config set workflows '["propose","explore","apply","update","sync","archive","verify"]'
openspec init --tools <агент>   # claude, opencode, codex…
```

Первые две команды задают глобальный профиль OpenSpec: стандартный профиль `core` не создаёт `openspec-verify-change`. Остальные скиллы — глобальные, в `~/.agents/skills/`.

## Новая возможность (`enhancement`)

| # | Что сделать | Что получится |
|---|---|---|
| 1 | `gh`: «заведи issue: …» | Issue `#N` с меткой типа |
| 2 | `grill-me #N`, если есть открытые решения | Решения приняты; новые термины, ADR и исследования записаны |
| 3 | Ветка `<N>-<change>`, `openspec-propose <change>` — в той же сессии | `proposal.md`, дельты спек, `design.md`, `tasks.md` |
| 4 | Прочитать `proposal.md` и дельты, `commit` | План согласован до кода |
| 5 | Новая сессия, `tdd`, `openspec-apply-change <change>` | Код, отмеченные задачи |
| 6 | `openspec-verify-change <change>` | Код соответствует спеке |
| 7 | `code-review main openspec/changes/<change>` | Замечания исправлены |
| 8 | `openspec-archive-change <change>`, `ghpr` | Спеки обновлены, изменение в архиве, PR открыт, CI зелёный |
| 9 | `ghm` | PR влит, issue закрыт, ветка удалена |

Спеки и код попадают в `main` одним PR: архивация — его последний коммит.

## Другие типы issue

| Тип | Как |
|---|---|
| `bug` | `tdd`: сначала тест, который воспроизводит баг, потом исправление → `ghpr` → `ghm`. Если в спеке не хватает требования — как новая возможность |
| `chore`, `documentation` | `ghpr` → `ghm`, без OpenSpec |
| Чужой issue | Поставь метку типа, дальше — по типу |

Большая неясная работа на несколько сессий — сначала интервью (`grill-me`), потом разбиение на sub-issue: каждая — одно изменение OpenSpec в своей ветке, её PR закрывает её через `Fixes`. Параллельная работа — каждая ветка в своём worktree.

## Интервью: `grill-me`

`grill-me` расспрашивает, пока все решения не приняты. Когда ты подтвердил итог, агент по правилам `AGENTS.md` записывает новые термины в `GLOSSARY.md`, решения — в `docs/adr/`, исследования — в `docs/research/`. Записывается только то, от чего зависит код или поведение Spook; правила — в конце `GLOSSARY.md` и в `README.md` этих папок.

## Скиллы

| Когда | Скиллы |
|---|---|
| В цепочке | `gh`, `ghpr`, `ghm`, `commit`, `grill-me`, `tdd`, `code-review`, `openspec-propose`, `openspec-apply-change`, `openspec-verify-change`, `openspec-archive-change` |
| По ситуации | `openspec-update-change`, `openspec-sync-specs` |
| Сами не вызываются | `grilling` — его читает `grill-me`; `codebase-design` — `tdd` |
| Не используем | Остальные скиллы `mattpocock/skills`: у проекта своя цепочка на OpenSpec. `grill-with-docs` и `domain-modeling` — правила словаря, ADR и исследований свои, в репозитории. `setup-matt-pocock-skills` не нужен: `code-review` берёт трекер из `AGENTS.md`. `openspec-explore` — пока вместо него `grill-me` |

## Проверки и merge

- CI на каждый PR: быстрые тесты и `openspec validate --all --strict`.
- В `main` — только через PR с зелёным CI и только squash.
- Вливай через `ghm`: он собирает сообщение squash-коммита из коммитов ветки, без повторов трейлеров. Кнопка на сайте подставит сообщения коммитов как есть, с повторами.
- Апрув не нужен: GitHub не даёт одобрить свой PR.
