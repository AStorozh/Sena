// The sections below the first screen: live small agents (canvas.mini), the faces picker, the counters, the author's playground.
// The bigger living pictures are in viz.js. Everything starts once the cursors have built its section ("built" from build.js).
(function () {
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
  const agent = (code) => Sena.AGENTS.find((a) => a.code === code);
  const once = (id, fn) => { const el = document.getElementById(id); el && el.addEventListener("built", () => fn(el), { once: true }); };

  // Every canvas.mini is a live agent: data-agent, data-emotion, data-thought; its face is kept on the canvas for the section
  for (const cv of $$("canvas.mini")) {
    cv.face = Sena.Character.create(cv, agent(cv.dataset.agent).color);
    cv.face.emotion = cv.dataset.emotion || "calm";
    cv.face.thought = cv.dataset.thought || "pause";
  }

  // 08 Faces: the big agent wears what is picked; until then it walks through the emotions by itself
  once("faces", () => {
    const face = $("#faceBig").face, say = $("#faceSay"), emos = $$("#emotions button"), thoughts = $$("#thoughts button");
    let auto = true, i = 0;
    const wear = (btn) => { emos.forEach((b) => b.classList.toggle("on", b === btn)); face.emotion = btn.dataset.emotion; say.textContent = btn.dataset.say; };
    const think = (btn) => { thoughts.forEach((b) => b.classList.toggle("on", b === btn)); face.thought = btn.dataset.thought; };
    emos.forEach((b) => b.addEventListener("click", () => { auto = false; wear(b); }));
    thoughts.forEach((b) => b.addEventListener("click", () => { auto = false; think(b); }));
    wear(emos[0]); think(thoughts[0]);
    setInterval(() => { if (auto) { i++; wear(emos[i % emos.length]); think(thoughts[i % thoughts.length]); } }, 2600);
  });

  // 10 Numbers count up once built
  once("numbers", (sec) => {
    for (const b of $$("[data-count]", sec)) {
      const to = +b.dataset.count, plus = b.dataset.plus || "", t0 = performance.now();
      (function tick(now) {
        const p = Math.min(1, (now - t0) / 1400), v = Math.round(to * (1 - Math.pow(1 - p, 3)));
        b.textContent = v.toLocaleString(Sena.lang === "en" ? "en-US" : "ru-RU") + (p === 1 ? plus : "");
        if (p < 1) requestAnimationFrame(tick);
      })(t0);
    }
  });

  // the portfolio's address, one place for all its buttons (to fill in later)
  const PORTFOLIO = "https://asto-portfolio.ru";
  document.querySelectorAll("[data-author-site]").forEach((a) => { a.href = PORTFOLIO; if (PORTFOLIO !== "#") { a.target = "_blank"; a.rel = "noreferrer"; } });

  // the handwritten "asto" (from the Luma site) draws itself when the cursors have built the author — it was hidden till then —
  // and again each time the author comes back into view
  const asto = document.getElementById("astoLogo"), draw = () => (asto.src = `assets/asto-logo.svg?t=${Date.now()}`);
  if (asto) {
    let built = false;
    document.getElementById("author").addEventListener("built", () => { built = true; draw(); }, { once: true });
    new IntersectionObserver(([e]) => { if (e.isIntersecting && built) draw(); }, { threshold: 0.4 }).observe(asto);
  }

  // 11 The author: a second playground; the agents come down onto the name, the role line and the buttons once it is built
  const author = document.getElementById("author");
  if (author) {
    const world = Sena.World({ root: author, field: $("#authorField"), stage: $("#authorStage"), solids: ".author .kicker, .au-name, .au-role, .author .sec-lead, .site-btn, .btn" });
    world.start();
    author.addEventListener("built", () => {
      world.measure();
      // onto the logo's letters, the name, and down to the floor: at three heights, not a row
      const hr = author.getBoundingClientRect(), box = (s) => $(s).getBoundingClientRect(), at = (b, k) => b.left - hr.left + b.width * k;
      const logo = box(".au-logo"), name = box(".au-name");
      world.drop([at(logo, 0.14), at(name, 0.15), at(logo, 0.41), at(logo, 0.6), at(name, 0.7), at(logo, 0.86), world.width * 0.97].map((v) => v + (Math.random() - 0.5) * 8));
    }, { once: true });
  }
})();
