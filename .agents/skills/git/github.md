# GitHub: issue, просмотр, прочее

## Чтение

- Запрашивай только нужные поля: `gh pr list --json number,title,state,headRefName --jq '...'`, `gh pr view <n> --json title,body,state,reviews,statusCheckRollup`.
- Дифф: `gh pr diff <n>`. Комментарии и ревью: `gh pr view <n> --comments`.
- CI: `gh run list -L 5`, затем `gh run view <id> --log-failed` (не полный `--log`).
- Всё, для чего нет подкоманды, — через `gh api`: REST `gh api repos/{owner}/{repo}/...` с `--paginate` и `--jq`, GraphQL `gh api graphql -f query='...'`. `gh issue view` в `gh` 2.45 падает на устаревших Projects (classic) — тогда `gh api repos/{owner}/{repo}/issues/<n> --jq .body`.

## Запись

Только то, о чём просит пользователь: issue, комментарий, правка, закрытие, релиз.

- Длинный текст (тело issue, комментарий) — во временный файл и `--body-file <файл>`, не `--body "..."`.
- Не удалять ветки, релизы, теги, не менять настройки, секреты и участников репозитория без прямой просьбы.
- После записи показать ссылку (`gh` её печатает).

## Issue

Правила issue в `AGENTS.md` корня репозитория (типы, метки, текст, sub-issue, связи) важнее этого раздела.

- Тип — ровно одна метка: `labels:` подходящего шаблона из `.github/ISSUE_TEMPLATE/`. Тело — разделы этого шаблона без YAML-шапки и HTML-комментариев.
- Одна проблема на issue, на один экран: что не так и почему это важно; у бага — шаги воспроизведения. Решений, требований и плана в issue нет: они живут в документах и спеках, а две копии расходятся.
- Заголовок — на языке шаблонов, терминами `GLOSSARY.md`, если он есть.
- Sub-issue и блокировки: `gh` 2.45 не знает `--parent` и `--blocked-by` — через REST: `gh api -X POST repos/{owner}/{repo}/issues/<родитель>/sub_issues -F sub_issue_id=<id>`, `gh api -X POST repos/{owner}/{repo}/issues/<задача>/dependencies/blocked_by -F issue_id=<id>`, где `<id>` — не номер, а `gh api repos/{owner}/{repo}/issues/<номер> --jq .id`.
- Закрыть без PR: сначала комментарий с причиной, затем `gh issue close`.
