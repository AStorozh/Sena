package com.tema.ia.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Parsing server responses: shapes the Sena server really sends (see tests/test_android.py there) plus empty, extra, and malformed-looking input. */
class ParseTest {
    private fun j(text: String) = parseJson(text)

    @Test fun `overview from a real server shape`() {
        val o = parseOverview(j("""{"now":1.5,"core":{"pid":null,"alive":true,"is_core":false},"accounts":{"keys":{"OPENAI_API_KEY":true,"GEMINI_API_KEY":false}},
            "today":{"tasks":3,"errors":2,"usd":0.5},"budget":{"limit":5,"spent":0.25},"question":{"id":"q7","question":"Какой код из СМС?","has_image":true,"time":1.0,"count":2},
            "pending_sends":[{"id":"a1","name":"правка сайта","text":"","time":1.0}],"active":[{"id":"6170939392","text":"Работаю","start":10.0,"steps":[{"who":"ag_web","text":"Открываю","time":11.0}],"service":false,"cost":0.01,"tid":"ab12cd34"}],
            "unknown_field":{"x":1}}"""))
        assertTrue(o.coreAlive)
        assertEquals(Question("q7", "Какой код из СМС?", hasImage = true, count = 2), o.question)
        assertEquals(1, o.pendingSends.size)
        assertEquals("правка сайта", o.pendingSends[0].name)
        assertEquals(1, o.active.size)
        assertTrue(o.active[0].running)
        assertEquals("a running task opens by its entry in the logs", "ab12cd34", o.active[0].id)
        assertEquals("and stops by its thread", "6170939392", o.active[0].thread)
        assertEquals("Открываю", o.active[0].steps[0].text)
        assertEquals(5.0, o.budgetLimit, 0.0)
        assertEquals(0.25, o.spentToday, 0.0)
        assertEquals(2, o.errorsToday)
        assertEquals(true, o.keys["OPENAI_API_KEY"])
        assertEquals(false, o.keys["GEMINI_API_KEY"])
    }

    @Test fun `a secret's sites come through and typed sites split into hosts`() {
        val snap = parseSecrets(j("""{"items":[{"name":"hh","note":"","allow":["ag_web"],"sites":["hh.ru","mail.example"],"reveal":"confirm","t":1.0,"changed":0},
            {"name":"server","allow":[],"reveal":"never","t":2.0,"changed":1}],"agents":["ag_mac","ag_web"]}"""))
        assertEquals(listOf("hh.ru", "mail.example"), snap.items[0].sites)
        assertTrue(snap.items[1].sites.isEmpty())   // an older server without sites: any site
        assertEquals(listOf("hh.ru", "mail.google.com"), com.tema.ia.ui.screens.secrets.siteList(" hh.ru, mail.google.com ;"))
    }

    @Test fun `overview survives nulls and wrong types`() {
        val o = parseOverview(j("""{"core":null,"question":null,"pending_sends":"oops","active":[1,2,null],"budget":{"limit":"x"},"today":[]}"""))
        assertFalse(o.coreAlive)
        assertNull(o.question)
        assertTrue(o.pendingSends.isEmpty())
        assertTrue(o.active.isEmpty())
        assertEquals(0.0, o.budgetLimit, 0.0)
    }

    @Test fun `overview from an empty object or a list`() {
        assertFalse(parseOverview(j("{}")).coreAlive)
        assertFalse(parseOverview(j("[]")).coreAlive)
    }

    @Test fun `tasks with outcome verdicts`() {
        val t = parseTasks(j("""{"now":100.0,"active":[],"finished":[
            {"id":"1","text":"a","start":1.0,"end":5.0,"steps":[{"who":"ag_night","text":"Готово","time":4.0}],"answer":"ok","comments":[{"text":"hm","time":2.0}],"service":true,"cost":0.01,"outcome":{"verdict":"done_with_remarks","reason":"r"}},
            {"id":"2","text":"b","start":2.0,"end":null,"steps":[],"answer":null,"comments":[],"cost":0},
            {"id":"3","text":"c","start":3.0,"outcome":{"verdict":"weird"}}]}"""))
        assertEquals(3, t.finished.size)
        assertEquals(Verdict.DoneWithRemarks, t.finished[0].outcome?.verdict)
        assertTrue(t.finished[0].service)
        assertEquals("hm", t.finished[0].comments[0].text)
        assertNull(t.finished[1].outcome)
        assertNull(t.finished[1].answer)
        assertEquals(Verdict.None, t.finished[2].outcome?.verdict)
        assertFalse(t.finished[0].running)
    }

    @Test fun `steps may be plain strings (chat task)`() {
        val s = parseAsk(j("""{"steps":["Принято","Ищу"],"answer":null,"done":false}"""))
        assertEquals(listOf("Принято", "Ищу"), s.steps)
        assertFalse(s.done)
        assertNull("an older server names no task", s.tid)
        val d = parseAsk(j("""{"steps":[],"answer":"Готово","done":true,"failed":true}"""))
        assertTrue(d.done); assertTrue(d.failed); assertEquals("Готово", d.answer)
        val named = parseAsk(j("""{"steps":["Принято"],"done":false,"tid":"ab12cd34","thread":6170939392}"""))
        assertEquals("ab12cd34", named.tid)
        assertEquals("6170939392", named.thread)   // a number from the server reads as the same text
    }

    @Test fun `a task's two numbers from a list and from its own page`() {
        val listed = parseTask(j("""{"id":"6170939392","tid":"ab12cd34","text":"t","start":1.0,"steps":[]}""").obj(), running = true)
        assertEquals("ab12cd34" to "6170939392", listed.id to listed.thread)
        val page = parseTask(j("""{"id":"ab12cd34","thread":"6170939392","text":"t","start":1.0,"done":false}""").obj(), running = true)
        assertEquals("ab12cd34" to "6170939392", page.id to page.thread)
        val finished = parseTask(j("""{"id":"ab12cd34","text":"t","start":1.0,"end":2.0,"done":true}""").obj(), running = false)
        assertEquals("ab12cd34", finished.id)
        assertNull(finished.thread)
        val older = parseTask(j("""{"id":"6170939392","text":"t","start":1.0}""").obj(), running = true)   // a server before tid: the thread is all it has
        assertEquals("6170939392", older.id)
    }

    @Test fun `costs top tasks are read by their words, not their ids`() {
        val r = parseCostsReport(j("""{"summary":{"title":"7 дней","total":0.2,"calls":3,
            "by_day":[["2026-10-25",{"usd":0.2,"calls":3}]],"by_model":[["claude-opus-5-5",{"usd":0.2,"calls":3}]],
            "top_tasks":[["83c98a9a",{"usd":0.00015,"task":"Проверь билеты в Лиссабон","calls":1}],["9f00",{"usd":0.0001,"task":"","calls":1}]]}}"""))
        assertEquals(listOf("Проверь билеты в Лиссабон", "9f00"), r.topTasks.map { it.key })   // a task without words keeps its id
        assertEquals(listOf("2026-10-25"), r.byDay.map { it.key })
        assertEquals(listOf("claude-opus-5-5"), r.byModel.map { it.key })
    }

    @Test fun `history from both shapes`() {
        val rows = """[{"role":"user","content":"Привет","time":"2026-10-17T04:01:20","id":"e3"},{"role":"assistant","content":"","time":"x","id":"e4"},{"role":"assistant","content":"Я тут","time":"2026-10-17T04:01:24","id":"e5"}]"""
        assertEquals(2, parseHistory(j(rows)).size)                      // the empty message is skipped
        assertEquals(2, parseHistory(j("""{"messages":$rows,"total":3}""")).size)
        assertTrue(parseHistory(j("""{"messages":5}""")).isEmpty())
        assertTrue(parseHistory(j(rows))[0].fromUser)
    }

    @Test fun `rules combine rules jobs and alerts`() {
        val r = parseRules(j("""{"rules":[{"id":"r1","text":"Каждое утро новости","enabled":true,"note":"запланировано","jobs":[{"id":"j9","at":"21.10.2026 09:00","repeat":"daily","enabled":true,
              "fails":2,"last":{"t":5.0,"text":"Главное за ночь"}}]},
            {"id":"r2","text":"Без времени","enabled":false,"jobs":[]},
            {"id":"r3","text":"Сводка по hh.ru","enabled":true,"jobs":[{"id":"j8","at":"22.10.2026 12:00","repeat":"daily","enabled":false,"fails":10}]}],
            "jobs":[{"id":"j1","text":"Напомнить","at":"20.10.2026 17:00","repeat":"once"}],
            "alerts":[{"id":"a1","kind":"crypto","symbol":"BTC","condition":"above","value":90000.0,"once":true,"enabled":false},
                      {"id":"a2","kind":"flight","symbol":"TP-LIS","condition":"below","value":150.5,"once":false}]}"""))
        assertEquals(6, r.size)
        assertEquals(RuleKind.Rule, r[0].kind)
        assertEquals("21.10.2026 09:00" to "daily", r[0].at to r[0].repeat)   // codes: the screen says them in the app's language
        assertEquals(2, r[0].fails)
        assertEquals(LastRun(5.0, "Главное за ночь"), r[0].last)
        assertFalse(r[1].enabled)
        assertTrue("a rule whose job was switched off after failures is shown as such", r[2].paused && r[2].enabled && r[2].fails == 10)
        assertEquals(RuleKind.Job, r[3].kind)
        assertEquals("20.10.2026 17:00", r[3].at)
        assertNull("once is no repeat", r[3].repeat)
        assertEquals("BTC", r[4].text)
        assertEquals(AlertSpec(above = true, value = 90000.0, everyTime = false), r[4].alert)
        assertFalse(r[4].enabled)
        assertEquals(AlertSpec(above = false, value = 150.5, everyTime = true), r[5].alert)
    }

    @Test fun `rules without ids are dropped and empty is fine`() {
        assertTrue(parseRules(j("""{"rules":[{"text":"no id"}]}""")).isEmpty())
        assertTrue(parseRules(j("{}")).isEmpty())
    }

    @Test fun `memory sections from list and object`() {
        val list = """[{"title":"Обо мне","items":["a","b"]},{"title":"X","items":null}]"""
        assertEquals(2, parseSections(j(list)).size)
        assertEquals(listOf("a", "b"), parseSections(j(list))[0].items)
        assertTrue(parseSections(j(list))[1].items.isEmpty())
        assertEquals(1, parseSections(j("""{"sections":[{"title":"Т","items":["x"]}]}""")).size)
    }

    @Test fun `memory lines show Sena's stamp apart from the words`() {
        assertEquals(FactLine("24.10.2026 14:03", "живёт в Порту"), factLine("24.10.2026 14:03: живёт в Порту"))
        assertEquals(FactLine(null, "Кофе без сахара"), factLine("Кофе без сахара"))
        assertEquals(FactLine(null, "Встреча: 12:00"), factLine("Встреча: 12:00"))   // a colon in the words isn't a stamp
    }

    @Test fun `a person's facts are their texts, and only those still true`() {
        // desk/api/people as the server sends it now (texts), and as it sent them before (whole records with valid_to)
        val now = parsePeople(j("""[{"id":"p1","name":"Ольга","relationship":"сестра","aliases":["Оля"],"facts":["живёт в Порту"],"voices":2}]""")).single()
        assertEquals(listOf("живёт в Порту"), now.facts)
        val before = parsePeople(j("""[{"id":"p1","name":"Ольга","aliases":[],"facts":[
            {"text":"живёт в Порту","t":1.0,"tz":"+01:00","source":"chat","valid_to":null},
            {"text":"работает в банке","t":1.0,"source":"chat","valid_to":5.0},
            {"text":"любит кофе","source":"vault"}],"voices":0}]""")).single()
        assertEquals(listOf("живёт в Порту", "любит кофе"), before.facts)
        val nameless = parsePeople(j("""[{"id":"p2","name":null,"aliases":[],"facts":[]}]""")).single()   // known only by voice
        assertEquals("", nameless.name)
    }

    @Test fun `people notes skills search`() {
        assertEquals("Ольга", parsePeople(j("""[{"id":"p","name":"Ольга","relationship":"сестра","aliases":["Оля"],"facts":["f"],"voices":2}]""")).single().name)
        val n = parseNotes(j("""{"folders":["a"],"notes":[{"path":"Идеи/Сайт.md","title":"Сайт","folder":"Идеи","updated":1.0,"preview":"p"}]}""")).single()
        assertEquals("Идеи/Сайт.md", n.path)
        val s = parseSkills(j("""[{"name":"mac-volume","when":"Громкость","has_code":true,"updated":2.0,"size":10}]""")).single()
        assertTrue(s.hasCode)
        assertEquals("память", parseSearch(j("""[{"kind":"память","ref":"x","text":"t","time":1.0,"score":0.5}]""")).single().kind)
        assertTrue(parseNotes(j("[]")).isEmpty())
    }

    @Test fun `integrations map`() {
        val m = parseIntegrations(j("""{"browser":{"flag":"use_browser","enabled":true,"uses_today":3,"last_used":5.0,"errors_today":1,"last_error":"сбой","usd_today":0.1,"restart":false},
            "telegram_account":{"flag":"use_telegram_account","enabled":false,"uses_today":0,"last_used":null,"errors_today":0,"last_error":null,"usd_today":0,"restart":true},
            "openai":{"flag":null,"enabled":null,"uses_today":14,"last_used":null,"errors_today":0,"last_error":null,"usd_today":0}}"""))
        assertEquals(3, m.size)
        assertEquals(true, m.getValue("browser").canToggle)
        assertEquals(true, m.getValue("browser").enabled)
        assertEquals(1, m.getValue("browser").errorsToday)
        assertNull(m.getValue("openai").enabled)
        assertFalse(m.getValue("openai").canToggle)
        assertTrue(m.getValue("telegram_account").restart)
        assertFalse(m.getValue("browser").restart)
    }

    @Test fun `config and settings change`() {
        val c = parseConfig(j("""{"settings":{"verbosity":"medium","effort":"low","budget_usd_day":2.5,"heartbeat":false,"tidy":true,"weekly":true,"voice_reply":false},
            "options":{"verbosity":{"low":"кратко","medium":"подробно"},"effort":{"low":"быстро"}},"ranges":{"budget_usd_day":[0,1000]},
            "keys":{"SENA_CLAUDE_BRAIN_KEY":true,"OPENAI_API_KEY":false},"process":{"git":{"commit":"abc1234"}},"core":{"alive":true}}"""))
        assertEquals("medium", c.settings.verbosity)
        assertEquals("the daily budget keeps its cents", 2.5, c.settings.budgetUsdDay, 0.0)
        assertFalse(c.settings.heartbeat)
        assertEquals(1000, c.budgetMax)
        assertEquals("abc1234", c.commit)
        assertEquals("подробно", c.verbosityOptions["medium"])
        assertEquals(mapOf("SENA_CLAUDE_BRAIN_KEY" to true, "OPENAI_API_KEY" to false), c.keys)
        val ch = parseSettingsChange(j("""{"settings":{"budget_usd_day":1000},"changed":{},"rejected":{"budget_usd_day":"слишком много"}}"""))
        assertEquals(1, ch.rejected.size)
        assertTrue(ch.rejected[0].contains("budget_usd_day"))
        assertTrue(parseSettingsChange(j("""{"settings":{},"rejected":[]}""")).rejected.isEmpty())
    }

    @Test fun `bad json raises an api exception`() {
        try {
            parseJson("<html>oops")
            org.junit.Assert.fail("must throw")
        } catch (e: ApiException) {
            assertEquals(ApiError.BadAnswer, e.kind)
        }
    }

    @Test fun `task detail keeps outcome reason`() {
        val t = parseTask(j("""{"id":"x","text":"t","start":1.0,"done":true,"outcome":{"verdict":"failed","reason":"не открылся сайт"}}""").obj(), running = false)
        assertNotNull(t.outcome)
        assertEquals("не открылся сайт", t.outcome?.reason)
    }

    @Test fun `home report splits switchable roles from fixed jobs`() {
        val h = parseHome(j("""{"roles":[
            {"role":"ears","title":"Разбор подслушанного","place":"home","default":"home","model":"Qwen3.5-4B","why":"чужая речь","switchable":true,"learning":false,"examples":null},
            {"role":"compact","title":"Сводка","place":"cloud","default":"cloud","model":"gpt-5.4-mini","why":"догонит","switchable":true,"learning":true,"examples":12},
            {"role":null,"title":"Мозг","place":"cloud","default":"cloud","model":"claude-opus-5-5","why":"ум","switchable":false,"learning":false,"examples":null}],
            "status":{"installed":{"embed":true,"chat":false},"server":false,"models":{}},"learning":{"min_examples":40,"graduate":0.9},
            "today":{"home":17,"home_by_service":{"embeddings":15,"read":2},"home_seconds":3.1,"out":4,"usd":0.12}}"""))
        assertEquals(listOf("ears", "compact"), h.switchable.map { it.role })
        assertEquals(listOf("Мозг"), h.fixed.map { it.title })
        assertTrue(h.switchable[0].atHome)
        assertEquals(12, h.switchable[1].examples)
        assertNull(h.fixed[0].role)
        assertEquals(17, h.homeToday)
        assertEquals(15, h.homeByService["embeddings"])
        assertEquals(4, h.outToday)
        assertEquals(40, h.minExamples)
    }

    // ---------- panels the server describes ----------

    @Test fun `a panel from the server's shape keeps every block and skips unknown ones`() {
        val p = parsePanel(j("""{"id":"goals","title":"Цели","subtitle":"то, к чему идёшь","blocks":[
            {"type":"stats","items":[{"label":"в работе","value":"2"},{"label":"достигнуто","value":"1","tone":"good"}]},
            {"type":"items","empty":"нет","items":[{"id":"g1","title":"Английский","subtitle":"ход 50%","progress":50,
              "actions":[{"id":"goal_note","label":"Как идёт","args":{"id":"g1"},"input":{"label":"Как идёт?","kind":"multiline"}},
                         {"id":"goal_close","label":"Оставить","args":{"id":"g1","status":"dropped"},"confirm":"Оставить?","danger":true}]}]},
            {"type":"form","id":"goal_add","title":"Новая цель","submit":{"id":"goal_add","label":"Добавить","args":{}},
              "fields":[{"name":"title","label":"Цель","kind":"text"},{"name":"agent","label":"Кому","kind":"choice","value":"ag_web","options":[{"value":"ag_web","label":"Браузер"}]}]},
            {"type":"switch","label":"Раз в неделю","value":true,"action":{"id":"exam_weekly","label":"Раз в неделю","args":{}}},
            {"type":"hologram","x":1},
            {"type":"buttons","items":[{"id":"exam_run","label":"Запустить","args":{}}]}]}"""))
        assertEquals(5, p.blocks.size)   // the unknown "hologram" is skipped, the rest stay usable
        val items = p.blocks[1] as PanelBlock.Items
        assertEquals(50, items.items[0].progress)
        val close = items.items[0].actions[1]
        assertTrue(close.danger)
        assertEquals("dropped", close.args.str("status"))
        assertEquals("multiline", items.items[0].actions[0].input?.kind)
        val form = p.blocks[2] as PanelBlock.Form
        assertEquals(listOf("ag_web" to "Браузер"), form.fields[1].options)
        assertTrue((p.blocks[3] as PanelBlock.Toggle).value)
        assertEquals("good", (p.blocks[0] as PanelBlock.Stats).items[1].tone)
    }

    @Test fun `panels list and an action's answer`() {
        assertEquals(listOf(PanelInfo("confirm", "Ждут «да»", 2)), parsePanels(j("""{"items":[{"id":"confirm","title":"Ждут «да»","badge":2}]}""")))
        val r = parsePanelResult(j("""{"result":"Отмечено","panel":{"id":"goals","title":"Цели","blocks":[]}}"""))
        assertEquals("Отмечено", r.message)
        assertEquals("goals", r.panel.id)
        assertTrue(parsePanel(j("""{"blocks":"oops"}""")).blocks.isEmpty())
    }

    @Test fun `a panel input may take an empty answer when the server says so`() {
        val panel = parsePanel(j("""{"id":"works","blocks":[{"type":"items","items":[{"id":"w1","title":"Сайт","actions":[
            {"id":"work_resume","label":"Продолжить","args":{"id":"w1"},"input":{"label":"Ответ (можно пусто)","kind":"multiline","optional":true}},
            {"id":"work_note","label":"Указание","args":{"id":"w1"},"input":{"label":"Что учесть","kind":"multiline"}}]}],"empty":""}]}"""))
        val (resume, note) = (panel.blocks.single() as PanelBlock.Items).items.single().actions.map { it.input!! }
        assertTrue(resume.optional)
        assertTrue("an empty answer resumes the work", resume.accepts(""))
        assertFalse(note.optional)
        assertFalse(note.accepts("  "))
        assertTrue(note.accepts("учти бюджет"))
    }

    @Test fun `integrations carry whether they are installed and needed`() {
        val m = parseIntegrations(j("""{"claude":{"flag":"use_claude","enabled":true,"ready":false},"openai":{"needed":false},"browser":{"flag":"use_browser"}}"""))
        assertFalse(m.getValue("claude").ready)
        assertEquals(false, m.getValue("openai").needed)
        assertTrue("an older server says nothing: installed", m.getValue("browser").ready)
        assertNull(m.getValue("browser").needed)
    }

    @Test fun `a search hit carries where it lives`() {
        val hits = parseSearch(j("""[{"kind":"заметка","ref":"Люди/Оля.md#0","text":"любит чай","time":1.0,"path":"Люди/Оля.md","title":"Оля"},
            {"kind":"задача","ref":"ab12cd34","text":"«Погода»","time":2.0}]"""))
        assertEquals(SearchHit("заметка", "любит чай", 1.0, "Люди/Оля.md#0", "Люди/Оля.md", "Оля"), hits[0])
        assertEquals("ab12cd34", hits[1].ref)
        assertNull(hits[1].path)
    }

    @Test fun `a note Sena keeps is split into her block and his part`() {
        val text = "---\nsena: auto\n---\n<!-- sena:begin -->\nЭтот блок обновляет Сена.\nИмя: Оля\n<!-- sena:end -->\n\nмоя строка\n"
        val parts = noteParts(text)
        assertEquals("моя строка", parts.own)
        assertTrue(parts.auto!!.endsWith(NOTE_END))
        assertEquals(NoteParts(null, "просто заметка"), noteParts("просто заметка"))
        assertNull("a broken block (no end) is not split: the server keeps the file as it is", noteParts("$NOTE_BEGIN\nтекст").auto)
        assertEquals("Этот блок обновляет Сена.\nИмя: Оля", com.tema.ia.ui.screens.memory.autoText(parts.auto!!))
    }

    @Test fun `his own running work leaves Sena's service chores out`() {
        val o = parseOverview(j("""{"active":[{"id":"1","tid":"a","text":"Найди билеты","start":1.0,"service":false},
            {"id":"2","tid":"b","text":"Разбор задачи: найди билеты","start":2.0,"service":true}]}"""))
        assertEquals(2, o.active.size)
        assertEquals(listOf("Найди билеты"), o.ownWork.map { it.text })
    }

    @Test fun `one task's budget comes with the settings and its range`() {
        val c = parseConfig(j("""{"settings":{"task_budget_usd":7.5},"ranges":{"budget_usd_day":[0,1000],"task_budget_usd":[0,100]}}"""))
        assertEquals(7.5, c.settings.taskBudgetUsd, 0.0)
        assertEquals(100, c.taskBudgetMax)
        assertEquals(5.0, parseConfig(j("{}")).settings.taskBudgetUsd, 0.0)   // the server's default when it says nothing
    }
}
