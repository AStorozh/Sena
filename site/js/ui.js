// Small shared behaviors: buttons with .magnet lean toward the cursor
(function () {
  for (const btn of document.querySelectorAll(".magnet")) {
    btn.addEventListener("pointermove", (e) => {
      const r = btn.getBoundingClientRect();
      btn.style.transform = `translate(${(e.clientX - r.left - r.width / 2) * 0.22}px, ${(e.clientY - r.top - r.height / 2) * 0.3}px)`;
    });
    btn.addEventListener("pointerleave", () => (btn.style.transform = ""));
  }
})();

// Came here from the portfolio (the same site, another page): a white "back" button in the top right corner leads there.
// The page it came from is kept for the tab, so the button stays after a language switch reloads the page
(function () {
  const KEY = "sena-back";
  let back = null;
  try {
    const from = document.referrer && new URL(document.referrer);
    const home = location.pathname.replace(/[^/]*$/, "");   // this site's folder, e.g. /sena/
    if (from && from.origin === location.origin && !from.pathname.startsWith(home)) sessionStorage.setItem(KEY, from.href);
    back = sessionStorage.getItem(KEY);
  } catch (e) { /* no storage: no button */ }
  if (!back) return;
  const a = document.createElement("a");
  a.className = "back-btn"; a.href = back;
  a.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 6l-6 6 6 6" /></svg><span>${Sena.t("Вернуться")}</span>`;
  document.body.appendChild(a);
  document.documentElement.classList.add("has-back");
  // The button matches the menu: the same height and the same vertical position at every width (the menu changes size
  // with the screen and, in places, while scrolling). On a phone the button is a circle, so its width follows too
  const syncTo = (bar) => {
    if (!bar) return;
    const fit = () => {
      const r = bar.getBoundingClientRect();
      if (!r.height) return;
      a.style.top = `${Math.round(r.top)}px`;
      a.style.height = `${Math.round(r.height)}px`;
      a.style.width = getComputedStyle(a.querySelector("span")).display === "none" ? `${Math.round(r.height)}px` : "";
    };
    fit();
    [600, 2000, 5000].forEach((ms) => setTimeout(fit, ms));   // after the page has built itself and the fonts arrived
    if (window.ResizeObserver) new ResizeObserver(fit).observe(bar);
    addEventListener("resize", fit);
    addEventListener("scroll", fit, { passive: true });
    bar.addEventListener("transitionend", fit);
  };
  syncTo(document.querySelector("#island .nt-bar"));   // the folded bar, not the whole island: open, it grows down
})();

// The menu is Sena's shelf at the notch: folded it is a black island with the reading ring and the section on screen;
// pointed at, it opens with a spring into six cards; a click makes the agent hop, the shelf closes and the page goes there
(function () {
  const island = document.getElementById("island"), shelf = document.getElementById("links");
  const cards = [...shelf.querySelectorAll("a")], prog = document.getElementById("navProg");
  if (!cards.length) return;
  const ring = 2 * Math.PI * 16;
  prog.style.strokeDasharray = ring; prog.style.strokeDashoffset = ring;
  let here = null, closeTimer = 0;
  const open = () => { clearTimeout(closeTimer); island.classList.add("open"); };
  const close = () => { closeTimer = setTimeout(() => island.classList.remove("open"), 160); };
  // Hover opens it only for a mouse. On a touch screen a tap also fires pointerenter and pointerleave, so the shelf
  // opened, then the click closed it again a moment later; there the tap alone toggles it, a tap elsewhere closes it
  island.addEventListener("pointerenter", (e) => { if (e.pointerType === "mouse") open(); });
  island.addEventListener("pointerleave", (e) => { if (e.pointerType === "mouse") close(); });
  island.querySelector(".nt-now").addEventListener("click", () => (island.classList.contains("open") ? close() : open()));
  document.addEventListener("pointerdown", (e) => { if (e.pointerType !== "mouse" && !island.contains(e.target)) close(); });
  const hop = (cv) => { if (!cv || !cv.face) return; cv.face.emotion = "wow"; cv.animate([{ transform: "none" }, { transform: "translateY(-10px) scale(1.1)" }, { transform: "none" }], { duration: 420, easing: "ease-out" }); setTimeout(() => (cv.face.emotion = "calm"), 700); };
  for (const a of cards) a.addEventListener("click", () => { hop(a.querySelector("canvas")); island.classList.remove("open"); });
  cards.forEach((a) => a.addEventListener("pointerenter", () => { const f = a.querySelector("canvas").face; if (f) { f.emotion = "happy"; f.thought = a.dataset.thought; } }));
  cards.forEach((a) => a.addEventListener("pointerleave", () => { const f = a.querySelector("canvas").face; if (f) f.emotion = "calm"; }));
  // every section is followed, not only the six in the menu: the island shows the section really on screen (its own number and
  // name from its kicker), and the card of the menu group it belongs to is marked
  const GROUP = { can: "can", agents: "agents", faces: "agents", cases: "cases", inside: "cases", home: "cases", self: "cases", everywhere: "cases",
    security: "security", numbers: "cases", download: "download", author: "author" };
  const card = new Map(cards.map((a, i) => [a.getAttribute("href").slice(1), i]));
  function mark(sec) {
    const i = sec ? card.get(GROUP[sec.id]) : null;
    here = sec;
    cards.forEach((a, k) => a.classList.toggle("here", k === i));
    const kicker = sec && sec.querySelector(".kicker"), [num, ...name] = kicker ? kicker.textContent.split("·").map((x) => x.trim()) : ["00", Sena.t("Начало")];
    const title = name.join(" · ");
    document.getElementById("navNum").textContent = num;
    document.getElementById("navName").textContent = title ? title[0].toUpperCase() + title.slice(1) : Sena.t("Начало");
  }
  const sections = [...document.querySelectorAll("[data-build-section]")];
  const pick = () => {   // the section crossing the line a third of the way down the screen
    const line = innerHeight * 0.35;
    let now = null;
    for (const s of sections) { const r = s.getBoundingClientRect(); if (r.top <= line && r.bottom > line) now = s; }
    if (now !== here) mark(now);
  };
  addEventListener("scroll", () => {
    const max = document.documentElement.scrollHeight - innerHeight;
    prog.style.strokeDashoffset = ring * (1 - (max > 0 ? scrollY / max : 0));
    pick();
  }, { passive: true });
  pick();
})();
