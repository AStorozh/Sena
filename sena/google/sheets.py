"""Google Sheets: read and write cells, find a spreadsheet by name, link, or id. Cell content is someone else's data."""
import re
from urllib.parse import quote

from sena.core.safety import external, spill
from sena.core.tools import string, tool
from sena.google import drive
from sena.google.auth import GoogleError, call

BASE = "https://sheets.googleapis.com/v4/spreadsheets"
ID_RE = re.compile(r"^[A-Za-z0-9_-]{25,}$")


def resolve(ref):
    """Spreadsheet by link, id, or name -> id."""
    ref = ref.strip()
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", ref)
    if m:
        return m.group(1)
    if ID_RE.match(ref):
        return ref
    q = f"mimeType = 'application/vnd.google-apps.spreadsheet' and name contains '{drive.escape_query(ref)}' and trashed = false"
    files = call("GET", drive.BASE, params={"q": q, "pageSize": 10, "orderBy": "modifiedTime desc", "fields": "files(id,name)"}).get("files", [])
    exact = [f for f in files if f["name"].lower() == ref.lower()]
    if len(exact) == 1 or len(files) == 1:
        return (exact or files)[0]["id"]
    if not files:
        raise GoogleError(f"Таблицы «{ref}» не нашла")
    raise GoogleError("Таблиц несколько, уточни: " + "; ".join(f"{f['name']} [{f['id']}]" for f in files))


def info(spreadsheet):
    """The spreadsheet's title and its sheets (size of each)."""
    data = call("GET", f"{BASE}/{resolve(spreadsheet)}", params={"fields": "properties.title,sheets.properties"})
    tabs = [f"«{s['properties']['title']}» {s['properties']['gridProperties']['rowCount']}x{s['properties']['gridProperties']['columnCount']}"
            for s in data.get("sheets", [])]
    return external(f"Таблица «{data['properties']['title']}»; листы: {'; '.join(tabs)}", "Google Таблицы: описание")


def read(spreadsheet, range, max_rows=200):
    """Values from a range (A1:D20, 'Sheet 1'!A:C, or a whole sheet name)."""
    sid = resolve(spreadsheet)
    data = call("GET", f"{BASE}/{sid}/values/{quote(range, safe='')}", params={"valueRenderOption": "FORMATTED_VALUE"})
    rows = data.get("values", [])
    if not rows:
        return f"Диапазон {data.get('range', range)} пуст"
    lines = [" | ".join(str(c) for c in row) for row in rows[:max_rows]]
    tail = f"\n...ещё строк: {len(rows) - max_rows}" if len(rows) > max_rows else ""
    return external(spill(f"{data.get('range', range)}\n" + "\n".join(lines) + tail, 8000, "таблица"), "Google Таблицы: ячейки")


def write(spreadsheet, range, values, append=False):
    """Write values. Rows and formulas as a human would type them ("=SUM(A1:A9)" becomes a formula).
    append=True — append rows after the last occupied one in the range."""
    sid = resolve(spreadsheet)
    rows = [[c if isinstance(c, (int, float, bool)) or c is None else str(c) for c in row] for row in values]
    params = {"valueInputOption": "USER_ENTERED"}
    path = f"{BASE}/{sid}/values/{quote(range, safe='')}"
    if append:
        done = call("POST", path + ":append", params={**params, "insertDataOption": "INSERT_ROWS"}, json={"values": rows})
        return f"Дописано строк: {len(rows)} ({done.get('updates', {}).get('updatedRange', range)})"
    done = call("PUT", path, params=params, json={"values": rows})
    return f"Записано ячеек: {done.get('updatedCells', 0)} ({done.get('updatedRange', range)})"


def clear(spreadsheet, range):
    """Clear values in a range (formatting stays)."""
    done = call("POST", f"{BASE}/{resolve(spreadsheet)}/values/{quote(range, safe='')}:clear", json={})
    return f"Очищено: {done.get('clearedRange', range)}"


def create(title, values=None):
    """A new spreadsheet (with the first rows, if given). Returns the link."""
    made = call("POST", BASE, json={"properties": {"title": title}})
    if values:
        write(made["spreadsheetId"], "A1", values)
    return f"Создана таблица «{title}»: {made['spreadsheetUrl']}"


_ROWS = {"type": "array", "items": {"type": "array", "items": {}}, "description": "Строки таблицы: список строк, каждая — список значений"}
TOOLS = [
    tool("sheet_info", "Название Google Таблицы и её листы. spreadsheet — ссылка, id или название таблицы.", {"spreadsheet": string()}, ["spreadsheet"]),
    tool("sheet_read", "Прочитать ячейки Google Таблицы. range вида A1:D20 или 'Лист 1'!A:C или просто имя листа.",
         {"spreadsheet": string("Ссылка, id или название"), "range": string(), "max_rows": {"type": "integer", "description": "Не больше строк, по умолчанию 200"}},
         ["spreadsheet", "range"]),
    tool("sheet_write", "Записать значения в Google Таблицу (формулы вида =SUM(A1:A9) работают). append=true — дописать строки в конец данных диапазона.",
         {"spreadsheet": string("Ссылка, id или название"), "range": string("С какой ячейки или диапазон, например A1 или 'Лист 1'!A1"),
          "values": _ROWS, "append": {"type": "boolean"}}, ["spreadsheet", "range", "values"]),
    tool("sheet_clear", "Очистить значения в диапазоне Google Таблицы. Делай только если Тема прямо попросил.",
         {"spreadsheet": string(), "range": string()}, ["spreadsheet", "range"]),
    tool("sheet_create", "Создать новую Google Таблицу (можно сразу с первыми строками). Вернёт ссылку.",
         {"title": string(), "values": _ROWS}, ["title"]),
]
FUNCTIONS = {"sheet_info": info, "sheet_read": read, "sheet_write": write, "sheet_clear": clear, "sheet_create": create}
