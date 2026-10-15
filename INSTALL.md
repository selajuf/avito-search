# Установка / Installation

Для пользователя достаточно [короткого запроса из README](README.md#самый-простой-способ).
Ниже порядок действий для агента. Он не требует AI Wiki или чужого каталога.

## Агенту

1. Уточни папку, в которой пользователь хочет вести поиск. Если она уже задана
   текущим проектом, используй её. Не подменяй профиль пользователя примерами
   автора и не читай соседние личные каталоги.
2. Склонируй `https://github.com/selajuf/avito-search.git` в отдельную папку.
   Прочитай `install.py`, `skills/avito-search/SKILL.md` и инструкцию браузера.
   Выполняй скачивание и запуск в рамках разрешений своей среды.
3. Запусти `python3 install.py --project /absolute/shopping-project`.
   По умолчанию используется общая `.agents/skills`. Для Claude Code добавь
   `--host claude`; это меняет только папку назначения на `.claude/skills`.
   Установщик не перезаписывает отличающийся существующий skill.
4. Перейди в shopping-project. Подготовь зависимости через
   `python3 <installed-skill>/scripts/setup_browser.py`.
   Если Python/Git/Bun отсутствует, объясни, что нужно установить; не запускай
   произвольные install-скрипты с повышенными правами молча.
5. Открой проект в агенте. Если skill не появился, обнови список skills или
   начни новую сессию. Для совместимого агента без автоматического discovery
   попроси его прочитать установленный `SKILL.md` напрямую.
6. Инициализируй каталог, явно запусти видимый браузер и узнай вкладки:

```sh
python3 <installed-skill>/scripts/catalog.py init
python3 <installed-skill>/scripts/browser.py start
python3 <installed-skill>/scripts/browser.py tabs
```

7. Если Авито требует вход или CAPTCHA, дай пользователю пройти это вручную.
   Спросить критерии поиска можно уже до входа. Не копируй cookies из другого
   браузера и не переключай VPN для обхода ограничения.
8. Прочитай пустой или существующий локальный профиль, спроси запрос и начни
   подбор. Не выдавай успешную установку за проверенный результат поиска.

## Совместимость путей

| Среда | Папка проекта | Подтверждение формата |
| --- | --- | --- |
| Codex | `.agents/skills/avito-search` | [OpenAI docs](https://learn.chatgpt.com/docs/build-skills) |
| OpenCode | `.agents/skills/avito-search` | [OpenCode docs](https://opencode.ai/docs/skills/) |
| Antigravity | `.agents/skills/avito-search` | [Google docs](https://antigravity.google/docs/skills) |
| Claude Code | `.claude/skills/avito-search` | Явный выбор `--host claude` |
| Другой локальный агент | Любая папка с целым skill | Прочитать `SKILL.md` напрямую, предоставить терминал |

Поддержка формата установки не доказывает запуск каждой модели или IDE.
Список выполненных проверок находится в `docs/VERIFICATION.md`.

## Быстрая диагностика

- **Skill не виден:** проверь целевую папку, `SKILL.md` и перезапусти сессию.
- **Не установлен Bun:** установи его по [официальной инструкции](https://bun.sh/docs/installation), затем повтори подготовку браузера.
- **Нет браузерного runtime:** запусти `setup_browser.py` из установленного skill.
- **Браузер закрыт:** явно вызови `browser.py start`. Обычные команды сами его не запускают.
- **CAPTCHA/ограничение доступа:** остановись; ручное разрешение в видимой вкладке, без автоматического обхода.
- **Пустая галерея:** проверь доступность и время проверки кандидатов. Старые или отклонённые карточки скрыты, данные остаются в базе.
- **Отказ сохранился, галерея не обновилась:** повтори только `catalog.py render <batch>`.

## For English-speaking agents

Clone the repository, review the installer and skill, and run
`python3 install.py --project /absolute/shopping-project`. Open that project
in the host. Install browser dependencies with the installed skill's
`scripts/setup_browser.py`. Run `catalog.py init`, explicitly `browser.py start`,
then discover the current tabs. Load the skill and ask the user what to find.

Keep an existing installation intact. Do not change global agent settings,
reuse personal browser sessions, import cookies or silently work around an
access restriction. Local state goes into the shopping project's
`.avito-search/`, or an explicit `AVITO_HOME`. The base skill uses no model API
key of its own; the user provides their existing agent/model access.

