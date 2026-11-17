# Как работать в Spook

Spook ведёт один человек вместе с агентами разработки (Claude Code, opencode, Codex…). Каждый шаг — скилл: агент выбирает его сам по тому, о чём ты просишь. Правила для агентов — в `AGENTS.md`.

## После клона

Скиллы агентов разработки приходят с клоном, в `.agents/skills/`: OpenSpec, `grill-me`, `tdd`, `code-review` и `git` — коммит, push, PR, merge и issue. opencode и Codex читают эту папку сами. Claude Code — нет: поставь у себя ссылки `.claude/skills/<имя> -> ../../.agents/skills/<имя>`. Глобальный скилл с тем же именем в `~/.claude/skills/` перекрывает проектный.

Нужен OpenSpec 2.0, как в CI. Скиллы OpenSpec обновляет `openspec update`, изменения коммитятся. Перед ним задай профиль, иначе он удалит `openspec-verify-change`:

```bash
openspec config set profile custom
openspec config set workflows '["propose","explore","apply","update","sync","archive","verify"]'
```

OpenSpec 2.1 и новее по умолчанию ставит скиллы глобально — при переходе на него оставь их в проекте.

## Путь задачи

```mermaid
flowchart TD
    idea["Идея"] -->|"заведи задачу"| issue["Issue с типом"]
    issue --> open{"Есть открытые решения?"}
    open -->|да| talk["grill-me или openspec-explore"]
    open -->|нет| type{"Тип issue"}
    talk --> type

    type -->|feature| propose["openspec-propose"]
    propose --> approve["прочитай proposal и дельты спек, commit"]
    approve --> apply["новая сессия: tdd, openspec-apply-change"]
    apply --> verify["openspec-verify-change"]
    verify --> review["code-review"]
    review --> archive["openspec-archive-change"]
    archive --> pr["git: PR → merge"]

    type -->|bug| fix["tdd: тест на баг, потом исправление"]
    fix --> pr

    type -->|"chore, documentation"| edit["правка"]
    edit --> pr

    type -->|research| research["статья по docs/research/README.md"]
    research --> pr

    type -->|epic| split["разбей на sub-issue"]
    split -.->|"каждая sub-issue в новой сессии"| type
```

- Тип ставит агент разработки, когда заводит issue. После обсуждения задача больше чем на один PR становится `epic`.
- `grill-me` — когда варианты понятны и решения надо принять. `openspec-explore` — когда задачу ещё надо понять.
- Работаешь параллельно — каждая ветка в своём worktree.

## Merge

- В `main` попадают только PR с зелёным CI, через squash. Апрув не нужен.
- Вливай через скилл `git` («влей PR»), а не кнопкой на сайте: кнопка повторит трейлеры в сообщении коммита.
