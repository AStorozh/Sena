#!/usr/bin/env python3
"""The live app in a browser: shows the Android emulator's screen and passes mouse input to it (a click, a drag to scroll).
Needs a running emulator and adb. Run: python3 tools/phone_viewer.py [port]. Controls the emulator only: the Mac's address and password are never typed into it (demo)."""
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ADB = os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")
APK = Path(__file__).resolve().parent.parent / "sena.apk"
W, H = 1080, 2400   # the Pixel 8 screen size in the emulator
KEYS = {"back": "4", "home": "3"}
_lock = threading.Lock()
_frame = (0.0, b"")


def adb(*args, capture=False, timeout=15):
    out = subprocess.run([ADB, *args], capture_output=True, timeout=timeout)
    return out.stdout if capture else out.returncode


def screen():
    global _frame
    with _lock:
        if time.time() - _frame[0] < 0.2:
            return _frame[1]
        png = adb("exec-out", "screencap", "-p", capture=True)
        if png[:4] == b"\x89PNG":
            _frame = (time.time(), png)
        return _frame[1]


PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Сена: живое приложение</title><style>
body{margin:0;background:#1e1e1e;color:#f3f1ea;font:15px/1.4 system-ui,sans-serif;display:flex;flex-direction:column;align-items:center;padding:16px;gap:12px}
#phone{border:10px solid #000;border-radius:44px;overflow:hidden;background:#000;line-height:0;box-shadow:0 8px 40px #0008}
img{height:min(86vh,860px);width:auto;display:block;cursor:pointer;user-select:none;-webkit-user-drag:none}
.row{display:flex;gap:10px;flex-wrap:wrap;justify-content:center}
button,a.b{background:#f2c85b;color:#1c1c1c;border:0;border-radius:999px;padding:12px 22px;font:600 15px system-ui;text-decoration:none;cursor:pointer}
small{color:#b9b6ad;max-width:520px;text-align:center}
</style></head><body>
<div id="phone"><img id="s" src="/screen.png" draggable="false"></div>
<div class="row"><button onclick="key('back')">Назад</button><button onclick="key('home')">Домой</button><a class="b" href="/sena.apk">Скачать sena.apk для телефона</a></div>
<small>Это настоящее приложение, запущенное в эмуляторе Android 16. Клик мышью — нажатие, протяжка — прокрутка. Данные вымышленные (демо-режим), с вашей Сеной не связаны.</small>
<script>
const img=document.getElementById('s');let down=null;
function refresh(){const n=new Image();n.onload=()=>{img.src=n.src;setTimeout(refresh,120)};n.onerror=()=>setTimeout(refresh,1000);n.src='/screen.png?'+Date.now()}
refresh();
function pos(e){const r=img.getBoundingClientRect();return[Math.round((e.clientX-r.left)/r.width*%d),Math.round((e.clientY-r.top)/r.height*%d)]}
img.addEventListener('mousedown',e=>{down={p:pos(e),t:Date.now()};e.preventDefault()});
img.addEventListener('mouseup',e=>{if(!down)return;const a=down.p,b=pos(e),d=Math.hypot(a[0]-b[0],a[1]-b[1]);
 if(d<25)fetch('/tap?x='+a[0]+'&y='+a[1],{method:'POST'});
 else fetch('/swipe?x1='+a[0]+'&y1='+a[1]+'&x2='+b[0]+'&y2='+b[1]+'&ms='+Math.min(600,Math.max(150,Date.now()-down.t)),{method:'POST'});
 down=null});
function key(k){fetch('/key?k='+k,{method:'POST'})}
</script></body></html>""" % (W, H)


def num(q, name, hi):
    return max(0, min(hi, int(float(q[name][0]))))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, body, kind="text/html; charset=utf-8", code=200):
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            self.send(PAGE.encode())
        elif path == "/screen.png":
            self.send(screen(), "image/png")
        elif path == "/sena.apk" and APK.is_file():
            self.send(APK.read_bytes(), "application/vnd.android.package-archive")
        else:
            self.send(b"not found", "text/plain", 404)

    def do_POST(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path == "/tap":
                adb("shell", "input", "tap", str(num(q, "x", W)), str(num(q, "y", H)))
            elif u.path == "/swipe":
                adb("shell", "input", "swipe", str(num(q, "x1", W)), str(num(q, "y1", H)), str(num(q, "x2", W)), str(num(q, "y2", H)), str(num(q, "ms", 1000)))
            elif u.path == "/key" and q.get("k", [""])[0] in KEYS:
                adb("shell", "input", "keyevent", KEYS[q["k"][0]])
            else:
                return self.send(b"no", "text/plain", 404)
            self.send(b"ok", "text/plain")
        except (ValueError, KeyError, subprocess.TimeoutExpired):
            self.send(b"bad", "text/plain", 400)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8801
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
