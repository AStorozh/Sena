"""Finance with no keys and no paid subscriptions: exchange rates, crypto, stocks, indexes, gold, the Moscow Exchange.
Sources: open.er-api.com (exchange rates, 160+ currencies) and the Russian Central Bank, Binance and Coinbase (real-time crypto), Yahoo Finance
(stocks, indexes, commodities; unofficial, can sometimes be unresponsive) and the Moscow Exchange (ISS). Quotes are delayed by up to 15 minutes.
These are numbers and trends, not investment advice."""
from concurrent.futures import ThreadPoolExecutor

from sena.core.tools import string, strings, tool
from sena.world import net

ER = "https://open.er-api.com/v6/latest/{}"
CBR = "https://www.cbr-xml-daily.ru/daily_json.js"
BINANCE = "https://api.binance.com/api/v3/ticker/24hr"
COINBASE = "https://api.coinbase.com/v2/prices/{}-USD/spot"
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{}"
MOEX = "https://iss.moex.com/iss/engines/stock/markets/shares/boards/TQBR/securities/{}.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (Sena personal assistant)"}
SUMMARY = [("fx", "USD/VND"), ("fx", "USD/RUB"), ("fx", "EUR/USD"), ("crypto", "BTC"), ("crypto", "ETH"),
           ("stock", "^GSPC"), ("stock", "^IXIC"), ("stock", "GC=F"), ("stock", "BZ=F")]
NAMES = {"^GSPC": "S&P 500", "^IXIC": "Nasdaq", "^DJI": "Dow Jones", "GC=F": "Золото (унция)", "SI=F": "Серебро (унция)",
         "BZ=F": "Нефть Brent", "CL=F": "Нефть WTI", "^N225": "Nikkei 225", "^FTSE": "FTSE 100", "^GDAXI": "DAX"}


class FinanceError(net.WorldError):
    pass


def fmt(x, digits=None):
    """Number formatted the Russian way: a space between thousands, a comma for the decimal part; as many digits as the price needs."""
    if digits is None:
        digits = 0 if abs(x) >= 1000 else 2 if abs(x) >= 1 else 4 if abs(x) >= 0.01 else 6
    return f"{x:,.{digits}f}".replace(",", " ").replace(".", ",")


def big(x):
    """Large amounts in words: 2.4 trillion, 1.2 billion, 850 million."""
    for size, word in ((1e12, "трлн"), (1e9, "млрд"), (1e6, "млн")):
        if abs(x) >= size:
            return f"{fmt(x / size, 1 if abs(x) < size * 100 else 0)} {word}"
    return fmt(x, 0)


DAILY = 3600   # s the exchange rates are kept: both sources update once a day, and price alerts ask every few minutes


def _get(url, params=None, ttl=0):
    return net.as_json(net.get(url, params, HEADERS, error=FinanceError, ttl=ttl), "источник котировок", FinanceError)


def _percent(now, before):
    return (now - before) / before * 100 if before else 0.0


def trend(pct):
    return f"{'+' if pct >= 0 else '−'}{abs(pct):.2f}%".replace(".", ",")


# ---------- exchange rates ----------
def rates(base):
    data = _get(ER.format(base.upper()), ttl=DAILY)
    if data.get("result") != "success":
        raise FinanceError(f"Валюта «{base}» не найдена")
    return data["rates"], data.get("time_last_update_utc", "")[5:22]


def exchange_rate(base, quote, amount=1):
    """Exchange rate and amount conversion: 100 USD to VND, how many rubles in a euro."""
    base, quote, amount = base.upper().strip(), quote.upper().strip(), float(amount)
    table, updated = rates(base)
    if quote not in table:
        raise FinanceError(f"Валюты «{quote}» нет в списке (коды вида USD, EUR, VND, RUB, THB)")
    rate = table[quote]
    lines = [f"{fmt(amount, 0 if amount == int(amount) else 2)} {base} = {fmt(amount * rate)} {quote} (курс 1 {base} = {fmt(rate)} {quote}; обновлено {updated} UTC)"]
    if "RUB" in (base, quote) and base != quote:   # the official Central Bank rate, if the ruble is in the pair
        try:
            cbr = _get(CBR, ttl=DAILY)
            code = quote if base == "RUB" else base
            if code in cbr["Valute"]:
                v = cbr["Valute"][code]
                per_unit = v["Value"] / v["Nominal"]
                lines.append(f"Официальный курс ЦБ РФ на {cbr['Date'][:10]}: 1 {code} = {fmt(per_unit)} RUB")
        except FinanceError:
            pass
    return "\n".join(lines)


# ---------- crypto ----------
def _crypto(symbol):
    sym = symbol.upper().strip().replace("-USD", "").replace("USDT", "")
    try:
        t = _get(BINANCE, {"symbol": f"{sym}USDT"})
        price = float(t["lastPrice"])
        return {"symbol": sym, "price": price, "change": float(t["priceChangePercent"]),
                "high": float(t["highPrice"]), "low": float(t["lowPrice"])}
    except FinanceError:
        data = _get(COINBASE.format(sym))   # fallback source: no 24-hour change available
        return {"symbol": sym, "price": float(data["data"]["amount"]), "change": None, "high": None, "low": None}


def crypto_price(symbols, vs="USD"):
    """Price and 24-hour change: BTC, ETH, SOL... Can be shown directly in another currency (vs: VND, RUB, EUR)."""
    symbols = [s.strip() for s in (symbols.split(",") if isinstance(symbols, str) else symbols) if s.strip()]
    vs = vs.upper()
    factor = 1.0
    if vs not in ("USD", "USDT"):
        factor = rates("USD")[0].get(vs)
        if not factor:
            raise FinanceError(f"Валюты «{vs}» нет в списке")
    lines = []
    for s in symbols[:10]:
        try:
            c = _crypto(s)
        except FinanceError as e:
            lines.append(f"{s.upper()}: не удалось получить цену ({e})")
            continue
        line = f"{c['symbol']}: {fmt(c['price'] * factor)} {vs}"
        if c["change"] is not None:
            line += f" ({trend(c['change'])} за сутки; сутки: {fmt(c['low'] * factor)}–{fmt(c['high'] * factor)})"
        lines.append(line)
    return "\n".join(lines) or "Нечего показать"


# ---------- stocks, indexes, commodities ----------
def _moex(secid):
    data = _get(MOEX.format(secid), {"iss.meta": "off", "iss.only": "marketdata,securities",
                                     "marketdata.columns": "SECID,LAST,LASTTOPREVPRICE,UPDATETIME",
                                     "securities.columns": "SECID,SHORTNAME,PREVPRICE"})
    market, names = data["marketdata"]["data"], data["securities"]["data"]
    if not market or market[0][1] is None:
        raise FinanceError(f"На Мосбирже нет цены для «{secid}»")
    return {"symbol": f"MOEX:{secid}", "name": names[0][1] if names else secid, "price": market[0][1], "change": market[0][2] or 0.0,
            "currency": "RUB", "extra": f"обновлено {market[0][3]}"}


def _yahoo(symbol):
    data = _get(YAHOO.format(symbol), {"range": "5d", "interval": "1d"})
    result = (data.get("chart", {}).get("result") or [None])[0]
    if not result:
        raise FinanceError(f"Тикер «{symbol}» не найден")
    meta = result.get("meta") or {}
    price = meta.get("regularMarketPrice")
    if not isinstance(price, (int, float)):   # seen for delisted and odd symbols: an answer, but no price in it
        raise FinanceError(f"У «{symbol}» сейчас нет цены (источник ответил без неё)")
    closes = [c for c in ((result.get("indicators", {}).get("quote") or [{}])[0].get("close") or []) if c is not None]
    prev = closes[-2] if len(closes) >= 2 else meta.get("previousClose") or meta.get("chartPreviousClose")   # yesterday's close, not a week-old one
    return {"symbol": symbol, "name": NAMES.get(symbol) or meta.get("longName") or meta.get("shortName") or symbol, "price": price,
            "change": _percent(price, prev), "currency": meta.get("currency", ""), "extra": meta.get("exchangeName", "")}


def stock_quote(symbols):
    """Price and daily change: stocks (AAPL), indexes (^GSPC), gold (GC=F), oil (BZ=F), pairs (EURUSD=X), Moscow Exchange (MOEX:SBER)."""
    symbols = [s.strip() for s in (symbols.split(",") if isinstance(symbols, str) else symbols) if s.strip()]
    lines = []
    for s in symbols[:10]:
        try:
            q = _moex_or_yahoo(s)
        except FinanceError as e:
            lines.append(f"{s}: не удалось получить цену ({e})")
            continue
        lines.append(f"{q['name']} ({q['symbol']}): {fmt(q['price'])} {q['currency']} ({trend(q['change'])} за день)"
                     + (f"; {q['extra']}" if q["extra"] else ""))
    return "\n".join(lines) or "Нечего показать"


def _moex_or_yahoo(symbol):
    return _moex(symbol.split(":", 1)[1].upper()) if symbol.upper().startswith("MOEX:") else _yahoo(symbol)


def price_of(kind, symbol):
    """A single number for alerts: a ticker's price, a coin's price, or a pair's rate like USD/VND."""
    if kind == "crypto":
        return _crypto(symbol)["price"]
    if kind == "stock":
        return _moex_or_yahoo(symbol)["price"]
    if kind == "fx":
        base, _, quote = symbol.upper().replace(" ", "").partition("/")
        if not base or not quote:
            raise FinanceError("Валютная пара пишется через дробь, например USD/VND")
        table = rates(base)[0]
        if quote not in table:
            raise FinanceError(f"Валюты «{quote}» нет в списке")
        return table[quote]
    raise FinanceError("kind — одно из: crypto, stock, fx")


def _summary_row(kind, symbol):
    try:
        if kind == "fx":
            value = price_of("fx", symbol)
            return {"kind": kind, "symbol": symbol, "name": symbol, "price": value, "change": None, "currency": "", "digits": 4 if value < 10 else 2}
        if kind == "crypto":
            c = _crypto(symbol)
            return {"kind": kind, "symbol": c["symbol"], "name": c["symbol"], "price": c["price"], "change": c["change"], "currency": "USD"}
        q = _moex_or_yahoo(symbol)
        return {"kind": kind, "symbol": symbol, "name": q["name"], "price": q["price"], "change": q["change"], "currency": q["currency"]}
    except FinanceError as e:
        return {"kind": kind, "symbol": symbol, "name": symbol, "error": str(e)}


def summary_rows(items=None):
    """Market summary as rows for market_summary: [{symbol, name, price, change, currency} or {symbol, error}]. Sources are queried simultaneously."""
    items = items or SUMMARY
    with ThreadPoolExecutor(max_workers=len(items)) as pool:
        return list(pool.map(lambda it: _summary_row(*it), items))


def _summary_line(r):
    if r.get("error"):
        return f"{r['symbol']}: нет данных ({r['error']})"
    if r["kind"] == "fx":
        return f"{r['symbol']}: {fmt(r['price'], r['digits'])}"
    line = f"{r['name']}: {fmt(r['price'])} {r['currency']}".rstrip()
    return line + (f" ({trend(r['change'])})" if r["change"] is not None else "")


def market_summary(items=None):
    """A one-message summary: major exchange rates, crypto, indexes, gold, oil."""
    return "\n".join(_summary_line(r) for r in summary_rows(items))


TOOLS = [
    tool("exchange_rate", "Курс валют и пересчёт суммы (USD, EUR, VND, RUB, THB, 160+ валют). Для рубля добавляет официальный курс ЦБ РФ.",
         {"base": string("Из какой валюты, код: USD"), "quote": string("В какую, код: VND"),
          "amount": {"type": "number", "description": "Сумма, по умолчанию 1"}}, ["base", "quote"]),
    tool("crypto_price", "Цена криптовалют и изменение за сутки: BTC, ETH, SOL и другие, можно сразу в другой валюте (vs: VND, RUB, EUR).",
         {"symbols": strings("Тикеры монет: BTC, ETH"), "vs": string("В чём показать, по умолчанию USD")}, ["symbols"]),
    tool("stock_quote", "Цена и изменение за день: акции (AAPL, TSLA), индексы (^GSPC S&P 500, ^IXIC Nasdaq), золото GC=F, нефть BZ=F, "
                        "валютные пары (EURUSD=X), Московская биржа через MOEX:SBER. Данные с задержкой до 15 минут.",
         {"symbols": strings("Тикеры")}, ["symbols"]),
    tool("market_summary", "Сводка рынков одним ответом: USD/VND, USD/RUB, EUR/USD, BTC, ETH, S&P 500, Nasdaq, золото, нефть."),
]
FUNCTIONS = {"exchange_rate": exchange_rate, "crypto_price": crypto_price, "stock_quote": stock_quote, "market_summary": market_summary}
