"""Books from Open Library (Internet Archive's open library): search, authors, year, number of editions, rating, cover and description. No key needed.
Titles and descriptions are someone else's text; the response is marked as external data."""
import re

from sena.core.safety import external
from sena.core.tools import clamp, string, tool
from sena.world import net

SEARCH = "https://openlibrary.org/search.json"
WORK = "https://openlibrary.org{}.json"
COVER = "https://covers.openlibrary.org/b/id/{}-L.jpg"
HEADERS = {"User-Agent": "Sena/1.0 (personal assistant; https://github.com/AStorozh/Sena)"}
FIELDS = "key,title,author_name,first_publish_year,edition_count,cover_i,ratings_average,ratings_count,number_of_pages_median,subject"
CACHE_SECONDS = 3600


class BooksError(net.WorldError):
    pass


def _json(url, params=None):
    return net.as_json(net.get(url, params, HEADERS, ttl=CACHE_SECONDS, who="Open Library", error=BooksError), "Open Library", BooksError)


def _description(key):
    """The book's description from the work page: it can be a plain string or a {value} dict."""
    try:
        text = _json(WORK.format(key)).get("description") or ""
    except BooksError:
        return ""
    text = text.get("value", "") if isinstance(text, dict) else str(text)
    return " ".join(text.split("----------")[0].split())   # after the dashes in the description come links to editions


def book_search(query, limit=5):
    """Book search: by title, author or words (plain words work too: dune herbert; if using fields, write them all together: author:herbert title:dune)."""
    query = str(query or "").strip()
    if not query:
        raise BooksError("Что искать: название, автор или тема?")
    limit = clamp(limit, 5, 1, 10)
    docs = _json(SEARCH, {"q": query, "limit": limit, "fields": FIELDS}).get("docs") or []
    plain = re.sub(r"\b(author|title|subject):", "", query)
    if not docs and plain != query:   # Open Library doesn't understand a field mixed with a plain word: search by plain words instead
        docs = _json(SEARCH, {"q": plain, "limit": limit, "fields": FIELDS}).get("docs") or []
    if not docs:
        return f"В Open Library по «{query}» ничего нет. Попробуй название на языке оригинала."
    lines = []
    for i, d in enumerate(docs, 1):
        facts = [x for x in (", ".join((d.get("author_name") or [])[:3]), str(d["first_publish_year"]) if d.get("first_publish_year") else "",
                             f"изданий: {d['edition_count']}" if d.get("edition_count") else "",
                             f"рейтинг {d['ratings_average']:.1f} из 5 ({d.get('ratings_count', 0)} оценок)" if d.get("ratings_average") else "",
                             f"{d['number_of_pages_median']} стр." if d.get("number_of_pages_median") else "") if x]
        lines.append(f"{i}. {d.get('title', '(без названия)')}: {'; '.join(facts)}\n   https://openlibrary.org{d['key']}" + (f"\n   обложка {COVER.format(d['cover_i'])}" if d.get("cover_i") else ""))
    about = _description(docs[0]["key"])
    return external("\n".join(lines) + (f"\nО первой книге: {about[:600]}{'…' if len(about) > 600 else ''}" if about else ""), "Open Library")


TOOLS = [tool("book_search", "Поиск книг в Open Library: название, автор, год первого издания, число изданий, рейтинг читателей, страницы, обложка и описание первой найденной. "
                             "Запрос можно уточнить полями, но тогда пиши их все: author:Herbert title:Dune; или просто слова: dune herbert. Лучше искать на языке оригинала; русских книг там меньше.",
              {"query": string("Название, автор или тема"), "limit": {"type": "integer", "description": "Сколько книг, от 1 до 10, по умолчанию 5"}}, ["query"])]
FUNCTIONS = {"book_search": book_search}
