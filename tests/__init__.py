# Tests never touch Sena's real data: before importing the project, the data folder is swapped for a temporary one.
import atexit
import os
import shutil
import tempfile

_tmp = tempfile.mkdtemp(prefix="sena-test-")
# keys exported in the shell would reach the test config too (SENA_ENV only hides the .env file): gone before sena is imported
for _name in [k for k in os.environ if k.endswith(("_KEY", "_TOKEN", "_HASH", "_SECRET")) or k.startswith(("SENA_CLAUDE_", "ANTHROPIC_", "TG_API"))]:
    os.environ.pop(_name)
os.environ["SENA_DATA"] = _tmp
os.environ["SENA_HEADLESS"] = "1"   # browser tests run without a window
os.environ["SENA_VAULT"] = os.path.join(_tmp, "vault")   # the notes vault also lives in the temp folder
import secrets as _secrets   # noqa: E402
os.environ["APP_TOKEN"] = "test-" + _secrets.token_hex(16)   # a server without a password is closed to everyone: tests need their own, not the real .env —
                                                            # a new one each run: a test server listens on every address while it runs
os.environ["SENA_ENV"] = os.path.join(_tmp, ".env")   # no file there: tests never read the real keys, so nothing can reach a real service
os.environ["TG_OWNER_ID"] = "1001"   # the test owner: messages from this chat are his
os.environ["SENA_KEYCHAIN"] = "off"   # the Mac's real Keychain is never touched: keys stay files in the temp folder
os.environ["SENA_OFFSITE"] = os.path.join(_tmp, "offsite", "Sena")   # the off-site copy goes to the temp folder, never to the real iCloud Drive
os.environ["SITE_PUBLIC_URL"] = "off"   # the owner's real site is never probed by a test
os.environ["LUMA_URL"] = "http://127.0.0.1:9"   # the owner's Luma is never reached: nothing listens there (a test that needs Luma runs its own)
os.environ["LUMA_APP"] = os.path.join(_tmp, "no-Luma.app")   # and never opened
atexit.register(shutil.rmtree, _tmp, ignore_errors=True)

# Tests never leave this Mac: a connection to any other address fails at once (a test that needs a server runs its own on 127.0.0.1)
import socket   # noqa: E402

_connect = socket.socket.connect


def _this_mac_only(sock, address):
    host = address[0] if isinstance(address, tuple) else None
    if host is not None and not (str(host).startswith("127.") or host in ("::1", "localhost")):
        raise OSError(f"тесты не выходят в сеть: {host}")
    return _connect(sock, address)


socket.socket.connect = _this_mac_only

# nor ask the network's name service: an outside name looks like a public address (and the connection above still goes
# nowhere), a reserved test name is not found, as on the real net; a test that needs its own answers patches getaddrinfo itself
_getaddrinfo = socket.getaddrinfo
RESERVED = (".invalid", ".test", ".example", ".localhost")


def _no_lookups(host, port, *args, **kwargs):
    name = (host.decode() if isinstance(host, bytes) else str(host or "")).rstrip(".").lower()
    if not name or name == "localhost" or name.replace(".", "").isdigit() or ":" in name:
        return _getaddrinfo(host, port, *args, **kwargs)
    if name.endswith(RESERVED) or name in ("invalid", "test", "example"):
        raise socket.gaierror(socket.EAI_NONAME, "nodename nor servname provided, or not known")
    return _getaddrinfo("93.184.215.14", port, *args, **kwargs)


socket.getaddrinfo = _no_lookups

# Tests never open a window on the owner's screen (a Google sign-in once did): a test that expects it patches webbrowser itself
import webbrowser   # noqa: E402


def _no_window(*args, **kw):
    raise RuntimeError("тесты не открывают браузер")


webbrowser.open = webbrowser.open_new = webbrowser.open_new_tab = _no_window

# Tests never reach OpenAI: a stub stands in for the client. Embeddings are computed from words (shared words give similar vectors),
# and any other call fails with a clear error. A test that needs its own model patches llm.respond or llm._api itself.
import re   # noqa: E402
import zlib   # noqa: E402
from types import SimpleNamespace   # noqa: E402
from sena.core import llm   # noqa: E402


def _refuse(**kw):
    raise RuntimeError("тесты не обращаются к OpenAI")


from sena import config   # noqa: E402

# the config is read once, when first imported: if anything loaded it before this sandbox (a package unittest's discovery walked
# through on its way here), every path in it is the real Sena's — the data, the Keychain, the notes vault, iCloud, the real keys.
# That once ran a whole suite on them; now the run stops before its first test
if not str(config.DATA).startswith(_tmp) or config.KEYCHAIN or not str(config.VAULT_DIR).startswith(_tmp):
    raise SystemExit("Тесты остановлены: настройки Сены загрузились раньше песочницы тестов и смотрят на настоящие данные")
config.LOCAL_PORT = 9   # the owner's home model server listens on the real port: a test asking its status never reaches it
if [name for _, name in config.secret_values() if name != "APP_TOKEN"]:
    raise SystemExit("Тесты остановлены: в настройках тестов видны настоящие ключи")


def _fake_embeddings(input, **kw):
    data = []
    for t in input:
        vec = [0.0] * config.EMBED_DIMS   # same as the real model: otherwise the index would treat the embeddings as foreign
        for w in re.findall(r"[a-zа-яё]+", t.lower()):
            vec[zlib.crc32(w.encode()) % config.EMBED_DIMS] += 1.0   # a stable hash: Python's hash() of a string changes every run, and collisions made search tests flaky
        data.append(SimpleNamespace(embedding=vec))
    return SimpleNamespace(model="fake-embeddings", data=data, usage=SimpleNamespace(prompt_tokens=0, total_tokens=0))


_client = SimpleNamespace(responses=SimpleNamespace(create=_refuse), embeddings=SimpleNamespace(create=_fake_embeddings),
                          audio=SimpleNamespace(speech=SimpleNamespace(create=_refuse), transcriptions=SimpleNamespace(create=_refuse)))
llm._api = lambda: _client

# The home models (core/local.py) are never started by tests either: their server's door gives the same word-based fingerprints
# (without the model's question and record prefixes, so a question and a record still share words), and refuses everything else.
# Speech recognition points at a program that doesn't exist: a test that needs it patches local.transcribe itself.
from sena.core import local   # noqa: E402


def _fake_local(path, body, timeout=300):
    if path == "/v1/embeddings":
        query, record = config.LOCAL_EMBED_PROMPTS
        texts = [t.removeprefix(query).removeprefix(record) for t in body["input"]]
        return {"data": [{"index": i, "embedding": item.embedding} for i, item in enumerate(_fake_embeddings(texts).data)], "usage": {"prompt_tokens": 0}}
    raise local.Unavailable("тесты не обращаются к домашним моделям")


local._post = _fake_local
local.WHISPER = "whisper-cli-not-in-tests"

# Claude Code is never started by tests either: a stub named "claude" comes first on the PATH and refuses
_bin = os.path.join(_tmp, "bin")
os.makedirs(_bin, exist_ok=True)
with open(os.path.join(_bin, "claude"), "w") as _stub:
    _stub.write("#!/bin/sh\necho 'тесты не запускают Claude Code' >&2\nexit 1\n")
os.chmod(os.path.join(_bin, "claude"), 0o755)
os.environ["PATH"] = _bin + os.pathsep + os.environ.get("PATH", "")
config.MODELS_DIR = config.DATA / "models"   # tests behave the same whether or not the home models are downloaded on this Mac

from sena.memory import files   # noqa: E402

files.AUTO_READ = False   # a file isn't read in a background thread under a running test: tests that need it call files.read()
