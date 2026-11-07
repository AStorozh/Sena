"""Fresh and useful content for developers and about AI, no ads and no keys needed: Hacker News (Algolia search), arXiv preprints, Stack Overflow answers,
new models and daily papers from Hugging Face. Titles, scores, answers and abstracts are someone else's text, so the response is marked as external data."""
import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from sena.core.safety import external
from sena.core.tools import clamp, string, tool
from sena.world import net

HN = "https://hn.algolia.com/api/v1/{}"
ARXIV = "https://export.arxiv.org/api/query"
STACK = "https://api.stackexchange.com/2.3/"
HUGGING = "https://huggingface.co/api/"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; SenaTech/1.0)"}
CACHE_SECONDS = 600   # arXiv asks for no more than one request every 3 seconds: repeated questions are served from the cache
ATOM = {"a": "http://www.w3.org/2005/Atom"}
CATEGORY = re.compile(r"[a-z\-]+(\.[A-Za-z\-]+)?")   # cs.AI, cs.CL, stat.ML, physics.optics
CYRILLIC = re.compile(r"[а-яё]", re.I)


class TechError(net.WorldError):
    pass


# ---------- Hacker News ----------
def hacker_news(query="", days=0, limit=10):
    """Hacker News highlights: without a query, the site's front page; with a query, story search (most-discussed first); days — how many recent days to cover."""
    query, limit, days = str(query or "").strip(), clamp(limit, 10, 1, 20), clamp(days, 0, 0, 3650)   # "пять" from the model: no limit, not a crash
    params = {"tags": "story", "hitsPerPage": limit}
    if query:
        params["query"] = query
    elif not days:
        params["tags"] = "front_page"
    if days:
        params["numericFilters"] = f"created_at_i>{int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())}"
    hits = net.as_json(net.get(HN.format("search"), params, HEADERS, ttl=CACHE_SECONDS, who="Hacker News", error=TechError), "Hacker News", TechError).get("hits", [])
    if not hits:
        return "На Hacker News по этому запросу ничего нет."
    lines = []
    for i, h in enumerate(hits, 1):
        talk = f"https://news.ycombinator.com/item?id={h.get('objectID')}"
        link = h.get("url") or talk
        lines.append(f"{i}. {h.get('title') or '(без названия)'}: {h.get('points') or 0} баллов, {h.get('num_comments') or 0} комментариев; {link}"
                     + (f" (обсуждение {talk})" if h.get("url") else ""))
    head = f"Hacker News, «{query}»" if query else "Hacker News, первая страница"
    return external(head + (f", за {days} дн." if days else "") + ":\n" + "\n".join(lines), "Hacker News")


# ---------- arXiv ----------
def _arxiv_query(query, category):
    if CYRILLIC.search(query):
        raise TechError("arXiv ищет только по-английски: переведи запрос и повтори")
    words = re.findall(r"[\w\-]+", query)[:8]
    parts = [f"all:{w}" for w in words]
    if category:
        if not CATEGORY.fullmatch(category):
            raise TechError("Раздел arXiv пишется как cs.AI, cs.CL, cs.LG, cs.CV, stat.ML")
        parts.append(f"cat:{category}")
    if not parts:
        raise TechError("Нужны слова для поиска или раздел arXiv (например cs.AI)")
    return " AND ".join(parts)


def arxiv_search(query="", category="", limit=8, newest=True):
    """arXiv preprints: by words (in English) and/or category (cs.AI, cs.CL, cs.LG); newest — most recent first, otherwise best-matching first."""
    limit = clamp(limit, 8, 1, 20)
    reply = net.get(ARXIV, {"search_query": _arxiv_query(str(query or "").strip(), str(category or "").strip()), "max_results": limit,
                            "sortBy": "submittedDate" if newest else "relevance", "sortOrder": "descending"},
                    HEADERS, ttl=CACHE_SECONDS, who="arXiv", error=TechError)
    try:
        entries = ET.fromstring(reply.content).findall("a:entry", ATOM)
    except ET.ParseError:
        raise TechError("arXiv ответил не тем, что ждали (сбой на их стороне)")
    rows = []
    for e in entries:
        link = (e.findtext("a:id", "", ATOM) or "").strip()
        if "/api/errors" in link:   # arXiv returns a request error as an ordinary-looking entry
            raise TechError("arXiv не принял запрос: " + " ".join((e.findtext("a:summary", "", ATOM) or "").split())[:150])
        names = [a.findtext("a:name", "", ATOM) for a in e.findall("a:author", ATOM)]
        title = " ".join((e.findtext("a:title", "", ATOM) or "").split())
        abstract = " ".join((e.findtext("a:summary", "", ATOM) or "").split())
        rows.append(f"{title} ({', '.join(names[:3])}{' и др.' if len(names) > 3 else ''}; {(e.findtext('a:published', '', ATOM) or '')[:10]})\n"
                    f"   {abstract[:350]}{'…' if len(abstract) > 350 else ''}\n   {link}")
    if not rows:
        return "В arXiv по этому запросу ничего нет."
    return external("arXiv, " + ("новые" if newest else "самые подходящие") + ":\n" + "\n".join(f"{i}. {r}" for i, r in enumerate(rows, 1)), "arXiv")


# ---------- Stack Overflow ----------
def plain(body):
    """A Stack Overflow answer from HTML to text; code stays code, inside ``` blocks."""
    body = re.sub(r"</?pre[^>]*>", "\n```\n", body)
    body = re.sub(r"<br\s*/?>|</?(?:p|li|div|ul|ol|tr|h\d)\b[^>]*>", "\n", body)   # block boundaries: otherwise adjacent words run together
    body = html.unescape(re.sub(r"<[^>]+>", "", body))
    return re.sub(r"\n{3,}", "\n\n", body).strip()


def stackoverflow(query, limit=3):
    """Stack Overflow questions with an accepted answer: what was asked, how many votes, the text of the best answer. For errors and "how do I" in code."""
    query, limit = str(query or "").strip(), clamp(limit, 3, 1, 5)
    if not query:
        raise TechError("Что искать? Текст ошибки или вопрос по коду")
    found = net.as_json(net.get(STACK + "search/advanced", {"order": "desc", "sort": "relevance", "q": query, "accepted": "True", "site": "stackoverflow", "pagesize": min(limit * 3, 15)},
                                HEADERS, ttl=CACHE_SECONDS, who="Stack Overflow", error=TechError), "Stack Overflow", TechError).get("items") or []
    if not found:
        return "На Stack Overflow по этому запросу нет вопросов с принятым ответом. Попробуй короче: только имя ошибки и язык."
    good = [q for q in found if q.get("score", 0) >= 3]   # questions with no votes are usually junk or "solved in the comments"
    found = (good or found)[:limit]
    ids = ";".join(str(q["accepted_answer_id"]) for q in found if q.get("accepted_answer_id"))
    answers = {}
    if ids:
        for a in net.as_json(net.get(STACK + f"answers/{ids}", {"site": "stackoverflow", "filter": "withbody"}, HEADERS, ttl=CACHE_SECONDS, who="Stack Overflow",
                                     error=TechError), "Stack Overflow", TechError).get("items") or []:
            answers[a["answer_id"]] = a
    rows = []
    for i, q in enumerate(found, 1):
        answer = answers.get(q.get("accepted_answer_id"))
        text = plain(answer.get("body", "")) if answer else ""
        rows.append(f"{i}. {html.unescape(q.get('title', ''))} (голосов {q.get('score', 0)}, просмотров {q.get('view_count', 0)}; {', '.join(q.get('tags', [])[:4])})\n   {q.get('link')}"
                    + (f"\n   Принятый ответ ({answer.get('score', 0)} голосов):\n   " + text[:900].replace("\n", "\n   ") + ("…" if len(text) > 900 else "") if text else ""))
    return external("Stack Overflow, «" + query + "»:\n" + "\n".join(rows) + "\n(Проверь, что версия и условия совпадают с твоими: ответы бывают старыми.)", "Stack Overflow")


# ---------- Hugging Face ----------
def huggingface(what="models", task="", query="", limit=10):
    """Hugging Face: trending models (what=models; task: text-generation, image-text-to-text, automatic-speech-recognition; query — words) or daily papers (what=papers)."""
    limit = clamp(limit, 10, 1, 20)
    if what == "papers":
        rows = net.as_json(net.get(HUGGING + "daily_papers", {"limit": limit}, HEADERS, ttl=CACHE_SECONDS, who="Hugging Face", error=TechError), "Hugging Face", TechError)
        lines = []
        for i, row in enumerate(rows if isinstance(rows, list) else [], 1):
            p = row.get("paper") or {}
            summary = " ".join((p.get("summary") or "").split())
            lines.append(f"{i}. {p.get('title')} (голосов {p.get('upvotes', 0)}, {(p.get('publishedAt') or '')[:10]})\n   {summary[:260]}{'…' if len(summary) > 260 else ''}\n   https://huggingface.co/papers/{p.get('id')}")
        return external("Статьи дня Hugging Face:\n" + "\n".join(lines), "Hugging Face") if lines else "Статей дня пока нет."
    params = {"sort": "trendingScore", "direction": -1, "limit": limit}
    for key, value in (("pipeline_tag", task), ("search", query)):
        if str(value or "").strip():
            params[key] = str(value).strip()
    rows = net.as_json(net.get(HUGGING + "models", params, HEADERS, ttl=CACHE_SECONDS, who="Hugging Face", error=TechError), "Hugging Face", TechError)
    if not isinstance(rows, list) or not rows:
        return "Моделей по этому запросу нет."
    lines = [f"{i}. {m.get('id')} ({m.get('pipeline_tag') or 'без типа'}; лайков {m.get('likes', 0)}, скачиваний {m.get('downloads', 0)}; создана {(m.get('createdAt') or '')[:10]})\n   https://huggingface.co/{m.get('id')}"
             for i, m in enumerate(rows, 1)]
    return external("Растущие модели Hugging Face" + (f" ({task})" if task else "") + ":\n" + "\n".join(lines), "Hugging Face")


TOOLS = [
    tool("hacker_news", "Hacker News: что обсуждают в технологиях и ИИ. Без запроса: первая страница сайта; с запросом (по-английски): истории по теме, "
                        "самые обсуждаемые сверху; days — только за последние N дней. Показывает баллы, число комментариев, ссылку и обсуждение. Бесплатно, без рекламы.",
         {"query": string("О чём искать, по-английски: LLM agents"), "days": {"type": "integer", "description": "Только за последние столько дней, по умолчанию без ограничения"},
          "limit": {"type": "integer", "description": "Сколько историй, от 1 до 20, по умолчанию 10"}}),
    tool("arxiv_search", "Научные препринты arXiv: свежие работы по ИИ и не только. По словам запроса (только английский) и/или разделу: cs.AI, cs.CL (язык), cs.LG (обучение), "
                         "cs.CV (зрение), stat.ML. Показывает название, авторов, дату, начало аннотации и ссылку. newest=false — сначала самые подходящие, не самые новые.",
         {"query": string("Слова для поиска по-английски: agents tool use"), "category": string("Раздел arXiv: cs.AI"),
          "limit": {"type": "integer", "description": "Сколько работ, от 1 до 20, по умолчанию 8"},
          "newest": {"type": "boolean", "description": "Сначала самые свежие (по умолчанию да)"}}),
    tool("stackoverflow", "Stack Overflow: вопросы с принятым ответом и текст лучшего ответа. Для ошибок в коде и «как сделать». Запрос короткий, по-английски: имя ошибки, язык, библиотека. "
                          "Ответы бывают старыми: сверяй версию.",
         {"query": string("Текст ошибки или вопрос по-английски: python KeyError in dict"), "limit": {"type": "integer", "description": "Сколько вопросов, от 1 до 5, по умолчанию 3"}}, ["query"]),
    tool("huggingface", "Hugging Face: растущие модели (what=models; task: text-generation, image-text-to-text, automatic-speech-recognition, text-to-image; query — слова в названии) "
                        "или статьи дня по ИИ (what=papers). Для вопросов «какие новые модели вышли», «что нового в ИИ».",
         {"what": {"type": "string", "enum": ["models", "papers"], "description": "models (по умолчанию) или papers"}, "task": string("Тип модели, например text-generation"),
          "query": string("Слова в названии модели"), "limit": {"type": "integer", "description": "Сколько, от 1 до 20, по умолчанию 10"}}),
]
FUNCTIONS = {"hacker_news": hacker_news, "arxiv_search": arxiv_search, "stackoverflow": stackoverflow, "huggingface": huggingface}
