// The page builds itself: the agents' cursors fly in, drag out a frame for each element with its size, type the text in,
// guides and marks flash like in a design tool, a counter at the bottom counts the layers. Any element with data-build takes part:
// "type" — a text frame, then typed in; "box" — its background painted by the drag, then its text typed; "carry" — brought in by hand.
// Elements are built in document order. Sena.build.run(elements) builds them now; Sena.build.watch() builds each element of
// a <section data-build-section> when that element itself scrolls into view, so the show keeps pace with any scrolling speed.
(function () {
  const AGENTS = Sena.AGENTS.map((a) => [a.code, a.cursor || a.color]);
  const HELPERS = 7;   // unnamed cursors: they scan around and take work too, so a section is built in about a second
  const TYPE_MS = 380;   // a whole block of text is typed in this long, however long it is
  const reduced = Sena.reduced;

  const layer = document.createElement("div");
  layer.className = "build-layer";
  document.body.appendChild(layer);

  const ease = (p) => 1 - Math.pow(1 - p, 3);
  const easeIO = (p) => (p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2);
  let pace = 1;   // the swarm hurries when many elements wait (fast scrolling): every motion takes 1/pace of its time
  const wait = (ms) => new Promise((r) => setTimeout(r, ms / pace));
  const tween = (ms, fn) => new Promise((done) => {
    const t0 = performance.now();
    ms /= pace;
    (function tick(now) {
      const p = Math.min(1, (now - t0) / ms);
      fn(p);
      p < 1 ? requestAnimationFrame(tick) : done();
    })(t0);
  });
  const box = (el) => {
    const r = el.getBoundingClientRect();
    return { x: r.left + scrollX, y: r.top + scrollY, w: r.width, h: r.height };
  };
  const make = (cls, html = "") => { const d = document.createElement("div"); d.className = cls; d.innerHTML = html; layer.appendChild(d); return d; };
  const fadeOut = (el, ms = 350) => el.animate([{ opacity: 1 }, { opacity: 0 }], { duration: ms, fill: "forwards" }).finished.then(() => el.remove());

  const ARROW = (c) => `<svg viewBox="0 0 24 24"><path d="M3 2 L3 19 L7.6 14.8 L10.8 21.6 L13.6 20.3 L10.5 13.6 L16.8 13.6 Z" fill="${c}" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>`;

  class Cursor {
    constructor(name, color, small) {
      this.el = make("b-cursor" + (small ? " small" : ""), ARROW(color) + (name ? `<span style="background:${color}">${name}</span>` : ""));
      const side = Math.random() * 4 | 0, W = innerWidth, H = innerHeight;
      this.x = scrollX + (side === 0 ? -30 : side === 1 ? W + 30 : Math.random() * W);
      this.y = scrollY + (side === 2 ? -30 : side === 3 ? H + 30 : Math.random() * H);
      this.busy = false; this.place();
    }
    place() { this.el.style.transform = `translate(${this.x}px, ${this.y}px)`; }
    async move(x, y, ms) {
      const x0 = this.x, y0 = this.y, bend = (Math.random() - 0.5) * 120;   // a slight curve, as a hand moves
      const my = this.token = (this.token || 0) + 1;   // a newer move (or a drag) takes over from this one
      await tween(ms, (p) => {
        if (my !== this.token) return;
        const e = easeIO(p), arc = Math.sin(Math.PI * e) * bend;
        this.x = x0 + (x - x0) * e + arc * 0.3; this.y = y0 + (y - y0) * e - arc * 0.5; this.place();
      });
      if (my === this.token) { this.x = x; this.y = y; this.place(); }
    }
    async wander(stop) {
      while (!stop.done) {
        if (!this.busy) await this.move(scrollX + innerWidth * (0.1 + Math.random() * 0.8), scrollY + innerHeight * (0.15 + Math.random() * 0.7), 700 + Math.random() * 700);
        await wait(120 + Math.random() * 500);
      }
    }
    leave() {
      const a = Math.random() * Math.PI * 2;
      return this.move(this.x + Math.cos(a) * innerWidth, this.y + Math.sin(a) * innerHeight, 500).then(() => this.el.remove());
    }
  }

  function mark(x, y) {   // a crosshair where the drag starts
    const m = make("b-cross");
    m.style.transform = `translate(${x}px, ${y}px)`;
    setTimeout(() => fadeOut(m, 500), 900);
  }

  function guides(b) {
    const v = make("b-guide v"), h = make("b-guide h");
    v.style.left = b.x + "px"; h.style.top = b.y + "px";
    setTimeout(() => { fadeOut(v, 600); fadeOut(h, 600); }, 1300);
  }

  // The element's text, one hidden span per character, so the cursor can type it in; restore() puts plain text back
  function letters(el) {
    const nodes = [], walk = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, {
      acceptNode: (n) => (n.textContent.trim() && !n.parentElement.closest("svg") ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT),
    });
    while (walk.nextNode()) nodes.push(walk.currentNode);
    const chars = [], groups = [];
    for (const n of nodes) {
      const frag = document.createDocumentFragment(), spans = [];
      for (const c of n.textContent) {
        const s = document.createElement("span");
        s.textContent = c; s.style.visibility = "hidden";
        frag.appendChild(s); spans.push(s); chars.push(s);
      }
      groups.push({ text: n.textContent, spans });
      n.replaceWith(frag);
    }
    return {
      chars,
      restore() { for (const g of groups) g.spans[0] && (g.spans[0].replaceWith(document.createTextNode(g.text)), g.spans.slice(1).forEach((s) => s.remove())); },
    };
  }

  async function typeIn(text, cursor) {
    const caret = make("b-caret"), n = text.chars.length;
    let shown = 0;
    await tween(Math.min(TYPE_MS, 60 + n * 16), (p) => {   // by time, not by letter: many letters may land in one frame
      const upto = Math.round(p * n);
      for (; shown < upto; shown++) text.chars[shown].style.visibility = "";
      if (!shown) return;
      const r = text.chars[shown - 1].getBoundingClientRect();
      caret.style.height = r.height + "px";
      caret.style.transform = `translate(${r.right + scrollX + 1}px, ${r.top + scrollY}px)`;
      cursor.x = r.right + scrollX + 6; cursor.y = r.top + scrollY + r.height * 0.7; cursor.place();
    });
    caret.remove();
    text.restore();
  }

  // Drags a frame from the corner; with fill, the element's own background is painted under it as it grows
  async function frame(cursor, b, el, fill) {
    const sel = make("b-sel", "<i></i><i></i><i></i><i></i>"), tag = make("b-size");
    const round = parseFloat(getComputedStyle(el).borderTopLeftRadius) || 0;
    sel.style.transform = `translate(${b.x}px, ${b.y}px)`;
    await tween(Math.min(320, 140 + (b.w + b.h) * 0.14), (p) => {
      const e = ease(p), w = b.w * e, h = b.h * e;
      sel.style.width = w + "px"; sel.style.height = h + "px";
      tag.textContent = `${Math.round(w)} × ${Math.round(h)}`;
      tag.style.transform = `translate(${b.x + w / 2}px, ${b.y + h + 6}px) translateX(-50%)`;
      if (fill) el.style.clipPath = `inset(0 ${b.w - w}px ${b.h - h}px 0 round ${Math.min(round, h / 2)}px)`;
      cursor.x = b.x + w; cursor.y = b.y + h; cursor.place();
    });
    el.style.clipPath = "";
    return () => { fadeOut(sel); fadeOut(tag); };
  }

  // A letter is picked up somewhere above and carried into its place
  async function carry(cursor, el, b) {
    const sx = scrollX + innerWidth * (0.2 + Math.random() * 0.6), sy = scrollY + 90 + Math.random() * innerHeight * 0.25;
    await cursor.move(sx, sy, 200 + Math.random() * 100);
    const dx = sx - b.x - b.w * 0.3, dy = sy - b.y - b.h * 0.3, spin = (Math.random() - 0.5) * 30;
    el.style.opacity = 1;
    el.animate([{ transform: `translate(${dx}px, ${dy}px) rotate(${spin}deg) scale(0)` }, { transform: `translate(${dx}px, ${dy}px) rotate(${spin}deg) scale(1)` }], { duration: 140, easing: "ease-out" });
    await wait(140);
    const tag = make("b-size");
    await tween(420 + Math.random() * 120, (p) => {
      const e = easeIO(p), k = 1 - e;
      el.style.transform = `translate(${dx * k}px, ${dy * k}px) rotate(${spin * k}deg)`;
      cursor.x = b.x + b.w * 0.3 + dx * k; cursor.y = b.y + b.h * 0.3 + dy * k; cursor.place();
      tag.textContent = `x ${Math.round(b.x + dx * k)}  y ${Math.round(b.y + dy * k)}`;
      tag.style.transform = `translate(${cursor.x + 14}px, ${cursor.y + 24}px)`;
    });
    el.style.transform = "";
    el.animate([{ transform: "translateY(0) scaleY(1)" }, { transform: "translateY(4px) scaleY(.94)" }, { transform: "none" }], { duration: 280, easing: "ease-out" });
    mark(b.x, b.y + b.h);
    fadeOut(tag, 300);
  }

  async function work(cursor, el, hud) {
    cursor.busy = true; cursor.token++;
    const kind = el.dataset.build, b = box(el);
    if (kind === "carry") await carry(cursor, el, b);
    else {
      const text = letters(el);
      await cursor.move(b.x, b.y, 150 + Math.random() * 90);
      mark(b.x, b.y); guides(b);
      el.style.opacity = 1;
      if (kind === "box") el.style.clipPath = "inset(0 100% 100% 0)";
      const done = await frame(cursor, b, el, kind === "box");
      await typeIn(text, cursor);
      setTimeout(done, 200);
    }
    hud.done();
    cursor.busy = false;
  }

  function makeHud(total) {
    const dots = AGENTS.map(([, c]) => `<i style="background:${c}"></i>`).join("");
    const el = make("b-hud", `<span class="b-dots">${dots}</span><b>${AGENTS.length}</b>&nbsp;${Sena.t("агентов")}<span class="b-sep"></span><b class="b-n">0</b>&nbsp;/&nbsp;<span class="b-all">${total}</span> ${Sena.t("слоёв")}<span class="b-sep"></span><span class="b-state">${Sena.t("сканирую страницу…")}</span>`);
    let n = 0, all = total;
    return {
      el,
      state(t) { el.querySelector(".b-state").textContent = Sena.t(t); },
      done() { el.querySelector(".b-n").textContent = ++n; },
      add(k) { all += k; el.querySelector(".b-all").textContent = all; },
    };
  }

  // Builds the given elements; resolves when all are in place
  async function run(targets) {
    targets = targets.filter((el) => el.dataset.build);
    if (reduced || Sena.instant) { targets.forEach((el) => (el.style.opacity = 1)); return; }
    const hud = makeHud(targets.length), stop = { done: false };
    const crew = AGENTS.map(([n, c]) => new Cursor(n, c, false));
    const helpers = Array.from({ length: HELPERS }, (_, i) => new Cursor("", AGENTS[i % AGENTS.length][1], true));
    [...crew, ...helpers].forEach((c) => c.wander(stop));
    await wait(120);   // the swarm glances over the empty page first
    hud.state("собираю страницу…");
    const queue = [...targets];
    await Promise.all([...crew, ...helpers].map(async (c, i) => {
      await wait(i * 15);
      while (queue.length) await work(c, queue.shift(), hud);
    }));
    hud.state("готово");
    stop.done = true;
    await wait(120);
    [...crew, ...helpers].forEach((c) => c.leave());
    setTimeout(() => fadeOut(hud.el, 300), 300);
    await wait(150);
  }

  // Below the first screen every element is built when it itself comes into view, by one swarm that stays while there is work.
  // Elements scrolled past before a cursor got to them just appear; a long backlog makes the swarm hurry. A section gets
  // a "built" event once everything of it the reader has reached is in place — its own script starts its motion then.
  const inView = (el) => { const r = el.getBoundingClientRect(); return r.bottom > 0 && r.top < innerHeight; };
  function watch() {
    const owner = new Map(), fired = new Set(), ready = [];   // unbuilt element → its section; sections already "built"; elements in view
    const show = (el) => { el.style.opacity = 1; finish(el); };
    function finish(el) {
      if (!owner.has(el)) return;   // shown on the scroll while a cursor was still typing it
      const sec = owner.get(el);
      owner.delete(el);
      if (fired.has(sec)) return;
      const reached = [...owner].some(([e, s]) => s === sec && e.getBoundingClientRect().top < innerHeight * 0.9);
      if (!reached) { fired.add(sec); sec.dispatchEvent(new Event("built")); }
    }
    const look = new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (!e.isIntersecting) continue;
        look.unobserve(e.target);
        ready.push(e.target);
      }
      ready.sort((a, b) => (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1));
      if (crew) crew.hud.add(entries.filter((e) => e.isIntersecting).length); else if (ready.length) gather();
    }, { rootMargin: "0px 0px -10% 0px" });
    document.querySelectorAll("[data-build-section]").forEach((sec) => {
      sec.querySelectorAll("[data-build]").forEach((el) => {
        owner.set(el, sec);
        reduced || Sena.instant ? new IntersectionObserver(([e], o) => e.isIntersecting && (o.disconnect(), show(el))).observe(el) : look.observe(el);
      });
    });
    let tick = false;
    addEventListener("scroll", () => {   // a jump or a fast flick: whatever went above the screen unbuilt just appears
      if (tick) return;
      tick = true;
      requestAnimationFrame(() => {
        tick = false;
        for (const el of owner.keys()) if (el.getBoundingClientRect().bottom < 0) { look.unobserve(el); ready.includes(el) && ready.splice(ready.indexOf(el), 1); show(el); }
      });
    }, { passive: true });

    let crew = null;
    async function gather() {
      const hud = makeHud(ready.length), stop = { done: false };
      const hands = [...AGENTS.map(([n, c]) => new Cursor(n, c, false)), ...Array.from({ length: HELPERS }, (_, i) => new Cursor("", AGENTS[i % AGENTS.length][1], true))];
      crew = { hud };
      hands.forEach((c) => c.wander(stop));
      hud.state("собираю страницу…");
      await Promise.all(hands.map(async (c, i) => {
        await wait(i * 15);
        for (let idle = 0; idle < 700; ) {
          const el = ready.shift();
          if (!el) { await new Promise((r) => setTimeout(r, 100)); idle += 100; continue; }
          idle = 0;
          if (!owner.has(el)) continue;
          if (!inView(el)) { el.getBoundingClientRect().top >= innerHeight ? look.observe(el) : show(el); continue; }   // scrolled back up: wait for it again
          pace = ready.length > 8 ? 2.4 : ready.length > 3 ? 1.6 : 1;
          await work(c, el, hud);
          finish(el);
        }
      }));
      crew = null; pace = 1;
      hud.state("готово");
      stop.done = true;
      hands.forEach((c) => c.leave());
      setTimeout(() => fadeOut(hud.el, 300), 300);
      if (ready.length) gather();
    }
  }

  Sena.build = { run, watch };
})();
