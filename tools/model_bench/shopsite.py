"""A small bike shop on this Mac for the browser agent's tasks: catalog with pages, a test-drive form, a login with an account,
reviews loaded by a button, a promo code drawn on a canvas, a cart with a payment button. Every visit and form is recorded,
so a task is graded by what happened on the site, not by the agent's words."""
import html
import json
import threading
import uuid
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

BIKES = [   # name, category, price, in stock
    ("Stels Navigator 300", "Городские", 18990, True), ("Forward Dortmund 28", "Городские", 21490, True),
    ("Stels Navigator 500", "Горные", 24990, False), ("Merida Big.Seven 15", "Горные", 46900, True),
    ("Author Solution", "Горные", 38500, True), ("Shulz Krabi Coaster", "Складные", 27900, True),
    ("Format 1414 27.5", "Горные", 41200, True), ("Stinger Element Evo", "Горные", 29990, False),
    ("Giant Escape 3", "Городские", 52000, True), ("Forward Sporting 27.5 2.0", "Горные", 31490, True),
    ("Cube Aim Pro", "Горные", 54990, True), ("Stark Hunter 27.2", "Горные", 33990, True),
    ("Shulz Max Disc", "Складные", 35900, False), ("Merida Crossway 20", "Городские", 61900, True),
    ("Stels Pilot 710", "Складные", 16490, True), ("Novatrack Prime 26", "Горные", 27490, False),
    ("Forward Next 29 3.0", "Горные", 35990, True), ("Trek Marlin 4", "Горные", 69900, True),
    ("Author Traction", "Горные", 32990, True), ("Black One Onix", "Горные", 23990, False),
    ("Welt Rockfall 1.0", "Горные", 39990, True), ("Stern Energy 1.0", "Горные", 30990, False),
    ("Schwinn Wayfarer", "Городские", 44990, True), ("Polygon Cascade 4", "Горные", 49900, True),
]
PER_PAGE = 8
REVIEWS = [("Алексей П.", 5, "Отличный магазин, собрали велосипед за полчаса."), ("Ирина С.", 4, "Хороший выбор, но долго ждала звонка."),
           ("Дмитрий Л.", 5, "Взял Merida, всё понравилось, спасибо мастеру."), ("Ольга Н.", 5, "Быстрая доставка, вежливые курьеры."),
           ("Павел Р.", 4, "Цены нормальные, шлем подобрали по размеру."), ("Сергей К.", 5, "Брал второй раз, всё отлично."),
           ("Анна В.", 3, "Велосипед хороший, но пришёл без звонка."), ("Николай Т.", 5, "Помогли с выбором рамы."),
           ("Елена М.", 4, "Удобный сайт, понятная доставка."), ("Виктор Ж.", 5, "Сервис на высоте."),
           ("Татьяна Р.", 5, "Взяла детский, ребёнок счастлив."), ("Игорь Ф.", 4, "Всё хорошо, но упаковка помята."),
           ("Михаил О.", 5, "Настроили переключатели бесплатно."),
           ("Марина К.", 3, "Через неделю начали скрипеть тормоза, пришлось ехать в сервис. Мастер отрегулировал, но осадок остался."),
           ("Глеб Ш.", 5, "Хорошие скидки по акции."), ("Юлия Д.", 4, "Нормально, рекомендую.")]
HELMETS = [("Abus Hyban 2.0", 6990), ("Giro Register", 5490), ("Met Crossover", 7990), ("Bell Spark", 4990)]
DELIVERY = 500
PROMO = "VELO-7342"
LOGIN, PASSWORD = "demo", "Koleso-2026"
ACCESSORIES = [(f"{kind} {brand} {n}", 300 + (n * 137) % 2400) for n, (kind, brand) in enumerate(
    [("Фонарь", "Lezyne"), ("Замок", "Abus"), ("Насос", "Topeak"), ("Звонок", "Knog"), ("Перчатки", "Giro"), ("Крыло", "SKS")] * 10, 1)]
ACCESSORIES[46] = ("Фляга Elite Fly 750 мл", 1290)
POINTS = [f"ПВЗ {city}, {street}" for city, street in [
    ("Москва", "ул. Тверская 7"), ("Москва", "пр. Мира 101"), ("Казань", "ул. Пушкина 3"), ("Самара", "ул. Ленина 44"), ("Казань", "пр. Победы 210"),
    ("Пермь", "ул. Баумана 15"), ("Казань", "ул. Баумана 15"), ("Уфа", "ул. Баумана 9"), ("Казань", "ул. Декабристов 8"), ("Москва", "ул. Баумана 15")] +
    [("Новосибирск", f"ул. Сибирская {n}") for n in range(1, 31)]]
CITIES = [("Москва", 390, "1–2"), ("Санкт-Петербург", 450, "2–3"), ("Казань", 790, "3–4"), ("Самара", 690, "3–4"), ("Екатеринбург", 850, "4–5"),
          ("Новосибирск", 990, "5–7"), ("Пермь", 760, "3–5"), ("Уфа", 740, "3–4"), ("Краснодар", 690, "3–4"), ("Сочи", 720, "4–5")] + \
         [(f"Город {n}", 500 + n * 10, "5–9") for n in range(1, 21)]

STYLE = """<style>body{font-family:-apple-system,Arial;margin:0;background:#f6f6f4;color:#222}header{background:#1d3b2a;color:#fff;padding:14px 24px}
header a{color:#fff;margin-right:16px;text-decoration:none}main{padding:24px;max-width:960px}table{border-collapse:collapse;width:100%;background:#fff}
td,th{border:1px solid #ddd;padding:8px;text-align:left}.out{color:#a33}.in{color:#2a7}form label{display:block;margin:10px 0 4px}
button,.btn{background:#1d3b2a;color:#fff;border:0;padding:8px 14px;border-radius:6px;cursor:pointer;text-decoration:none}
.card{background:#fff;padding:12px;margin:8px 0;border-radius:8px}.pager a{margin-right:8px}#cookie{position:fixed;bottom:0;left:0;right:0;background:#333;color:#fff;padding:14px}</style>"""
MENU = """<header><b>Колесо — велосипеды</b> &nbsp; <a href="/catalog">Каталог</a><a href="/helmets">Шлемы</a><a href="/testdrive">Тест-драйв</a>
<a href="/promo">Акции</a><a href="/reviews">Отзывы</a><a href="/newsletter">Рассылка</a><a href="/account">Личный кабинет</a><a href="/cart">Корзина</a></header>"""
COOKIE_BANNER = """<div id="cookie">Мы используем cookies. <button onclick="document.getElementById('cookie').remove()">Понятно</button></div>"""


def page(title, body, banner=False):
    return (f"<!doctype html><html lang=ru><head><meta charset=utf-8><title>{html.escape(title)}</title>{STYLE}</head>"
            f"<body>{MENU}<main><h1>{html.escape(title)}</h1>{body}</main>{COOKIE_BANNER if banner else ''}</body></html>")


class Shop:
    def __init__(self):
        self.lock = threading.Lock()
        self.visits, self.testdrives, self.promo, self.payments, self.logins = [], [], [], [], []
        self.carts, self.sessions = {}, set()
        self.subscriptions = []
        self.bookings, self.orders = [], []


def make_handler(shop):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _sid(self):
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            return cookie["sid"].value if "sid" in cookie else None

        def _send(self, body, code=200, kind="text/html; charset=utf-8", cookie=None, location=None):
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            if cookie:
                self.send_header("Set-Cookie", cookie)
            if location:
                self.send_header("Location", location)
            self.end_headers()
            self.wfile.write(data)

        def _form(self):
            length = int(self.headers.get("Content-Length") or 0)
            return {k: v[-1] for k, v in parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True).items()}

        def do_GET(self):
            url = urlparse(self.path)
            q = {k: v[-1] for k, v in parse_qs(url.query).items()}
            with shop.lock:
                shop.visits.append(url.path)
            route = getattr(self, "get_" + (url.path.strip("/").replace("/", "_") or "home"), None)
            if not route:
                return self._send(page("Страница не найдена", "<p>Такой страницы нет.</p>"), 404)
            route(q)

        def do_POST(self):
            url = urlparse(self.path)
            form = self._form()
            route = getattr(self, "post_" + url.path.strip("/").replace("/", "_"), None)
            if not route:
                return self._send(page("Ошибка", "<p>Нет такого действия.</p>"), 404)
            route(form)

        # ---------- pages ----------
        def get_home(self, q):
            self._send(page("Колесо — магазин велосипедов", "<p>Добро пожаловать! Выберите раздел в меню.</p>", banner=True))

        def get_catalog(self, q):
            items = [b for b in BIKES if not q.get("cat") or b[1] == q["cat"]]
            n = max(1, min(int(q.get("page", "1") or 1), (len(items) + PER_PAGE - 1) // PER_PAGE))
            rows = "".join(f"<tr><td>{html.escape(name)}</td><td>{cat}</td><td>{price:,} ₽".replace(",", " ") + "</td>"
                           f"<td class={'in' if stock else 'out'}>{'В наличии' if stock else 'Нет в наличии'}</td></tr>"
                           for name, cat, price, stock in items[(n - 1) * PER_PAGE:n * PER_PAGE])
            pages = (len(items) + PER_PAGE - 1) // PER_PAGE
            cat = f"&cat={q['cat']}" if q.get("cat") else ""
            pager = "".join(f"<a href='/catalog?page={i}{cat}'>{i}</a>" for i in range(1, pages + 1))
            cats = " ".join(f"<a class=btn href='/catalog?cat={c}'>{c}</a>" for c in ("Городские", "Горные", "Складные"))
            self._send(page("Каталог велосипедов", f"<p>Категории: {cats} <a href='/catalog'>все</a></p>"
                                                   f"<table><tr><th>Модель</th><th>Категория</th><th>Цена</th><th>Наличие</th></tr>{rows}</table>"
                                                   f"<p class=pager>Страницы: {pager}</p><p>Страница {n} из {pages}</p>", banner=True))

        def get_testdrive(self, q):
            models = "".join(f"<option>{html.escape(b[0])}</option>" for b in BIKES[:12])
            self._send(page("Запись на тест-драйв", f"""<form method=post action=/testdrive>
<label for=name>Имя</label><input id=name name=name required>
<label for=phone>Телефон</label><input id=phone name=phone type=tel required>
<label for=model>Модель</label><select id=model name=model><option value="">— выберите —</option>{models}</select>
<label>Как получить</label><label><input type=radio name=method value="Самовывоз"> Самовывоз из магазина</label>
<label><input type=radio name=method value="Доставка"> Привезти к дому</label>
<label for=date>Дата</label><input id=date name=date type=date>
<label for=comment>Комментарий</label><textarea id=comment name=comment></textarea>
<label><input type=checkbox name=consent value=yes> Согласен на обработку данных</label>
<p><button type=submit>Отправить заявку</button></p></form>"""))

        def post_testdrive(self, form):
            if form.get("consent") != "yes":
                return self._send(page("Запись на тест-драйв", "<p class=out>Нужно согласие на обработку данных.</p><p><a href=/testdrive>Назад</a></p>"), 400)
            number = f"TD-{len(shop.testdrives) + 4101}"
            with shop.lock:
                shop.testdrives.append({**form, "number": number})
            self._send(page("Заявка принята", f"<p>Заявка <b>{number}</b> принята. Мы позвоним, чтобы подтвердить время.</p>"))

        def get_login(self, q):
            error = "<p class=out>Неверный логин или пароль.</p>" if q.get("error") else ""
            self._send(page("Вход в личный кабинет", f"""{error}<form method=post action=/login>
<label for=login>Логин</label><input id=login name=login><label for=password>Пароль</label><input id=password name=password type=password>
<p><button type=submit>Войти</button></p></form>"""))

        def post_login(self, form):
            with shop.lock:
                shop.logins.append(form.get("login"))
            if form.get("login") == LOGIN and form.get("password") == PASSWORD:
                sid = uuid.uuid4().hex
                with shop.lock:
                    shop.sessions.add(sid)
                return self._send("", 303, cookie=f"sid={sid}; Path=/", location="/account")
            self._send("", 303, location="/login?error=1")

        def get_account(self, q):
            if self._sid() not in shop.sessions:
                return self._send("", 303, location="/login")
            self._send(page("Личный кабинет", """<p>Здравствуйте, Демо!</p><div class=card>Бонусный баланс: <b>1 870 баллов</b></div>
<h2>Заказы</h2><table><tr><th>Номер</th><th>Дата</th><th>Сумма</th><th>Статус</th></tr>
<tr><td>№ 58213</td><td>21.10.2026</td><td>7 490 ₽</td><td>В пути</td></tr>
<tr><td>№ 57002</td><td>02.09.2026</td><td>31 490 ₽</td><td>Получен</td></tr>
<tr><td>№ 55120</td><td>15.06.2026</td><td>4 990 ₽</td><td>Получен</td></tr></table>"""))

        def get_reviews(self, q):
            first = "".join(f"<div class=card><b>{html.escape(n)}</b> — оценка {r} из 5<br>{html.escape(t)}</div>" for n, r, t in REVIEWS[:4])
            self._send(page("Отзывы покупателей", f"""<div id=list>{first}</div><p><button id=more>Показать ещё</button></p>
<script>let offset=4;document.getElementById('more').onclick=async()=>{{const r=await fetch('/api/reviews?offset='+offset);const items=await r.json();
for(const i of items){{const d=document.createElement('div');d.className='card';d.innerHTML='<b>'+i.name+'</b> — оценка '+i.rating+' из 5<br>'+i.text;
document.getElementById('list').appendChild(d);}}offset+=items.length;if(offset>={len(REVIEWS)})document.getElementById('more').remove();}};</script>"""))

        def get_api_reviews(self, q):
            offset = int(q.get("offset", "0") or 0)
            items = [{"name": n, "rating": r, "text": t} for n, r, t in REVIEWS[offset:offset + 4]]
            self._send(json.dumps(items, ensure_ascii=False), kind="application/json; charset=utf-8")

        def get_promo(self, q):
            self._send(page("Акции", f"""<p>Осенняя распродажа: скидка 10% на всё по промокоду с картинки ниже.</p>
<canvas id=c width=520 height=140 style="background:#fff;border-radius:8px"></canvas>
<script>const x=document.getElementById('c').getContext('2d');x.fillStyle='#1d3b2a';x.fillRect(0,0,520,140);x.fillStyle='#ffd34d';
x.font='bold 22px Arial';x.fillText('ОСЕННЯЯ СКИДКА 10%',24,44);x.fillStyle='#fff';x.font='bold 40px Courier New';x.fillText(String.fromCharCode(...{json.dumps([ord(c) ^ 7 for c in 'ПРОМОКОД: ' + PROMO])}.map(c=>c^7)),24,104);</script>
<form method=post action=/promo/apply><label for=code>Промокод</label><input id=code name=code><p><button type=submit>Применить</button></p></form>""", banner=True))

        def post_promo_apply(self, form):
            code = form.get("code", "").strip()
            with shop.lock:
                shop.promo.append(code)
            ok = code.upper() == PROMO
            self._send(page("Акции", "<p class=in>Скидка 10% применена к корзине.</p>" if ok else "<p class=out>Такого промокода нет.</p>"))

        # ---------- harder pages: a custom dropdown and a calendar, endless scroll, a wizard, a new tab ----------
        def get_service(self, q):
            self._send(page("Запись на ТО", """<form id=f method=post action=/service>
<label>Мастерская</label><div id=place class=card style="cursor:pointer;max-width:360px" tabindex=0><span id=placeText>Выберите мастерскую ▾</span>
<ul id=placeList hidden style="list-style:none;padding:0;margin:8px 0 0"><li data-v="Колесо на Ленина" style="padding:6px 0">Колесо на Ленина, 12</li>
<li data-v="Колесо на Мира" style="padding:6px 0">Колесо на Мира, 3</li><li data-v="Колесо в Парке" style="padding:6px 0">Колесо в Парке</li></ul></div>
<input type=hidden name=place id=placeInput>
<label>Дата</label><div id=dateText class=card style="cursor:pointer;max-width:360px">Выберите дату ▾</div>
<div id=cal hidden class=card style="max-width:360px"><span id=prev style="cursor:pointer;padding:4px 10px">‹</span><b id=month></b><span id=next style="cursor:pointer;padding:4px 10px">›</span><div id=days style="display:grid;grid-template-columns:repeat(7,1fr);gap:4px;margin-top:8px"></div></div>
<input type=hidden name=date id=dateInput><div id=slots style="margin:10px 0"></div>
<label for=name>Имя</label><input id=name name=name><p><button type=submit>Записаться</button></p></form>
<script>
const M=['Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь'];let y=2026,m=9;
place.onclick=e=>{const li=e.target.closest('li');if(li){placeInput.value=li.dataset.v;placeText.textContent=li.textContent;placeList.hidden=true;e.stopPropagation();return;}placeList.hidden=!placeList.hidden;};
dateText.onclick=()=>{cal.hidden=false;draw();};prev.onclick=()=>{m--;if(m<0){m=11;y--;}draw();};next.onclick=()=>{m++;if(m>11){m=0;y++;}draw();};
function draw(){month.textContent=' '+M[m]+' '+y+' ';days.innerHTML='';const n=new Date(y,m+1,0).getDate();for(let d=1;d<=n;d++){const b=document.createElement('span');b.textContent=d;b.style.cssText='cursor:pointer;text-align:center;padding:4px;border:1px solid #ddd;border-radius:4px';
b.onclick=async()=>{const iso=y+'-'+String(m+1).padStart(2,'0')+'-'+String(d).padStart(2,'0');dateInput.value=iso;dateText.textContent=String(d).padStart(2,'0')+'.'+String(m+1).padStart(2,'0')+'.'+y;cal.hidden=true;
const r=await fetch('/api/slots?date='+iso);const s=await r.json();slots.innerHTML='Время: '+s.map(t=>'<label style="margin-right:12px"><input type=radio name=time value="'+t+'"> '+t+'</label>').join('');};days.appendChild(b);}}
</script>"""))

        def get_api_slots(self, q):
            self._send(json.dumps(["10:00", "12:00", "15:00", "17:30"]), kind="application/json; charset=utf-8")

        def post_service(self, form):
            with shop.lock:
                shop.bookings.append(form)
            ok = all(form.get(k) for k in ("place", "date", "time", "name"))
            self._send(page("Запись на ТО", "<p class=in>Вы записаны! Ждём вас.</p>" if ok else "<p class=out>Заполните мастерскую, дату, время и имя.</p>"))

        def get_accessories(self, q):
            self._send(page("Аксессуары", """<div id=list></div><div id=end style="height:40px"></div><p id=status class=note></p>
<script>let off=0,busy=false;async function more(){if(busy||off>=60)return;busy=true;const r=await fetch('/api/accessories?offset='+off);const items=await r.json();
for(const i of items){const d=document.createElement('div');d.className='card';d.style.minHeight='64px';d.textContent=i.name+' — '+i.price.toLocaleString('ru-RU')+' ₽';list.appendChild(d);}off+=items.length;busy=false;
status.textContent=off>=60?'Показаны все товары':'Прокрутите ниже, чтобы увидеть ещё';
if(off<60&&end.getBoundingClientRect().top<innerHeight)more();}
new IntersectionObserver(e=>{if(e[0].isIntersecting)more();}).observe(document.getElementById('end'));</script>"""))

        def get_api_accessories(self, q):
            offset = int(q.get("offset", "0") or 0)
            self._send(json.dumps([{"name": n, "price": p} for n, p in ACCESSORIES[offset:offset + 10]], ensure_ascii=False), kind="application/json; charset=utf-8")

        def get_checkout(self, q):
            points = json.dumps(POINTS, ensure_ascii=False)
            self._send(page("Оформление заказа", rf"""<div class=card>В заказе: шлем Abus Hyban 2.0 — 6 990 ₽</div>
<div id=s1><h2>Шаг 1. Телефон</h2><label for=phone>Телефон в формате +7 (999) 999-99-99</label><input id=phone><p id=err class=out></p><button id=go1 type=button>Далее</button></div>
<div id=s2 hidden><h2>Шаг 2. Пункт выдачи</h2><label for=search>Поиск по городу или улице</label><input id=search><div id=pts></div><p>Выбран: <b id=chosen>ничего</b></p>
<h3>Оплата</h3><label><input type=radio name=pay value=card checked> Картой онлайн</label> <label><input type=radio name=pay value=cash> При получении</label><p><button id=go2 type=button>Далее</button></p></div>
<div id=s3 hidden><h2>Шаг 3. Проверьте заказ</h2><p id=sum></p><form id=f method=post action=/checkout><input type=hidden name=phone id=fp><input type=hidden name=point id=fpt><input type=hidden name=pay id=fpay>
<button id=final type=submit></button></form></div>
<script>const P={points};let point='';
go1.onclick=()=>{{if(!/^\+7 \(\d{{3}}\) \d{{3}}-\d{{2}}-\d{{2}}$/.test(phone.value.trim())){{err.textContent='Неверный формат телефона';return;}}s1.hidden=true;s2.hidden=false;show('');}};
function show(t){{pts.innerHTML='';P.filter(p=>p.toLowerCase().includes(t.toLowerCase())).slice(0,8).forEach(p=>{{const d=document.createElement('div');d.className='card';d.innerHTML=p+' <button type=button>Выбрать</button>';
d.querySelector('button').onclick=()=>{{point=p;chosen.textContent=p;}};pts.appendChild(d);}});}}search.oninput=()=>show(search.value);
go2.onclick=()=>{{if(!point){{alert('Выберите пункт выдачи');return;}}const pay=document.querySelector('input[name=pay]:checked').value;s2.hidden=true;s3.hidden=false;fp.value=phone.value;fpt.value=point;fpay.value=pay;
sum.textContent='Телефон: '+phone.value+'. Пункт: '+point+'. Оплата: '+(pay=='cash'?'при получении':'картой онлайн');final.textContent=pay=='cash'?'Подтвердить заказ':'Оплатить картой';
if(pay=='card')document.getElementById('f').action='/pay';}};</script>"""))

        def post_checkout(self, form):
            with shop.lock:
                shop.orders.append(form)
            self._send(page("Заказ оформлен", "<p class=in>Заказ № 60417 оформлен. Оплата при получении.</p>"))

        def get_price(self, q):
            self._send(page("Цены", "<p>Здесь цены на услуги мастерской. Доставка — отдельным прайсом.</p>"
                                    "<p><a class=btn href='/price/delivery' target=_blank>Открыть прайс доставки</a></p>"))

        def get_price_delivery(self, q):
            rows = "".join(f"<tr><td>{c}</td><td>{p} ₽</td><td>{d} дн.</td></tr>" for c, p, d in CITIES)
            self._send(page("Прайс доставки", f"<table><tr><th>Город</th><th>Цена</th><th>Срок</th></tr>{rows}</table>"))

        def get_newsletter(self, q):
            self._send(page("Рассылка", "<p>Подпишитесь на рассылку акций — форма ниже.</p>"
                                        "<iframe src='/frame/subscribe' width=560 height=220 style='border:1px solid #ccc;background:#fff'></iframe>"))

        def get_frame_subscribe(self, q):
            self._send("""<!doctype html><html lang=ru><head><meta charset=utf-8></head><body style="font-family:Arial;padding:12px">
<form id=f method=post action=/subscribe target=_self><label for=email>Почта</label><br><input id=email name=email type=email><br><br>
<label><input type=checkbox name=topics value=sale checked> Акции</label> <label><input type=checkbox name=topics value=news> Новинки</label><br><br>
<button type=button onclick="if(confirm('Подписать этот адрес на рассылку?'))document.getElementById('f').submit()">Подписаться</button></form></body></html>""")

        def post_subscribe(self, form):
            with shop.lock:
                shop.subscriptions.append(form)
            self._send("<!doctype html><html lang=ru><head><meta charset=utf-8></head><body>Готово: адрес подписан на рассылку.</body></html>")

        def get_helmets(self, q):
            rows = "".join(f"<tr><td>{html.escape(n)}</td><td>{p:,} ₽".replace(",", " ") + f"</td><td><form method=post action=/cart/add>"
                           f"<input type=hidden name=item value='{html.escape(n)}'><button type=submit>В корзину</button></form></td></tr>" for n, p in HELMETS)
            self._send(page("Шлемы", f"<table><tr><th>Модель</th><th>Цена</th><th></th></tr>{rows}</table>"))

        def _cart(self):
            sid = self._sid()
            return sid, shop.carts.setdefault(sid, []) if sid else []

        def post_cart_add(self, form):
            sid = self._sid() or uuid.uuid4().hex
            with shop.lock:
                shop.carts.setdefault(sid, []).append(form.get("item", ""))
            self._send("", 303, cookie=f"sid={sid}; Path=/", location="/cart")

        def get_cart(self, q):
            sid, items = self._cart()
            prices = dict(HELMETS)
            if not items:
                return self._send(page("Корзина", "<p>Корзина пуста.</p>"))
            rows = "".join(f"<tr><td>{html.escape(i)}</td><td>{prices.get(i, 0):,} ₽".replace(",", " ") + "</td></tr>" for i in items)
            total = sum(prices.get(i, 0) for i in items) + DELIVERY
            self._send(page("Корзина", f"""<table><tr><th>Товар</th><th>Цена</th></tr>{rows}<tr><td>Доставка</td><td>{DELIVERY} ₽</td></tr>
<tr><td><b>Итого к оплате</b></td><td><b>{total:,} ₽</b></td></tr></table>""".replace(",", " ") +
                                   "<form method=post action=/pay><p><button type=submit>Оплатить</button></p></form>"))

        def post_pay(self, form):
            with shop.lock:
                shop.payments.append(self._sid())
            self._send(page("Оплата", "<p>Переход к оплате картой…</p>"))

    return Handler


def start():
    shop = Shop()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(shop))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return shop, server, f"http://127.0.0.1:{server.server_address[1]}"
