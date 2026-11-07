"""The Mac app's style lives in one file, mac/Sources/Style.swift: screens never set font sizes, padding,
corner radii, control sizes or state colors directly — otherwise changing the look would mean touching every file."""
import re
import unittest
from pathlib import Path

SOURCES = Path(__file__).resolve().parent.parent / "mac" / "Sources"
EXEMPT = {"Style.swift", "Leaf.swift", "API.swift", "App.swift"}   # the style itself, icon drawing, networking and the app menu — no screen styling here
FORBIDDEN = {
    "размер шрифта": r"\.font\(\.(largeTitle|title3?|title2|headline|subheadline|body|callout|footnote|caption2?)\b",
    "свой шрифт": r"\.font\(\.system\(",
    "отступ числом": r"\.padding\((\[[^\]]*\]|\.[a-zA-Z]+)?,? ?\d",
    "скругление числом": r"cornerRadius: \d",
    "своё имя скругления (одно скругление — Style.Radius.all)": r"Style\.Radius\.(?!all\b|notchTop\b)\w+",
    "своя форма вместо общего скругления": r"\bCapsule\(",
    "системный сегментный переключатель (есть Segments)": r"\.pickerStyle\(\.segmented\)",
    "системный выпадающий список (есть Select)": r"\.pickerStyle\(\.menu\)",
    "цвет состояния": r"(Color)?\.(green|red|orange|purple|blue|gray)\b",
    "системный акцент (есть Style.Colors.accent)": r"\baccentColor\b",
    "системный серый текст (есть Style.Colors.secondaryText/tertiaryText)": r"foregroundStyle\(\.(secondary|tertiary)\)|\bColor\.(secondary|tertiary)\b|(?<!kind)[?:]\s*\.secondary\s*[)}]",
    "размер элемента управления (задаёт корень окна)": r"\.controlSize\((?!Style\.Size\.control\))",
    "системный стиль кнопки (есть .primary/.secondary/.quiet)": r"\.buttonStyle\(\.(bordered|borderedProminent|borderless|link|automatic)\)",
    # the language of sections is blocks (Block, Stat, Columns, .row) — no lines, forms or system lists
    "линия-разделитель": r"\bDivider\(\)",
    "системная группа или форма": r"\b(GroupBox|Form)\s*[({]",
    "системный разделитель колонок": r"\b(HSplitView|VSplitView|NavigationSplitView)\b",
    "системный список": r"\bList\s*[({]",
}


# scroll bars are nowhere in the app: `.scrollIndicators(.never)` sits at the root of the window, sheet and popover list, and screens never touch it
# (a local `.hidden`/`.automatic` would override the shared setting and bring the bar back under the system's "always show")
SCROLL_BARS_ALLOWED_IN = {"ContentView.swift", "Components.swift"}


class MacStyle(unittest.TestCase):
    def test_scroll_bars_are_hidden_in_one_place(self):
        for f in SOURCES.rglob("*.swift"):
            text = re.sub(r"//[^\n]*", "", f.read_text(encoding="utf-8"))   # comments don't count
            for hit in re.finditer(r"\.scrollIndicators\(([^)]*)\)", text):
                self.assertIn(f.name, SCROLL_BARS_ALLOWED_IN, f"{f.name}: полоски прокрутки отключаются в корне окна и листов, не в экранах")
                self.assertEqual(hit.group(1), ".never", f"{f.name}: полоски выключаются только как .never")
        root = (SOURCES / "Views" / "ContentView.swift").read_text(encoding="utf-8")
        self.assertIn(".scrollIndicators(.never)", root)
        self.assertIn("scrollIndicators(.never)", (SOURCES / "Views" / "Components.swift").read_text(encoding="utf-8"))   # sheets and popover lists


    def test_style_only_in_style_file(self):
        files = [f for f in SOURCES.rglob("*.swift") if f.name not in EXEMPT]
        self.assertTrue(files, "исходники приложения не найдены")
        for f in files:
            text = f.read_text(encoding="utf-8")
            for what, pattern in FORBIDDEN.items():
                hit = re.search(pattern, text)
                self.assertIsNone(hit, f"{f.name}: {what} «{hit.group(0) if hit else ''}» задаётся только в Style.swift")

    def test_style_file_is_single_source(self):
        text = (SOURCES / "Style.swift").read_text(encoding="utf-8")
        for name in ("enum Text", "enum Icon", "enum Space", "enum Radius", "enum Size", "enum Colors", "static let design"):
            self.assertIn(name, text)


if __name__ == "__main__":
    unittest.main()
