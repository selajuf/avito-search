#!/usr/bin/env python3
"""Local Avito catalog. SQLite owns identities and human decisions."""
import argparse
import copy
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(os.environ.get("AVITO_HOME", Path.cwd() / ".avito-search")).expanduser().resolve()
SCRIPTS = Path(__file__).resolve().parent
DECISIONS = {"unreviewed", "kept", "maybe", "rejected"}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def listing_url(url, item_id=None):
    if not isinstance(url, str) or any(ord(char) <= 32 for char in url):
        raise ValueError("Некорректная ссылка объявления")
    p = urlsplit(url)
    if p.scheme != "https" or p.hostname not in {"avito.ru", "www.avito.ru"} or p.username or p.password or p.port:
        raise ValueError("Нужна HTTPS-ссылка объявления Авито")
    if "/odezhda_obuv_aksessuary/" not in p.path:
        raise ValueError("Нужна ссылка вещи, а не профиля или действия")
    found = p.path.rsplit("_", 1)[-1]
    if not re.fullmatch(r"[0-9]{6,14}", found) or (item_id is not None and found != str(item_id)):
        raise ValueError("ID в ссылке не совпадает с карточкой")
    return urlunsplit(("https", "www.avito.ru", p.path, "", ""))


def seller_url(url):
    if not isinstance(url, str) or any(ord(char) <= 32 for char in url):
        raise ValueError("Некорректная ссылка продавца")
    p = urlsplit(url)
    if p.scheme != "https" or p.hostname not in {"avito.ru", "www.avito.ru"} or p.username or p.password or p.port or not p.path.startswith(("/brands/", "/user/")):
        raise ValueError("Нужна публичная ссылка продавца Авито")
    return urlunsplit(("https", "www.avito.ru", p.path.rstrip("/"), "", ""))


@contextmanager
def database(root=ROOT):
    (root / "data").mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / "data/catalog.sqlite", timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS listings(
      id TEXT PRIMARY KEY, number INTEGER UNIQUE NOT NULL, category TEXT NOT NULL,
      payload TEXT NOT NULL, decision TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
      observed_at TEXT NOT NULL, shown_at TEXT);
    CREATE TABLE IF NOT EXISTS events(
      seq INTEGER PRIMARY KEY, id TEXT NOT NULL REFERENCES listings(id),
      kind TEXT NOT NULL, value TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS batches(key TEXT PRIMARY KEY, metadata TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS batch_items(
      batch TEXT NOT NULL REFERENCES batches(key), id TEXT NOT NULL REFERENCES listings(id),
      PRIMARY KEY(batch,id));
    CREATE TABLE IF NOT EXISTS blocked_sellers(url TEXT PRIMARY KEY, reason TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS counters(key TEXT PRIMARY KEY, value INTEGER NOT NULL);
    INSERT OR IGNORE INTO counters VALUES('next_number',1);
    """)
    try:
        with db:
            yield db
    finally:
        db.close()


def photo_path(photo, source_dir, root=ROOT):
    src = Path(photo["path"])
    if not src.is_absolute():
        src = Path(source_dir) / src
    src = src.resolve()
    allowed = (Path(source_dir).resolve(), Path(root).resolve() / "media")
    if not any(src.is_relative_to(base) for base in allowed):
        raise ValueError("Фото должно находиться в папке JSON или AVITO_HOME/media")
    return src


def save_photo(photo, source_dir, root=ROOT, reference=False):
    photo = copy.deepcopy(photo)
    src = photo_path(photo, source_dir, root)
    if not src.is_file() or src.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif"} or src.stat().st_size == 0:
        raise ValueError(f"Фото отсутствует или пустое: {src}")
    content = src.read_bytes()
    dest = root / "media" / ("references" if reference else "photos") / (hashlib.sha256(content).hexdigest() + src.suffix.lower())
    if not dest.resolve().is_relative_to(Path(root).resolve() / "media"):
        raise ValueError("Папка media выходит за пределы хранилища")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        dest.write_bytes(content)
    photo["path"] = str(dest.relative_to(root))
    photo.setdefault("source", "reference_frame" if reference else "original")
    return photo


def local_reference(ref, source_dir, root=ROOT):
    if ref is None:
        return None
    ref = copy.deepcopy(ref)
    ref["photos"] = [save_photo(p, source_dir, root, True) for p in ref.get("photos", [])]
    return ref


def normalize(item, source_dir, root=ROOT):
    item = copy.deepcopy(item)
    item["id"] = str(item["id"])
    item["url"] = listing_url(item["url"], item["id"])
    if not item.get("title"):
        raise ValueError("Название карточки обязательно")
    if not isinstance(item.get("size", {}), dict):
        item["size"] = {"card": str(item["size"])}
    item.setdefault("availability", {"status": "unknown", "checked_at": item.get("checked"), "evidence": "Доступность не проверена"})
    item.setdefault("fit", {"status": "unknown"})
    item.setdefault("decision", "unreviewed")
    if item["decision"] not in DECISIONS:
        raise ValueError("Неизвестное решение")
    availability = item["availability"]
    if not isinstance(availability, dict) or availability.get("status") not in {"available", "reserved", "removed", "sold", "unknown"}:
        raise ValueError("Неизвестный статус объявления")
    if availability["status"] == "available" and not all(availability.get(x) for x in ("checked_at", "evidence")):
        raise ValueError("Для активной карточки нужны время и основание проверки")
    photos = []
    for p in item.get("photos", []):
        if isinstance(p, str):
            p = {"path": str(Path(item.get("folder", "")) / p), "source": "original"}
        photos.append(save_photo(p, source_dir, root))
    item["photos"] = photos
    if availability["status"] == "available" and not photos:
        raise ValueError("Для активного объявления нужны настоящие фото")
    if item.get("reference"):
        item["reference"] = local_reference(item["reference"], source_dir, root)
    return item


def put(db, item, category):
    old = db.execute("SELECT * FROM listings WHERE id=?", (item["id"],)).fetchone()
    if old:
        item["number"] = old["number"]
        decision, reason, shown = old["decision"], old["reason"], old["shown_at"]
    else:
        number = db.execute("SELECT value FROM counters WHERE key='next_number'").fetchone()[0]
        item["number"] = number
        db.execute("UPDATE counters SET value=? WHERE key='next_number'", (number + 1,))
        decision, reason = item["decision"], item.get("feedback", "")
        shown = None
    db.execute("INSERT OR IGNORE INTO listings VALUES(?,?,?,?,?,?,?,?)", (item["id"], item["number"], category, json.dumps(item, ensure_ascii=False), decision, reason, now(), shown))
    item["decision"] = decision
    item["feedback"] = reason
    if item.get("seller_url"):
        item["seller_url"] = seller_url(item["seller_url"])
    db.execute("UPDATE listings SET payload=?,decision=?,reason=?,observed_at=? WHERE id=?", (json.dumps(item, ensure_ascii=False), decision, reason, now(), item["id"]))
    return item["number"]


def import_catalog(path, category, batch=None, root=ROOT):
    if not isinstance(category, str) or not category.strip():
        raise ValueError("Укажи категорию")
    path = Path(path).resolve()
    raw = json.loads(path.read_text())
    data = {"title": "Подборка Авито", "items": raw} if isinstance(raw, list) else raw
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ValueError("JSON должен содержать список items")
    batch = batch or path.parent.name
    if not batch or Path(batch).name != batch or batch in {".", ".."}:
        raise ValueError("Название партии должно быть одним именем папки")
    metadata = {k: v for k, v in data.items() if k not in {"items", "previous_catalogs", "search"}}
    metadata["reference"] = local_reference(data.get("reference"), path.parent, root)
    items = [normalize(x, path.parent, root) for x in data["items"]]
    with database(root) as db:
        db.execute("INSERT INTO batches VALUES(?,?) ON CONFLICT(key) DO UPDATE SET metadata=excluded.metadata", (batch, json.dumps(metadata, ensure_ascii=False)))
        for item in items:
            put(db, item, category)
            db.execute("INSERT OR IGNORE INTO batch_items VALUES(?,?)", (batch, item["id"]))
    return len(items)


def resolve(db, target, category=None):
    if re.fullmatch(r"[0-9]{6,14}", str(target)):
        row = db.execute("SELECT * FROM listings WHERE id=?", (str(target),)).fetchone()
        if row and (not category or category == row["category"]):
            return row
        raise ValueError("Такого ID в базе нет")
    number = int(target)
    row = db.execute("SELECT * FROM listings WHERE number=?", (number,)).fetchone()
    if row and (not category or row["category"] == category):
        return row
    raise ValueError("Номер не найден")


def feedback(target, decision, reason, category=None, block_seller=False, root=ROOT):
    if decision not in DECISIONS or not reason.strip():
        raise ValueError("Нужны решение и причина")
    with database(root) as db:
        db.execute("BEGIN IMMEDIATE")
        row = resolve(db, target, category)
        item = json.loads(row["payload"])
        if block_seller and not item.get("seller_url"):
            raise ValueError("Нет проверенной ссылки продавца; нельзя блокировать по имени")
        item.update(decision=decision, feedback=reason)
        db.execute("UPDATE listings SET decision=?,reason=?,payload=? WHERE id=?", (decision, reason, json.dumps(item, ensure_ascii=False), row["id"]))
        explicit = db.execute("SELECT 1 FROM events WHERE id=? AND kind='user-feedback'", (row["id"],)).fetchone()
        if row["decision"] != decision or row["reason"] != reason or not explicit:
            db.execute("INSERT INTO events(id,kind,value,created_at) VALUES(?,?,?,?)", (row["id"], "user-feedback", json.dumps({"decision": decision, "reason": reason}, ensure_ascii=False), now()))
        if block_seller:
            db.execute("INSERT INTO blocked_sellers VALUES(?,?) ON CONFLICT(url) DO UPDATE SET reason=excluded.reason", (seller_url(item["seller_url"]), reason))
        return {"id": row["id"], "number": row["number"], "decision": decision, "reason": reason}


def unseen(ids, root=ROOT):
    if any(not re.fullmatch(r"[0-9]{6,14}", str(x)) for x in ids):
        raise ValueError("Передай числовые ID объявлений")
    with database(root) as db:
        known = {r[0] for r in db.execute("SELECT id FROM listings")}
    return list(dict.fromkeys(str(x) for x in ids if str(x) not in known))


def render_batch(batch, root=ROOT):
    if not batch or Path(batch).name != batch or batch in {".", ".."}:
        raise ValueError("Название партии должно быть одним именем папки")
    dest = root / "galleries" / batch
    dest.resolve().relative_to((root / "galleries").resolve())
    with database(root) as db:
        # Keep human feedback serialized with publication, including the shown marker.
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT metadata FROM batches WHERE key=?", (batch,)).fetchone()
        if not row:
            raise ValueError("Партия не найдена")
        data = json.loads(row[0])
        rows = db.execute("SELECT l.* FROM listings l JOIN batch_items b ON b.id=l.id WHERE b.batch=? ORDER BY number", (batch,)).fetchall()
        blocked = {r[0] for r in db.execute("SELECT url FROM blocked_sellers")}
        data["items"] = [json.loads(r["payload"]) for r in rows if r["decision"] != "rejected" and (r["decision"] == "kept" or json.loads(r["payload"]).get("seller_url") not in blocked)]
        stale = False
        for item in data["items"]:
            available = item["availability"]
            if available["status"] == "available" and item["decision"] != "rejected":
                try:
                    checked = datetime.fromisoformat(available["checked_at"].replace("Z", "+00:00"))
                    age = (datetime.now(timezone.utc) - checked).total_seconds()
                    fresh = 0 <= age <= 1800
                except (KeyError, ValueError, TypeError):
                    fresh = False
                if not fresh:
                    available["status"] = "unknown"
                    stale = True
            for photo in item.get("photos", []):
                photo["path"] = str(root / photo["path"])
            if item.get("reference"):
                for p in item["reference"]["photos"]:
                    p["path"] = str(root / p["path"])
        data.update(title=data.get("title", "Подборка Авито"), timezone="UTC", needs_refresh=stale, refresh_note="Старые объявления скрыты: доступность нужно проверить заново.")
        if data.get("reference"):
            for p in data["reference"]["photos"]:
                p["path"] = str(root / p["path"])
        export = root / "data" / "render.json"
        export.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        versions = dest / "versions"
        versions.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="render-", dir=root / "data") as scratch:
            stage = Path(scratch) / "gallery"
            subprocess.run([sys.executable, str(SCRIPTS / "render_gallery.py"), str(export), "--output-dir", str(stage)], check=True, capture_output=True, text=True, timeout=30, env={**os.environ, "AVITO_HOME": str(root.resolve())})
            document = (stage / "index.html").read_text()
            if "<head>" not in document:
                raise ValueError("Renderer не создал корректную страницу")
            version = versions / Path(scratch).name
            stage.rename(version)
            page = dest / "index.html"
            prior = page.read_bytes() if page.exists() else None
            pending = Path(scratch) / "index.html"
            pending.write_text(document.replace("<head>", f'<head><base href="versions/{version.name}/">', 1))
            published = False
            try:
                stamp = now()
                for item in data["items"]:
                    if item["decision"] != "rejected" and item["availability"]["status"] == "available":
                        db.execute("UPDATE listings SET shown_at=? WHERE id=?", (stamp, item["id"]))
                os.replace(pending, page)
                published = True
                db.commit()
            except Exception:
                try:
                    if published:
                        if prior is None:
                            page.unlink()
                        else:
                            pending.write_bytes(prior)
                            os.replace(pending, page)
                except OSError as recovery_error:
                    raise RuntimeError("Сбой публикации: прежняя страница не восстановлена; база остаётся главным источником, повтори render этой партии") from recovery_error
                raise
    return str(dest / "index.html")


def profile(path=None, root=ROOT):
    destination = root / "preferences.json"
    if path is None:
        return json.loads(destination.read_text()) if destination.exists() else {"categories": {}, "preferences": {}}
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or not isinstance(data.get("categories", {}), dict) or not isinstance(data.get("preferences", {}), dict):
        raise ValueError("Профиль должен быть JSON-объектом; categories и preferences тоже объекты")
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=root, delete=False) as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        pending = Path(handle.name)
    try:
        os.replace(pending, destination)
    finally:
        pending.unlink(missing_ok=True)
    return data


def initialize(root=ROOT):
    with database(root):
        pass
    destination = root / "preferences.json"
    if not destination.exists():
        with destination.open("x", encoding="utf-8") as handle:
            json.dump({"categories": {}, "preferences": {}}, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    return {"home": str(root), "profile": str(destination)}


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    p = sub.add_parser("profile"); p.add_argument("file", type=Path, nargs="?")
    p = sub.add_parser("import")
    p.add_argument("file", type=Path); p.add_argument("--category", required=True); p.add_argument("--batch")
    p = sub.add_parser("feedback")
    p.add_argument("target"); p.add_argument("decision", choices=sorted(DECISIONS)); p.add_argument("reason"); p.add_argument("--category"); p.add_argument("--block-seller", action="store_true")
    p = sub.add_parser("new"); p.add_argument("ids", nargs="+")
    p = sub.add_parser("show"); p.add_argument("target"); p.add_argument("--category")
    p = sub.add_parser("list"); p.add_argument("--category"); p.add_argument("--decision", choices=sorted(DECISIONS))
    p = sub.add_parser("render"); p.add_argument("batch")
    args = parser.parse_args()
    if args.command == "init":
        result = initialize()
    elif args.command == "profile":
        result = profile(args.file)
    elif args.command == "import":
        result = {"imported": import_catalog(args.file, args.category, args.batch)}
    elif args.command == "feedback":
        result = feedback(args.target, args.decision, args.reason, args.category, args.block_seller)
        with database() as db:
            batches = [r[0] for r in db.execute("SELECT batch FROM batch_items WHERE id=?", (result["id"],))]
        for batch in batches:
            if (ROOT / "galleries" / batch / "index.html").exists():
                try:
                    render_batch(batch)
                except (ValueError, RuntimeError, OSError, sqlite3.Error, subprocess.SubprocessError) as exc:
                    result.setdefault("gallery_refresh_failed", []).append({"batch": batch, "message": "Решение сохранено, обновление галереи не удалось; повторяй только render", "error": str(exc)})
    elif args.command == "new":
        result = {"new_ids": unseen(args.ids)}
    elif args.command == "render":
        result = {"gallery": render_batch(args.batch)}
    else:
        with database() as db:
            if args.command == "show":
                result = dict(resolve(db, args.target, args.category))
                result["payload"] = json.loads(result["payload"])
            else:
                result = [{k: r[k] for k in ("id", "number", "category", "decision", "reason", "shown_at")} for r in db.execute("SELECT * FROM listings ORDER BY number") if (not args.category or args.category == r["category"]) and (not args.decision or args.decision == r["decision"])]
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (KeyError, TypeError, ValueError, RuntimeError, OSError, sqlite3.Error, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
