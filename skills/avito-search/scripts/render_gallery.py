#!/usr/bin/env python3
"""Render a local clothing comparison from an agent-maintained JSON catalog."""

import argparse
import hashlib
import html
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from catalog import listing_url, photo_path


AVAILABLE = {"available", "reserved", "removed", "sold", "unknown"}
AVAILABILITY_LABELS = {
    "available": "Объявление активно",
    "reserved": "Забронировано",
    "removed": "Объявление снято",
    "sold": "Продано",
    "unknown": "Статус объявления не подтвержден",
}
DECISIONS = {"unreviewed", "kept", "maybe", "rejected"}
FIT_LABELS = {
    "unknown": "Посадка пока неизвестна",
    "candidate": "Кандидат по силуэту; посадка не подтверждена",
    "measurements_match": "Замеры совпадают с ориентиром",
    "tried_on": "Посадка проверена примеркой",
}
DECISION_LABELS = {
    "unreviewed": "Еще не оценено",
    "kept": "Оставить",
    "maybe": "Под вопросом",
    "rejected": "Отклонено",
}
PHOTO_LABELS = {
    "original": "Фото из объявления",
    "gallery_screenshot": "Снимок галереи объявления",
}


def text(value):
    return "Неизвестно" if value is None or value == "" else str(value)


def esc(value):
    return html.escape(text(value), quote=True)


def display_time(value, tz):
    if not value:
        return "Неизвестно"
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return text(value) + " (формат времени не распознан)"
    if parsed.tzinfo is None:
        return parsed.strftime("%d.%m.%Y, %H:%M") + " (часовой пояс не указан)"
    converted = parsed.astimezone(tz)
    offset = converted.strftime("%z")
    label = "UTC" if offset == "+0000" else f"UTC{offset[:3]}:{offset[3:]}"
    return converted.strftime("%d.%m.%Y, %H:%M") + f" ({label})"


def validate_photos(photos, source_dir, sources, owner):
    for photo in photos:
        if photo.get("source") not in sources:
            raise ValueError(f"Photo source required: {owner}")
        path = photo_path(photo, source_dir)
        if not path.is_file() or path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif"}:
            raise ValueError(f"Missing or unsupported photograph: {owner} {path}")
        if not path.stat().st_size:
            raise ValueError(f"Empty photograph: {owner} {path}")


def validate_reference(reference, source_dir):
    if reference is not None:
        if not reference.get("photos"):
            raise ValueError("A supplied reference requires real photographs")
        validate_photos(reference["photos"], source_dir, {"reference_frame"}, "reference")


def validate(catalog, source_dir):
    items = catalog["items"]
    validate_reference(catalog.get("reference"), source_dir)
    ids, numbers = set(), set()
    for item in items:
        item_id, number = str(item["id"]), item["number"]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", item_id):
            raise ValueError(f"Invalid item id: {item_id}")
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            raise ValueError(f"Positive integer number required: {item_id}")
        if item_id in ids or number in numbers:
            raise ValueError(f"Duplicate id or stable number: {item_id}")
        ids.add(item_id)
        numbers.add(number)
        item["url"] = listing_url(item["url"], item_id)
        availability = item.get("availability", {"status": "unknown"})
        if availability["status"] not in AVAILABLE:
            raise ValueError(f"Unknown availability status: {item_id}")
        if availability["status"] == "available" and not all(
            availability.get(field) for field in ("checked_at", "evidence")
        ):
            raise ValueError(f"Availability evidence required: {item_id}")
        if item.get("decision", "unreviewed") not in DECISIONS:
            raise ValueError(f"Unknown decision: {item_id}")
        fit = item.get("fit", {"status": "unknown"})
        if fit["status"] not in FIT_LABELS:
            raise ValueError(f"Unknown fit status: {item_id}")
        if fit["status"] in {"measurements_match", "tried_on"} and not fit.get("reason"):
            raise ValueError(f"Fit evidence required: {item_id}")
        item["id"] = item_id
        item["availability"] = availability
        item["fit"] = fit
        item.setdefault("decision", "unreviewed")
        if availability["status"] == "available" and not item.get("photos"):
            raise ValueError(f"Photographs required for an available item: {item_id}")
        validate_photos(item.get("photos", []), source_dir, PHOTO_LABELS, item_id)
        validate_reference(item.get("reference"), source_dir)


def facts(item, tz):
    size, delivery, available = item.get("size", {}), item.get("delivery", {}), item["availability"]
    listing = item.get("listing", {})
    confirmed = "Наличие подтверждено продавцом" if available.get("seller_confirmed") is True else "Наличие у продавца не подтверждено"
    return [
        ("Цена объявления", item.get("price")),
        ("Предложенная цена", item.get("offer_price")),
        ("Согласованная цена", item.get("agreed_price")),
        ("Размер в карточке", size.get("card")),
        ("Маркировка на фото", size.get("marking")),
        ("Замеры, см", size.get("measurements")),
        ("Материал, источник", item.get("material")),
        ("Состояние, источник", item.get("condition")),
        ("Посадка", FIT_LABELS[item["fit"]["status"]]),
        ("Основание посадки", item["fit"].get("reason")),
        ("Твой выбор", DECISION_LABELS[item["decision"]]),
        ("Твоя оценка", item.get("feedback")),
        ("Продавец", item.get("seller")),
        ("Оценка продавца", item.get("seller_assessment")),
        ("Доставка", delivery.get("method")),
        ("Примерка", delivery.get("fitting")),
        ("Оплата", delivery.get("payment")),
        ("Стоимость отказа", delivery.get("refusal_cost")),
        ("Источник условий", delivery.get("source")),
        ("Статус страницы", AVAILABILITY_LABELS[available["status"]]),
        ("Проверка страницы", display_time(available.get("checked_at"), tz)),
        ("Основание доступности", available.get("evidence")),
        ("Наличие", confirmed),
        ("Публикация объявления, как на Авито", listing.get("published_text")),
        ("Просмотры всего", listing.get("views")),
        ("Просмотры сегодня", listing.get("views_today")),
        ("Время чтения даты и просмотров", display_time(listing.get("checked_at"), tz)),
        ("Источник даты и просмотров", listing.get("source")),
    ]


def save_reference(reference, source_dir, output_dir):
    if reference is None:
        return
    folder = output_dir / "reference"
    folder.mkdir(parents=True, exist_ok=True)
    for photo in reference["photos"]:
        source = photo_path(photo, source_dir)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        destination = folder / f"{digest}{source.suffix.lower()}"
        if source.resolve() != destination.resolve() and not destination.exists():
            shutil.copy2(source, destination)
        photo["path"] = destination.relative_to(output_dir).as_posix()


def save_item(item, source_dir, output_dir, tz):
    folder = output_dir / "items" / item["id"]
    folder.mkdir(parents=True, exist_ok=True)
    for number, photo in enumerate(item.get("photos", []), 1):
        source = photo_path(photo, source_dir)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()[:12]
        filename = f"{number:02d}-{digest}{source.suffix.lower()}"
        destination = folder / filename
        if source.resolve() != destination.resolve():
            shutil.copy2(source, destination)
        photo["path"] = destination.relative_to(output_dir).as_posix()
    lines = [f'# {item["number"]}. {item["title"]}', "", f'Ссылка: {item["url"]}', ""]
    for label, value in facts(item, tz):
        lines.extend([f"{label}: {text(value)}", ""])
    lines.extend(["## Сходство и сомнения", "", text(item.get("note")), "", "## Описание продавца", "", text(item.get("description")), ""])
    for number, photo in enumerate(item.get("photos", []), 1):
        filename = Path(photo["path"]).name
        lines.extend([f'![Фото {number}]({filename})', "", f'{PHOTO_LABELS[photo["source"]]}. {photo.get("caption", "")}', ""])
    (folder / "README.md").write_text("\n".join(lines), encoding="utf-8")


def photo_gallery(photos, title):
    images = []
    for number, photo in enumerate(photos, 1):
        url = quote(photo["path"], safe="/")
        caption = photo.get("caption") or f"Фото {number}"
        images.append(f'<figure><a href="{esc(url)}" aria-label="Увеличить фото {number}"><img loading="lazy" src="{esc(url)}" alt="{esc(title + ": " + caption)}"></a></figure>')
    return '<div class="photos">' + ''.join(images) + '</div>'


def card(item, reference, tz):
    summary = item.get("summary", {})
    listing = item.get("listing", {})
    rows = [
        ("Выложено", summary.get("published", listing.get("published_text"))),
        ("Просмотры", f"{text(listing.get('views'))} (+{text(listing.get('views_today'))} сегодня)"),
        ("Продавец", summary.get("seller", item.get("seller"))),
        ("Состояние", summary.get("condition", item.get("condition"))),
        ("Размер", summary.get("size", item.get("size", {}).get("card"))),
        ("Замеры", summary.get("measurements", item.get("size", {}).get("measurements"))),
        ("Материал", summary.get("material", item.get("material"))),
        ("Примерка", summary.get("fitting", item.get("delivery", {}).get("fitting"))),
        ("Доставка", summary.get("delivery", item.get("delivery", {}).get("method"))),
    ]
    facts_html = "".join(f"<dt>{esc(label)}</dt><dd>{esc(value)}</dd>" for label, value in rows)
    photo_hint = f"{len(item.get('photos', []))} фото · листай вбок · нажми для увеличения" if len(item.get("photos", [])) > 1 else "1 фото · нажми для увеличения"
    compare = ""
    if reference is not None:
        compare = f'<details><summary>Сравнить с ориентиром</summary><p class="muted">{esc(reference.get("note"))}</p><div class="comparison"><section>{photo_gallery(item.get("photos", [])[:1], item["title"])}</section><section>{photo_gallery(reference["photos"], reference.get("title", "Ориентир"))}</section></div></details>'
    return f'''<article id="item-{item['number']}">
<div class="card-head"><div><h2>{item['number']}. {esc(item['title'])}</h2><div class="price">{esc(item.get('price'))}</div></div><a class="open" href="{esc(item['url'])}" target="_blank" rel="noopener noreferrer">Открыть на Авито ↗</a></div>
<div class="item-body"><section>{photo_gallery(item.get('photos', []), item['title'])}<p class="muted">{esc(photo_hint)}</p></section><section><dl class="compact-facts" title="Проверено {esc(display_time(listing.get('checked_at'), tz))}">{facts_html}</dl></section></div>
<details><summary>Описание продавца</summary><p>{esc(item.get('description'))}</p></details>
{compare}
</article>'''


STYLE = """
body{margin:0;background:#f3f0e9;color:#242420;font:17px/1.55 system-ui,sans-serif}
main{max-width:1240px;margin:auto;padding:32px}header{margin-bottom:32px}
h1{font-size:clamp(28px,4vw,43px);line-height:1.15}h2{font-size:25px;line-height:1.35;margin:0}
a{color:inherit;text-underline-offset:3px}article{background:white;border:1px solid #e4e0d8;border-radius:16px;padding:25px;margin-bottom:30px}
.price{font-size:25px;font-weight:700}.note,.availability{white-space:pre-wrap}.choice,.fit{font-weight:600}
.availability{font-size:15px;color:#5b5a52}.warning{border-left:4px solid #ac6420;padding:16px;background:#fff2df}
.photos{display:flex;gap:16px;overflow-x:auto;scroll-snap-type:x mandatory;padding-bottom:12px}
figure{margin:0;flex:0 0 100%;scroll-snap-align:start;min-width:0}.photos a{cursor:zoom-in;display:block}
.photos img{display:block;width:100%;height:78vh;object-fit:contain;object-position:top;background:#f8f8f7}
.comparison{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:20px;align-items:start}
.comparison section{min-width:0}.comparison h3{font-size:20px;margin:12px 0}.reference p{font-size:15px;white-space:pre-wrap}
.listing-info{background:#f4f2ed;border-radius:10px;padding:8px 16px;margin:15px 0;font-size:16px}.listing-info p{margin:8px 0}
figcaption{font-size:14px;color:#65645b;margin-top:9px}dl{display:grid;grid-template-columns:minmax(170px,1fr) 3fr;gap:10px 24px}
dt{font-weight:600}dd{margin:0;white-space:pre-wrap;overflow-wrap:anywhere}details p{white-space:pre-wrap}
summary{cursor:pointer}dialog{border:0;padding:0;background:transparent;max-width:96vw;max-height:96vh;overflow:visible}
dialog::backdrop{background:#000e}dialog img{display:block;max-width:96vw;max-height:92vh;object-fit:contain}
dialog button{position:absolute;top:10px;right:10px;width:46px;height:46px;font-size:32px;border:0;border-radius:50%;cursor:pointer;background:white;color:#222}
@media(max-width:640px){main{padding:14px}article{padding:16px}h2{font-size:22px}dl{grid-template-columns:1fr;gap:3px}dd{margin-bottom:10px}}
@media(max-width:850px){.comparison{grid-template-columns:minmax(0,1fr)}}
"""
SCRIPT = """
const viewer = document.getElementById('viewer');
document.querySelectorAll('.photos a').forEach(link => link.addEventListener('click', event => {
  event.preventDefault();
  const enlarged = viewer.querySelector('img');
  enlarged.src = link.href;
  enlarged.alt = link.querySelector('img').alt;
  viewer.showModal();
}));
viewer.querySelector('button').addEventListener('click', () => viewer.close());
viewer.addEventListener('click', event => { if (event.target === viewer) viewer.close(); });
"""

STYLE += """
main{max-width:1160px;padding:22px}header{margin-bottom:18px}h1{font-size:30px;margin:0 0 8px}h2{font-size:22px}
article{padding:20px;margin-bottom:24px}.card-head{display:flex;justify-content:space-between;align-items:center;gap:16px;position:sticky;top:0;background:white;z-index:2;padding:6px 0}
.price{font-size:24px;margin:3px 0}.open{background:#242420;color:white;padding:10px 16px;border-radius:8px;text-decoration:none;white-space:nowrap;font-weight:600}
.meta{font-size:14px;margin:8px 0 14px;color:#62625c}.item-body{display:grid;grid-template-columns:minmax(0,1.8fr) minmax(0,1fr);gap:24px}.item-body>section{min-width:0}
.photos img{height:min(46vh,440px)}figcaption{font-size:12px;margin-top:3px}.muted{font-size:13px;color:#62625c;margin:8px 0}
.compact-facts{grid-template-columns:85px 1fr;font-size:14px;line-height:1.45;gap:9px 12px;margin:0}.note{font-size:14px;line-height:1.45;padding:10px;background:#f4f2ed;border-radius:8px}
details{border-top:1px solid #e4e0d8;padding:10px 0;font-size:14px}details p{margin:10px 0}.audit{color:#62625c;font-size:12px}
@media(max-width:760px){main{padding:12px}.item-body{grid-template-columns:1fr;gap:10px}.card-head{align-items:flex-start}h2{font-size:18px}.open{font-size:13px;padding:9px}.photos img{height:38vh}.compact-facts{grid-template-columns:80px 1fr}article{padding:14px}}
"""


def render(catalog, source_dir, output_dir):
    validate(catalog, source_dir)
    if not catalog.get("created_at"):
        existing = output_dir / "_catalog.json"
        previous = json.loads(existing.read_text(encoding="utf-8")) if existing.is_file() else {}
        if not isinstance(previous, dict):
            previous = {}
        catalog["created_at"] = previous.get("created_at")
        if not existing.exists() and not (output_dir / "index.html").exists():
            catalog["created_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    catalog["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        tz = ZoneInfo(catalog.get("timezone") or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        tz = timezone.utc
    output_dir.mkdir(parents=True, exist_ok=True)
    save_reference(catalog.get("reference"), source_dir, output_dir)
    for item in catalog["items"]:
        save_reference(item.get("reference"), source_dir, output_dir)
        save_item(item, source_dir, output_dir, tz)
    active = sorted((item for item in catalog["items"] if item["availability"]["status"] == "available" and item["decision"] != "rejected"), key=lambda item: item["number"])
    warning = f'<p class="warning">{esc(catalog.get("refresh_note", "Требуется обновить доступность объявлений."))}</p>' if catalog.get("needs_refresh") else ""
    count = f"Новых кандидатов: {len(active)}"
    dates = f'Создана: {display_time(catalog.get("created_at"), tz)} · Обновлена: {display_time(catalog["updated_at"], tz)}'
    document = f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(catalog['title'])}</title><style>{STYLE}</style></head><body>
<main><header><h1>{esc(catalog['title'])}</h1>{warning}</header>
{''.join(card(item, item.get('reference', catalog.get('reference')), tz) for item in active) if active else '<p>Подтвержденных активных кандидатов пока нет.</p>'}
<p class="muted">{esc(dates)}. Данные относятся к моменту проверки страницы; наличие у продавца и посадка не подтверждены.</p></main><dialog id="viewer"><button type="button" aria-label="Закрыть фото">×</button><img alt=""></dialog><script>{SCRIPT}</script></body></html>'''
    (output_dir / "index.html").write_text(document, encoding="utf-8")
    (output_dir / "_catalog.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [f'# {catalog["title"]}', "", text(catalog.get("brief")), "", count, "", dates, "", "[Открыть галерею](index.html)", ""]
    if catalog.get("needs_refresh"):
        lines.extend(["Требуется обновить доступность: " + text(catalog.get("refresh_note")), ""])
    for item in active:
        lines.extend([f'{item["number"]}. [{item["title"]}](items/{item["id"]}/README.md) · {text(item.get("price"))} · {DECISION_LABELS[item["decision"]]}', ""])
    (output_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"gallery": str(output_dir / "index.html"), "shown": len(active), "stored": len(catalog["items"])}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("catalog", type=Path, help="JSON with title, brief and items")
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    catalog_path = args.catalog.resolve()
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    try:
        render(catalog, catalog_path.parent, args.output_dir.resolve())
    except (KeyError, TypeError, ValueError, OSError) as error:
        parser.exit(2, f"Cannot render gallery: {error}\n")


if __name__ == "__main__":
    main()
