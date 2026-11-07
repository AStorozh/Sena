"""Words from Wiktionary (an open dictionary covering hundreds of languages): meanings, etymology, synonyms. No key needed. Russian words are taken from the Russian Wiktionary,
English and others from the English one; lang picks the edition."""
import re

from sena.core.safety import external
from sena.core.tools import string, tool
from sena.world import net

API = "https://{}.wiktionary.org/w/api.php"
HEADERS = {"User-Agent": "Sena/1.0 (https://github.com/AStorozh/Sena)"}
CACHE_SECONDS = 86400
LANG = re.compile(r"[a-z]{2,3}")
HEADING = re.compile(r"^(=+)\s*(.*?)\s*\1\s*$")
OWN = {"ru": "Русский", "en": "English"}   # the dictionary section about words of the edition's own language
WANT = {"ru": (("Значение", 800), ("Этимология", 700), ("Синонимы", 250), ("Фразеологизмы и устойчивые сочетания", 0)),
        "en": (("Etymology", 700), ("Noun", 800), ("Verb", 800), ("Adjective", 800), ("Adverb", 500), ("Synonyms", 250))}


class DictionaryError(net.WorldError):
    pass


def _sections(text, language):
    """Headings and the text under them for the requested language only (a single dictionary page can hold dozens of languages)."""
    blocks, current, inside, own = {}, None, False, None
    for line in text.splitlines():
        m = HEADING.match(line.strip())
        if m:
            level, title = len(m.group(1)), m.group(2)
            if level <= (1 if language == "ru" else 2):   # a language heading: "= Русский =" for Russian, "== English ==" for the English Wiktionary
                inside = title == OWN.get(language, title) or (own is None and language not in OWN)   # for an edition without a known name for its own language, take whichever section comes first
                own = own or inside
                if not inside:
                    current = None
                continue
            current = title if inside else None
            if current:
                blocks.setdefault(current, [])
        elif current and line.strip():
            blocks[current].append(line.strip())
    return {k: v for k, v in blocks.items() if v}


def word_info(word, lang="ru"):
    """A word's meaning and origin. lang — the Wiktionary edition: ru for Russian words, en for English and everything else."""
    word, lang = " ".join(str(word or "").split()), str(lang or "ru").strip().lower()
    if not word:
        raise DictionaryError("Какое слово?")
    if not LANG.fullmatch(lang):
        raise DictionaryError("Язык пишется кодом: ru или en")
    for candidate in dict.fromkeys([word, word.lower()]):
        pages = net.as_json(net.get(API.format(lang), {"action": "query", "prop": "extracts", "explaintext": 1, "titles": candidate, "redirects": 1, "format": "json", "formatversion": 2},
                                    HEADERS, ttl=CACHE_SECONDS, who="Викисловарь", error=DictionaryError), "Викисловарь", DictionaryError).get("query", {}).get("pages") or []
        if pages and not pages[0].get("missing") and pages[0].get("extract"):
            break
    else:
        return f"В Викисловаре ({lang}) слова «{word}» нет. Проверь написание или попробуй lang={'en' if lang == 'ru' else 'ru'}."
    parts = _sections(pages[0]["extract"], lang)
    out = []
    for title, limit in WANT.get(lang, WANT["en"]):
        lines = next((v for k, v in parts.items() if k == title or k.startswith(title + " ")), [])
        if lang == "ru" and title == "Значение":
            lines = [l.split("◆")[0].strip() for l in lines if l.split("◆")[0].strip()]   # book examples after ◆ aren't needed: the meanings are shorter and clearer without them
        elif title in ("Noun", "Verb", "Adjective", "Adverb"):
            lines = lines[:3]   # the form's heading and the first couple of senses; quotations follow after that
        text = " ".join(f"{i}) {l}" for i, l in enumerate(lines, 1)) if (lang == "ru" and title == "Значение") else " ".join(lines)
        if text and limit:
            out.append(f"{title}: {text[:limit]}{'…' if len(text) > limit else ''}")
    if not out:
        return external(f"{pages[0]['title']}: в словаре есть страница, но без разобранных значений. Смотри https://{lang}.wiktionary.org/wiki/{pages[0]['title'].replace(' ', '_')}", "Викисловарь")
    return external(f"{pages[0]['title']} (Викисловарь, {lang}):\n" + "\n".join(out), "Викисловарь")


TOOLS = [tool("word_info", "Значение и происхождение слова (Викисловарь): определения, этимология, синонимы. Русские слова: lang=ru, английские и другие: lang=en. "
                           "Для «что значит слово», «откуда оно взялось». Статья пишется людьми: у редких слов может быть коротко.",
              {"word": string("Слово в начальной форме"), "lang": string("Редакция словаря: ru (по умолчанию) или en")}, ["word"])]
FUNCTIONS = {"word_info": word_info}
