"""Tema's portfolio site: texts, works, resume, the site's AI assistant settings, tracking links for applications, and visit analytics.
Everything goes through the site's own private API (/api/sena/v1, key SENA_SITE_KEY): Sena never touches the site's database directly. Reading and creating links happen
immediately; anything that changes what's visible on the site goes through two steps: a dry run (the site shows "before → after"), then Tema's "yes" (confirm_send)."""
import json
import re
from urllib.parse import quote, urlparse

from sena import config
from sena.core import safety
from sena.core.tools import string, tool
from sena.world import net

SOURCE = "Сайт"
SHOWN = 12000   # this many characters of the site's response the model sees; the rest is "cut", and can be requested already
LOOPBACK = ("127.0.0.1", "localhost", "::1")

# where writing is allowed: only known API paths (the site itself re-validates every field)
WRITABLE = re.compile(
    r"^/(texts/[a-z_]+|works/[\w\-]+|contacts/methods/[\w\-]+|resume/sections(/[\w\-]+)?|quotes(/[\w\-]+)?|ai/settings"
    r"|ai/capabilities/[\w\-]+|links(/[\w\-]+)?|analytics/export|revalidate|audit/\d{1,9}/revert)$")
NO_DRY_RUN = ("/revalidate", "/analytics/export")   # these paths have no dry run: they don't change any text
READABLE = re.compile(r"^/[A-Za-z0-9_\-./]*$")   # the path; parameters after "?" are encoded automatically (Cyrillic letters and spaces are allowed)


class SiteError(net.WorldError):
    """The site is unreachable or refused the request. The text can be shown to Tema as is."""


def _base():
    url = config.SITE_URL.rstrip("/")
    if not url:
        raise SiteError("Адрес сайта не задан: впиши SITE_URL в .env")
    if urlparse(url).scheme != "https" and urlparse(url).hostname not in LOOPBACK:
        raise SiteError("SITE_URL должен начинаться с https://: ключ сайта нельзя слать по открытому каналу")
    if not config.SITE_KEY:
        raise SiteError("Ключа сайта нет: впиши SENA_SITE_KEY в .env (тот же, что задан в окружении сайта)")
    return url + "/api/sena/v1"


def _headers():
    return {"Authorization": "Bearer " + config.SITE_KEY}


def _explain(reply):
    # site errors arrive as {"error": {"code", "message", "details"}}
    try:
        error = reply.json()["error"]
    except (ValueError, KeyError, TypeError):
        return None
    extra = f" {json.dumps(error['details'], ensure_ascii=False)[:400]}" if error.get("details") else ""
    return f"Сайт отказал ({error.get('code')}): {error.get('message')}{extra}"


def _path(path):
    base, _, query = ("/" + str(path or "").strip().lstrip("/")).partition("?")
    if not READABLE.match(base) or ".." in base:
        raise SiteError(f"Адрес «{base}» записан неверно")
    base = base.rstrip("/") if base != "/" else ""
    return base + ("?" + quote(query, safe="=&,:%-_.~/") if query else "")


def _get(path):
    reply = net.get(_base() + _path(path), headers=_headers(), timeout=60, who=SOURCE, error=SiteError, explain=_explain, redirects=False)
    return net.as_json(reply, SOURCE, SiteError)


def _send(method, path, body):
    reply = net.send(method, _base() + _path(path), body, headers=_headers(), timeout=60, who=SOURCE, error=SiteError, explain=_explain, redirects=False)
    return net.as_json(reply, SOURCE, SiteError)


def shown(data):
    """The site's response for the model: compressed JSON, marked as external data (analytics can contain other people's strings — referrer URLs, labels)."""
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    if len(text) > SHOWN:
        text = text[:SHOWN] + f"…[вырезано {len(text) - SHOWN} знаков: запроси уже, например ?prefix=, ?search=, ?parts=]"
    return safety.external(text, SOURCE)


# ---------- reading ----------
def site_overview():
    return shown(_get("/"))


def site_read(path):
    return shown(_get(path))


def site_stats(range="30", link="all"):
    if not re.fullmatch(r"all|\d{1,4}", str(range)):
        raise SiteError("range — число дней (7, 30, 90) или all")
    if not re.fullmatch(r"all|direct|[a-z0-9\-]{1,40}", str(link)):
        raise SiteError("link — all, direct или код ссылки")
    parts = "kpis,scopeCards,topPages,topWorks,sources,devices,geo,funnel,behavior,returns,times"
    return shown(_get(f"/analytics?range={range}&link={link}&parts={parts}"))


def site_link_create(label, audience=None, target=None, code=None):
    # a link changes nothing externally and can be removed with one command, so no confirmation is needed
    body = {"label": label, **{k: v for k, v in (("audience", audience), ("target", target), ("code", code)) if v}}
    return shown(_send("POST", "/links", body))


# ---------- edits: dry run and apply ----------
def _check(method, path):
    method = str(method).upper()
    path = _path(path)
    if method not in ("PUT", "PATCH", "POST", "DELETE") or not WRITABLE.match(path) or (path.endswith("/revert") and method != "POST"):
        raise SiteError(f"Так менять сайт нельзя: {method} {path}. Адреса и поля смотри в site_overview")
    return method, path


def preview(method, path, body=None):
    """Dry run: the site writes nothing and just shows "before → after". Returns its response."""
    method, path = _check(method, path)
    if path in NO_DRY_RUN:
        return {"dry_run": True, "note": f"{method} {path}: пробного прогона нет, действие безобидное (сброс кэша или выгрузка аналитики)"}
    if method == "DELETE":
        return _send(method, path + "?dry_run=1", None)
    return _send(method, path, {**(body or {}), "dry_run": True})


def apply(method, path, body=None):
    """Actually apply the edit. Called only after Tema's "yes" (confirm_send)."""
    method, path = _check(method, path)
    data = _send(method, path, None if method == "DELETE" else (body or {}))
    return "Применено на сайте. Ответ сайта (в нём прежние значения — по ним можно откатить):\n" + shown(data)


def summary(method, path, data):
    # a short line for the "awaiting confirmation" list
    n = data.get("changed") or len(data.get("changes", []) or data.get("results", []) or []) or 1
    return f"{str(method).upper()} {path}: изменений {n}"


CHANGE_TOOL = tool(
    "site_change",
    "Подготовить правку сайта-портфолио: тексты на всех языках, работы, резюме, цитаты, контакты, настройки AI-ассистента сайта, ссылки. "
    "Ничего не меняет сразу: сайт делает пробный прогон и показывает «было → стало». Покажи это Теме коротко и спроси; после его «да» применяй через confirm_send. "
    "Сначала прочитай нужное (site_read) и сверься с адресами и полями в site_overview. Журнал всего, что делалось на сайте: site_read /audit (с ?full=1 видно «было → стало»); откат правки: POST /audit/<номер>/revert (пробный прогон покажет конфликты). Примеры: "
    "PUT /texts/home {\"changes\":[{\"key\":\"disclaimer\",\"value\":\"…\"},{\"key\":\"disclaimer__en\",\"value\":\"…\"}]} (перевод — тот же ключ с __en или __de; value null убирает перевод); "
    "PATCH /works/<slug> {\"title\":\"…\",\"tags\":[…]}; PUT /ai/settings {\"changes\":[{\"key\":\"temperature\",\"value\":\"0.5\"}]}; PATCH /links/<код> {\"label\":\"…\"}. "
    "Тексты на сайте на трёх языках (ru, en, de): меняя русский, обнови и переводы, иначе на других языках останется старое.",
    {"method": {"type": "string", "enum": ["PUT", "PATCH", "POST", "DELETE"]},
     "path": string("Адрес API после /api/sena/v1, например /texts/home или /works/site-a"),
     "body": {"type": "object", "description": "Тело запроса (для DELETE не нужно)"}},
    ["method", "path"])

TOOLS = [
    tool("site_overview",
         "Сайт-портфолио Темы: карта API (все адреса и что они делают), разделы текстов, работы, ссылки для откликов, расход AI-ассистента сайта. "
         "Начни с этого, когда нужно что-то узнать про сайт или поменять на нём.", {}),
    tool("site_read",
         "Прочитать с сайта-портфолио: тексты (/texts/home, /texts/about_blocks, /texts/heroes, /texts/ui?search=…), работы (/works, /works/<slug>), "
         "резюме (/resume/sections), контакты (/contacts/methods), цитаты (/quotes), настройки и расход AI-ассистента (/ai/settings, /ai/budget), ссылки (/links). Только чтение. "
         "НЕ ЗНАЕШЬ, ГДЕ ЛЕЖИТ ТЕКСТ — сначала /texts/search?q=фраза: он ищет по всему сайту (тексты, переводы, подписи, работы, контакты, резюме) и в каждой находке пишет, чем её править.",
         {"path": string("Адрес после /api/sena/v1, можно с параметрами, например /texts/ui?prefix=works_&limit=40")}, ["path"]),
    tool("site_stats",
         "Статистика посещений сайта-портфолио: посетители, просмотры и скачивания резюме, клики по контактам, страницы, источники, страны, поведение. "
         "link — код ссылки-метки (посмотреть, как сработал конкретный отклик), all или direct (прямые заходы).",
         {"range": string("Дней: 7, 30, 90 или all (по умолчанию 30)"), "link": string("all, direct или код ссылки (по умолчанию all)")}),
    tool("site_link_create",
         "Создать ссылку-метку для отклика на вакансию или для человека: по ней сайт запишет, что пришёл именно он, и видно, открыл ли он резюме и что смотрел. "
         "Верни Теме готовый адрес. Подтверждение не нужно: ссылка никого не затрагивает и удаляется одной командой.",
         {"label": string("Кому или на что, например «Acme, вакансия фронтенд»"),
          "audience": string("recruiter, client, friend и т.п. (необязательно)"),
          "target": string("Страница сайта, куда ведёт ссылка, например /resume-file (по умолчанию главная)"),
          "code": string("Код ссылки латиницей (необязательно, иначе из названия)")}, ["label"]),
    CHANGE_TOOL,
]
FUNCTIONS = {"site_overview": site_overview, "site_read": site_read, "site_stats": site_stats, "site_link_create": site_link_create}
