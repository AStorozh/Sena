#!/usr/bin/env python3
"""Adds strings to values/strings.xml (Russian) and values-en/strings.xml (English).
Input (stdin): lines of the form  name|Russian|English. Empty lines and lines starting with # are skipped.
An existing name is updated in place, a new one goes to the end; everything else in the file (plurals, comments,
order) stays as it was. Escaping (apostrophe, ampersand, angle brackets) is done here."""
import re
import sys
from pathlib import Path

RES = Path(__file__).resolve().parent.parent / "app" / "src" / "main" / "res"
FILES = {"ru": RES / "values" / "strings.xml", "en": RES / "values-en" / "strings.xml"}
EMPTY = '<?xml version="1.0" encoding="utf-8"?>\n<resources>\n</resources>\n'


def esc(text):
    text = text.replace("\\", "\\\\").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = text.replace("'", "\\'").replace('"', '\\"').replace("\n", "\\n")
    if text[:1] in "@?":
        text = "\\" + text
    return text


def upsert(xml, name, value):
    """The file's text with this one string set: replaced where it is, or added before </resources>."""
    line = f'    <string name="{name}">{value}</string>\n'
    old = re.compile(r'[ \t]*<string name="%s">.*?</string>\n' % re.escape(name), re.S)
    if old.search(xml):
        return old.sub(lambda m: line, xml, count=1)
    end = xml.rindex("</resources>")
    return xml[:end] + line + xml[end:]


def main():
    texts = {lang: path.read_text(encoding="utf-8") if path.exists() else EMPTY for lang, path in FILES.items()}
    count = 0
    for line in sys.stdin.read().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("|")
        if len(parts) != 3:
            sys.exit(f"Нужно имя|русский|английский: {line!r}")
        name, ru, en = (p.strip() for p in parts)
        if not re.fullmatch(r"[a-z0-9_]+", name):
            sys.exit(f"Имя строки — латиница, цифры и _: {name!r}")
        texts["ru"], texts["en"] = upsert(texts["ru"], name, esc(ru)), upsert(texts["en"], name, esc(en))
        count += 1
    for lang, path in FILES.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(texts[lang], encoding="utf-8")
    print(f"строк записано: {count}")


if __name__ == "__main__":
    main()
