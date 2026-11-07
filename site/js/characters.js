// Agent characters for the site: a port of mac/Sources/Views/Character.swift (itself from the owner's logo project) to canvas.
// All geometry is in the original's 344x344 units. The face (emotion) says how the agent is, the bubble (thought) what it does.
(function () {
  const SIDE = 344, HEAD = { x: 172, y: 172 }, HEAD_R = 150, BUBBLE_R = 63;
  const BLOCKER = { x: -100, y: -100, r: 75 };
  const EYES = [{ x: -35, y: 0 }, { x: 34, y: 0 }];
  const HALF_H = 30.7, STROKE = 37.8, MAX_SHIFT = HEAD_R * 0.38, MAX_BEND = HEAD_R * 0.03, EDGE_PAD = 6, BLOCK_PAD = 8;
  const TRANSITION = 0.42, MOUSE_FRESH = 3.0;
  const rnd = (a, b) => a + Math.random() * (b - a);

  function arc(c, r, from, to) {
    const n = Math.max(4, Math.floor(Math.abs(to - from) / 3)), pts = [];
    for (let i = 0; i <= n; i++) {
      const a = (from + (to - from) * i / n) * Math.PI / 180;
      pts.push([c.x + r * Math.cos(a), c.y + r * Math.sin(a)]);
    }
    return pts;
  }
  function lines(pts, close) {
    const p = new Path2D();
    pts.forEach(([x, y], i) => (i ? p.lineTo(x, y) : p.moveTo(x, y)));
    if (close) p.closePath();
    return p;
  }

  const BODY = (() => {
    const pts = [...arc(HEAD, HEAD_R, 185.42, -95.42), ...arc({ x: 159.535, y: 40.59 }, 18, -95.42, -199.74),
      ...arc({ x: 72, y: 72 }, 75, -19.74, 109.74), ...arc({ x: 40.59, y: 159.535 }, 18, -70.26, -174.58)];
    return lines(pts, true);
  })();
  const SPIRAL = (() => {
    const total = 1.75 * 360, pts = [];
    for (let a = 0; a <= total; a += 12) { const r = 4 + 22 * a / total, rad = a * Math.PI / 180; pts.push([r * Math.cos(rad), r * Math.sin(rad)]); }
    return lines(pts);
  })();
  const HEART = new Path2D("M0 28 C-16 16 -28 4 -28 -8 C-28 -20 -18 -26 -10 -26 C-4 -26 -1 -22 0 -19 C1 -22 4 -26 10 -26 C18 -26 28 -20 28 -8 C28 4 16 16 0 28 Z");
  const CROSS = new Path2D("M-20 -20 L20 20 M20 -20 L-20 20");
  const STAR = (() => {
    const s = 1.15, q = (x, y) => `${(x - 37) * s} ${(y - 37) * s}`;
    return new Path2D(`M${q(37, 10)} C${q(39.5, 27)} ${q(47, 34.5)} ${q(64, 37)} C${q(47, 39.5)} ${q(39.5, 47)} ${q(37, 64)} ` +
      `C${q(34.5, 47)} ${q(27, 39.5)} ${q(10, 37)} C${q(27, 34.5)} ${q(34.5, 27)} ${q(37, 10)} Z`);
  })();

  // Emotions: the eye's shape and the gaze's habits (Character.swift Mood)
  const BASE = { bx: 0, by: 0, rx: 0.9, ry: 0.9, ease: 0.07, gmin: 0.9, gmax: 3.8, fw: 1, still: false, blink: "squash", bmin: 1.2, bmax: 9, shape: null };
  const M = (o) => ({ ...BASE, ...o });
  const MOODS = {
    calm: M({ len: 1, width: 1, tilt: 0, bend: 0, dy: 0 }),
    tense: M({ len: 0.9, width: 0.42, tilt: -75, bend: 0, dy: 0, by: 0.05, rx: 0.8, ry: 0.7, ease: 0.22, gmin: 0.26, gmax: 0.76, bmin: 2.2, bmax: 7 }),
    surprise: M({ len: 0.12, width: 1.85, tilt: 0, bend: 0, dy: -6, by: -0.1, rx: 0.85, ry: 0.8, ease: 0.3, gmin: 0.18, gmax: 0.52, blink: "off" }),
    sad: M({ len: 0.7, width: 0.9, tilt: 38, bend: 0, dy: 6, by: 0.45, rx: 0.7, ry: 0.5, ease: 0.03, gmin: 2.2, gmax: 5.2, fw: 0.55, blink: "slow", bmin: 2.5, bmax: 8 }),
    happy: M({ len: 0.55, width: 1, tilt: 90, bend: -16, dy: 6, by: -0.1, rx: 0.85, ry: 0.8, ease: 0.12, gmin: 0.6, gmax: 1.8, blink: "off" }),
    sleepy: M({ len: 0.95, width: 0.3, tilt: 90, bend: 6, dy: 10, by: 0.62, rx: 0.3, ry: 0.26, ease: 0.018, gmin: 3.2, gmax: 7.5, fw: 0.35, blink: "slow", bmin: 1.4, bmax: 4.5 }),
    angry: M({ len: 0.7, width: 1.1, tilt: -50, bend: 0, dy: 2, by: 0.18, rx: 0.7, ry: 0.55, ease: 0.055, gmin: 1.3, gmax: 3.2, fw: -0.75, bmin: 2.5, bmax: 9 }),
    ko: M({ len: 0.05, width: 1.8, tilt: 0, bend: 0, dy: 0, by: 0.3, rx: 0, ry: 0, ease: 0.02, fw: 0, still: true, blink: "off", shape: "cross" }),
    love: M({ len: 0.05, width: 1.8, tilt: 0, bend: 0, dy: 0, by: -0.18, rx: 0.8, ry: 0.75, ease: 0.035, gmin: 1.5, gmax: 3.6, blink: "pulse", bmin: 0.9, bmax: 2.2, shape: "heart" }),
    dizzy: M({ len: 0.05, width: 1.9, tilt: 0, bend: 0, dy: 0, rx: 0, ry: 0, ease: 0.05, fw: 0, still: true, blink: "off", shape: "spiral" }),
    wow: M({ len: 0.05, width: 1.9, tilt: 0, bend: 0, dy: 0, by: -0.15, rx: 0.9, ry: 0.85, ease: 0.18, gmin: 0.42, gmax: 1.2, blink: "pulse", bmin: 1.2, bmax: 3.2, shape: "star" }),
  };

  const easeInOut = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
  const easeBack = (t) => { const c = 1.1; return 1 + (c + 1) * Math.pow(t - 1, 3) + c * Math.pow(t - 1, 2); };
  const smooth = (x) => (1 - Math.cos(Math.PI * Math.min(1, Math.max(0, x)))) / 2;
  const mirror = (i) => (EYES[i].x < 0 ? 1 : -1);

  class Face {
    constructor() {
      this.mood = MOODS.calm; this.eye = { len: 1, width: 1, tilt: 0, bend: 0, dy: 0 };
      this.from = { ...this.eye }; this.to = { ...this.eye }; this.shiftStart = 0; this.progress = 1;
      this.shapeNow = null; this.shapeTarget = null; this.morph = 1;
      this.current = { x: 0, y: 0 }; this.target = { x: 0, y: 0 };
      this.last = 0; this.nextGaze = 0; this.nextBlink = 0; this.blinkStart = -10; this.blinkMode = "off";
      this.lastMouse = null; this.mouseMoved = -10; this.emotion = "";
    }

    step(t, id, toMouse) {
      const dt = this.last > 0 ? Math.min(0.1, t - this.last) : 1 / 60;
      this.last = t;
      if (id !== this.emotion) { this.emotion = id; this.change(MOODS[id] || MOODS.calm, t); }
      if (this.progress < 1) {
        this.progress = Math.min(1, (t - this.shiftStart) / TRANSITION);
        const e = easeBack(this.progress), f = this.from, g = this.to;
        this.eye = { len: f.len + (g.len - f.len) * e, width: f.width + (g.width - f.width) * e, tilt: f.tilt + (g.tilt - f.tilt) * e,
          bend: f.bend + (g.bend - f.bend) * e, dy: f.dy + (g.dy - f.dy) * e };
      }
      if (this.shapeNow !== this.shapeTarget) {
        this.morph -= dt / (TRANSITION * 0.45);
        if (this.morph <= 0) { this.morph = 0; this.shapeNow = this.shapeTarget; }
      } else if (this.morph < 1) this.morph = Math.min(1, this.morph + dt / (TRANSITION * 0.55));
      if (toMouse) {
        if (!this.lastMouse || Math.abs(this.lastMouse.x - toMouse.x) + Math.abs(this.lastMouse.y - toMouse.y) > 0.5) this.mouseMoved = t;
        this.lastMouse = toMouse;
      }
      if (toMouse && t - this.mouseMoved < MOUSE_FRESH && this.mood.fw !== 0) {
        const d = Math.max(Math.hypot(toMouse.x, toMouse.y), 1), reach = Math.min(1, d / 160);
        this.look(toMouse.x / d * reach, toMouse.y / d * reach);
      } else if (t >= this.nextGaze) {
        this.pickGaze(); this.nextGaze = t + rnd(this.mood.gmin, Math.max(this.mood.gmin, this.mood.gmax));
      }
      if (this.mood.blink !== "off" && t >= this.nextBlink) {
        this.blinkStart = t; this.blinkMode = this.mood.blink; this.nextBlink = t + rnd(this.mood.bmin, this.mood.bmax);
      }
      const k = 1 - Math.pow(1 - this.mood.ease, dt * 60);
      this.current.x += (this.target.x - this.current.x) * k;
      this.current.y += (this.target.y - this.current.y) * k;
    }

    change(next, t) {
      let tilt = next.tilt, bend = next.bend;
      while (tilt - this.eye.tilt > 90) { tilt -= 180; bend = -bend; }
      while (tilt - this.eye.tilt < -90) { tilt += 180; bend = -bend; }
      this.mood = next; this.from = { ...this.eye };
      this.to = { len: next.len, width: next.width, tilt, bend, dy: next.dy };
      this.shiftStart = t; this.progress = 0; this.shapeTarget = next.shape;
      this.blinkMode = "off"; this.nextBlink = t + rnd(0.3, 2.5);
      this.pickGaze(); this.nextGaze = t + rnd(next.gmin, Math.max(next.gmin, next.gmax));
    }

    look(nx, ny) {
      const m = this.mood;
      let ax = m.bx + nx * m.fw * m.rx, ay = m.by + ny * m.fw * m.ry;
      const len = Math.hypot(ax, ay);
      if (len > 1) { ax /= len; ay /= len; }
      this.aim(ax, ay);
    }

    pickGaze() {
      const m = this.mood;
      let nx = m.bx, ny = m.by;
      if (!m.still) { const r = Math.sqrt(Math.random()), a = Math.random() * 2 * Math.PI; nx += Math.cos(a) * r * m.rx; ny += Math.sin(a) * r * m.ry; }
      const len = Math.hypot(nx, ny);
      if (len > 1) { nx /= len; ny /= len; }
      this.aim(nx, ny);
    }

    aim(nx, ny) {
      const len = Math.hypot(nx, ny);
      if (len < 1e-6) { this.target = { x: 0, y: 0 }; return; }
      const ux = nx / len, uy = ny / len;
      let lo = 0, hi = HEAD_R;
      for (let i = 0; i < 14; i++) {
        const mid = (lo + hi) / 2, c = this.constrain(ux * mid, uy * mid, this.to, true);
        if (Math.hypot(c.x - ux * mid, c.y - uy * mid) < 0.5) lo = mid; else hi = mid;
      }
      this.target = { x: ux * lo * len, y: uy * lo * len };
    }

    ends(i, tx, ty, e) {
      const h = HALF_H * e.len, ang = e.tilt * mirror(i) * Math.PI / 180;
      const cx = EYES[i].x + tx, cy = EYES[i].y + ty + e.dy;
      return { a: { x: cx + h * Math.sin(ang), y: cy - h * Math.cos(ang) }, b: { x: cx - h * Math.sin(ang), y: cy + h * Math.cos(ang) } };
    }

    static nearest(p, a, b) {
      const vx = b.x - a.x, vy = b.y - a.y, l2 = vx * vx + vy * vy;
      const t = l2 > 0 ? Math.max(0, Math.min(1, ((p.x - a.x) * vx + (p.y - a.y) * vy) / l2)) : 0;
      return { x: a.x + t * vx, y: a.y + t * vy };
    }

    constrain(x, y, e, edgeOnly) {
      let tx = x, ty = y;
      const cap = STROKE * e.width / 2 + (Math.abs(e.bend) + MAX_BEND) / 2, o = BLOCKER;
      const edgeLim = [], need = [];
      for (let i = 0; i < 2; i++) {
        const r = this.ends(i, 0, 0, e);
        edgeLim.push(Math.max(HEAD_R - EDGE_PAD - cap, Math.max(Math.hypot(r.a.x, r.a.y), Math.hypot(r.b.x, r.b.y))));
        const q = Face.nearest(o, r.a, r.b);
        need.push(Math.min(o.r + cap + BLOCK_PAD, Math.hypot(q.x - o.x, q.y - o.y)));
      }
      for (let n = 0; n < 8; n++) {
        let moved = false;
        for (let i = 0; i < 2; i++) {
          let r = this.ends(i, tx, ty, e);
          const da = Math.hypot(r.a.x, r.a.y), db = Math.hypot(r.b.x, r.b.y), far = da > db ? r.a : r.b, fd = Math.max(da, db);
          if (fd > edgeLim[i] && fd > 0) { const k = (fd - edgeLim[i]) / fd; tx -= far.x * k; ty -= far.y * k; moved = true; }
          if (edgeOnly) continue;
          r = this.ends(i, tx, ty, e);
          const q = Face.nearest(o, r.a, r.b), d = Math.hypot(q.x - o.x, q.y - o.y);
          if (d < need[i] && d > 0) { const push = (need[i] - d) / d; tx += (q.x - o.x) * push; ty += (q.y - o.y) * push; moved = true; }
        }
        if (!moved) break;
      }
      return { x: tx, y: ty };
    }

    blinkScale(t) {
      const spec = { squash: [0.34, 0.08, 0.38, 0.58], slow: [0.95, 0.14, 0.42, 0.62], pulse: [0.5, 1.18, 0.45, 0.45], off: [0, 1, 0, 0] }[this.blinkMode];
      const [length, low, a, b] = spec, p = length > 0 ? (t - this.blinkStart) / length : 1;
      if (p < 0 || p >= 1) return [1, 1];
      const w = p < a ? smooth(p / a) : p <= b ? 1 : smooth((1 - p) / (1 - b)), v = 1 + (low - 1) * w;
      return this.blinkMode === "pulse" ? [v, v] : [1, v];
    }

    drawEyes(ctx, t) {
      const e = this.eye, c = this.constrain(this.current.x, this.current.y, e);
      const nx = c.x / MAX_SHIFT, ny = Math.max(-1, Math.min(1, c.y / MAX_SHIFT)), near = Math.pow(Math.min(1, Math.hypot(nx, ny)), 2);
      const tiltRad = near * (nx * 8 + ny * 4) * Math.PI / 180, bendX = near * nx * MAX_BEND;
      const vis = this.morph <= 0 ? 0 : this.shapeNow === this.shapeTarget ? easeBack(this.morph) : easeInOut(this.morph);
      const capScale = this.shapeNow == null ? vis : 0, specScale = this.shapeNow == null ? 0 : vis;
      const centers = EYES.map((p) => ({ x: HEAD.x + p.x + c.x, y: HEAD.y + p.y + c.y + e.dy }));
      const mid = { x: (centers[0].x + centers[1].x) / 2, y: (centers[0].y + centers[1].y) / 2 }, [sx, sy] = this.blinkScale(t);
      ctx.save();
      ctx.translate(mid.x, mid.y); ctx.scale(sx, sy); ctx.translate(-mid.x, -mid.y);
      if (this.emotion === "ko") {
        const w = Math.sin(t / 2.4 * 2 * Math.PI);
        ctx.translate(mid.x + 2 * w, mid.y); ctx.rotate(1.5 * w * Math.PI / 180); ctx.translate(-mid.x, -mid.y);
      }
      ctx.strokeStyle = ctx.fillStyle = "#000"; ctx.lineCap = "round"; ctx.lineJoin = "round";
      for (let i = 0; i < 2; i++) {
        const m = mirror(i), p = centers[i];
        ctx.save(); ctx.translate(p.x, p.y);
        if (capScale > 0.001) {
          ctx.save(); ctx.scale(capScale, capScale);
          const h = HALF_H * e.len * (1 - near * 0.18), ang = tiltRad + e.tilt * m * Math.PI / 180, bend = bendX + e.bend * m;
          const pt = (lx, ly) => [lx * Math.cos(ang) - ly * Math.sin(ang), lx * Math.sin(ang) + ly * Math.cos(ang)];
          const a = pt(0, -h), b = pt(0, h), k = pt(bend, 0);
          ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.quadraticCurveTo(k[0], k[1], b[0], b[1]);
          ctx.lineWidth = STROKE * e.width; ctx.stroke(); ctx.restore();
        }
        if (specScale > 0.001 && this.shapeNow) {
          ctx.save(); ctx.scale(specScale, specScale);
          if (this.shapeNow === "spiral") { ctx.rotate((t % 3) / 3 * 2 * Math.PI); ctx.lineWidth = 9; ctx.stroke(SPIRAL); }
          else if (this.shapeNow === "cross") { ctx.lineWidth = 14; ctx.stroke(CROSS); }
          else if (this.shapeNow === "heart") ctx.fill(HEART);
          else ctx.fill(STAR);
          ctx.restore();
        }
        ctx.restore();
      }
      ctx.restore();
    }
  }

  // Thoughts, in the original's 74x74 box (Character.swift Thought)
  const PERIOD = 2.2, LINE = 14;
  const phase = (t, delay = 0, length = PERIOD) => (((t + delay) / length) % 1 + 1) % 1;
  const wave = (t, delay = 0, length = PERIOD) => smooth(1 - Math.abs(phase(t, delay, length) * 2 - 1));
  function keys(p, k) {
    for (let i = 1; i < k.length; i++) if (p <= k[i][0]) {
      const [a, va] = k[i - 1], [b, vb] = k[i];
      return va + (vb - va) * smooth(b > a ? (p - a) / (b - a) : 1);
    }
    return k[k.length - 1][1];
  }
  const dot = (x, y, r = 7) => { const p = new Path2D(); p.arc(x, y, r, 0, Math.PI * 2); return p; };
  const pill = (x, y, w, h, r) => { const p = new Path2D(); p.roundRect(x, y, w, h, r); return p; };
  const arcPath = (c, r, a, b) => lines(arc(c, r, a, b));

  function drawThought(id, ctx, t, paper) {
    const C = { x: 37, y: 37 };
    const turned = (c, deg, s = 1, mx = 0, my = 0) => { ctx.translate(c.x + mx, c.y + my); ctx.rotate(deg * Math.PI / 180); ctx.scale(s, s); ctx.translate(-c.x, -c.y); };
    const stroke = (p, o = 1) => { ctx.globalAlpha = o; ctx.stroke(p); ctx.globalAlpha = 1; };
    const fill = (p, o = 1) => { ctx.globalAlpha = o; ctx.fill(p); ctx.globalAlpha = 1; };
    const inside = (fn) => { ctx.save(); fn(); ctx.restore(); };
    ctx.strokeStyle = ctx.fillStyle = "#000"; ctx.lineWidth = LINE; ctx.lineCap = "round"; ctx.lineJoin = "round";
    switch (id) {
      case "dots": [15, 37, 59].forEach((x, i) => fill(dot(x, 37 + 6 - 12 * wave(t, i * 0.37)))); break;
      case "search": inside(() => {
        const a = phase(t) * 2 * Math.PI; ctx.translate(7 * Math.cos(a), 7 * Math.sin(a));
        const ring = new Path2D(); ring.arc(32, 32, 14, 0, Math.PI * 2); stroke(ring); stroke(lines([[42, 42], [53, 53]]));
      }); break;
      case "read": case "upload": inside(() => {
        const down = id === "read", y = keys(phase(t), [[0, down ? -5 : 5], [0.3, down ? 6 : -6], [1, down ? -5 : 5]]);
        ctx.translate(0, y);
        const tip = down ? 54 : 20, back = down ? 18 : 56, wing = down ? tip - 13 : tip + 13;
        stroke(lines([[37, back], [37, tip]])); stroke(lines([[24, wing], [37, tip], [50, wing]]));
      }); break;
      case "memory": [[16, 26.5], [37, 26.5], [58, 26.5], [16, 47.5], [37, 47.5], [58, 47.5]].forEach(([x, y], i) => inside(() => {
        const w = wave(t, i * 0.18); turned({ x, y }, 0, 0.55 + 0.45 * w); fill(dot(x, y), 0.45 + 0.55 * w);
      })); break;
      case "code": [1, -1].forEach((side, i) => inside(() => {
        const k = keys(phase(t, i * 0.55, 1.1), [[0, 0], [0.12, 1], [0.42, 0], [1, 0]]);
        ctx.translate(3 * k * side, 0);
        const x0 = side > 0 ? 26 : 48, x1 = side > 0 ? 13 : 61;
        stroke(lines([[x0, 20], [x1, 37], [x0, 54]]), 0.5 + 0.5 * k);
      })); break;
      case "send": {
        const w = wave(t); fill(dot(37, 37));
        inside(() => { turned(C, 0, 0.82 + 0.3 * w); stroke(arcPath(C, 20, 139.5, 220.5), 0.35 + 0.65 * w); stroke(arcPath(C, 20, -40.5, 40.5), 0.35 + 0.65 * w); });
        break;
      }
      case "question": {
        const w = wave(t), hook = new Path2D("M24 27.5 C24 19.5 31 13.5 38 13.5 C45 13.5 51 18.5 51 25.5 C51 33.5 38 35.5 38 41.5");
        inside(() => { turned({ x: 38, y: 41.5 }, -10 + 20 * w); stroke(hook); });
        inside(() => { turned({ x: 38, y: 60.5 }, 0, 0.85 + 0.29 * w); fill(dot(38, 60.5)); });
        break;
      }
      case "judge": fill(pill(14, 56, 46, 12, 6)); inside(() => {
        turned({ x: 37, y: 46 }, keys(phase(t), [[0, -26], [0.28, 6], [0.44, -3], [1, -26]]));
        fill(pill(20, 16, 34, 18, 9)); stroke(lines([[37, 34], [37, 46]]));
      }); break;
      case "count": {
        const p = phase(t), digits = p < 0.25 ? 7 : p < 0.5 ? 12 : p < 0.75 ? 17 : p < 0.94 ? 23 : 7;
        fill(pill(17, 10, 40, 54, 12)); ctx.fillStyle = paper; ctx.fill(pill(23, 16.5, 28, 12, 6)); ctx.fillStyle = "#000";
        fill(pill(25.5, 19, digits, 7, 3.5)); ctx.fillStyle = paper;
        [[29, 39, 0], [45, 54, 1.65], [45, 39, 1.1], [29, 54, 0.55]].forEach(([x, y, d]) =>
          ctx.fill(dot(x, y, keys(phase(t, d), [[0, 6], [0.08, 2], [0.14, 2], [0.3, 6], [1, 6]]))));
        ctx.fillStyle = "#000"; break;
      }
      case "bell": {
        const w = wave(t), bell = new Path2D("M37 13 C47 13 53 23 53 35 C53 40 57 40 57 43 C57 46.3 53.7 49 51 49 L23 49 C19.7 49 17 46.3 17 43 C17 40 21 40 21 35 C21 23 27 13 37 13 Z");
        inside(() => { turned({ x: 37, y: 15.9 }, -11 + 22 * w); fill(bell); });
        fill(dot(37 - 6 + 12 * w, 59)); break;
      }
      case "plus": inside(() => { turned(C, 0, 0.94 + 0.16 * wave(t)); stroke(lines([[37, 20], [37, 54]])); stroke(lines([[20, 37], [54, 37]])); }); break;
      case "stop": inside(() => { turned(C, phase(t, 0, 4.4) * 90); fill(pill(22, 22, 30, 30, 10)); }); break;
      case "minus": { const s = 1 - 0.3 * wave(t); stroke(lines([[37 - 17 * s, 37], [37 + 17 * s, 37]])); break; }
      case "tap": [0, 1.1].forEach((d) => {
        const p = phase(t, d); stroke(dot(37, 37, keys(p, [[0, 13], [0.3, 18], [1, 30]])), keys(p, [[0, 0], [0.3, 0.95], [1, 0]]));
      }); fill(dot(37, 37)); break;
      case "play": inside(() => { turned({ x: 42, y: 37 }, 0, 0.94 + 0.16 * wave(t)); const tri = lines([[29, 21], [55, 37], [29, 53]], true); fill(tri); stroke(tri); }); break;
      case "tune": [[14, 18, 44], [28, 37, 58], [14, 56, 38]].forEach(([a, y, b], i) => {
        const row = lines([[a, y], [b, y]]); stroke(row, 0.3);
        ctx.setLineDash([10, 30]); ctx.lineDashOffset = -phase(t, i * 0.73) * 40; ctx.stroke(row); ctx.setLineDash([]);
      }); break;
      case "signal": {
        const c = { x: 37, y: 56 };
        fill(dot(37, 56), 0.35 + 0.65 * wave(t)); stroke(arcPath(c, 16, 225, 315), 0.35 + 0.65 * wave(t, 0.27)); stroke(arcPath(c, 34, 225, 315), 0.35 + 0.65 * wave(t, 0.55));
        break;
      }
      case "media": [8.5, 30, 51.5].forEach((x, i) => { const w = wave(t, i * 0.73); fill(pill(x, 30 - 14 * w, 14, 14 + 28 * w, 7)); }); break;
      case "cloud": inside(() => { ctx.translate(0, 6 - 12 * wave(t)); [dot(26, 38, 11), dot(37, 31, 14), dot(48, 38, 11)].forEach((p) => fill(p)); ctx.fillRect(26, 38, 22, 11); }); break;
      case "horn": inside(() => {
        turned({ x: 34, y: 37 }, 0, 1.35 * (0.94 + 0.16 * wave(t)), 3, 0);
        fill(pill(23, 30, 14, 14, 5)); const cone = lines([[37, 37], [45, 23], [45, 51]], true); fill(cone); stroke(cone);
      }); break;
      case "listen": {
        const w = wave(t); stroke(lines([[37, 16], [37, 30]]));
        inside(() => { turned({ x: 37, y: 36 }, 0, 0.94 + 0.18 * w); stroke(arcPath({ x: 37, y: 36 }, 19, 180, 0), 0.8 + 0.2 * w); });
        break;
      }
      case "pause": { const w = wave(t); stroke(lines([[28 - 6 * w, 21], [28 - 6 * w, 53]])); stroke(lines([[46 + 6 * w, 21], [46 + 6 * w, 53]])); break; }
      case "spinner": inside(() => { turned(C, phase(t, 0, 1.1) * 360); ctx.setLineDash([99, 39]); ctx.stroke(dot(37, 37, 22)); ctx.setLineDash([]); }); break;
      case "check": inside(() => {
        turned(C, 0, keys(phase(t), [[0, 1], [0.12, 1.18], [0.24, 0.94], [0.4, 1], [1, 1]])); stroke(lines([[22, 38], [32, 48], [52, 26]]));
      }); break;
      case "cross": inside(() => { turned(C, -9 + 18 * wave(t)); stroke(lines([[24, 24], [50, 50]])); stroke(lines([[50, 24], [24, 50]])); }); break;
      case "exclaim": {
        const w = wave(t);
        inside(() => { ctx.translate(0, 4 - 9 * w); stroke(lines([[37, 17], [37, 35]])); });
        inside(() => { turned({ x: 37, y: 54 }, 0, 0.88 + 0.26 * w, 0, 3 - 6 * w); fill(dot(37, 54)); });
        break;
      }
    }
  }

  // One character on its own canvas: the page sets .emotion and .thought, the eyes follow the mouse
  const mouse = { x: -1, y: -1, seen: false };
  addEventListener("pointermove", (e) => { mouse.x = e.clientX; mouse.y = e.clientY; mouse.seen = true; }, { passive: true });
  const reduced = Sena.reduced;

  class Character {
    constructor(canvas, color) {
      this.canvas = canvas; this.ctx = canvas.getContext("2d"); this.color = color;
      this.face = new Face(); this.emotion = "calm"; this.thought = "pause";
    }
    frame(t) {
      const cv = this.canvas, rect = cv.getBoundingClientRect(), dpr = Math.min(2, devicePixelRatio || 1);
      if (cv.offsetWidth === 0 || rect.bottom < 0 || rect.top > innerHeight) return;
      const w = Math.round(cv.offsetWidth * dpr);   // the layout size: a turned or squashed canvas keeps its resolution; the page must give it a CSS size
      if (cv.width !== w) { cv.width = w; cv.height = w; }
      const toMouse = mouse.seen ? { x: mouse.x - (rect.left + rect.width / 2), y: mouse.y - (rect.top + rect.height / 2) } : null;
      this.face.step(reduced ? Math.floor(t) : t, this.emotion, toMouse);
      const ctx = this.ctx, s = w / SIDE;
      ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.clearRect(0, 0, w, w); ctx.setTransform(s, 0, 0, s, 0, 0);
      ctx.fillStyle = this.color; ctx.fill(BODY);
      this.face.drawEyes(ctx, t);
      ctx.fillStyle = this.color; ctx.beginPath(); ctx.arc(72, 72, BUBBLE_R, 0, Math.PI * 2); ctx.fill();
      if (this.thought) {
        ctx.save(); ctx.translate(72, 72); ctx.scale(1.5, 1.5); ctx.translate(-37, -37);
        drawThought(this.thought, ctx, reduced ? 0 : t, this.color); ctx.restore();
      }
    }
  }

  const all = [];
  function loop(ms) { const t = ms / 1000; all.forEach((c) => c.frame(t)); requestAnimationFrame(loop); }
  requestAnimationFrame(loop);

  // The canvas must have a CSS size: its resolution follows the layout size, and without one it would feed on itself on Retina
  Sena.Character = { create(canvas, color) { const c = new Character(canvas, color); all.push(c); return c; } };
})();
