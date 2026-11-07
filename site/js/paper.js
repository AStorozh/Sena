// The dot paper under the whole site: the same grid as the first screen, fixed to the page (it scrolls with it) and bending
// away from the cursor. Sections with their own playground (a canvas.field, see world.js) draw their dots themselves, so the
// paper leaves their area blank; both use one grid (Sena.GRID, anchored at the page's top left), so the dots line up.
(function () {
  const cv = document.createElement("canvas");
  cv.className = "paper";
  cv.setAttribute("aria-hidden", "true");
  document.body.prepend(cv);
  const ctx = cv.getContext("2d");
  const pointer = { x: -999, y: -999 };
  let dirty = true;

  Sena.GRID = () => (innerWidth < 600 ? 24 : 30);

  function draw() {
    dirty = false;
    const dpr = Math.min(2, devicePixelRatio || 1), W = innerWidth, H = innerHeight;
    if (cv.width !== Math.round(W * dpr) || cv.height !== Math.round(H * dpr)) { cv.width = W * dpr; cv.height = H * dpr; }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    const gap = Sena.GRID(), own = [...document.querySelectorAll("canvas.field")].map((f) => f.getBoundingClientRect()).filter((r) => r.bottom > 0 && r.top < H);
    const y0 = gap / 2 - (scrollY % gap), x0 = gap / 2 - (scrollX % gap);
    ctx.fillStyle = "#d6ccb9";
    for (let y = y0; y < H; y += gap) {
      const skip = own.filter((r) => y >= r.top && y < r.bottom);
      for (let x = x0; x < W; x += gap) {
        if (skip.some((r) => x >= r.left && x < r.right)) continue;
        let dx = x, dy = y, size = 1.3;
        const d = Math.hypot(x - pointer.x, y - pointer.y);
        if (d < 170 && d > 0) { const k = Math.pow(1 - d / 170, 2); dx += (x - pointer.x) / d * k * 22; dy += (y - pointer.y) / d * k * 22; size += k * 1.6; }
        ctx.beginPath(); ctx.arc(dx, dy, size, 0, 6.283); ctx.fill();
      }
    }
  }
  const soon = () => { if (!dirty) { dirty = true; requestAnimationFrame(draw); } };
  addEventListener("pointermove", (e) => { pointer.x = e.clientX; pointer.y = e.clientY; soon(); }, { passive: true });
  addEventListener("scroll", soon, { passive: true });
  addEventListener("resize", soon);
  requestAnimationFrame(draw);
})();
