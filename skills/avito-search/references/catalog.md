# Local catalog / локальная подборка

All commands below run from the shopping project, with the same `AVITO_HOME`
if one was explicitly set. Replace `<scripts>` with this skill's absolute
`scripts` directory. Python uses only its standard library.

```sh
python3 <scripts>/catalog.py init
python3 <scripts>/catalog.py profile
python3 <scripts>/catalog.py profile /absolute/my-profile.json
python3 <scripts>/catalog.py new 100001 100002
python3 <scripts>/catalog.py list
python3 <scripts>/catalog.py show 1
python3 <scripts>/catalog.py feedback 1 rejected "Причина пользователя"
```

Numbers in this document are synthetic examples, not real listings.
`init` creates an empty database and `{"categories": {}, "preferences": {}}`.
It does not replace an existing profile. The agent can save the user's stated
criteria in those objects; there is no preset category, size, style or city.

## Batch format

Create a local `batch.json` beside its incoming photographs. Use an actual
Avito listing URL and ID from the browser. This is a schema example, not a
candidate to import unchanged:

```json
{
  "title": "Подборка по моему запросу",
  "brief": "Критерии текущего пользователя",
  "timezone": "UTC",
  "items": [
    {
      "id": "100001",
      "url": "https://www.avito.ru/example/odezhda_obuv_aksessuary/item_100001",
      "title": "Название с реальной страницы",
      "price": "Цена с реальной страницы",
      "photos": [
        {"path": "photos/01.png", "source": "gallery_screenshot"}
      ],
      "size": {"card": "Как указано", "marking": "Не подтверждено", "measurements": "Неизвестно"},
      "material": "Текст и источник, без догадки",
      "condition": "Что видно на странице и фото",
      "seller": "Публичное имя со страницы",
      "seller_assessment": "Наблюдаемые признаки, без домыслов",
      "listing": {"published_text": "Как показано", "views": "Как показано", "checked_at": "Фактическое ISO-время", "source": "Страница объявления"},
      "availability": {"status": "unknown"},
      "fit": {"status": "unknown"},
      "decision": "unreviewed"
    }
  ]
}
```

For an actually observed active listing, set `availability.status` to
`available` and provide the real ISO timestamp `checked_at` and a brief
`evidence`. Do not mark a listing active just to make it appear in HTML.
The renderer hides availability observations older than 30 minutes; refresh
the observation before the final shortlist.

Accepted photo sources: `original` and `gallery_screenshot`. Never label a
screenshot as the seller's original file. Photo paths must resolve inside
the incoming JSON's directory or `AVITO_HOME/media`; paths escaping through
`..` or symlinks are rejected. A reference uses `reference.photos` with
`source: reference_frame` and an actual local file.

```sh
python3 <scripts>/catalog.py import /absolute/incoming/batch.json --category shirts --batch first-search
python3 <scripts>/catalog.py render first-search
```

The returned `gallery` path is a local `index.html`. Numbers remain stable;
a fresh observation does not erase the user's choice. `feedback` refreshes
an already-rendered batch. A `gallery_refresh_failed` field means the decision
is saved but the page is stale: repeat `render`, not the decision.

The HTML is a view, not a feedback editor. Ask the user to reply with a number
or listing ID, then record that answer with the CLI. `kept`/`maybe` describe
taste; neither proves fit or stock. Never create user feedback yourself.

