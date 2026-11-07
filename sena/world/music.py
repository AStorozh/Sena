"""Music from MusicBrainz (an open music encyclopedia): artists, albums, songs, release dates. No key needed; the site asks clients to identify themselves and limits to one request per second."""
import re
import time

from sena.core.safety import external
from sena.core.tools import string, tool
from sena.world import net

BASE = "https://musicbrainz.org/ws/2/"
HEADERS = {"User-Agent": "Sena/1.0 (https://github.com/AStorozh/Sena)"}
CACHE_SECONDS = 3600
PAUSE = 1.1   # MusicBrainz's rule: no more than one request per second
LUCENE = re.compile(r'[+\-&|!(){}\[\]^"~*?:\\/]')   # search special characters: they do appear in titles, but break the query


class MusicError(net.WorldError):
    pass


def _get(path, params):
    return net.as_json(net.get(BASE + path, {**params, "fmt": "json"}, HEADERS, ttl=CACHE_SECONDS, who="MusicBrainz", error=MusicError), "MusicBrainz", MusicError)


def _credit(item):
    return ", ".join(c.get("name", "") for c in item.get("artist-credit", []) if isinstance(c, dict))


def _albums(query):
    rows = _get("release-group/", {"query": query, "limit": 5}).get("release-groups") or []
    return [f"{i}. {r['title']} — {_credit(r)}; {r.get('first-release-date') or 'дата неизвестна'}; "
            f"{', '.join([r.get('primary-type', '')] + r.get('secondary-types', [])).strip(', ') or 'тип не указан'}" for i, r in enumerate(rows, 1)]


def _tracks(query):
    lines = []
    for i, r in enumerate(_get("recording/", {"query": query, "limit": 5}).get("recordings") or [], 1):
        releases = r.get("releases") or []
        where = f"; в «{releases[0]['title']}»" if releases else ""
        lines.append(f"{i}. {r['title']} — {_credit(r)}; {r.get('first-release-date') or 'дата неизвестна'}{where}"
                     + (f"; {r['length'] // 60000}:{r['length'] // 1000 % 60:02d}" if r.get("length") else ""))
    return lines


def _artist(query):
    # the best match with its studio albums and EPs, newest first (a second request: one second apart), and its namesakes
    artists = _get("artist/", {"query": query, "limit": 3}).get("artists") or []
    if not artists:
        return []
    top = artists[0]
    span = top.get("life-span") or {}
    tags = [t["name"] for t in sorted(top.get("tags") or [], key=lambda t: -t.get("count", 0))[:4]]
    head = (f"{top['name']} ({top.get('type', 'артист')}; {top.get('country') or 'страна не указана'}; "
            f"{span.get('begin') or '?'}–{'по сей день' if not span.get('ended') else span.get('end') or '?'}"
            + (f"; {top['disambiguation']}" if top.get("disambiguation") else "") + (f"; {', '.join(tags)}" if tags else "") + ")")
    time.sleep(PAUSE)
    groups = _get("release-group", {"artist": top["id"], "type": "album|ep", "limit": 100}).get("release-groups") or []
    studio = sorted((g for g in groups if not g.get("secondary-types") and g.get("first-release-date")), key=lambda g: g["first-release-date"], reverse=True)
    lines = [head, f"Студийных альбомов и EP: {len(studio)}. Последние:"] + [f"  {g['first-release-date']}: {g['title']} ({g.get('primary-type', '')})" for g in studio[:10]]
    others = [a['name'] for a in artists[1:] if a.get("name") != top["name"]]
    return lines + (["Тёзки и похожие имена: " + ", ".join(others)] if others else [])


KINDS = {"artist": _artist, "album": _albums, "track": _tracks}   # kind= -> its search


def music_search(query, kind="artist"):
    """Artist (kind=artist: about them and their albums by year), album (album), or song (track): release dates, performer, country."""
    query = LUCENE.sub(" ", str(query or "")).strip()
    if not query:
        raise MusicError("Что искать: артиста, альбом или песню?")
    lines = KINDS.get(kind, _artist)(query)
    return external("\n".join(lines), "MusicBrainz") if lines else f"В MusicBrainz по «{query}» ничего нет."


TOOLS = [tool("music_search", "Музыка (MusicBrainz): артист (kind=artist: страна, годы, жанры и его альбомы с датами выхода, свежие сверху), альбом (kind=album) или песня (kind=track) "
                              "с исполнителем и датой первого выхода. Для «когда вышел альбом», «что у артиста нового», «чья это песня». Названия лучше как на обложке.",
              {"query": string("Имя артиста, название альбома или песни"), "kind": {"type": "string", "enum": list(KINDS), "description": "artist (по умолчанию), album или track"}}, ["query"])]
FUNCTIONS = {"music_search": music_search}
