"""Health science papers from PubMed (the US National Library of Medicine, 37 million records): E-utilities, no key needed.
Searches reviews and meta-analyses by default: they're more reliable than individual studies. This is a literature summary, not medical advice."""
import xml.etree.ElementTree as ET

from sena.core.safety import external
from sena.core.tools import clamp, string, tool
from sena.world import net
from sena.world.tech import CYRILLIC

SEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
FETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
HEADERS = {"User-Agent": "Sena/1.0 (personal assistant; https://github.com/AStorozh/Sena)"}
CACHE_SECONDS = 3600
KINDS = {"reviews": "(systematic review[pt] OR meta-analysis[pt])", "trials": "randomized controlled trial[pt]", "any": ""}
SHOWN = 600   # characters of the abstract


class PubMedError(net.WorldError):
    pass


def _text(node):
    return " ".join("".join(node.itertext()).split()) if node is not None else ""


def _abstract(article):
    """The authors' conclusions, if they're labeled separately, otherwise the start of the abstract."""
    parts = [(p.get("Label", ""), _text(p)) for p in article.findall("Abstract/AbstractText")]
    for label, text in parts:
        if label.upper().startswith("CONCLUSION"):
            return text
    return " ".join(text for _, text in parts)


def health_research(query, kind="reviews", limit=5):
    """What science says: reviews and meta-analyses (reviews), clinical trials (trials), or everything (any). The query should be in English."""
    query, kind = str(query or "").strip(), kind if kind in KINDS else "reviews"
    if not query:
        raise PubMedError("Что искать? Например: melatonin sleep")
    if CYRILLIC.search(query):
        raise PubMedError("PubMed ищет по-английски: переведи запрос и повтори (например, melatonin sleep)")
    limit = clamp(limit, 5, 1, 10)
    term = f"({query})" + (f" AND {KINDS[kind]}" if KINDS[kind] else "")
    found = net.as_json(net.get(SEARCH, {"db": "pubmed", "term": term, "retmode": "json", "retmax": limit, "sort": "relevance"}, HEADERS, ttl=CACHE_SECONDS,
                                who="PubMed", error=PubMedError), "PubMed", PubMedError).get("esearchresult", {})
    ids = found.get("idlist") or []
    if not ids:
        return f"В PubMed по «{query}» ({kind}) ничего нет. Попробуй kind=any или другие слова."
    try:
        articles = ET.fromstring(net.get(FETCH, {"db": "pubmed", "id": ",".join(ids), "retmode": "xml"}, HEADERS, ttl=CACHE_SECONDS, who="PubMed",
                                         error=PubMedError).content).findall("PubmedArticle")
    except ET.ParseError:
        raise PubMedError("PubMed ответил не тем, что ждали (сбой на их стороне)")
    rows = []
    for node in articles:
        art = node.find("MedlineCitation/Article")
        if art is None:
            continue
        pmid = _text(node.find("MedlineCitation/PMID"))
        date = art.find("Journal/JournalIssue/PubDate")
        year = _text(date.find("Year")) if date is not None and date.find("Year") is not None else _text(date.find("MedlineDate"))[:4] if date is not None else ""
        kinds = [t for t in (_text(x) for x in art.findall("PublicationTypeList/PublicationType")) if t not in ("Journal Article", "Research Support, Non-U.S. Gov't")]
        names = [_text(a.find("LastName")) for a in art.findall("AuthorList/Author")[:2]]
        abstract = _abstract(art)
        rows.append(f"{_text(art.find('ArticleTitle'))} ({', '.join(n for n in names if n)}{' и др.' if len(art.findall('AuthorList/Author')) > 2 else ''}; "
                    f"{_text(art.find('Journal/Title'))}, {year}; {', '.join(kinds[:3]) or 'статья'})\n"
                    f"   {abstract[:SHOWN]}{'…' if len(abstract) > SHOWN else ''}\n   https://pubmed.ncbi.nlm.nih.gov/{pmid}/")
    total = found.get("count", len(rows))
    return external(f"PubMed, «{query}», {kind}: найдено {total}, показаны первые {len(rows)} по релевантности.\n" + "\n".join(f"{i}. {r}" for i, r in enumerate(rows, 1))
                    + "\n(Это научные статьи, а не медицинский совет. Смотри тип работы и год; одно исследование ничего не доказывает.)", "PubMed")


TOOLS = [tool("health_research", "Что говорят научные работы о здоровье, питании, сне, добавках, тренировках: PubMed, 37 млн статей. По умолчанию обзоры и метаанализы (самый надёжный уровень), "
                                 "kind=trials — клинические испытания, any — всё. Запрос по-английски: «creatine cognition». Пересказывай осторожно: что нашли, какого типа работы, "
                                 "насколько они согласны; не ставь диагнозы и не назначай лечение.",
              {"query": string("Тема по-английски: melatonin sleep"), "kind": {"type": "string", "enum": list(KINDS), "description": "reviews (по умолчанию), trials или any"},
               "limit": {"type": "integer", "description": "Сколько работ, от 1 до 10, по умолчанию 5"}}, ["query"])]
FUNCTIONS = {"health_research": health_research}
