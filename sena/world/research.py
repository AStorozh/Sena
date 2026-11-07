"""Academic papers on any topic: OpenAlex (250 million works, authors, citations) and Crossref as a fallback (DOI, citations, journals). Both are open.
Without a key, OpenAlex has a shared daily budget per IP; once it's used up, paper search falls back to Crossref."""
import re

from sena import config
from sena.core.safety import external
from sena.core.tools import clamp, string, tool
from sena.world import net

OPENALEX = "https://api.openalex.org/"
CROSSREF = "https://api.crossref.org/works"
HEADERS = {"User-Agent": "Sena/1.0 (https://github.com/AStorozh/Sena)"}
CACHE_SECONDS = 3600
WORK_FIELDS = "id,display_name,publication_year,cited_by_count,doi,authorships,primary_location,type,open_access"
SORTS = {"relevance": None, "cited": "cited_by_count:desc", "new": "publication_date:desc"}
DOI = re.compile(r"10\.\d{4,9}/\S+")
CAUTION = "\n(У препринтов в OpenAlex названия и авторы иногда перепутаны с чужой работой: сверяй название с DOI и ссылкой, прежде чем называть работу.)"


class ResearchError(net.WorldError):
    pass


class OpenAlexBusy(ResearchError):
    """OpenAlex's daily budget ran out or the key didn't work: fall back to Crossref."""


def _explain(reply):
    # net.get can only raise one error class per call: to distinguish "budget exhausted" (fall back to Crossref)
    # from other OpenAlex failures, we raise the more specific OpenAlexBusy directly here instead of returning text.
    if reply.status_code in (402, 403, 429):
        raise OpenAlexBusy("OpenAlex: дневной бюджет запросов исчерпан")
    return None


def _alex(path, params):
    key = {"api_key": config.OPENALEX_KEY} if config.OPENALEX_KEY else {}
    return net.as_json(net.get(OPENALEX + path, {**params, **key}, HEADERS, ttl=CACHE_SECONDS, who="OpenAlex", error=ResearchError, explain=_explain), "OpenAlex", ResearchError)


def _crossref(path, params):
    return net.as_json(net.get(CROSSREF + path, params, HEADERS, ttl=CACHE_SECONDS, who="Crossref", error=ResearchError), "Crossref", ResearchError).get("message") or {}


def _work(w):
    names = [a["author"]["display_name"] for a in w.get("authorships") or [] if a.get("author")]
    source = ((w.get("primary_location") or {}).get("source") or {}).get("display_name") or ""
    oa = (w.get("open_access") or {}).get("oa_url")
    return (f"{w.get('display_name')} ({w.get('publication_year')}{', ' + source if source else ''}; цитирований {_count(w.get('cited_by_count', 0))})\n"
            f"   {', '.join(names[:3])}{' и др.' if len(names) > 3 else ''}\n   {w.get('doi') or w.get('id')}" + (f"; читать бесплатно: {oa}" if oa and oa != w.get("doi") else ""))


def _count(n):
    return f"{int(n):,}".replace(",", " ")


def _crossref_row(item):
    names = [f"{a.get('given', '')} {a.get('family', '')}".strip() for a in item.get("author") or []]
    year = ((item.get("issued") or {}).get("date-parts") or [[None]])[0][0]
    return (f"{(item.get('title') or ['(без названия)'])[0]} ({year}{', ' + item['container-title'][0] if item.get('container-title') else ''}; цитирований {_count(item.get('is-referenced-by-count', 0))})\n"
            f"   {', '.join(names[:3])}{' и др.' if len(names) > 3 else ''}\n   https://doi.org/{item.get('DOI')}")


def _by_doi(code):
    # one paper by its DOI: from OpenAlex, from Crossref when OpenAlex doesn't have it (or its budget is spent)
    try:
        return external(_work(_alex(f"works/doi:{code}", {"select": WORK_FIELDS})), "OpenAlex")
    except ResearchError:
        return external(_crossref_row(_crossref(f"/{code}", {})), "Crossref")


def _searched(query, sort, limit):
    # the works asked for: by relevance a full-text search; sorted by citations or date only the title and abstract are searched
    # (full text pulls in unrelated works)
    params = {"per-page": limit, "select": WORK_FIELDS}
    if sort == "relevance":
        return _alex("works", {**params, "search": query}).get("results") or []
    return _alex("works", {**params, "filter": f"title_and_abstract.search:{query}", "sort": SORTS[sort]}).get("results") or []


def _works(query, sort, limit):
    works = _searched(query, sort, limit)
    return external("\n".join(f"{i}. {_work(w)}" for i, w in enumerate(works, 1)) + CAUTION, "OpenAlex") if works else f"Работ по «{query}» нет."


def _authors(query, sort, limit):
    rows = _alex("authors", {"search": query, "per-page": limit, "select": "id,display_name,works_count,cited_by_count,summary_stats,last_known_institutions"}).get("results") or []
    lines = [f"{i}. {a['display_name']} ({', '.join(x['display_name'] for x in (a.get('last_known_institutions') or [])[:2]) or 'место работы не указано'}; "
             f"работ {_count(a.get('works_count', 0))}, цитирований {_count(a.get('cited_by_count', 0))}, h-индекс {(a.get('summary_stats') or {}).get('h_index', '?')})\n"
             f"   {a['id']}" for i, a in enumerate(rows, 1)]
    return external("\n".join(lines), "OpenAlex") if lines else f"Авторов по «{query}» нет."


def _citations(query, sort, limit):
    # who cites the work that matches best, the most noticed first
    works = _searched(query, "relevance", limit)
    if not works:
        return f"Работу по «{query}» не нашла."
    target = works[0]
    citing = _alex("works", {"filter": f"cites:{target['id'].rsplit('/', 1)[-1]}", "sort": "cited_by_count:desc", "per-page": limit, "select": WORK_FIELDS}).get("results") or []
    return external(f"Работа: {_work(target)}\nЕё цитируют, самые заметные:\n" + "\n".join(f"{i}. {_work(w)}" for i, w in enumerate(citing, 1)) + CAUTION, "OpenAlex")


def _from_crossref(query, sort, limit):
    # OpenAlex's free daily budget is spent: works from Crossref, which has neither authors nor citations
    rows = _crossref("", {"query": query, "rows": limit, "sort": "is-referenced-by-count" if sort == "cited" else "published" if sort == "new" else "relevance", "order": "desc",
                          "select": "DOI,title,author,issued,container-title,is-referenced-by-count"}).get("items") or []
    if not rows:
        return f"Работ по «{query}» нет (OpenAlex сегодня не отвечает, искала в Crossref)."
    return external("(OpenAlex сегодня не отвечает, данные Crossref)\n" + "\n".join(f"{i}. {_crossref_row(r)}" for i, r in enumerate(rows, 1)), "Crossref")


KINDS = {"works": _works, "authors": _authors, "citations": _citations}   # kind= -> its search


def research_search(query, kind="works", sort="relevance", limit=5):
    """Academic works (works), authors (authors), or who cites the best-matching work (citations). For a specific paper by DOI, just send the DOI."""
    query, kind, sort = str(query or "").strip(), kind if kind in KINDS else "works", sort if sort in SORTS else "relevance"
    if not query:
        raise ResearchError("Что искать: тему, автора или DOI?")
    limit = clamp(limit, 5, 1, 10)
    doi = DOI.search(query)
    if doi and kind == "works":
        return _by_doi(doi.group(0).rstrip(".,;)"))
    try:
        return KINDS[kind](query, sort, limit)
    except OpenAlexBusy:
        if kind != "works":
            raise ResearchError("OpenAlex сегодня исчерпал бесплатный бюджет запросов для этого адреса: авторов и цитирования пока не посмотреть. "
                                "Ключ: openalex.org/settings/api (OPENALEX_KEY в .env).")
        return _from_crossref(query, sort, limit)


TOOLS = [tool("research_search", "Научные работы по любой теме (OpenAlex, 250 млн работ; запасной Crossref): название, год, журнал, авторы, сколько раз процитирована, DOI и ссылка на бесплатный текст. "
                                 "kind=authors: учёные (где работают, число работ, цитирования, h-индекс); kind=citations: кто цитирует лучшую найденную работу. sort: relevance, cited (самые цитируемые), new. "
                                 "Работа по DOI: пришли DOI в query. Шире, чем arxiv_search и health_research: любая наука. Запрос по-английски.",
              {"query": string("Тема, автор, название работы или DOI"), "kind": {"type": "string", "enum": list(KINDS), "description": "works (по умолчанию), authors или citations"},
               "sort": {"type": "string", "enum": list(SORTS), "description": "relevance (по умолчанию), cited или new"}, "limit": {"type": "integer", "description": "Сколько, от 1 до 10, по умолчанию 5"}}, ["query"])]
FUNCTIONS = {"research_search": research_search}
