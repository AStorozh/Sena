// The first screen: the page builds itself, then the agents drop onto the name's letters and act out real requests,
// one scene after another. The request and the answer show in the text.
(function () {
  // Each scene: a request, steps [seconds after sending, {agent: [emotion, thought, what it does]}], the answer
  const SCENES = [
    {
      ask: "Найди билет в Бангкок на март подешевле и напомни за день",
      steps: [
        [0, { ag_sena: ["surprise", "dots", "читает просьбу"] }],
        [1.2, { ag_sena: ["calm", "send", "раздаёт работу"], ag_web: ["surprise", "search", "ищет рейсы"] }],
        [2.8, { ag_web: ["tense", "count", "сравнивает цены"], ag_sena: ["calm", "bell", "ставит напоминание"] }],
        [4.8, { ag_web: ["happy", "check", "нашёл"], ag_sena: ["happy", "check", "отвечает"] }],
        [6.4, { ag_judge: ["calm", "judge", "разбирает задачу, пишет уроки"] }],
      ],
      reply: "Прямой рейс 12 марта за 214 $. Напомню 11 марта в 10:00.",
    },
    {
      ask: "Почини падающие тесты в Hireflow",
      steps: [
        [0, { ag_sena: ["surprise", "dots", "читает просьбу"] }],
        [1.0, { ag_sena: ["calm", "send", "передаёт коду"], ag_code: ["surprise", "code", "ищет причину"] }],
        [2.6, { ag_code: ["tense", "code", "правит код"] }],
        [4.6, { ag_code: ["calm", "play", "гоняет тесты"] }],
        [6.0, { ag_code: ["wow", "check", "тесты зелёные"], ag_sena: ["happy", "question", "ждёт твоего слова"] }],
        [7.2, { ag_judge: ["calm", "judge", "разбирает задачу, пишет уроки"] }],
      ],
      reply: "Исправила 2 теста, все зелёные. Коммит — по твоему слову.",
    },
    {
      ask: "Что я обещал Диме на прошлой неделе?",
      steps: [
        [0, { ag_sena: ["surprise", "dots", "читает просьбу"] }],
        [1.0, { ag_sena: ["calm", "memory", "ищет в памяти"] }],
        [3.0, { ag_sena: ["tense", "memory", "сверяет"] }],
        [4.6, { ag_sena: ["love", "check", "отвечает"] }],
      ],
      reply: "Скинуть ему макет к пятнице. Ты сказал это 28 октября на созвоне.",
    },
    {
      ask: "Поставь музыку на паузу и открой проект Luma",
      steps: [
        [0, { ag_sena: ["surprise", "dots", "читает просьбу"] }],
        [1.0, { ag_sena: ["calm", "send", "передаёт Mac"], ag_mac: ["surprise", "media", "ставит паузу"] }],
        [2.6, { ag_mac: ["calm", "tap", "открывает проект"] }],
        [4.2, { ag_mac: ["happy", "check", "готово"], ag_sena: ["happy", "check", "отвечает"] }],
      ],
      reply: "Музыка на паузе, проект Luma открыт.",
    },
    {
      ask: "Включи свет на кухне",
      steps: [
        [0, { ag_sena: ["surprise", "dots", "читает просьбу"] }],
        [1.0, { ag_sena: ["calm", "send", "передаёт Mac"], ag_mac: ["surprise", "signal", "ищет лампу"] }],
        [2.8, { ag_mac: ["tense", "signal", "не видит лампу"] }],
        [4.2, { ag_mac: ["sad", "cross", "не вышло"], ag_sena: ["calm", "exclaim", "говорит честно"] }],
      ],
      reply: "Не нашла умную лампу в сети. Подключить её? Это пара минут.",
    },
  ];

  const hero = document.getElementById("hero");
  const askText = document.getElementById("askText"), replyText = document.getElementById("replyText");
  const hint = document.getElementById("hint");

  const world = Sena.World({
    root: hero, field: document.getElementById("field"), stage: document.getElementById("stage"),
    solids: ".hero h1, .h-kicker, .h-side .lead, .live-line, .btn", onGrab: () => hint.classList.add("gone"),
  });

  // They fall from above onto different heights: two onto the headline's first line, one past its end onto the longer second
  // line, one down the gap between the text and the name to the floor, one onto the leaf, two onto the name's letters
  // beside it
  function spots() {
    const hr = hero.getBoundingClientRect(), r = world.bodies[0].r, [x0, x1] = world.letterSpan();
    const h1 = hero.querySelector("h1"), range = document.createRange(); range.selectNodeContents(h1);
    const lines = [...range.getClientRects()].filter((q) => q.width > 2);
    const first = lines.filter((q) => Math.abs(q.top - lines[0].top) < 4), firstEnd = Math.max(...first.map((q) => q.right)) - hr.left;
    const longest = Math.max(...lines.map((q) => q.right)) - hr.left, left = h1.getBoundingClientRect().left - hr.left;
    const side = hero.querySelector(".h-side").getBoundingClientRect(), floor = (side.right - hr.left + x0) / 2;
    const logo = hero.querySelector(".h-logo").getBoundingClientRect(), onLogo = logo.left - hr.left + logo.width * 0.21;   // the leaf's big left petal: in the middle is the gap between petals
    const xs = wide()
      ? [left + (firstEnd - left) * 0.15, x0 + (x1 - x0) * 0.08, left + (firstEnd - left) * 0.62, floor, onLogo, Math.min(longest - r * 0.9, firstEnd + r * 1.3), x0 + (x1 - x0) * 0.94]
      : [0.15, 0.55, 0.9].map((k) => left + (firstEnd - left) * k).concat([0.12, 0.4, 0.68, 0.94].map((k) => x0 + (x1 - x0) * k));
    const jitter = (v) => v + (Math.random() - 0.5) * 8;
    if (wide()) return xs.map(jitter);
    // Narrow screen: the headline spans the whole width, so agents falling from the top toward the name would land on the
    // headline in a heap over the text. The four for the name appear just above its letters instead and drop onto them.
    // If the name is too short for four (a small phone), one more stands on the headline: four up there, three on the letters.
    const above = hero.querySelector(".giant").getBoundingClientRect().top - hr.top - r * 1.2;
    const roomy = x1 - x0 > r * 2.3 * 4;
    const tops = roomy ? [0.15, 0.55, 0.9] : [0.1, 0.4, 0.7, 0.97];
    const onName = roomy ? [0.12, 0.4, 0.68, 0.94] : [0.1, 0.5, 0.92];
    return [...tops.map((k) => jitter(left + (firstEnd - left) * k)), ...onName.map((k) => [jitter(x0 + (x1 - x0) * k), above])];
  }
  const wide = () => world.width > 1000;

  async function type(text) {
    askText.textContent = "";
    for (const ch of Sena.t(text)) { askText.textContent += ch; await Sena.wait(ch === " " ? 0.06 : 0.03 + Math.random() * 0.04); }
  }

  async function play(scene) {
    world.clear();
    replyText.classList.remove("on");
    await Sena.wait(0.8);
    await type(scene.ask);
    await Sena.wait(0.4);
    let clock = 0;
    for (const [at, change] of scene.steps) {
      await Sena.wait(at - clock); clock = at;
      for (const code in change) {
        const [emotion, thought, doing] = change[code], now = Sena.t(doing);
        world.assign(code, [emotion, thought, now]);
      }
    }
    await Sena.wait(0.5);
    replyText.textContent = Sena.t(scene.reply); replyText.classList.add("on");
    await Sena.wait(4.5);
  }

  (async function start() {
    await Promise.race([document.fonts.ready, Sena.wait(3)]);   // the fonts are local now, this is only a safety limit   // slow web fonts must not hold the page empty; ledges are re-read later
    world.start();
    await Sena.build.run([document.getElementById("island"), ...hero.querySelectorAll("[data-build]")]);
    world.measure();
    world.drop(spots());
    Sena.build.watch();   // the sections below build themselves as they come into view
    await Sena.wait(1.6);
    for (let i = 0; ; i = (i + 1) % SCENES.length) await play(SCENES[i]);
  })();
})();
