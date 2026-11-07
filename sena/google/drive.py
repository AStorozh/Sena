"""Google Drive (read-only): find a file, download it, or export a document. Scope: drive.readonly."""
from sena import config
from sena.core import attachments, store
from sena.core.safety import cut, external
from sena.core.tools import Failure, clamp, string, tool
from sena.google.auth import call, call_raw

BASE = "https://www.googleapis.com/drive/v3/files"
# Google Docs files aren't downloaded as-is, but exported: into what format
EXPORT = {
    "application/vnd.google-apps.document": ("text/plain", ".txt"),
    "application/vnd.google-apps.spreadsheet": ("text/csv", ".csv"),
    "application/vnd.google-apps.presentation": ("text/plain", ".txt"),
}
KINDS = {"application/vnd.google-apps.document": "документ", "application/vnd.google-apps.spreadsheet": "таблица",
         "application/vnd.google-apps.presentation": "презентация", "application/vnd.google-apps.folder": "папка"}
MOST_BYTES = 500_000_000   # a file downloaded at most


def escape_query(text):
    """Escape text inside the quotes of a Drive search query (not to be confused with URL encoding)."""
    return text.replace("\\", "\\\\").replace("'", "\\'")


def search(query, limit=10):
    """Find files by part of their name or text content."""
    safe = escape_query(query)
    q = f"(name contains '{safe}' or fullText contains '{safe}') and trashed = false"
    files = call("GET", BASE, params={"q": q, "pageSize": clamp(limit, 10, 1, 25), "orderBy": "modifiedTime desc",
                                      "fields": "files(id,name,mimeType,modifiedTime,webViewLink)"}).get("files", [])
    if not files:
        return "Файлов по этому запросу нет"
    lines = [f"[{f['id']}] {f['name']} — {KINDS.get(f['mimeType'], f['mimeType'])}, изменён {f['modifiedTime'][:10]} {f.get('webViewLink', '')}" for f in files]
    return external("\n".join(lines), "Google Диск: список файлов")


def download(file_id):
    """Download a file to data/downloads (a document is exported as text, a spreadsheet as csv). Returns the path."""
    info = call("GET", f"{BASE}/{file_id}", params={"fields": "name,mimeType,size"})
    safe = store.safe_name(info["name"])
    size = int(info.get("size") or 0)   # the answer is held in memory whole: a file of gigabytes stays on the Drive
    if size > MOST_BYTES:
        return Failure(f"«{info['name']}» — {size // 1_000_000} МБ, больше {MOST_BYTES // 1_000_000} МБ: такой файл открывай на Диске в браузере")
    if info["mimeType"] in EXPORT:
        mime, ext = EXPORT[info["mimeType"]]
        data = call_raw("GET", f"{BASE}/{file_id}/export", params={"mimeType": mime})
        target = config.DOWNLOADS_DIR / f"drive-{file_id[:8]}-{safe}{ext}"
    elif info["mimeType"].startswith("application/vnd.google-apps."):
        return f"Файл типа {info['mimeType']} скачать нельзя (папка или особый документ Google)"
    else:
        data = call_raw("GET", f"{BASE}/{file_id}", params={"alt": "media"})
        target = config.DOWNLOADS_DIR / f"drive-{file_id[:8]}-{safe}"
    target.write_bytes(data)
    attachments.register(target, f"«{info['name']}» с Google Диска", "drive")
    preview = ""
    if target.suffix in (".txt", ".csv"):
        preview = "\n" + external(cut(data.decode("utf-8", errors="replace"), 2000), "Google Диск: начало файла")
    if info["mimeType"] == "application/vnd.google-apps.spreadsheet":   # Google exports one sheet to CSV: the rest aren't silently lost
        preview += "\n(В CSV только первый лист таблицы; остальные листы — sheet_info и sheet_read.)"
    return f"Сохранено: {target}{preview}"


TOOLS = [
    tool("drive_search", "Найти файлы на Google Диске по части названия или тексту внутри.",
         {"query": string(), "limit": {"type": "integer", "description": "Сколько файлов, по умолчанию 10"}}, ["query"]),
    tool("drive_download", "Скачать файл с Google Диска на компьютер по id из drive_search; вернёт путь (Теме — send_file). Документ выгружается текстом, таблица — csv.",
         {"file_id": string()}, ["file_id"]),
]
FUNCTIONS = {"drive_search": search, "drive_download": download}
