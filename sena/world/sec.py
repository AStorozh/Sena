"""Filings of US companies from SEC EDGAR (the official database of the US Securities and Exchange Commission): a list of documents (annual 10-K, quarterly 10-Q, events 8-K and others)
with links, and key figures (revenue, profit, earnings per share) from XBRL. No key needed, but SEC requires a requester's name and email on every request: SEC_CONTACT in .env.
These are facts from filings, not investment advice."""
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from sena import config
from sena.core.safety import external
from sena.core.tools import clamp, string, tool
from sena.world import finance, net

TICKERS = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK{}.json"
CONCEPT = "https://data.sec.gov/api/xbrl/companyconcept/CIK{}/us-gaap/{}.json"
CACHE_SECONDS = 6 * 3600
FORMS = {"10-K": "годовой отчёт", "10-Q": "квартальный отчёт", "8-K": "важное событие", "DEF 14A": "материалы собрания акционеров", "4": "сделки инсайдеров", "S-1": "регистрация выпуска акций",
         "20-F": "годовой отчёт иностранной компании", "6-K": "события иностранной компании", "13F-HR": "портфель фонда", "SC 13G": "крупный владелец акций"}
METRICS = [("Выручка", ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax"), "USD"),
           ("Операционная прибыль", ("OperatingIncomeLoss",), "USD"), ("Чистая прибыль", ("NetIncomeLoss", "ProfitLoss"), "USD"),
           ("Прибыль на акцию (разводнённая)", ("EarningsPerShareDiluted",), "USD/shares")]


class SecError(net.WorldError):
    pass


class SecAbsent(SecError):
    """SEC has no such document (404): a company that never used a reporting tag, not SEC being down."""


def _headers():
    contact = str(config.SEC_CONTACT or "").strip()
    if "@" not in contact:
        raise SecError("SEC EDGAR требует в каждом запросе имя и почту спрашивающего, без этого блокирует. Впиши в .env строку SEC_CONTACT=\"Имя Фамилия почта@пример.com\" и перезапусти Сену.")
    return {"User-Agent": contact}


def _explain(reply):
    if reply.status_code == 403:
        return "SEC EDGAR отказал (403): адрес или контакт в SEC_CONTACT не приняты, либо слишком частые запросы. Подожди немного и проверь SEC_CONTACT в .env."
    if reply.status_code == 404:
        return "SEC EDGAR: такого документа или компании нет"
    return None


def _json(url, ttl=CACHE_SECONDS):
    seen = {}

    def explain(reply):
        seen["status"] = reply.status_code
        return _explain(reply)
    try:
        return net.as_json(net.get(url, None, _headers(), timeout=40, ttl=ttl, who="SEC EDGAR", error=SecError, explain=explain), "SEC EDGAR", SecError)
    except SecError as e:
        if seen.get("status") == 404:
            raise SecAbsent(str(e)) from e
        raise


def _company(query):
    """Company by ticker (AAPL) or name (Apple): (ten-digit cik, name, ticker)."""
    query = str(query or "").strip()
    if not query:
        raise SecError("Какая компания? Тикер (AAPL) или название")
    rows = list(_json(TICKERS, 86400).values())
    exact = [r for r in rows if r["ticker"].lower() == query.lower()]
    words = [w for w in re.findall(r"\w+", query.lower())]
    named = sorted((r for r in rows if words and all(w in r["title"].lower() for w in words)), key=lambda r: len(r["title"]))
    found = exact or named
    if not found:
        raise SecError(f"Компании «{query}» нет в списке SEC (там только компании США). Попробуй тикер.")
    top = found[0]
    return f"{int(top['cik_str']):010d}", top["title"], top["ticker"]


def sec_filings(company, form="", limit=8):
    """The company's recent SEC filings: optionally filtered to annual (10-K), quarterly (10-Q) or events (8-K). With links to the filings themselves."""
    cik, title, ticker = _company(company)
    data = _json(SUBMISSIONS.format(cik))
    recent = (data.get("filings") or {}).get("recent") or {}
    form = str(form or "").strip().upper()
    limit = clamp(limit, 8, 1, 20)
    rows = []
    for i, f in enumerate(recent.get("form") or []):
        if form and not f.upper().startswith(form):
            continue
        acc, doc = recent["accessionNumber"][i], recent["primaryDocument"][i]
        rows.append(f"{recent['filingDate'][i]}: {f}" + (f" ({FORMS[f]})" if f in FORMS else "") + (f", за период по {recent['reportDate'][i]}" if recent.get("reportDate", [""] * (i + 1))[i] else "")
                    + f"\n   https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/{doc}")
        if len(rows) >= limit:
            break
    head = f"{title} ({ticker}), {data.get('sicDescription') or 'отрасль не указана'}; финансовый год заканчивается {str(data.get('fiscalYearEnd') or '')[:2]}.{str(data.get('fiscalYearEnd') or '')[2:]}"
    return external(head + "\n" + ("\n".join(f"{i}. {r}" for i, r in enumerate(rows, 1)) if rows else f"Документов формы {form} не нашла."), "SEC EDGAR")


def _periods(entries):
    """Annual and quarterly values: {period end: value}, one per period (the most recently published one is kept)."""
    year, quarter = {}, {}
    for e in sorted(entries, key=lambda e: e.get("filed", "")):
        if not e.get("start") or not e.get("end"):
            continue
        days = (date.fromisoformat(e["end"]) - date.fromisoformat(e["start"])).days
        if 350 <= days <= 380:
            year[e["end"]] = e["val"]
        elif 80 <= days <= 100:
            quarter[e["end"]] = e["val"]
    return year, quarter


def _same_a_year_ago(values, end):
    before = date.fromisoformat(end).replace(year=date.fromisoformat(end).year - 1) if not (date.fromisoformat(end).month == 2 and date.fromisoformat(end).day == 29) else date(date.fromisoformat(end).year - 1, 2, 28)
    near = [v for e, v in values.items() if abs((date.fromisoformat(e) - before).days) <= 15]
    return near[0] if near else None


def _show(value, unit):
    return f"{finance.fmt(value, 2)} $" if unit == "USD/shares" else f"{finance.big(value)} $"


def _change(now, before):
    return f" (год назад {_show(before[0], before[1])}, {finance.trend((now - before[0]) / abs(before[0]) * 100)})" if before and before[0] else ""


def sec_financials(company):
    """Key figures from the filings: revenue, operating and net profit, earnings per share; for the latest year and the latest quarter, with the year-over-year change."""
    cik, title, ticker = _company(company)

    def one(metric):
        label, tags, unit = metric
        failed = None
        for tag in tags:
            try:
                entries = (_json(CONCEPT.format(cik, tag)).get("units") or {}).get(unit) or []
            except SecAbsent:   # the company doesn't use this tag: the next one
                continue
            except SecError as e:   # SEC didn't answer: that is not "no data in the filings"
                failed = failed or e
                continue
            year, quarter = _periods(entries)
            if year or quarter:
                return label, unit, year, quarter, None
        return label, unit, {}, {}, failed
    with ThreadPoolExecutor(max_workers=len(METRICS)) as pool:
        results = list(pool.map(one, METRICS))
    if all(failed for *_, failed in results):
        raise SecError(f"SEC EDGAR сейчас не отвечает ({results[0][4]}). Попробуй позже.")
    lines = []
    for label, unit, year, quarter, failed in results:
        if failed and not (year or quarter):
            lines.append(f"{label}: не прочиталось ({str(failed)[:100]})")
            continue
        parts = []
        for name, values in (("за год", year), ("последний квартал", quarter)):
            if values:
                end = max(values)
                before = _same_a_year_ago(values, end)
                parts.append(f"{name} по {end}: {_show(values[end], unit)}" + _change(values[end], (before, unit) if before is not None else None))
        lines.append(f"{label}: " + ("; ".join(parts) if parts else "нет данных в отчётах"))
    return external(f"{title} ({ticker}), цифры из отчётов SEC:\n" + "\n".join(lines) + "\n(Это факты из отчётности, не прогноз и не совет. Подробности и примечания: sec_filings.)", "SEC EDGAR")


TOOLS = [
    tool("sec_filings", "Отчёты американской компании в SEC EDGAR: последние документы с датами и ссылками. form: 10-K (годовой), 10-Q (квартальный), 8-K (событие), 4 (сделки инсайдеров), DEF 14A. Тикер или название. Компании США и иностранные эмитенты.",
         {"company": string("Тикер или название: AAPL, Tesla"), "form": string("Тип документа: 10-K, 10-Q, 8-K; пусто — все"), "limit": {"type": "integer", "description": "Сколько документов, от 1 до 20, по умолчанию 8"}}, ["company"]),
    tool("sec_financials", "Ключевые цифры компании США из отчётов SEC: выручка, операционная и чистая прибыль, прибыль на акцию за последний год и квартал, с изменением к прошлому году. Только факты, без советов.",
         {"company": string("Тикер или название: AAPL, Nvidia")}, ["company"]),
]
FUNCTIONS = {"sec_filings": sec_filings, "sec_financials": sec_financials}
