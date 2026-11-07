// The one list of Sena's agents for the whole site: code, role, body color (from mac/Sources/Style.swift; ag_judge, ag_night
// and ag_ears from the brand palette), a darker cursor color where the body's is too pale for a cursor on paper.
// Every other script reads it from here — never copy the colors.
window.Sena = window.Sena || {};
Sena.AGENTS = [
  { code: "ag_sena", role: "координатор", color: "#E8B547", free: "свободна" },
  { code: "ag_web", role: "интернет", color: "#1F5BD8" },
  { code: "ag_mac", role: "компьютер", color: "#C3C7CD", cursor: "#9EA3AB" },
  { code: "ag_code", role: "код", color: "#7A5CE6" },
  { code: "ag_judge", role: "разбор задач", color: "#FA786B" },
  { code: "ag_night", role: "память", color: "#40C7B8", cursor: "#2FB3A5", sleeps: true },
  { code: "ag_ears", role: "слух", color: "#8FCC47", cursor: "#79B83A" },
];
Sena.wait = (seconds) => new Promise((r) => setTimeout(r, seconds * 1000));
Sena.reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
