"""TV shows from TVMaze (an open database of shows and air schedules): when the next episode airs, what the last one was, status, network, rating. No key needed."""
import html
import re
from datetime import datetime

from sena.core.safety import external
from sena.core.tools import string, tool
from sena.world import net

API = "https://api.tvmaze.com/singlesearch/shows"
HEADERS = {"User-Agent": "Sena/1.0 (https://github.com/AStorozh/Sena)"}
CACHE_SECONDS = 1800
STATUS = {"Running": "идёт", "Ended": "закончился", "To Be Determined": "судьба не решена", "In Development": "в разработке"}


class TVError(net.WorldError):
    pass


def _episode(e):
    if not e:
        return ""
    when = ""
    if e.get("airstamp"):
        moment = datetime.fromisoformat(e["airstamp"].replace("Z", "+00:00")).astimezone()   # in this Mac's local time: "your time"
        when = f", {moment:%d.%m.%Y %H:%M} (у тебя)"
    elif e.get("airdate"):
        when = f", {e['airdate']}"
    number = f"серия {e['number']}" if e.get("number") else "спецвыпуск"
    return f"сезон {e.get('season')}, {number} «{e.get('name')}»{when}"


def tv_show(name):
    """A TV show: status, when the next episode airs, what the last one was, where it airs, rating."""
    name = str(name or "").strip()
    if not name:
        raise TVError("Какой сериал?")
    reply = net.get(API, {"q": name, "embed[]": ["nextepisode", "previousepisode"]}, HEADERS, ttl=CACHE_SECONDS, who="TVMaze", error=TVError,
                    explain=lambda r: f"Сериала «{name}» в TVMaze нет. Попробуй название по-английски." if r.status_code == 404 else None)
    show = net.as_json(reply, "TVMaze", TVError)
    embedded = show.get("_embedded") or {}
    nxt, prev = embedded.get("nextepisode"), embedded.get("previousepisode")
    where = (show.get("network") or show.get("webChannel") or {}).get("name", "")
    summary = html.unescape(re.sub(r"<[^>]+>", "", show.get("summary") or "")).strip()
    lines = [f"{show.get('name')} ({', '.join(show.get('genres') or [])}): {STATUS.get(show.get('status'), show.get('status'))}"
             + (f"; на {where}" if where else "") + (f"; с {show['premiered']}" if show.get("premiered") else "") + (f"; рейтинг {show['rating']['average']}" if (show.get("rating") or {}).get("average") else ""),
             f"Следующая серия: {_episode(nxt)}" if nxt else ("Следующей серии нет: сериал закончился." if show.get("status") == "Ended" else "Дата следующей серии пока не объявлена.")]
    if prev:
        lines.append(f"Последняя вышла: {_episode(prev)}")
    if summary:
        lines.append(f"О чём: {summary[:400]}{'…' if len(summary) > 400 else ''}")
    lines.append(show.get("url", ""))
    return external("\n".join(x for x in lines if x), "TVMaze")


TOOLS = [tool("tv_show", "Сериал (TVMaze): статус, когда выходит следующая серия (по твоим часам), какая вышла последней, где идёт, рейтинг. Название лучше по-английски.",
              {"name": string("Название сериала: Severance")}, ["name"])]
FUNCTIONS = {"tv_show": tv_show}
