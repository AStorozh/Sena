// A playground for the agents: they are physical bodies on a section's letters and text blocks, can be grabbed and thrown,
// act out the tasks the page gives them, and light up a dot field behind. Their faces follow one logic: what happens to them
// (grabbed, thrown, hit) comes first, then their task, then rest.
//
//   const world = Sena.World({ root, field, stage, solids: ".btn, h1" });
//   world.start();                 // the field draws and reacts to the cursor
//   world.drop([x, x, ...]);       // one x per agent, in Sena.AGENTS order: they fall from above onto the ledges
//   world.assign("ag_web", ["tense", "search", "looking up flights"]);   // emotion, thought, what it does (shown in a tag)
//   world.clear();                 // everyone is free again
//
// Ledges are every letter of root's [data-text] lines (split by Sena.World.letters) and every element matching solids.
(function () {
  const G = 2600, MAX_V = 3400;
  const now = () => performance.now() / 1000;
  const hexA = (hex, a) => hex + Math.round(a * 255).toString(16).padStart(2, "0");

  // A line of big text: one span per letter, so each can be carried in by the build and stand as a ledge;
  // .base marks the baseline the ledges are measured from
  function letters(line) {
    line.innerHTML = "";
    const base = document.createElement("span");
    base.className = "base"; base.style.cssText = "display:inline-block;width:0;height:0";
    line.appendChild(base);
    for (const ch of line.dataset.text) {
      const s = document.createElement("span");
      s.className = "ch"; s.dataset.build = "carry"; s.textContent = ch === " " ? " " : ch;
      line.appendChild(s);
    }
  }

  function World({ root, field, stage, solids = "", onGrab = () => {} }) {
    const fctx = field.getContext("2d");
    root.querySelectorAll("[data-text]").forEach(letters);

    const bodies = Sena.AGENTS.map((a) => {
      const el = document.createElement("canvas"); el.className = "body";
      el.style.transform = "translate(-9999px, 0)";   // off screen until it drops
      el.style.width = el.style.height = "100px";
      const tag = document.createElement("span"); tag.className = "tag";
      stage.append(el, tag);
      return { ...a, el, tag, face: Sena.Character.create(el, a.color), x: 0, y: -999, vx: 0, vy: 0, r: 50, angle: 0, sq: 0, sqv: 0, axis: 0,
        held: false, heldAt: 0, inside: false, impactUntil: 0, impact: "calm", flying: false, scene: null };
    });
    const byCode = Object.fromEntries(bodies.map((b) => [b.code, b]));
    let W = 0, H = 0, rects = [], dropped = false, visible = true;
    const pointer = { x: -999, y: -999, held: null, ox: 0, oy: 0 };
    const pulses = [];

    function measure() {
      const hr = root.getBoundingClientRect();
      W = hr.width; H = hr.height;
      const dpr = Math.min(2, devicePixelRatio || 1);
      field.width = W * dpr; field.height = H * dpr; fctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const r = Math.max(30, Math.min(54, W * 0.036));
      bodies.forEach((b) => { b.r = r; b.el.style.width = b.el.style.height = 2 * r + "px"; });
      ledges();
    }

    // The solid parts: each letter's ink box and the text blocks; re-read every second, as text in them changes
    function ledges() {
      const hr = root.getBoundingClientRect(), probe = document.createElement("canvas").getContext("2d");
      rects = [];
      for (const line of root.querySelectorAll("[data-text]")) {
        const cs = getComputedStyle(line), baseY = line.querySelector(".base").getBoundingClientRect().top - hr.top;
        probe.font = `${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
        for (const s of line.querySelectorAll(".ch")) {
          if (!s.textContent.trim()) continue;
          const m = probe.measureText(s.textContent), left = s.getBoundingClientRect().left - hr.left;
          rects.push({ x0: left - m.actualBoundingBoxLeft, x1: left + m.actualBoundingBoxRight,
            y0: baseY - m.actualBoundingBoxAscent, y1: baseY + m.actualBoundingBoxDescent, letter: true });
        }
      }
      // a text block is a ledge line by line, as wide as each line really is: otherwise an agent would stand on air next to a
      // short last line; boxes (buttons, chips, cards) stay whole
      // a picture with its letters' tops given ([data-ledges]: "x0,x1,top" per letter, as shares of its box)
      for (const el of root.querySelectorAll("[data-ledges]")) {
        const q = el.getBoundingClientRect();
        for (const part of el.dataset.ledges.split(";")) {
          const [a, b, t] = part.split(",").map(Number);
          rects.push({ x0: q.left - hr.left + q.width * a, x1: q.left - hr.left + q.width * b, y0: q.top - hr.top + q.height * t, y1: q.bottom - hr.top, grip: true });
        }
      }
      // a drawn shape ([data-solid-shape]): each of its paths is a ledge, its box drawn in a little so nothing stands on air
      for (const path of root.querySelectorAll("[data-solid-shape] path")) {
        const q = path.getBoundingClientRect(), ix = q.width * 0.14, iy = q.height * 0.06;
        rects.push({ x0: q.left - hr.left + ix, x1: q.right - hr.left - ix, y0: q.top - hr.top + iy, y1: q.bottom - hr.top - iy, grip: true });
      }
      if (solids) for (const el of root.querySelectorAll(solids)) {
        const er = el.getBoundingClientRect(), boxy = getComputedStyle(el).backgroundColor !== "rgba(0, 0, 0, 0)" || el.classList.contains("btn");
        if (boxy) { rects.push({ x0: er.left - hr.left, x1: er.right - hr.left, y0: er.top - hr.top, y1: er.bottom - hr.top }); continue; }
        const range = document.createRange(); range.selectNodeContents(el);
        const lines = [];
        for (const q of range.getClientRects()) {
          if (q.width < 2) continue;
          const line = lines.find((l) => Math.abs(l.top - q.top) < q.height * 0.5);
          if (line) { line.left = Math.min(line.left, q.left); line.right = Math.max(line.right, q.right); line.bottom = Math.max(line.bottom, q.bottom); }
          else lines.push({ left: q.left, right: q.right, top: q.top, bottom: q.bottom });
        }
        for (const l of lines) rects.push({ x0: l.left - hr.left, x1: l.right - hr.left, y0: l.top - hr.top, y1: l.bottom - hr.top });
      }
    }

    // The span of the letters, off their rims: a body set on a rim rolls off
    function letterSpan() {
      const ls = rects.filter((q) => q.letter), r = bodies[0].r;
      return [Math.min(...ls.map((q) => q.x0)) + r * 0.6, Math.max(...ls.map((q) => q.x1)) - r * 1.2];
    }

    // spots: an x per agent (it falls from above the section), or [x, y] (it pops up there, just above its ledge, and drops onto it)
    function drop(spots) {
      bodies.forEach((b, i) => {
        const s = spots[i];
        if (Array.isArray(s)) {
          b.x = s[0]; b.y = Math.max(b.r, s[1]); b.vx = 0; b.vy = 0; b.inside = true;
          b.wait = i * 0.14;   // one after another: each waits in the air, unseen, for its turn
          b.el.animate([{ opacity: 0, scale: 0.2 }, { opacity: 1, scale: 1.1, offset: 0.7 }, { opacity: 1, scale: 1 }], { duration: 420, delay: i * 140, easing: "ease-out", fill: "backwards" });
        } else {
          b.x = s + (Math.random() - 0.5) * 16;
          b.y = -b.r - 60 - i * 110; b.vx = (Math.random() - 0.5) * 40; b.vy = 300; b.inside = false;
        }
      });
      dropped = true;
      setInterval(() => visible && ledges(), 1000);
    }

    function hit(b, n, speed, t) {
      if (speed > 260) { b.sq = Math.min(0.32, b.sq + speed / 2800); b.axis = Math.atan2(n.y, n.x); }
      if (speed > 1500 && b.flying) { b.impact = "dizzy"; b.impactUntil = t + 1.8; }
      else if (speed > 900) { b.impact = "surprise"; b.impactUntil = t + 0.5; }
      if (speed > 120) b.flying = false;
    }

    function step(dt, t) {
      for (const b of bodies) {
        if (b.held) {
          const tx = pointer.x - pointer.ox, ty = pointer.y - pointer.oy;
          const nx = b.x + (tx - b.x) * Math.min(1, dt * 30), ny = b.y + (ty - b.y) * Math.min(1, dt * 30);
          b.vx += ((nx - b.x) / dt - b.vx) * 0.35; b.vy += ((ny - b.y) / dt - b.vy) * 0.35;
          b.x = nx; b.y = ny;
          continue;
        }
        if (b.wait > 0) { b.wait -= dt; continue; }
        b.vy += G * dt; b.vx *= 0.9995;
        const sp = Math.hypot(b.vx, b.vy);
        if (sp > MAX_V) { b.vx *= MAX_V / sp; b.vy *= MAX_V / sp; }
        b.x += b.vx * dt; b.y += b.vy * dt;
        if (b.y > b.r) b.inside = true;
        // the section's walls; the ceiling only once the body has come in from above
        if (b.x < b.r) { hit(b, { x: 1, y: 0 }, -b.vx, t); b.x = b.r; b.vx = Math.abs(b.vx) * 0.5; }
        if (b.x > W - b.r) { hit(b, { x: -1, y: 0 }, b.vx, t); b.x = W - b.r; b.vx = -Math.abs(b.vx) * 0.5; }
        if (b.inside && b.y < b.r) { hit(b, { x: 0, y: 1 }, -b.vy, t); b.y = b.r; b.vy = Math.abs(b.vy) * 0.4; }
        if (b.y > H - b.r) { hit(b, { x: 0, y: -1 }, b.vy, t); b.y = H - b.r; b.vy = -Math.abs(b.vy) * 0.38; b.vx *= 0.97; if (Math.abs(b.vy) < 40) b.vy = 0; }
        for (const r of rects) {
          const cx = Math.max(r.x0, Math.min(b.x, r.x1)), cy = Math.max(r.y0, Math.min(b.y, r.y1));
          let dx = b.x - cx, dy = b.y - cy, d = Math.hypot(dx, dy);
          if (d >= b.r) continue;
          if (d < 1e-6) { dx = 0; dy = -1; d = 1; b.y = r.y0; }
          const n = { x: dx / d, y: dy / d }, vn = b.vx * n.x + b.vy * n.y;
          b.x += n.x * (b.r - d); b.y += n.y * (b.r - d);
          if (vn < 0) {
            hit(b, n, -vn, t);
            b.vx -= 1.42 * vn * n.x; b.vy -= 1.42 * vn * n.y;
            const tx = -n.y, ty = n.x, vt = b.vx * tx + b.vy * ty;
            b.vx -= vt * tx * 0.012; b.vy -= vt * ty * 0.012;
            if (-vn < 50 && n.y < -0.6) b.vy = 0;
          }
          if (r.grip && n.y < -0.3) { b.vx *= 0.6; if (Math.abs(b.vy) < 120) b.vy = Math.min(b.vy, 0); }   // a drawn shape holds what lands on it
        }
      }
      // bodies push each other; a held one is immovable and shoves the rest
      for (let i = 0; i < bodies.length; i++) for (let j = i + 1; j < bodies.length; j++) {
        const a = bodies[i], c = bodies[j];
        const dx = c.x - a.x, dy = c.y - a.y, d = Math.hypot(dx, dy), min = a.r + c.r;
        if (d >= min || d < 1e-6) continue;
        const n = { x: dx / d, y: dy / d }, over = min - d;
        const wa = a.held ? 0 : c.held ? 1 : 0.5, wc = 1 - wa;
        a.x -= n.x * over * wa; a.y -= n.y * over * wa; c.x += n.x * over * wc; c.y += n.y * over * wc;
        const rv = (c.vx - a.vx) * n.x + (c.vy - a.vy) * n.y;
        if (rv < 0) {
          const j2 = -1.4 * rv * (a.held || c.held ? 1 : 0.5);
          if (!a.held) { a.vx -= j2 * n.x; a.vy -= j2 * n.y; }
          if (!c.held) { c.vx += j2 * n.x; c.vy += j2 * n.y; }
          if (-rv > 600) { hit(a, { x: -n.x, y: -n.y }, -rv, t); hit(c, n, -rv, t); }
        }
      }
    }

    function moodOf(b, t) {
      const speed = Math.hypot(b.vx, b.vy);
      if (b.held) return [speed > 2200 ? "dizzy" : t - b.heldAt < 0.3 ? "surprise" : "happy", "exclaim"];
      if (t < b.impactUntil) return [b.impact, b.impact === "dizzy" ? "spinner" : b.scene ? b.scene[1] : "exclaim"];
      if (b.flying) return ["wow", "exclaim"];
      const over = Math.hypot(pointer.x - b.x, pointer.y - b.y) < b.r && !pointer.held;
      if (b.scene) return [over && b.scene[0] === "calm" ? "love" : b.scene[0], b.scene[1]];
      if (over) return ["love", "pause"];
      return [b.sleeps ? "sleepy" : "calm", "pause"];
    }

    function render(t, dt) {
      for (const b of bodies) {
        b.sqv += (-190 * b.sq - 13 * b.sqv) * dt; b.sq += b.sqv * dt;
        const lean = b.held ? Math.max(-0.6, Math.min(0.6, b.vx / 2400)) : Math.max(-0.35, Math.min(0.35, b.vx / 2000));
        b.angle += (lean - b.angle) * Math.min(1, dt * 9);
        [b.face.emotion, b.face.thought] = moodOf(b, t);
        const s = Math.max(-0.25, Math.min(0.32, b.sq));
        b.el.style.transform = `translate(${b.x - b.r}px, ${b.y - b.r}px) rotate(${b.axis}rad) scale(${1 - s}, ${1 + s * 0.7}) rotate(${b.angle - b.axis}rad)`;
        const showTag = !!b.scene && !b.held;
        b.tag.classList.toggle("on", showTag);
        if (showTag) {
          if (b.tag.textContent !== b.scene[2]) b.tag.textContent = b.scene[2];
          // kept inside the section (on a phone an agent at the edge would carry its tag off screen); the arrow still points at it
          const tw = b.tag.offsetWidth, tx = Math.max(6, Math.min(W - tw - 6, b.x - tw / 2));
          b.tag.style.transform = `translate(${tx}px, ${b.y - b.r - b.tag.offsetHeight - 12}px)`;
          b.tag.style.setProperty("--ax", `${Math.max(10, Math.min(tw - 10, b.x - tx))}px`);
        }
      }
    }

    // The field: a dot grid that bends away from the cursor and lights up around the agents; work flows from Sena along lines
    function drawField(t) {
      fctx.clearRect(0, 0, W, H);
      const gap = Sena.GRID(), hr = root.getBoundingClientRect();   // the page's grid (paper.js), so the dots line up at the edges
      const oy = (((gap / 2 - hr.top - scrollY) % gap) + gap) % gap, ox = (((gap / 2 - hr.left - scrollX) % gap) + gap) % gap;
      for (let gy = oy; gy < H; gy += gap) for (let gx = ox; gx < W; gx += gap) {
        let x = gx, y = gy, size = 1.3, color = "#d6ccb9";
        const pd = Math.hypot(gx - pointer.x, gy - pointer.y);
        if (pd < 170 && pd > 0) { const k = Math.pow(1 - pd / 170, 2); x += (gx - pointer.x) / pd * k * 22; y += (gy - pointer.y) / pd * k * 22; size += k * 1.6; }
        if (dropped) for (const b of bodies) {
          const d = Math.hypot(gx - b.x, gy - b.y), reach = b.r + 80;
          if (d < reach) { const k = 1 - d / reach; if (k > 0.05) { size += k * 2.2; color = b.color; } }
        }
        fctx.fillStyle = color;
        fctx.beginPath(); fctx.arc(x, y, size, 0, 6.283); fctx.fill();
      }
      if (!dropped) return;
      const sena = byCode.ag_sena;
      for (const b of bodies) {
        if (b === sena || !b.scene) continue;
        const mx = (sena.x + b.x) / 2, my = Math.min(sena.y, b.y) - Math.hypot(b.x - sena.x, b.y - sena.y) * 0.28;
        fctx.strokeStyle = hexA(b.color, 0.75); fctx.lineWidth = 2.5; fctx.setLineDash([2, 9]); fctx.lineDashOffset = -t * 40; fctx.lineCap = "round";
        fctx.beginPath(); fctx.moveTo(sena.x, sena.y); fctx.quadraticCurveTo(mx, my, b.x, b.y); fctx.stroke(); fctx.setLineDash([]);
        for (let k = 0; k < 3; k++) {
          const p = (t * 0.55 + k / 3) % 1, q = 1 - p;
          const px = q * q * sena.x + 2 * q * p * mx + p * p * b.x, py = q * q * sena.y + 2 * q * p * my + p * p * b.y;
          fctx.fillStyle = b.color; fctx.beginPath(); fctx.arc(px, py, 5, 0, 6.283); fctx.fill();
        }
      }
      for (let i = pulses.length - 1; i >= 0; i--) {
        const p = pulses[i], age = t - p.start;
        if (age > 0.9) { pulses.splice(i, 1); continue; }
        fctx.strokeStyle = hexA(p.body.color, 0.6 * (1 - age / 0.9)); fctx.lineWidth = 3;
        fctx.beginPath(); fctx.arc(p.body.x, p.body.y, p.body.r + age * 160, 0, 6.283); fctx.stroke();
      }
    }

    // A task for an agent: a ring goes out from it and it hops, so the eye finds who just got work
    function assign(code, scene) {
      const b = byCode[code], fresh = !b.scene || b.scene[1] !== scene[1];
      b.scene = scene;
      if (fresh && !b.held) {
        pulses.push({ body: b, start: now() });
        if (Math.abs(b.vy) < 30) b.vy = -520 - Math.random() * 160;
      }
      return fresh;
    }
    const clear = () => bodies.forEach((b) => (b.scene = null));

    // Pointer: grab, drag, throw
    const local = (e) => { const hr = root.getBoundingClientRect(); return { x: e.clientX - hr.left, y: e.clientY - hr.top }; };
    for (const b of bodies) b.el.addEventListener("pointerdown", (e) => {
      const p = local(e);
      Object.assign(pointer, p, { held: b, ox: p.x - b.x, oy: p.y - b.y });
      b.held = true; b.heldAt = now(); b.el.classList.add("held"); b.vx = b.vy = 0;
      onGrab(b);
      e.preventDefault();
    });
    addEventListener("pointermove", (e) => Object.assign(pointer, local(e)), { passive: true });
    addEventListener("pointerup", () => {
      const b = pointer.held;
      if (!b) return;
      b.held = false; b.el.classList.remove("held"); b.inside = true;
      b.flying = Math.hypot(b.vx, b.vy) > 700;
      pointer.held = null;
    });
    addEventListener("resize", () => {
      measure();
      bodies.forEach((b) => { b.x = Math.min(Math.max(b.x, b.r), W - b.r); b.y = Math.min(b.y, H - b.r); });
    });
    new IntersectionObserver(([e]) => (visible = e.isIntersecting)).observe(root);

    let last = now(), idleHop = 0;
    function frame() {
      const t = now(), dt = Math.min(1 / 30, t - last);
      last = t;
      if (visible && dropped) {
        for (let i = 0; i < 4; i++) step(dt / 4, t);
        if (t > idleHop) {   // now and then a free agent fidgets: it is alive, not a picture
          const free = bodies.filter((b) => !b.scene && !b.held && !b.sleeps && b.vy === 0);
          if (free.length) free[Math.floor(Math.random() * free.length)].vy = -300 - Math.random() * 200;
          idleHop = t + 2.5 + Math.random() * 3;
        }
        render(t, dt);
      }
      if (visible) drawField(t);
      requestAnimationFrame(frame);
    }
    const start = () => { measure(); requestAnimationFrame(frame); };

    return { bodies, byCode, start, measure, drop, letterSpan, assign, clear, get width() { return W; } };
  }

  World.letters = letters;
  Sena.World = World;
})();
