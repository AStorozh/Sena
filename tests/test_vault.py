"""Obsidian notes vault: safe paths, block ownership, projections of people, memory, skills and the diary."""
import time
import unittest
from unittest import mock
from datetime import date

from sena import config
from sena.memory import facts, people, recall, skills, task_log, vault, vault_sync


def note(path):
    return (config.VAULT_DIR / path).read_text(encoding="utf-8")


class Notes(unittest.TestCase):
    def test_vault_is_not_the_real_one(self):
        self.assertIn("sena-test-", str(config.VAULT_DIR))

    def test_ensure_creates_folders_and_obsidian_settings(self):
        vault.ensure()
        for folder in ("Дневник", "Люди", "Знания", "Проекты", "Входящие"):
            self.assertTrue((config.VAULT_DIR / folder).is_dir())
        self.assertTrue((config.VAULT_DIR / ".obsidian" / "daily-notes.json").exists())

    def test_paths_cannot_leave_the_vault(self):
        for bad in ("../secret.md", ".obsidian/app.json", "Знания/../../x.md", "Знания/.скрытая", ""):
            with self.assertRaises(ValueError):
                vault.write_note(bad, "x")
        # an absolute path does not escape the vault: it is read as a path inside the vault
        inside = vault._safe("/etc/passwd")
        self.assertTrue(str(inside).startswith(str(config.VAULT_DIR)))

    def test_create_append_replace(self):
        self.assertIn("Создана", vault.write_note("Знания/Налоги маркетплейсов", "# Налоги\nУСН 6%"))
        text = note("Знания/Налоги маркетплейсов.md")
        self.assertTrue(text.startswith("---\n"))
        self.assertIn("source: \"sena\"", text)
        self.assertIn("уже есть", vault.write_note("Знания/Налоги маркетплейсов", "ещё"))
        vault.write_note("Знания/Налоги маркетплейсов", "Патент для ИП", mode="append")
        self.assertIn("Патент для ИП", note("Знания/Налоги маркетплейсов.md"))
        vault.write_note("Знания/Налоги маркетплейсов", "Новый текст", mode="replace")
        self.assertNotIn("УСН", note("Знания/Налоги маркетплейсов.md"))
        self.assertIn("нет", vault.write_note("Знания/Нет такой", "x", mode="append"))

    def test_found_notes_and_read_notes_are_marked_as_foreign(self):
        vault.write_note("Проекты/KSclay", "Парсер кабинета WB. Сервер parser-ksclay, ssh.")
        found = recall.find("парсер ksclay", kinds=["заметка"])
        self.assertIn("ВНЕШНИЕ ДАННЫЕ", found)
        self.assertIn("Проекты/KSclay.md", found)
        self.assertIn("Ничего не нашла", recall.find("несуществующееслово", kinds=["заметка"]))
        self.assertIn("Парсер кабинета WB", vault.read_note("Проекты/KSclay"))

    def test_user_text_outside_block_survives_updates(self):
        vault.upsert("Люди/Проверка", {"type": "person", "updated": "2026-01-01"}, "Проверка", "Факт 1", "## Мои заметки\n\n")
        path = config.VAULT_DIR / "Люди" / "Проверка.md"
        path.write_text(path.read_text(encoding="utf-8") + "\nМоя мысль, которую Сена не должна потерять.\n", encoding="utf-8")
        vault.upsert("Люди/Проверка", {"type": "person", "updated": "2026-02-02"}, "Проверка", "Факт 2")
        text = path.read_text(encoding="utf-8")
        self.assertIn("Факт 2", text)
        self.assertNotIn("Факт 1", text)
        self.assertIn("Моя мысль, которую Сена не должна потерять.", text)
        self.assertIn('updated: "2026-02-02"', text)
        self.assertFalse(vault.upsert("Люди/Проверка", {"type": "person", "updated": "2026-02-02"}, "Проверка", "Факт 2"))

    def test_write_to_auto_note_keeps_the_block(self):
        vault.upsert("Дневник/2026-01-01", {"type": "daily"}, "2026-01-01", "Автоматический блок")
        vault.write_note("Дневник/2026-01-01", "Дописала от себя", mode="replace")
        text = note("Дневник/2026-01-01.md")
        self.assertIn("Автоматический блок", text)
        self.assertIn("Дописала от себя", text)
        self.assertLess(text.index(vault.END), text.index("Дописала от себя"))

    def test_note_of_the_user_without_block_gets_block_at_the_end(self):
        path = config.VAULT_DIR / "Знания" / "Моя заметка.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Моя заметка\nтекст Темы\n", encoding="utf-8")
        vault.upsert("Знания/Моя заметка", {"type": "x"}, "Моя", "от Сены")
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# Моя заметка\nтекст Темы"))
        self.assertIn("от Сены", text)

    def test_a_folder_name_keeps_its_dots(self):
        vault.write_note("Знания/v1.2/Заметка о версии", "текст", "create")
        self.addCleanup(lambda: (config.VAULT_DIR / "Знания" / "v1.2" / "Заметка о версии.md").unlink(missing_ok=True))
        self.assertIn("Знания/v1.2", {n["folder"] for n in vault.notes("Знания")})

    def test_size_limit(self):
        self.assertIn("Слишком длинно", vault.write_note("Знания/Огромная", "x" * (vault.MAX_NOTE + 1)))

    def test_the_whole_auto_note_sent_back_keeps_only_the_owners_part(self):
        """The phone sends the whole file back with replace: its frontmatter and Sena's block were written again after the real
        block, and the sync took their lines («sena: "auto"», the hint) for the owner's facts."""
        vault.upsert("Люди/Отправка", {"type": "person", "sena_id": "p1"}, "Отправка", f"{vault_sync.OWNER_HINT}\n\n**Что известно:**\n- сестра",
                     vault_sync.USER_PART)
        self.addCleanup((config.VAULT_DIR / "Люди" / "Отправка.md").unlink, missing_ok=True)   # other tests count the cards
        whole = note("Люди/Отправка.md") + "- любит горы\n"   # what the app sends: the file as the owner left it
        vault.write_note("Люди/Отправка", whole, mode="replace")
        text = note("Люди/Отправка.md")
        self.assertEqual((text.count(vault.BEGIN), text.count('sena: "auto"'), text.count("sena_id")), (1, 1, 1))
        self.assertEqual(vault_sync.owner_lines(config.VAULT_DIR / "Люди" / "Отправка.md"), ["любит горы"])

    def test_a_marker_line_deleted_in_the_phone_editor_keeps_his_lines_and_never_makes_hers_his(self):
        """The phone edits the whole file: with the end marker deleted his lines were thrown away with the block; with both
        markers deleted Sena's block and its hint came back through the sync as his own facts."""
        path = config.VAULT_DIR / "Люди" / "Маркер.md"
        self.addCleanup(path.unlink, missing_ok=True)
        block = f"{vault_sync.OWNER_HINT}\n\n**Что известно:**\n- живёт в Порту"
        for dropped in ([vault.END], [vault.BEGIN, vault.END], [vault.BEGIN]):
            path.unlink(missing_ok=True)
            vault.upsert("Люди/Маркер", {"type": "person", "sena_id": "m1"}, "Маркер", block, vault_sync.USER_PART + "- моя строка\n")
            edited = "\n".join(line for line in note("Люди/Маркер.md").splitlines() if line not in dropped)
            edited = edited.replace("- моя строка", "- моя строка, исправленная")
            self.assertIn("Заменена часть", vault.write_note("Люди/Маркер", edited, mode="replace"))
            text = note("Люди/Маркер.md")
            self.assertEqual((text.count(vault.BEGIN), text.count(vault.END), text.count("живёт в Порту")), (1, 1, 1), dropped)
            self.assertEqual(vault_sync.owner_lines(path), ["моя строка, исправленная"], dropped)

    def test_a_cards_preview_shows_what_it_says_not_the_service_line(self):
        vault.upsert("Люди/Превью", {"type": "person", "sena_id": "pv"}, "Превью", f"{vault_sync.OWNER_HINT}\n\n**Имя:** Проба Превью", vault_sync.USER_PART)
        self.addCleanup((config.VAULT_DIR / "Люди" / "Превью.md").unlink, missing_ok=True)
        shown = next(n for n in vault.notes("Люди") if n["path"] == "Люди/Превью.md")
        self.assertTrue(shown["preview"].startswith("Имя: Проба Превью"), shown["preview"])

    def test_the_notes_search_shows_every_find_up_to_fifty(self):
        """The screen showed ten finds, silently, and counted them as the total."""
        for n in range(15):
            vault.write_note(f"Идеи/Квазар {n}", f"# Квазар {n}\n\nИдея про квазарный проектор номер {n}.", "create")
            self.addCleanup((config.VAULT_DIR / "Идеи" / f"Квазар {n}.md").unlink, missing_ok=True)
        self.assertEqual(len(vault.find("квазарный")), 15)
        self.assertEqual(len(vault.find("квазарный", limit="много")), 15)   # garbage for a number: the default, not a crash

    def test_a_note_written_twice_in_one_second_is_read_once_more_not_every_time(self):
        """The registry kept an unchanged first chunk's old time within a second, and the note's time never matched it again:
        every sync (each app search, each note write) read, chunked and linked it anew."""
        import os
        from sena.memory import index
        path = config.VAULT_DIR / "Знания" / "Дважды.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        self.addCleanup(path.unlink, missing_ok=True)
        path.write_text("# Дважды\n\nПервый абзац.\n\nВторой абзац, вариант один.", encoding="utf-8")
        os.utime(path, (1_800_000_000.0, 1_800_000_000.0))
        vault.sync_index()
        path.write_text("# Дважды\n\nПервый абзац.\n\nВторой абзац, вариант два.", encoding="utf-8")   # the same first chunk
        os.utime(path, (1_800_000_000.5, 1_800_000_000.5))
        vault.sync_index()
        self.assertEqual(index.refs(vault.KIND)["Знания/Дважды.md#0"], 1_800_000_000.5)   # the marker matches: the next sync skips it
        self.assertIn("вариант два", index.search("вариант два", kinds=[vault.KIND], text_only=True, scope="all", match="all")[0]["text"])

    def test_a_note_with_a_hash_in_its_name_stays_in_the_registry(self):
        from sena.memory import index
        path = config.VAULT_DIR / "Знания" / "C# заметки.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# C#\n\nКортежи и записи в C# 12.", encoding="utf-8")
        self.addCleanup(path.unlink, missing_ok=True)
        vault.sync_index()
        self.assertEqual(vault.sync_index()["removed"], 0)   # the chunk's number is after the last "#", not the first
        self.assertIn("Знания/C# заметки.md#0", index.refs(vault.KIND))
        self.assertIn("[заметка Знания/C# заметки.md", recall.find("кортежи записи", kinds=["заметка"]))

    def test_a_projects_own_notes_are_its_parts_not_projects(self):
        """Notes nested under Проекты/<project>/ became projects of their own, and their names («Требования») were then
        "mentioned" by every message about requirements."""
        from sena.memory import index
        vault.write_note("Проекты/Гончар", "# Гончар\n\nСайт гончарной мастерской.", "create")
        vault.write_note("Проекты/Гончар/Требования", "# Требования\n\nКаталог и корзина.", "create")
        vault.write_note("Проекты/Лавка/План", "# План\n\nОткрыть лавку весной.", "create")   # a folder without a note of its own
        for rel in ("Проекты/Гончар.md", "Проекты/Гончар/Требования.md", "Проекты/Лавка/План.md"):
            self.addCleanup(lambda rel=rel: (config.VAULT_DIR / rel).unlink(missing_ok=True))
        vault.sync_index()
        projects = index.refs("проект")
        self.assertIn("Проекты/Гончар.md", projects)
        self.assertIn("Проекты/Лавка.md", projects)
        self.assertFalse({"Проекты/Гончар/Требования.md", "Проекты/Лавка/План.md"} & set(projects))
        self.assertEqual(index.mentions("пришли требования к отчёту для бухгалтерии"), [])
        self.assertEqual(index.lookup("проект", "Лавка"), "Проекты/Лавка.md")
        parts = [l["ref"] for l in index.links_of("проект", "Проекты/Гончар.md", rel="part_of")]
        self.assertEqual(parts, ["Проекты/Гончар/Требования.md#0"])
        vault.delete_note("Проекты/Гончар.md")
        self.assertIn("Проекты/Гончар.md", index.refs("проект"))   # its folder still has notes: the project stays
        vault.delete_note("Проекты/Гончар/Требования.md")
        self.assertNotIn("Проекты/Гончар.md", index.refs("проект"))


class Projections(unittest.TestCase):
    def setUp(self):
        people._people = []

    def test_people_become_cards_and_follow_renames(self):
        anna = people.new_person(name="Анна", alias="Аня", voices=[[1.0, 0.0]])
        people.update(anna["id"], facts=["работает в WB"], relationship="коллега", opinion="надёжная")
        people.new_person()   # a voice without a name or facts: no card is created
        vault.ensure()
        before = {p.name for p in (config.VAULT_DIR / "Люди").glob("*.md")}   # cards other tests left (one may not move to the archive: a namesake is there)
        vault_sync.sync_people()
        card = note("Люди/Анна.md")
        self.assertIn("работает в WB", card)
        self.assertIn("коллега", card)
        self.assertIn("надёжная", card)
        self.assertIn(f'sena_id: "{anna["id"]}"', card)
        self.assertNotIn("1.0", card)   # voice fingerprints do not end up in the notes
        self.assertEqual({p.name for p in (config.VAULT_DIR / "Люди").glob("*.md")} - before, {"Анна.md"})
        # the person was renamed, but Tema had already written his own notes into the card
        path = config.VAULT_DIR / "Люди" / "Анна.md"
        path.write_text(path.read_text(encoding="utf-8") + "\nМоя пометка: день рождения 5 мая\n", encoding="utf-8")
        people.update(anna["id"], name="Анна Петрова")
        vault_sync.sync_people()
        self.assertFalse(path.exists())
        self.assertIn("день рождения 5 мая", note("Люди/Анна Петрова.md"))

    def test_merged_person_goes_to_archive(self):
        a = people.new_person(name="Вася")
        b = people.new_person(name="Василий")
        people.update(a["id"], facts=["из Омска"])
        people.update(b["id"], facts=["любит чай"])
        vault_sync.sync_people()
        people.merge(a["id"], b["id"])
        vault_sync.sync_people()
        self.assertTrue((config.VAULT_DIR / "Люди" / "Вася.md").exists())
        self.assertFalse((config.VAULT_DIR / "Люди" / "Василий.md").exists())
        self.assertTrue((config.VAULT_DIR / "Архив" / "Человек - Василий.md").exists())

    def test_what_the_owner_writes_under_a_card_becomes_a_fact_and_leaves_with_the_line(self):
        config.VAULT_SYNC_STATE.unlink(missing_ok=True)
        olga = people.new_person(name="Ольга Р")
        people.update(olga["id"], facts=["сестра"])
        vault_sync.sync_people()
        path = config.VAULT_DIR / "Люди" / "Ольга Р.md"
        self.assertIn(vault_sync.OWNER_HINT, note("Люди/Ольга Р.md"))
        path.write_text(path.read_text(encoding="utf-8") + "- день рождения 5 мая\nЛюбит горький шоколад\n", encoding="utf-8")
        vault_sync.sync_people()
        self.assertEqual(people.facts(olga), ["сестра", "день рождения 5 мая", "Любит горький шоколад"])
        self.assertEqual({f["source"] for f in olga["facts"] if f["text"] != "сестра"}, {"vault"})
        card = note("Люди/Ольга Р.md")
        self.assertEqual(card.count("день рождения 5 мая"), 1)   # the owner's line stays his, not repeated in Sena's block
        self.assertIn("- сестра", card)
        path.write_text(path.read_text(encoding="utf-8").replace("- день рождения 5 мая\n", ""), encoding="utf-8")
        vault_sync.sync_people()
        self.assertEqual(people.facts(olga), ["сестра", "Любит горький шоколад"])
        self.assertIn("день рождения 5 мая", people.facts(olga, all=True))   # retired, not erased
        people.update(olga["id"], facts=["сестра"])   # a fact Sena knows herself is never retired by the owner's part
        vault_sync.sync_people()
        self.assertIn("сестра", people.facts(olga))

    def test_what_the_owner_writes_under_a_memory_section_goes_into_memory(self):
        config.VAULT_SYNC_STATE.unlink(missing_ok=True)
        facts.replace("## Обо мне\n- 01.01.2026: любит чай\n\n## Планы\n")
        vault_sync.sync_memory()
        path = config.VAULT_DIR / "Память" / "Обо мне.md"
        path.write_text(path.read_text(encoding="utf-8") + "- бегаю по утрам\n", encoding="utf-8")
        vault_sync.sync_memory()
        about = next(s for s in facts.sections() if s["title"] == "Обо мне")
        self.assertTrue(any(facts.plain(i) == "бегаю по утрам" for i in about["items"]))
        self.assertEqual(note("Память/Обо мне.md").count("бегаю по утрам"), 1)   # not repeated in the block
        path.write_text(path.read_text(encoding="utf-8").replace("- бегаю по утрам\n", ""), encoding="utf-8")
        vault_sync.sync_memory()
        about = next(s for s in facts.sections() if s["title"] == "Обо мне")
        self.assertFalse(any("бегаю" in i for i in about["items"]))
        self.assertIn("любит чай", facts.read())

    def test_a_line_memory_refuses_is_told_in_the_note_and_taken_once_rewritten(self):
        """A line with a relative date was dropped without a word and never tried again, though the note says such a line goes in."""
        config.VAULT_SYNC_STATE.unlink(missing_ok=True)
        facts.replace("## Обо мне\n- 01.01.2026: любит чай\n\n## Планы\n")
        vault_sync.sync_memory()
        path = config.VAULT_DIR / "Память" / "Планы.md"
        path.write_text(path.read_text(encoding="utf-8") + "- завтра встреча с Олей\n", encoding="utf-8")
        vault_sync.sync_memory()
        self.assertNotIn("встреча с Олей", facts.read())
        self.assertIn("«завтра встреча с Олей»: Не записала: в факте относительная дата", note("Память/Планы.md"))   # in Sena's part of the note
        self.assertEqual(vault_sync.owner_lines(path), ["завтра встреча с Олей"])   # his line stays his
        path.write_text(note("Память/Планы.md").replace("- завтра встреча с Олей", "- 26.10.2026 встреча с Олей"), encoding="utf-8")
        vault_sync.sync_memory()
        self.assertIn("26.10.2026 встреча с Олей", facts.read())
        self.assertNotIn("относительная дата", note("Память/Планы.md"))

    def test_owner_edits_in_the_vault_trigger_the_hourly_sync(self):
        vault_sync.sync_if_changed()
        self.assertEqual(vault_sync.sync_if_changed(), "без изменений")
        p = people.new_person(name="Триггер")
        people.update(p["id"], facts=["проверка"])
        vault_sync.sync_if_changed()
        path = config.VAULT_DIR / "Люди" / "Триггер.md"
        self.assertEqual(vault_sync.sync_if_changed(), "без изменений")
        import os
        import time as _time
        path.write_text(path.read_text(encoding="utf-8") + "- дописал руками\n", encoding="utf-8")
        os.utime(path, (_time.time() + 3, _time.time() + 3))
        self.assertNotEqual(vault_sync.sync_if_changed(), "без изменений")
        self.assertIn("дописал руками", people.facts(p))

    def test_memory_sections_and_skills(self):
        facts.replace("## Проекты\n- 01.10.2026: KSclay — парсер WB\n\n## Планы\n")
        skills.write_skill("test-vault", "Когда проверяем хранилище", "шаг 1\nшаг 2", code='def run(page):\n    return "ok"')
        try:
            vault_sync.sync_memory()
            vault_sync.sync_skills()
            self.assertIn("KSclay — парсер WB", note("Память/Проекты.md"))
            self.assertIn("(пока пусто)", note("Память/Планы.md"))
            skill = note("Навыки/test-vault.md")
            self.assertIn("**Когда применять:** Когда проверяем хранилище", skill)
            self.assertIn("```python", skill)
            self.assertIn('return "ok"', skill)
        finally:
            skills.delete_skill("test-vault")

    def test_daily_note_lists_tasks_with_outcome(self):
        entry = task_log.save({"text": "включи музыку", "start": time.time(), "steps": [{"who": "ag_web", "text": "ищу", "time": 1}],
                               "answer": "Играет: Death Stranding"})
        task_log.set_outcome(entry["id"], "done", "играет")
        task_log.add_comment(entry["id"], "реклама была")
        vault_sync.write_day(date.today())
        day = note(f"Дневник/{date.today().isoformat()}.md")
        self.assertIn("«включи музыку»", day)
        self.assertIn("(ag_web)", day)
        self.assertIn("Играет: Death Stranding", day)
        self.assertIn("[сделано]", day)
        self.assertIn("замечание Темы: реклама была", day)

    def test_sync_all_is_stable_and_survives_one_failure(self):
        first = vault_sync.sync_all()
        self.assertIsInstance(first, str)
        before = {p: p.read_text(encoding="utf-8") for p in config.VAULT_DIR.rglob("*.md")}
        vault_sync.sync_all()
        after = {p: p.read_text(encoding="utf-8") for p in config.VAULT_DIR.rglob("*.md")}
        self.assertEqual(before, after)   # running it again changes nothing
        self.assertTrue((config.VAULT_DIR / "Сена.md").exists())
        original = vault_sync.sync_memory
        vault_sync.sync_memory = lambda: 1 / 0
        try:
            self.assertIn("память: ошибка", vault_sync.sync_all())
            self.assertTrue((config.VAULT_DIR / "Сена.md").exists())
        finally:
            vault_sync.sync_memory = original


class WithRecall(unittest.TestCase):
    def test_only_free_notes_are_recalled(self):
        from sena.memory import index
        vault.write_note("Знания/Про налоги", "Для ИП на УСН 6% есть лимит дохода")
        vault_sync.write_home()
        vault.sync_index()
        self.assertIn("Знания/Про налоги.md#0", [r["ref"] for r in recall.find_rows("лимит дохода УСН", with_events=False)["rows"]])
        self.assertTrue(index.count(vault.KIND, meta={"author": "sena"}) > 0)   # the auto-generated pages are in the index (so word search finds them)...
        found = recall.find_rows("хранилище Сены", with_events=False)["rows"]
        self.assertNotIn("Хранилище Сены", " ".join(r["text"] for r in found))   # ...but they don't come up in a general search: they're just copies


class LongNotes(unittest.TestCase):
    def test_a_long_paragraph_is_cut_by_line_sentence_and_space(self):
        """Haystack's recursive splitter: a 5000-character list was one chunk cut at the registry's limit — item 55 unsearchable."""
        from sena.memory import index, vault
        note = "\n".join(f"{i}. Купить в поездку предмет номер {i} и проверить его заранее" for i in range(1, 90))
        chunks = vault._chunks(note)
        self.assertTrue(all(len(c) <= vault.CHUNK for c in chunks))
        self.assertTrue(any("55. Купить" in c for c in chunks))
        self.assertTrue(all(len(p) <= 800 for p in index.pieces("Слово. " * 400, 800)))
        self.assertEqual(len(index.pieces("а" * 5000, 1200)), 5)   # no separator at all: a plain cut

    def test_chunks_follow_headings_carry_their_section_and_keep_their_vectors_when_moved(self):
        """Flowise's check on this Mac: one paragraph added on top re-embedded every chunk of the note, and the chunk with
        «1200 евро» didn't say it sat under «Цены»."""
        from sena.memory import index, vault
        price = "Номер у моря стоит 1200 евро в неделю. " * 16
        note = f"# Поездка в Порту\n\nПлан на неделю.\n\n## Цены\n\n{price}\n\n{price}\n\n## Дорога\n\nПоезд из Лиссабона."
        chunks = vault._chunks(note)
        self.assertTrue(any(c.startswith("[Поездка в Порту > Цены]") and "1200 евро" in c for c in chunks))   # a continuation knows its section
        self.assertTrue(any(c.startswith("## Дорога") for c in chunks))   # a heading starts its own chunk
        vault.ensure()
        vault.write_note("Знания/Порту", note, "create")
        vault.sync_index()
        index.embed_pending()
        before = index.vectors_of(vault.KIND, "Знания/Порту.md#")
        calls = []
        real = index.vectors.embed
        with mock.patch.object(index.vectors, "embed", lambda texts, **kw: calls.append(len(texts)) or real(texts, **kw)):
            vault.write_note("Знания/Порту", "Новый первый абзац о сроках.\n\n" + note, "replace")
            vault.sync_index()
            index.embed_pending()
        self.assertEqual(sum(calls), 1)   # only the new paragraph's chunk is embedded; the moved ones keep theirs
        self.assertTrue(set(before) <= set(index.vectors_of(vault.KIND, "Знания/Порту.md#")))


if __name__ == "__main__":
    unittest.main()
