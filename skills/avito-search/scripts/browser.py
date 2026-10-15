#!/usr/bin/env python3
"""Bounded public Avito browsing in a separate, user-visible Chromium profile."""
import argparse
import json
import math
import re
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit
from urllib.request import ProxyHandler, Request, build_opener

from catalog import ROOT, listing_url, seller_url
from setup_browser import CLI, child_env, installed

STATE = ROOT / "browser/.gstack/browse.json"
CAPTCHA = r"/Доступ ограничен|подтвердите,? что вы|captcha|geetest/i.test(document.title+' '+document.body.innerText)"
READ_CARD = r"""JSON.stringify((()=>{
 const text=m=>document.querySelector('[data-marker="'+m+'"]')?.innerText||'';
 const contacts=text('item-view/item-view-contacts');
 const seller=document.querySelector('[data-marker="seller-link/link"]');
 return {url:location.href,id:text('item-view/item-id').replace(/\D/g,''),title:text('item-view/title-info'),
 description:text('item-view/item-description'),seller:text('seller-info/name'),seller_url:seller?.href,
 params:text('item-view/item-params'),price:text('item-view/item-price'),published_text:text('item-view/item-date'),
 checked_at:new Date().toISOString(),
 captcha:/Доступ ограничен|подтвердите,? что вы|captcha|geetest/i.test(document.title+' '+document.body.innerText),
 unavailable:/Товар зарезервирован|Забронировано|Объявление снято|Объявление завершено|Объявление закрыто/i.test(contacts),
 buy:contacts.includes('Купить с доставкой'),
 photos:[...new Set(Array.from(document.querySelectorAll('[role="option"][data-index]')).map(e=>Number(e.getAttribute('data-index'))))].sort((a,b)=>a-b)};
})())"""
PHOTO_READY = r"""new Promise((resolve,reject)=>{
 const {selector,previous}=__SETTINGS__;
 let done=false;
 const fail=message=>{if(!done){done=true;clearTimeout(deadline);reject(new Error(message));}};
 const deadline=setTimeout(()=>fail('Gallery image did not decode/change source within 8 seconds; inspect manually'),8000);
 const retry=()=>{if(!done)setTimeout(check,25);};
 function check(){
  if(done)return;
  let matches;
  try{matches=document.querySelectorAll(selector);}catch{fail('Invalid gallery image CSS selector');return;}
  if(matches.length>1){fail('Gallery selector must match exactly one image');return;}
  if(!matches.length){retry();return;}
  const image=matches[0];
  if(!(image instanceof HTMLImageElement)){fail('Gallery selector must match an img element');return;}
  const source=image.currentSrc;
  if(!source||source===previous||!image.complete||image.naturalWidth<=0){retry();return;}
  image.decode().then(()=>{
   if(done)return;
   if(image.currentSrc!==source||!image.complete||image.naturalWidth<=0){retry();return;}
   const r=image.getBoundingClientRect(),style=getComputedStyle(image);
   if(style.display==='none'||style.visibility!=='visible'||Number(style.opacity)===0||
      ![r.x,r.y,r.width,r.height].every(Number.isFinite)||r.width<=0||r.height<=0||
      r.x<0||r.y<0||r.right>innerWidth||r.bottom>innerHeight){retry();return;}
   done=true;clearTimeout(deadline);
   resolve(JSON.stringify({source,box:[r.x,r.y,r.width,r.height]}));
  }).catch(retry);
 }
 check();
})"""


def public_url(url, allow_blank=False):
    if not isinstance(url, str) or any(ord(c) <= 32 for c in url) or "\\" in url:
        raise ValueError("Нужна публичная ссылка Авито или локальная HTML-подборка")
    if allow_blank and url == "about:blank":
        return url
    p = urlsplit(url)
    if p.scheme == "file" and not p.netloc and not p.query and not p.fragment:
        path = Path(unquote(p.path)).resolve()
        gallery = ROOT / "galleries"
        if gallery.resolve() == gallery and path.is_relative_to(gallery) and path.name == "index.html" and path.is_file():
            return path.as_uri()
        raise ValueError("Локальная страница должна быть AVITO_HOME/galleries/.../index.html")
    if p.scheme != "https" or p.hostname not in {"avito.ru", "www.avito.ru"} or p.username or p.password or p.port:
        raise ValueError("Нужен HTTPS на avito.ru без credentials/порта")
    path = unquote(unquote(p.path))
    if any(part in {".", "..", "login", "logout", "profile", "messages", "favorites", "checkout", "cart", "payment", "delete", "remove"}
           for part in path.lower().split("/")):
        raise ValueError("Разрешены только публичные страницы поиска, вещей и продавцов")
    if not (path in {"", "/", "/all"} or re.search(r"(?:^|/)odezhda_obuv_aksessuary(?:/|$)", path) or path.startswith(("/brands/", "/user/"))):
        raise ValueError("Эта страница не относится к публичному поиску одежды")
    return urlunsplit(("https", "www.avito.ru", p.path or "/", p.query, ""))


def media_path(value):
    base = ROOT / "media"
    if base.resolve() != base:
        raise ValueError("Папка media не должна ссылаться за пределы AVITO_HOME")
    target = Path(value).expanduser()
    if not target.is_absolute():
        target = base / target
    target = target.resolve()
    if not target.is_relative_to(base) or target == base or target.suffix.lower() != ".png":
        raise ValueError("Снимок должен быть PNG внутри AVITO_HOME/media")
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def unwrap_json(value):
    if value.startswith("--- BEGIN UNTRUSTED EXTERNAL CONTENT"):
        value = value.split("\n", 1)[1].rsplit("\n--- END UNTRUSTED EXTERNAL CONTENT", 1)[0]
    return json.loads(value)


def command(name, *args, tab=None):
    """Direct loopback transport cannot spawn or restart a browser."""
    try:
        state = json.loads(STATE.read_text())
    except (OSError, ValueError) as exc:
        raise RuntimeError("Видимый браузер не запущен. Запусти browser.py start явно.") from exc
    port, token = state.get("port"), state.get("token")
    if type(port) is not int or not 1 <= port <= 65535 or not isinstance(token, str) or not token:
        raise RuntimeError("Некорректное локальное состояние браузера")
    body = {"command": name, "args": list(args)}
    if tab is not None:
        body["tabId"] = tab
    request = Request(f"http://127.0.0.1:{port}/command", data=json.dumps(body).encode(),
                      headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=35) as response:
            data = response.read(8_000_001)
            if len(data) > 8_000_000:
                raise RuntimeError("Ответ слишком большой; сузь DOM-селектор")
            return data.decode("utf-8")
    except HTTPError as exc:
        raise RuntimeError(f"Браузер отклонил шаг (HTTP {exc.code}); проверь видимую вкладку") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError("Браузер не ответил; автозапуск и повтор команды не выполнялись") from exc


def healthy():
    status = command("status").lower()
    if "status: healthy" not in status or "mode: headed" not in status:
        raise RuntimeError("Нужен здоровый видимый headed-браузер; запусти browser.py start")


def tabs():
    healthy()
    result = []
    for line in command("tabs").splitlines():
        match = re.match(r"\s*(?:→\s*)?\[(\d+)\] (.*) — (\S+)\s*$", line)
        if match:
            try:
                url = public_url(match[3], allow_blank=True)
            except ValueError:
                continue
            result.append({"id": int(match[1]), "title": match[2], "url": url})
    return result


def start(url):
    url = public_url(url)
    if not installed():
        raise RuntimeError("Сначала запусти setup_browser.py: runtime не установлен или изменён")
    env = child_env()
    env.pop("BROWSE_NO_AUTOSTART")
    (ROOT / "browser").mkdir(parents=True, exist_ok=True)
    result = subprocess.run([str(CLI), "--headed", "status"], cwd=ROOT, env=env,
                            text=True, capture_output=True, timeout=80)
    if result.returncode:
        raise RuntimeError("Не удалось открыть Chromium. Проверь Bun, установку Chromium, видимый рабочий стол и лог AVITO_HOME/browser/.gstack/browse-startup-error.log; глобальные профили не использовались.")
    healthy()
    opened = json.loads(command("newtab", url, "--json"))
    return {"tab": opened["tabId"], "url": url, "headed": True}


class Browser:
    def __init__(self, tab):
        if not str(tab).isdigit() or int(tab) < 1:
            raise ValueError("Нужен положительный ID своей вкладки")
        self.tab = int(tab)
        healthy()
        if self.tab not in {item["id"] for item in tabs()}:
            raise ValueError("Вкладка не найдена; заново выполни browser.py tabs")
        self.current_url()

    def _run(self, name, *args):
        return command(name, *args, tab=self.tab).strip()

    def current_url(self):
        return public_url(self._run("url"), allow_blank=True)

    def no_captcha(self):
        if self._run("js", CAPTCHA) == "true":
            raise RuntimeError("CAPTCHA: остановись; пользователь проходит её вручную в видимой вкладке")

    def inspect(self, expected_id=None):
        listing_url(self.current_url(), expected_id)
        card = json.loads(self._run("js", READ_CARD))
        if card.get("captcha"):
            raise RuntimeError("CAPTCHA: пользователь проходит её вручную в видимой вкладке")
        listing_url(card["url"], expected_id or card["id"])
        if not card["id"] or (expected_id is not None and card["id"] != str(expected_id)):
            raise RuntimeError("Вкладка изменилась: ID карточки не соответствует ожидаемому")
        if card.get("seller_url"):
            card["seller_url"] = seller_url(card["seller_url"])
        return card

    def image_ready(self, selector, previous=None):
        if not isinstance(selector, str) or not selector.strip() or len(selector) > 1000:
            raise ValueError("Укажи CSS-селектор ровно одной большой фотографии галереи")
        expression = PHOTO_READY.replace("__SETTINGS__", json.dumps({"selector": selector, "previous": previous}))
        image = json.loads(self._run("js", expression))
        box = image.get("box")
        if not image.get("source") or image["source"] == previous or not isinstance(box, list) or len(box) != 4 or not all(isinstance(n, (int, float)) and math.isfinite(n) for n in box) or min(box[2:]) <= 0:
            raise RuntimeError("Нет подтверждённой загруженной фотографии; нужен ручной разбор")
        return image

    def capture(self, expected_id, selector):
        card = self.inspect(expected_id)
        self._run("reload")
        card = self.inspect(expected_id)
        indices = card["photos"]
        if not indices or indices != list(range(len(indices))):
            raise RuntimeError("Не удалось подтвердить все фото галереи; проверь её вручную")
        photos = []
        previous = None
        for index in indices:
            if index:
                self._run("click", f'[role="option"][data-index="{index}"]')
            fresh = self.inspect(expected_id)
            if fresh["photos"] != indices:
                raise RuntimeError("Галерея изменилась во время сохранения")
            image = self.image_ready(selector, previous)
            path = media_path(f"captures/{expected_id}/{index + 1:02}.png")
            self._run("screenshot", "--selector", selector, str(path))
            if not path.is_file() or not path.stat().st_size:
                raise RuntimeError("Фото не сохранилось")
            photos.append({"path": str(path), "source": "gallery_screenshot", "caption": f"Фото {index + 1}"})
            previous = image["source"]
        return {"id": str(expected_id), "photos": photos, "visual_review_required": True}

    def run(self, name, args):
        allowed = {"goto", "url", "reload", "text", "html", "links", "media", "attrs", "css", "is",
                   "snapshot", "scroll", "wait", "click", "screenshot"}
        if name not in allowed or any(arg.startswith(("--tab", "--proxy", "--out", "--force")) for arg in args):
            raise ValueError("Команда или флаг не разрешены; см. references/browser.md")
        self.current_url()
        if name == "goto":
            if len(args) != 1:
                raise ValueError("goto принимает одну публичную ссылку")
            args = [public_url(args[0])]
        elif name == "screenshot":
            if len(args) != 1:
                raise ValueError("screenshot принимает только путь PNG внутри media")
            args = ["--viewport", str(media_path(args[0]))]
        elif name == "snapshot":
            if any(arg not in {"-i", "-c", "-D", "-C", "--interactive", "--compact", "--diff", "--cursor-interactive"} for arg in args):
                raise ValueError("Разрешены только snapshot -i -c -D -C без записи файлов")
        elif name == "scroll":
            if len(args) > 1 or any(arg.startswith("--") for arg in args):
                raise ValueError("scroll делает один шаг: без аргументов или с одним селектором")
            if args:
                args = ["--times", "1", args[0]]
        elif name == "wait":
            if len(args) != 1 or (args[0].startswith("--") and args[0] != "--networkidle"):
                raise ValueError("wait принимает один селектор или --networkidle; время фиксировано 10 секундами")
            args = [args[0], "10000"]
        elif name == "click":
            if len(args) != 1:
                raise ValueError("click принимает один селектор/свежий @ref")
            self.no_captcha()
            attrs = unwrap_json(self._run("attrs", args[0]))
            if attrs.get("role") == "option" and str(attrs.get("data-index", "")).isdigit():
                pass
            elif attrs.get("href"):
                # Navigate the verified public link rather than triggering arbitrary click handlers.
                name, args = "goto", [public_url(urljoin(self.current_url(), attrs["href"]))]
            else:
                raise ValueError("Разрешены ссылки публичного поиска и миниатюры фотогалереи; контакты/покупки запрещены")
        if name != "goto":
            self.no_captcha()
        result = self._run(name, *args)
        self.current_url()
        self.no_captcha()
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("start"); p.add_argument("--url", default="https://www.avito.ru/all/odezhda_obuv_aksessuary")
    sub.add_parser("tabs")
    p = sub.add_parser("run"); p.add_argument("--tab", required=True); p.add_argument("action"); p.add_argument("args", nargs=argparse.REMAINDER)
    p = sub.add_parser("inspect"); p.add_argument("--tab", required=True); p.add_argument("--id")
    p = sub.add_parser("capture"); p.add_argument("id"); p.add_argument("--tab", required=True); p.add_argument("--selector", required=True)
    args = parser.parse_args()
    if args.command == "start":
        result = start(args.url)
    elif args.command == "tabs":
        result = tabs()
    elif args.command == "inspect":
        result = Browser(args.tab).inspect(args.id)
    elif args.command == "capture":
        result = Browser(args.tab).capture(args.id, args.selector)
    else:
        result = Browser(args.tab).run(args.action, args.args)
    print(result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
