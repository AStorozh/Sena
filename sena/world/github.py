"""Tema's GitHub: repositories, issues, pull requests, releases, Actions, profile. Through the official REST API and a fine-grained
SENA_GITHUB_TOKEN token: Tema himself chooses the token's permissions, so the assistant can never do more than GitHub allows it.
Reading happens immediately; any edit goes through two steps: Sena shows the request and the "before → after", and applies it only after Tema's "yes" (confirm_send).
Code in repositories is edited by ag_code (Claude Code via subscription), not this module. Issue text, comments and READMEs are someone else's data."""
import base64
import json
import re
from urllib.parse import quote, unquote, urlparse

from sena import config
from sena.core import safety
from sena.core.tools import string, tool
from sena.world import net

SOURCE = "GitHub"
SHOWN = 10000   # characters of the response the model sees; the rest is requested in pages (?per_page=, ?page=)
LOOPBACK = ("127.0.0.1", "localhost", "::1")
NAME = r"[A-Za-z0-9_.\-]+"

# reading is allowed almost everywhere, except where secrets and access keys live
READ_PREFIXES = ("/user", "/users/", "/repos/", "/orgs/", "/search/", "/notifications", "/rate_limit", "/gists", "/issues")
READ_DENIED = re.compile(
    r"^/(user/(keys|gpg_keys|ssh_signing_keys|emails|memberships|codespaces|settings|applications|migrations|installations|blocks)(/.*)?"
    rf"|authorizations(/.*)?|repos/{NAME}/{NAME}/(actions/(secrets|variables)|dependabot/secrets|codespaces/secrets|keys|hooks|collaborators|invitations)(/.*)?)$",
    re.I)

# where writing is allowed: known paths and methods. Deleting a repository, changing visibility, transfers, invitations, secrets, keys, webhooks and branch protection are not included
WRITE_RULES = [
    (r"^/user$", {"PATCH"}),
    (r"^/user/repos$", {"POST"}),
    (rf"^/repos/{NAME}/{NAME}$", {"PATCH"}),
    (rf"^/repos/{NAME}/{NAME}/topics$", {"PUT"}),
    (rf"^/repos/{NAME}/{NAME}/issues$", {"POST"}),
    (rf"^/repos/{NAME}/{NAME}/issues/\d+$", {"PATCH"}),
    (rf"^/repos/{NAME}/{NAME}/issues/\d+/(comments|labels|assignees)$", {"POST"}),
    (rf"^/repos/{NAME}/{NAME}/issues/\d+/labels/[^/]+$", {"DELETE"}),
    (rf"^/repos/{NAME}/{NAME}/issues/comments/\d+$", {"PATCH", "DELETE"}),
    (rf"^/repos/{NAME}/{NAME}/pulls$", {"POST"}),
    (rf"^/repos/{NAME}/{NAME}/pulls/\d+$", {"PATCH"}),
    (rf"^/repos/{NAME}/{NAME}/pulls/\d+/(reviews|comments|requested_reviewers)$", {"POST"}),
    (rf"^/repos/{NAME}/{NAME}/pulls/\d+/merge$", {"PUT"}),
    (rf"^/repos/{NAME}/{NAME}/labels$", {"POST"}),
    (rf"^/repos/{NAME}/{NAME}/labels/[^/]+$", {"PATCH", "DELETE"}),
    (rf"^/repos/{NAME}/{NAME}/milestones$", {"POST"}),
    (rf"^/repos/{NAME}/{NAME}/milestones/\d+$", {"PATCH"}),
    (rf"^/repos/{NAME}/{NAME}/releases$", {"POST"}),
    (rf"^/repos/{NAME}/{NAME}/releases/\d+$", {"PATCH"}),
    (rf"^/repos/{NAME}/{NAME}/actions/workflows/[^/]+/dispatches$", {"POST"}),
    (rf"^/repos/{NAME}/{NAME}/actions/runs/\d+/(cancel|rerun|rerun-failed-jobs)$", {"POST"}),
    (rf"^/repos/{NAME}/{NAME}/actions/workflows/[^/]+/(enable|disable)$", {"PUT"}),
    (r"^/notifications$", {"PUT"}),
    (r"^/notifications/threads/\d+$", {"PATCH"}),
    # files, branches and commits (via the Contents and Git Data API, without cloning)
    (rf"^/repos/{NAME}/{NAME}/contents/.+$", {"PUT", "DELETE"}),
    (rf"^/repos/{NAME}/{NAME}/git/(blobs|trees|commits|refs)$", {"POST"}),
    (rf"^/repos/{NAME}/{NAME}/git/refs/(heads|tags)/.+$", {"PATCH", "DELETE"}),
    (rf"^/repos/{NAME}/{NAME}/(merges|forks|dispatches)$", {"POST"}),
    (rf"^/repos/{NAME}/{NAME}/branches/[^/]+/rename$", {"POST"}),
    (rf"^/repos/{NAME}/{NAME}/pulls/\d+/(update-branch|requested_reviewers)$", {"PUT", "DELETE"}),
    (rf"^/repos/{NAME}/{NAME}/pulls/\d+/reviews/\d+(/(events|dismissals))?$", {"POST", "PUT", "DELETE"}),
    (rf"^/repos/{NAME}/{NAME}/pulls/comments/\d+$", {"PATCH", "DELETE"}),
    # the rest covering issues, releases and milestones
    (rf"^/repos/{NAME}/{NAME}/issues/\d+/(lock|reactions)$", {"PUT", "POST"}),
    (rf"^/repos/{NAME}/{NAME}/issues/\d+/(labels|assignees)$", {"PUT", "DELETE"}),
    (rf"^/repos/{NAME}/{NAME}/milestones/\d+$", {"DELETE"}),
    (rf"^/repos/{NAME}/{NAME}/releases/\d+$", {"DELETE"}),
    (rf"^/repos/{NAME}/{NAME}/releases/generate-notes$", {"POST"}),
    # stars, subscriptions and gists
    (rf"^/user/starred/{NAME}/{NAME}$", {"PUT", "DELETE"}),
    (rf"^/repos/{NAME}/{NAME}/subscription$", {"PUT", "DELETE"}),
    (r"^/user/following/[^/]+$", {"PUT", "DELETE"}),
    (r"^/gists$", {"POST"}),
    (r"^/gists/[0-9a-f]+$", {"PATCH", "DELETE"}),
]
# fields that change who can see the repository: done by Tema himself on GitHub
BLOCKED_FIELDS = {"private", "visibility", "archived"}


class GitHubError(net.WorldError):
    """GitHub is unreachable or refused the request. The text can be shown to Tema as is."""


def _base():
    url = config.GITHUB_API.rstrip("/")
    if urlparse(url).scheme != "https" and urlparse(url).hostname not in LOOPBACK:
        raise GitHubError("Адрес GitHub API должен начинаться с https://: токен нельзя слать по открытому каналу")
    if not config.GITHUB_TOKEN:
        raise GitHubError("Токена GitHub нет: впиши SENA_GITHUB_TOKEN в .env (fine-grained токен на нужные репозитории, образец — .env.example)")
    return url


def _headers():
    return {"Authorization": "Bearer " + config.GITHUB_TOKEN, "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}


def _explain(reply):
    try:
        message = reply.json().get("message", "")
    except ValueError:
        message = ""
    code = reply.status_code
    if code in (403, 429) and reply.headers.get("x-ratelimit-remaining") == "0":
        return f"GitHub: лимит запросов исчерпан, сбросится позже ({message})"
    if code == 401:
        return "GitHub не принял токен: он неверный или истёк. Выпусти новый и впиши в SENA_GITHUB_TOKEN"
    if code in (403, 404):
        return f"GitHub {code}: {message}. Если ты уверен, что это есть, у токена, скорее всего, нет прав на этот репозиторий или действие"
    return f"GitHub {code}: {message}"[:400]


def _segments(path):
    # doubled slashes made one and "." segments dropped, as requests drops them before sending: /user/./keys reaches GitHub
    # as /user/keys
    return "/".join(part for part in re.sub(r"/{2,}", "/", path).split("/") if part != ".")


def _plain(base):
    """The path as GitHub reads it — percent-escapes decoded, doubled slashes made one, "." segments dropped: what is allowed
    and refused is decided on this, so /user/%6Beys and /user/./keys are /user/keys (a file name may still be sent escaped:
    "My%20File.md")."""
    for _ in range(3):
        decoded = unquote(base)
        if decoded == base:
            break
        base = decoded
    return _segments(base)


def _path(path):
    base, _, query = ("/" + str(path or "").strip().lstrip("/")).partition("?")
    base = _segments(base)   # what is sent is what was checked
    if not re.fullmatch(r"/[A-Za-z0-9_\-./%:@+]*", base) or ".." in _plain(base):
        raise GitHubError(f"Адрес «{base}» записан неверно")
    return base.rstrip("/") or "/", quote(query, safe="=&,:%-_.~/+")


def _url(path):
    base, query = _path(path)
    return _base() + base + ("?" + query if query else "")


# ---------- compressing the response: GitHub returns dozens of links on every object, the model doesn't need them ----------
_PEOPLE = {"user", "owner", "author", "committer", "assignee", "merged_by", "closed_by", "creator", "actor", "sender", "requested_reviewer"}
_DROP = {"_links", "node_id", "permissions", "score", "performed_via_github_app", "author_association", "reactions", "site_admin", "gravatar_id",
         "temp_clone_token", "template_repository", "network_count", "subscribers_count", "topics_url", "has_wiki", "has_pages", "has_projects",
         "has_downloads", "has_discussions", "web_commit_signoff_required", "allow_forking", "is_template", "forks_count", "watchers", "watchers_count",
         "mirror_url", "disabled", "svn_url", "git_url", "ssh_url", "clone_url", "default_branch_url"}


def slim(obj):
    if isinstance(obj, list):
        return [slim(x) for x in obj]
    if not isinstance(obj, dict):
        return obj
    out = {}
    for key, value in obj.items():
        if value is None or key in _DROP or (key.endswith("_url") and key != "html_url") or key == "url":
            continue
        if key == "body" and isinstance(value, str):   # an HTML comment in an issue is shown to no reader on GitHub, only to a model
            out[key] = safety.HTML_COMMENT.sub("", value)
        elif key in _PEOPLE and isinstance(value, dict):
            out[key] = value.get("login")
        elif key in ("assignees", "requested_reviewers") and isinstance(value, list):
            out[key] = [v.get("login") if isinstance(v, dict) else v for v in value]
        elif key == "labels" and isinstance(value, list):
            out[key] = [v.get("name") if isinstance(v, dict) else v for v in value]
        elif key in ("repo", "repository") and isinstance(value, dict):
            out[key] = value.get("full_name")
        elif key in ("head", "base") and isinstance(value, dict):
            out[key] = {"ref": value.get("ref"), "repo": (value.get("repo") or {}).get("full_name")}
        elif key in ("milestone", "license") and isinstance(value, dict):
            out[key] = value.get("title") or value.get("spdx_id")
        else:
            out[key] = slim(value)
    return out


def shown(data, note=""):
    """GitHub's response for the model: compressed JSON, marked as external data (issue and comment text is written by other people)."""
    text = json.dumps(slim(data), ensure_ascii=False, separators=(",", ":"))
    if len(text) > SHOWN:
        text = text[:SHOWN] + f"…[вырезано {len(text) - SHOWN} знаков: запроси страницами ?per_page=10&page=2 или уточни путь]"
    return safety.external(text + (f"\n{note}" if note else ""), SOURCE)


def _decode_file(data):
    # file contents arrive as base64: decode it to text for the model
    if isinstance(data, dict) and data.get("encoding") == "base64" and "content" in data:
        raw = base64.b64decode(data["content"])
        try:
            data = {**data, "content": raw.decode("utf-8")[:20000], "encoding": "text"}
        except UnicodeDecodeError:
            data = {**data, "content": f"(бинарный файл, {len(raw)} байт)", "encoding": "binary"}
    return data


# ---------- reading ----------
def _get(path):
    reply = net.get(_url(path), headers=_headers(), timeout=30, who=SOURCE, error=GitHubError, explain=_explain)
    link = reply.headers.get("Link", "")
    follow = re.search(r'[?&]page=(\d+)[^>]*>;\s*rel="next"', link)
    return net.as_json(reply, SOURCE, GitHubError), (f"Есть следующая страница: добавь &page={follow.group(1)}" if follow else "")


def github_read(path):
    base, _ = _path(path)
    plain = _plain(base)
    if not plain.startswith(READ_PREFIXES) or READ_DENIED.match(plain):
        raise GitHubError(f"Читать «{base}» нельзя: адрес не из разрешённых или там секреты")
    data, note = _get(path)
    return shown(_decode_file(data), note)


def github_overview():
    me, _ = _get("/user")
    repos, _ = _get("/user/repos?sort=pushed&per_page=30&affiliation=owner,collaborator")
    limit, _ = _get("/rate_limit")
    return shown({"profile": {k: me.get(k) for k in ("login", "name", "bio", "company", "location", "blog", "public_repos", "followers", "hireable")},
                  "repos": [{k: r.get(k) for k in ("full_name", "description", "private", "language", "pushed_at", "open_issues_count", "stargazers_count", "archived", "fork")}
                            for r in (repos if isinstance(repos, list) else [])],
                  "limit": (limit.get("resources", {}).get("core") or {})})


# ---------- edits: preview and apply ----------
def _check(method, path, body):
    method = str(method).upper()
    base, query = _path(path)
    plain = _plain(base)
    allowed = any(re.fullmatch(rule, plain) and method in methods for rule, methods in WRITE_RULES)
    if query or not allowed:
        raise GitHubError(f"Так менять GitHub нельзя: {method} {base}. Разрешены задачи, pull request, ветки, коммиты и файлы, релизы, Actions, форки, звёзды, gist, профиль и настройки "
                          "репозитория; удаление репозитория, доступ, секреты, ключи и смена видимости делает Тема сам на GitHub")
    if body is not None and not isinstance(body, dict):
        raise GitHubError("Тело запроса должно быть объектом")
    forbidden = BLOCKED_FIELDS & set(body or {})
    if re.fullmatch(rf"/repos/{NAME}/{NAME}", plain) and forbidden:
        raise GitHubError(f"Поля {', '.join(sorted(forbidden))} меняют видимость репозитория: так делает только сам Тема на GitHub")
    return method, base


CONTENTS = re.compile(rf"/repos/{NAME}/{NAME}/contents/(.+)")
RUNS_ON_GITHUB = (".github/workflows/", ".github/actions/")   # files GitHub itself runs, with the repository's secrets
SHOWN_TEXT = 4000


def _decoded(value):
    # a file's text from GitHub's base64 (its line breaks are skipped), or None for a file that isn't text
    try:
        return base64.b64decode(str(value or "")).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def preview(method, path, body=None):
    """What will be done: the request and, for edits and deletions, how things look right now ("before"). Sends nothing. A file
    written is shown as its text, not base64 — the owner says yes to what he can read; a file GitHub Actions run is flagged
    (the awesome-claude-code study: workflow files run outside any sandbox, with the repository's secrets)."""
    method, base = _check(method, path, body)
    out = {"method": method, "path": base, "body": body or {}}
    current_path = base[:-len("/merge")] if base.endswith("/merge") else base
    file = CONTENTS.fullmatch(_plain(base))   # %2Egithub/workflows is the workflow file all the same
    if method in ("PATCH", "DELETE", "PUT") and not base.endswith(("/topics",)):
        try:
            data, _ = _get(current_path)
        except GitHubError as e:
            out["before"] = f"(не удалось прочитать: {e})" if not file else "(файла ещё нет)"
        else:
            slimmed = slim(data) if isinstance(data, dict) else data
            out["before"] = {k: slimmed.get(k) for k in (body or {})} if method == "PATCH" and isinstance(slimmed, dict) and body else slimmed
            if file and isinstance(data, dict) and data.get("content"):
                was = _decoded(data["content"])
                out["before"] = {"sha": data.get("sha"), "text": (was if was is not None else "(не текст)")[:SHOWN_TEXT]}
    if file and method == "PUT":
        text = _decoded((body or {}).get("content"))
        out["text"] = (text if text is not None else "(не текст: двоичный файл)")[:SHOWN_TEXT]
        out["body"] = {k: v for k, v in (body or {}).items() if k != "content"}
    if file and file.group(1).lstrip("/").startswith(RUNS_ON_GITHUB):
        out["warning"] = "Это файл GitHub Actions: GitHub выполнит его сам, с секретами репозитория. Покажи Теме текст целиком."
    return out


def apply(method, path, body=None):
    """Actually apply the edit. Called only after Tema's "yes" (confirm_send)."""
    method, base = _check(method, path, body)
    # a DELETE carries its body when it has one: removing a file needs {message, sha}, removing assignees {assignees}
    payload = body if method == "DELETE" else (body or {})
    reply = net.send(method, _url(base), payload, headers=_headers(), timeout=60, who=SOURCE, error=GitHubError, explain=_explain)
    data = {"ok": True, "status": reply.status_code} if not reply.content else net.as_json(reply, SOURCE, GitHubError)
    return "Применено на GitHub. Ответ:\n" + shown(data)


def summary(method, path, data):
    flag = " — файл GitHub Actions, выполнится на GitHub" if isinstance(data, dict) and data.get("warning") else ""
    text = f": «{data['text'][:300]}»" if isinstance(data, dict) and data.get("text") else ""
    return f"{str(method).upper()} {path}{flag}{text}"


CHANGE_TOOL = tool(
    "github_change",
    "Подготовить правку на GitHub Темы: создать задачу или pull request, написать комментарий, закрыть, поставить метку, слить pull request, выпустить релиз, запустить Actions, "
    "создать репозиторий, ветку, коммит или файл (PUT /repos/o/r/contents/путь с base64-содержимым), форкнуть, поставить звезду, создать gist, поправить профиль или описание репозитория. Ничего не отправляет сразу: GitHub показывает запрос и «было»; покажи это Теме коротко и спроси. После его «да» применяй через confirm_send. "
    "Удалять и архивировать репозитории, менять видимость, доступы и секреты нельзя (это делает Тема на GitHub). Код в репозиториях правит claude_task, а не этот инструмент. "
    "Примеры: POST /repos/AStorozh/Sena/issues {\"title\":\"…\",\"body\":\"…\"}; PATCH /repos/AStorozh/Sena/issues/5 {\"state\":\"closed\"}; "
    "POST /repos/AStorozh/Sena/issues/5/comments {\"body\":\"…\"}; PUT /repos/AStorozh/Sena/pulls/7/merge {\"merge_method\":\"squash\"}; PATCH /user {\"bio\":\"…\"}.",
    {"method": {"type": "string", "enum": ["POST", "PATCH", "PUT", "DELETE"]},
     "path": string("Адрес API GitHub, например /repos/AStorozh/Sena/issues"),
     "body": {"type": "object", "description": "Тело запроса; у DELETE — только где GitHub его ждёт (удалить файл: {message, sha}; снять исполнителей: {assignees})"}},
    ["method", "path"])

TOOLS = [
    tool("github_overview", "GitHub Темы: профиль, его последние репозитории (описание, язык, открытые задачи, звёзды) и остаток лимита запросов. Начни с этого, когда спрашивают про GitHub.", {}),
    tool("github_read",
         "Прочитать с GitHub (только чтение): репозитории, задачи, pull request, комментарии, коммиты, релизы, запуски Actions, файлы, уведомления, поиск. "
         "Примеры: /user/repos, /repos/AStorozh/Sena/issues?state=open, /repos/AStorozh/Sena/pulls/7, /repos/AStorozh/Sena/pulls/7/files, "
         "/repos/AStorozh/Sena/contents/README.md, /repos/AStorozh/Sena/commits?per_page=10, /repos/AStorozh/Sena/actions/runs, /notifications, "
         "/search/issues?q=is:open+is:pr+author:AStorozh. Токен видит только те репозитории, которые Тема выбрал при выпуске.",
         {"path": string("Адрес API GitHub, можно с параметрами")}, ["path"]),
    CHANGE_TOOL,
]
FUNCTIONS = {"github_overview": github_overview, "github_read": github_read}
