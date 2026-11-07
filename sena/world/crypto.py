"""The crypto market beyond price, with no keys and no access to money: candles and volume from Binance (spot), the funding rate and open interest
of Binance perpetual futures, the fear and greed index (alternative.me), money in blockchains, protocols and stablecoins and their yield (DeFiLlama). Public data only: numbers and trends, not advice."""
import re
from datetime import datetime, timezone

from sena.core.tools import clamp, string, tool
from sena.world import finance, net

SPOT = "https://api.binance.com/api/v3/"
FUTURES = "https://fapi.binance.com/fapi/v1/"
FEAR = "https://api.alternative.me/fng/"
LLAMA = "https://api.llama.fi/"
STABLES = "https://stablecoins.llama.fi/stablecoins"
YIELDS = "https://yields.llama.fi/pools"
HEADERS = {"User-Agent": "Mozilla/5.0 (Sena personal assistant)"}
CACHE_SECONDS = 60
INTERVALS = ("15m", "1h", "4h", "1d", "1w")
MOODS = {"Extreme Fear": "крайний страх", "Fear": "страх", "Neutral": "нейтрально", "Greed": "жадность", "Extreme Greed": "крайняя жадность"}
SHOWN = 7   # candles shown one by one; the rest only appear in the totals


class CryptoError(finance.FinanceError):
    pass


def _explain(reply):
    if reply.status_code in (451, 403):
        return "Binance не отвечает на запросы из этой страны или сети (код %d)" % reply.status_code
    if reply.status_code == 400:
        return "Binance не знает такой пары: проверь тикер монеты (BTC, ETH, SOL)"
    if reply.status_code in (418, 429):
        return "Binance просит подождать: слишком много запросов"
    return None


def _get(base, path, params=None):
    return net.as_json(net.get(base + path, params, HEADERS, ttl=CACHE_SECONDS, who="Binance", error=CryptoError, explain=_explain), "Binance", CryptoError)


def _symbol(value):
    coin = str(value or "").upper().strip().replace("-USD", "").replace("/USDT", "").replace("USDT", "")
    if not re.fullmatch(r"[A-Z0-9]{2,12}", coin):
        raise CryptoError("Тикер монеты пишется как BTC, ETH, SOL")
    return coin


def _when(ms, interval):
    moment = datetime.fromtimestamp(ms / 1000, timezone.utc)
    return moment.strftime("%m-%d %H:%M") if interval.endswith(("m", "h")) else moment.strftime("%Y-%m-%d")


# ---------- candles and volume ----------
def crypto_candles(symbol, interval="1d", limit=30):
    """Binance candles (spot, pair against USDT): how the price and volume moved over the period. interval: 15m, 1h, 4h, 1d, 1w."""
    coin = _symbol(symbol)
    interval = interval if interval in INTERVALS else "1d"
    limit = clamp(limit, 30, 8, 200)
    rows = _get(SPOT, "klines", {"symbol": f"{coin}USDT", "interval": interval, "limit": limit})
    if not isinstance(rows, list) or len(rows) < 2:
        raise CryptoError(f"По {coin} нет свечей")
    first_open, last_close = float(rows[0][1]), float(rows[-1][4])
    high, low = max(float(r[2]) for r in rows), min(float(r[3]) for r in rows)
    volumes = [float(r[7]) for r in rows]   # volume in USDT: so it can be compared with other coins
    swing = sum((float(r[2]) - float(r[3])) / float(r[3]) * 100 for r in rows if float(r[3])) / len(rows)
    recent, before = volumes[-SHOWN:], volumes[:-SHOWN]
    lines = [f"{coin}/USDT, свечи {interval}, {len(rows)} шт. (с {_when(rows[0][0], interval)} по {_when(rows[-1][0], interval)}, Binance спот)",
             f"Цена: {finance.fmt(first_open)} → {finance.fmt(last_close)} ({finance.trend((last_close - first_open) / first_open * 100)}); максимум {finance.fmt(high)}, минимум {finance.fmt(low)}",
             f"Размах свечи в среднем {finance.fmt(swing, 1)}%; объём в среднем {finance.big(sum(volumes) / len(volumes))} USDT за свечу"]
    if before:
        lines.append(f"Объём последних {len(recent)} свечей против прежних: {finance.trend((sum(recent) / len(recent)) / (sum(before) / len(before)) * 100 - 100)}")
    lines.append(f"Последние {len(recent)}:")
    for r in rows[-SHOWN:]:
        lines.append(f"{_when(r[0], interval)}: {finance.fmt(float(r[1]))} → {finance.fmt(float(r[4]))} (макс {finance.fmt(float(r[2]))}, мин {finance.fmt(float(r[3]))}), объём {finance.big(float(r[7]))} USDT")
    return "\n".join(lines)


# ---------- futures: funding rate and open interest ----------
def crypto_derivatives(symbol):
    """Binance perpetual futures: funding rate, premium over spot, open interest."""
    coin = _symbol(symbol)
    pair = f"{coin}USDT"
    mark = _get(FUTURES, "premiumIndex", {"symbol": pair})
    interest = _get(FUTURES, "openInterest", {"symbol": pair})
    history = _get(FUTURES, "fundingRate", {"symbol": pair, "limit": 9})   # 9 payouts of 8 hours each — three days
    price, index, rate = float(mark["markPrice"]), float(mark["indexPrice"]), float(mark["lastFundingRate"])
    contracts = float(interest["openInterest"])
    past = [float(h["fundingRate"]) for h in history] if isinstance(history, list) else []
    pct = lambda x: f"{x * 100:+.4f}%".replace(".", ",").replace("-", "−")   # rates are tiny: two decimal places would lose all the detail
    lines = [f"{coin}, бессрочные фьючерсы Binance",
             f"Ставка финансирования: {pct(rate)} за 8 ч (≈ {finance.trend(rate * 3 * 365 * 100)} годовых)"
             + (f"; среднее за трое суток {pct(sum(past) / len(past))}" if past else ""),
             "Плюс: покупатели платят продавцам (рынок тянет вверх), минус: наоборот. Выплата каждые 8 часов, следующая в "
             + datetime.fromtimestamp(int(mark["nextFundingTime"]) / 1000, timezone.utc).strftime("%H:%M UTC"),
             f"Цена фьючерса {finance.fmt(price)} против индекса {finance.fmt(index)} (премия {finance.trend((price - index) / index * 100)})",
             f"Открытый интерес: {finance.fmt(contracts, 0)} {coin} ≈ {finance.big(contracts * price)} USDT"]
    return "\n".join(lines)


# ---------- fear and greed index ----------
def fear_greed():
    """Crypto market fear and greed index: 0 — extreme fear, 100 — extreme greed."""
    data = net.as_json(net.get(FEAR, {"limit": 31}, HEADERS, ttl=600, who="alternative.me", error=CryptoError), "alternative.me", CryptoError).get("data") or []
    if not data:
        raise CryptoError("Индекс страха и жадности сейчас недоступен")
    mood = lambda row: f"{row['value']} ({MOODS.get(row['value_classification'], row['value_classification'])})"
    line = f"Индекс страха и жадности крипторынка сегодня: {mood(data[0])}, шкала от 0 до 100."
    back = [f"{name} {mood(data[i])}" for name, i in (("вчера", 1), ("неделю назад", 7), ("месяц назад", 30)) if i < len(data)]
    return line + (" " + ", ".join(back).capitalize() + "." if back else "")


# ---------- DeFi: money in blockchains and protocols (DeFiLlama) ----------
def _llama(url, params=None, ttl=600):
    return net.as_json(net.get(url, params, HEADERS, timeout=60, ttl=ttl, who="DeFiLlama", error=CryptoError), "DeFiLlama", CryptoError)


def _change(p, key):
    return finance.trend(p[key]) if p.get(key) is not None else "—"


def _chains(query, limit):
    # where the most money sits, chain by chain (the query isn't needed: there are few of them)
    rows = sorted((c for c in _llama(LLAMA + "v2/chains") if c.get("tvl")), key=lambda c: -c["tvl"])
    lines = [f"{i}. {c['name']}: {finance.big(c['tvl'])} $" + (f" ({c['tokenSymbol']})" if c.get("tokenSymbol") else "") for i, c in enumerate(rows[:limit], 1)]
    return f"Деньги, запертые в DeFi по блокчейнам (TVL), всего {finance.big(sum(c['tvl'] for c in rows))} $:\n" + "\n".join(lines)


def _protocols(query, limit):
    # the biggest protocols, or the ones whose name has the query; custodial exchanges aren't protocols
    rows = [p for p in _llama(LLAMA + "protocols") if isinstance(p, dict) and p.get("tvl") and (query in p.get("name", "").lower() if query else p.get("category") != "CEX")]
    rows.sort(key=lambda p: -p["tvl"])
    if not rows:
        return f"Протокола «{query}» в DeFiLlama нет."
    return "\n".join(f"{i}. {p['name']} ({p.get('category', '')}): {finance.big(p['tvl'])} $; за сутки {_change(p, 'change_1d')}, за неделю {_change(p, 'change_7d')}; "
                     f"сетей {len(p.get('chains') or [])}" for i, p in enumerate(rows[:limit], 1))


def _stablecoins(query, limit):
    # how much of each is in circulation and whether it still holds the dollar
    rows = sorted((s for s in _llama(STABLES, {"includePrices": "true"}).get("peggedAssets", []) if (s.get("circulating") or {}).get("peggedUSD")),
                  key=lambda s: -s["circulating"]["peggedUSD"])
    lines = []
    for i, s in enumerate(rows[:limit], 1):
        now, week, price = s["circulating"]["peggedUSD"], (s.get("circulatingPrevWeek") or {}).get("peggedUSD"), s.get("price")
        lines.append(f"{i}. {s['name']} ({s['symbol']}): {finance.big(now)} $" + (f"; за неделю {finance.trend((now - week) / week * 100)}" if week else "")
                     + (f"; цена {finance.fmt(price, 4)} $" + (" (ОТКЛОНЕНИЕ от доллара)" if abs(price - 1) > 0.005 else "") if price else "") + f"; {s.get('pegMechanism', '')}")
    return f"Стейблкоины в обращении, всего {finance.big(sum(s['circulating']['peggedUSD'] for s in rows))} $:\n" + "\n".join(lines)


def _yields(query, limit):
    # the highest stablecoin yields in big single-asset pools (no impermanent loss), with the risk said every time
    rows = [p for p in _llama(YIELDS, ttl=900).get("data") or []
            if p.get("stablecoin") and (p.get("tvlUsd") or 0) >= 20e6 and p.get("apy") is not None and p.get("ilRisk") == "no" and p.get("exposure") == "single"
            and (not query or query in f"{p.get('project', '')} {p.get('symbol', '')} {p.get('chain', '')}".lower())]
    rows.sort(key=lambda p: -p["apy"])
    if not rows:
        return "Крупных пулов по стейблкоинам с такими условиями нет."
    return ("Самая высокая доходность по стейблкоинам (пулы от 20 млн $, без непостоянных потерь):\n"
            + "\n".join(f"{i}. {p['project']} / {p['chain']} / {p['symbol']}: {finance.fmt(p['apy'], 2)}% годовых (база {finance.fmt(p.get('apyBase') or 0, 2)}%, "
                        f"награды токенами {finance.fmt(p.get('apyReward') or 0, 2)}%); пул {finance.big(p['tvlUsd'])} $" for i, p in enumerate(rows[:limit], 1))
            + "\nВысокая доходность почти всегда значит высокий риск: ошибки в смарт-контрактах, награды в падающем токене, отвязка стейблкоина. Это цифры, не совет.")


DEFI = {"chains": _chains, "protocols": _protocols, "stablecoins": _stablecoins, "yields": _yields}   # what= -> its view


def defi(what="chains", query="", limit=10):
    """DeFi in numbers: chains (where the most money sits), protocols (protocols by volume, filterable by name), stablecoins (stablecoins and their peg), yields (the highest stablecoin yields)."""
    return DEFI.get(what, _chains)(str(query or "").strip().lower(), clamp(limit, 10, 1, 20))


TOOLS = [
    tool("crypto_candles", "История цены и объёмов монеты на Binance (спот): куда шла цена, насколько качало, рос или падал объём. Свечи 15m, 1h, 4h, 1d, 1w. "
                           "Для вопросов «как BTC вёл себя за месяц», «растёт ли интерес». Только цифры, советов не давай.",
         {"symbol": string("Тикер монеты: BTC, ETH, SOL"), "interval": {"type": "string", "enum": list(INTERVALS), "description": "Размер свечи, по умолчанию 1d"},
          "limit": {"type": "integer", "description": "Сколько свечей, от 8 до 200, по умолчанию 30"}}, ["symbol"]),
    tool("crypto_derivatives", "Фьючерсы монеты на Binance: ставка финансирования (кто кому платит, перегрет ли рынок), премия к споту, открытый интерес. Только цифры.",
         {"symbol": string("Тикер монеты: BTC, ETH, SOL")}, ["symbol"]),
    tool("fear_greed", "Индекс страха и жадности крипторынка: одна цифра от 0 до 100 и как он менялся за вчера, неделю и месяц."),
    tool("defi", "DeFi в цифрах (DeFiLlama): what=chains (сколько денег в каких блокчейнах), protocols (крупнейшие протоколы, query — по названию: aave, uniswap), "
                 "stablecoins (объём и курс стейблкоинов, отклонение от доллара), yields (где самая высокая доходность по стейблкоинам). Только цифры и риски, не советы вложить.",
         {"what": {"type": "string", "enum": list(DEFI), "description": "chains (по умолчанию), protocols, stablecoins или yields"}, "query": string("Название протокола или слово для отбора"),
          "limit": {"type": "integer", "description": "Сколько строк, от 1 до 20, по умолчанию 10"}}),
]
FUNCTIONS = {"crypto_candles": crypto_candles, "crypto_derivatives": crypto_derivatives, "fear_greed": fear_greed, "defi": defi}
