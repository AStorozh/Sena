package com.tema.ia.ui

import androidx.lifecycle.SavedStateHandle
import com.tema.ia.MainDispatcherRule
import com.tema.ia.R
import com.tema.ia.data.ActionResult
import com.tema.ia.data.ApiError
import com.tema.ia.data.ApiException
import com.tema.ia.data.DemoApi
import com.tema.ia.data.FactSection
import com.tema.ia.data.IntegrationCatalog
import com.tema.ia.data.IntegrationState
import com.tema.ia.data.ScriptedApi
import com.tema.ia.data.TaskItem
import com.tema.ia.data.TaskStep
import com.tema.ia.data.Outcome
import com.tema.ia.data.SearchHit
import com.tema.ia.data.SearchKind
import com.tema.ia.data.TasksSnapshot
import com.tema.ia.data.Verdict
import com.tema.ia.ui.screens.memory.SearchTarget
import com.tema.ia.ui.screens.memory.factChanges
import com.tema.ia.ui.screens.memory.searchTarget
import com.tema.ia.ui.screens.tasks.VERDICT_WAIT_MS
import com.tema.ia.ui.screens.tasks.pollAfter
import com.tema.ia.ui.screens.integrations.IntegrationsViewModel
import com.tema.ia.ui.screens.integrations.Link
import com.tema.ia.ui.screens.integrations.linkOf
import com.tema.ia.ui.screens.memory.MemoryViewModel
import com.tema.ia.ui.screens.memory.SearchState
import com.tema.ia.ui.screens.tasks.TaskDetailViewModel
import com.tema.ia.ui.screens.tasks.TasksViewModel
import com.tema.ia.viewModelOf
import com.tema.ia.watch
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class MoreViewModelsTest {
    @get:Rule val main = MainDispatcherRule()

    // ---------- tasks ----------

    @Test fun `service tasks are hidden unless asked`() = runTest {
        val vm = viewModelOf { TasksViewModel(ScriptedApi(DemoApi(latencyMs = 0))) }
        vm.start()
        val snapshot = vm.loader.state.value.valueOrNull!!
        assertEquals(4, snapshot.finished.size)
        assertEquals(3, vm.visible(snapshot, showService = false).second.size)
        assertEquals(4, vm.visible(snapshot, showService = true).second.size)
        vm.stop()
    }

    @Test fun `task detail rating and remark go to the server and are confirmed`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        val seen = mutableListOf<UiText>()
        val vm = viewModelOf { TaskDetailViewModel(api, "t1") }
        watch(vm.messages) { seen += it }
        vm.loader.start()
        vm.rate(2)
        vm.comment("  не так  ")
        vm.comment("   ")
        runCurrent()
        assertEquals(1, api.count("rate"))
        assertEquals(1, api.count("comment"))
        assertTrue(seen.contains(UiText.Res(R.string.task_rated)))
        assertTrue(seen.contains(UiText.Res(R.string.task_comment_sent)))
        assertEquals(2, vm.loader.state.value.valueOrNull?.rating)
        assertEquals("не так", vm.loader.state.value.valueOrNull?.comments?.single()?.text)
    }

    @Test fun `a finished task is not polled`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        val vm = viewModelOf { TaskDetailViewModel(api, "t1") }
        vm.loader.start(); runCurrent()
        advanceTimeBy(60_000)
        assertEquals(1, api.count("task"))
    }

    @Test fun `missing task is a failure`() = runTest {
        val vm = viewModelOf { TaskDetailViewModel(ScriptedApi(DemoApi(latencyMs = 0)), "nope") }
        vm.loader.start()
        assertTrue(vm.loader.state.value is Load.Failed)
    }

    @Test fun `a running task opened in detail shows that it finished, with its answer`() = runTest {
        // the server as it is (sena/phone/data.py): a list row of a running task gives its thread as "id" and its entry as "tid";
        // the task's page finds a running task by either, a finished one only by its entry in the log
        var ended = false
        val running = TaskItem("ab12cd34", "Найди билеты", 1.0, null, listOf(TaskStep("ag_web", "Открываю", 2.0)), null, emptyList(), false, 0.0, null,
            running = true, thread = "6170939392")
        val calls = mutableListOf<String>()
        val server = object : com.tema.ia.data.SenaApi by ScriptedApi(DemoApi(latencyMs = 0)) {
            override suspend fun task(id: String): TaskItem {
                calls += id
                return when {
                    !ended && (id == "ab12cd34" || id == "6170939392") -> running
                    ended && id == "ab12cd34" -> running.copy(running = false, end = 9.0, answer = "Нашла: 142 доллара", thread = null)
                    else -> throw ApiException(ApiError.Server, "Такой задачи нет")
                }
            }
        }
        val listed = com.tema.ia.data.parseTasks(com.tema.ia.data.parseJson(
            """{"now":5.0,"active":[{"id":"6170939392","tid":"ab12cd34","text":"Найди билеты","start":1.0,"steps":[]}],"finished":[]}""")).active.single()
        val vm = viewModelOf { TaskDetailViewModel(server, listed.id) }   // the list opens the page with what it parsed
        vm.loader.start(); runCurrent()
        ended = true
        advanceTimeBy(3_001); runCurrent()
        val shown = vm.loader.state.value as Load.Ready
        assertNull(shown.error)
        assertFalse("shown as finished", shown.value.running)
        assertEquals("Нашла: 142 доллара", shown.value.answer)
        val polls = calls.size
        advanceTimeBy(15_001); runCurrent()
        assertEquals("a just-finished task is looked at again slowly: its verdict comes after it", polls + 1, calls.size)
        vm.loader.stop()
    }

    @Test fun `a finished task's page waits a few minutes for the verdict, then stops`() {
        val done = TaskItem("t1", "x", 1.0, 2.0, emptyList(), "ok", emptyList(), false, 0.0, null, running = false)
        assertEquals(3_000L, pollAfter(null, null, 0))
        assertEquals(3_000L, pollAfter(done.copy(running = true, end = null), null, 0))
        assertEquals("no verdict yet", 15_000L, pollAfter(done, 1_000L, 61_000L))
        assertEquals("waited long enough", 0L, pollAfter(done, 1_000L, 1_000L + VERDICT_WAIT_MS))
        assertEquals("the verdict came", 0L, pollAfter(done.copy(outcome = Outcome(Verdict.Unverified, "")), 1_000L, 2_000L))
        assertEquals("a service chore gets no verdict", 0L, pollAfter(done.copy(service = true), 1_000L, 2_000L))
    }

    @Test fun `the task list moves fast only while his own task runs`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        val chore = TaskItem("n1", "Ночная уборка", 1.0, null, emptyList(), null, emptyList(), true, 0.0, null, running = true, thread = "5")
        api.answers["tasks"] = { TasksSnapshot(10.0, listOf(chore), emptyList()) }
        val vm = viewModelOf { TasksViewModel(api) }
        vm.start(); runCurrent()
        advanceTimeBy(14_000); runCurrent()
        assertEquals("only a service chore runs: the slow look", 1, api.count("tasks"))
        vm.stop()
    }

    @Test fun `stop in the detail stops this very task, by its entry`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        val stopped = mutableListOf<String>()
        val running = TaskItem("ab12cd34", "Найди билеты", 1.0, null, emptyList(), null, emptyList(), false, 0.0, null, running = true, thread = "6170939392")
        api.answers["task"] = { running }
        val vm = viewModelOf { TaskDetailViewModel(object : com.tema.ia.data.SenaApi by api {
            override suspend fun stopTask(id: String): ActionResult { stopped += id; return ActionResult("Задача остановлена") }
        }, "ab12cd34") }
        vm.loader.start(); runCurrent()
        vm.stopTask(); runCurrent()
        assertEquals(listOf("ab12cd34"), stopped)
        vm.loader.stop()
    }

    // ---------- memory ----------

    private fun TestScope.memory(api: ScriptedApi) = viewModelOf { MemoryViewModel(api, SavedStateHandle()) }

    @Test fun `a fact goes in one at a time, as the words typed, through the server's own door`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        val vm = memory(api)
        val seen = mutableListOf<UiText>()
        watch(vm.messages) { seen += it }
        var done = false
        vm.addFact("Обо мне", "  Люблю чай  ") { done = true }
        runCurrent()
        assertTrue(done)
        assertEquals(1, api.count("addFact"))
        val about = runTestFacts(api).first { it.title == "Обо мне" }
        assertEquals("Люблю чай", about.items.last())
        assertTrue(seen.contains(UiText.Res(R.string.memory_fact_added)))
    }

    @Test fun `adding a fact to a new section creates it`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        val vm = memory(api)
        vm.addFact("Здоровье", "Бегаю по утрам")
        assertEquals("Бегаю по утрам", runTestFacts(api).first { it.title == "Здоровье" }.items.single())
    }

    @Test fun `blank fact is ignored`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        memory(api).addFact("Обо мне", "   ")
        assertEquals(0, api.count("addFact"))
    }

    @Test fun `editing one of Sena's facts sends the line as it is and only the new words`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        var sent: Triple<String, String, String>? = null
        val vm = viewModelOf { MemoryViewModel(object : com.tema.ia.data.SenaApi by api {
            override suspend fun editFact(section: String, old: String, text: String): ActionResult { sent = Triple(section, old, text); return ActionResult("ok") }
        }, SavedStateHandle()) }
        vm.editFact("Обо мне", "24.10.2026 14:03: живу в Порту", "живу в Лиссабоне")
        runCurrent()
        assertEquals(Triple("Обо мне", "24.10.2026 14:03: живу в Порту", "живу в Лиссабоне"), sent)
        sent = null
        vm.editFact("Обо мне", "24.10.2026 14:03: живу в Порту", " живу в Порту ")   // the same words: nothing to send
        runCurrent()
        assertNull(sent)
    }

    @Test fun `deleting a fact removes one occurrence from its section only`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        val vm = memory(api)
        vm.deleteFact("Обо мне", "Живу в Лиссабоне")
        val facts = runTestFacts(api)
        assertFalse(facts.first { it.title == "Обо мне" }.items.contains("Живу в Лиссабоне"))
        assertEquals(2, facts.first { it.title == "Обо мне" }.items.size)
        assertEquals(2, facts.first { it.title == "Люди" }.items.size)
    }

    @Test fun `a person's card sends the whole alias list and the facts the owner erased`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        val vm = memory(api)
        val olga = api.people().first { it.name == "Ольга" }   // aliases [Оля], facts [Живёт в Порту, Любит кофе без сахара]
        vm.updatePerson(olga, "Ольга", listOf("Оля", " Олечка ", "Оля"), "сестра", "", listOf("Любит кофе без сахара", "Переехала в Лиссабон"))
        runCurrent()
        val after = api.people().first { it.id == olga.id }
        assertEquals(listOf("Оля", "Олечка"), after.aliases)
        assertEquals(listOf("Любит кофе без сахара", "Переехала в Лиссабон"), after.facts)   // the erased fact no longer holds
    }

    @Test fun `a shortened fact goes as the old one retired and the new one added`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        var sent: Pair<List<String>, List<String>>? = null
        val vm = viewModelOf {
            MemoryViewModel(object : com.tema.ia.data.SenaApi by api {
                override suspend fun updatePerson(id: String, name: String, aliases: List<String>, relationship: String, opinion: String, facts: List<String>, retire: List<String>): ActionResult {
                    sent = facts to retire
                    return ActionResult("Обновлено")
                }
            }, SavedStateHandle())
        }
        val olga = api.people().first { it.name == "Ольга" }   // facts [Живёт в Порту, Любит кофе без сахара]
        vm.updatePerson(olga, "Ольга", olga.aliases, "сестра", "", listOf("живёт в порту ", "Любит кофе"))
        runCurrent()
        // the kept fact isn't sent again; the shortened one is both: the server never takes it for "already known, said fuller"
        assertEquals(listOf("Любит кофе") to listOf("Любит кофе без сахара"), sent)
    }

    @Test fun `fact changes are matched without case and end spaces`() {
        assertEquals(listOf("Б") to listOf("в"), factChanges(listOf("а", "в"), listOf(" А ", "Б", "б")))
        assertEquals(emptyList<String>() to emptyList<String>(), factChanges(listOf("Живёт в Порту"), listOf("живёт в порту")))
    }

    @Test fun `a new person keeps every other name`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        val vm = memory(api)
        vm.addPerson("Ольга Петрова", listOf("Оля", " Олечка ", "Оля"), "", "", emptyList())
        runCurrent()
        assertEquals(listOf("Оля", "Олечка"), api.people().first { it.name == "Ольга Петрова" }.aliases)
    }

    @Test fun `a search hit leads where it lives`() {
        assertEquals(SearchTarget.Note, searchTarget(SearchHit(SearchKind.NOTE, "текст", null, ref = "Люди/Оля.md#0", path = "Люди/Оля.md")))
        assertEquals(SearchTarget.Note, searchTarget(SearchHit(SearchKind.PROJECT, "KSclay", null, ref = "Проекты/KSclay.md#1")))
        assertEquals(SearchTarget.Task, searchTarget(SearchHit(SearchKind.TASK, "«Погода»", 1.0, ref = "ab12cd34")))
        assertEquals(SearchTarget.Person, searchTarget(SearchHit(SearchKind.PERSON, "Оля", null, ref = "p1:f2")))
        assertEquals(SearchTarget.Skill, searchTarget(SearchHit(SearchKind.SKILL, "громкость", null, ref = "mac-volume")))
        assertEquals(SearchTarget.Chat, searchTarget(SearchHit(SearchKind.MESSAGE, "привет", 1.0, ref = "1:abc")))
        assertEquals(SearchTarget.Facts, searchTarget(SearchHit(SearchKind.FACT, "Обо мне: кофе", null)))
        assertNull("a rule stays a row", searchTarget(SearchHit(SearchKind.RULE, "каждое утро", null, ref = "r1")))
        assertNull("nothing to open without its place", searchTarget(SearchHit(SearchKind.TASK, "x", null)))
    }

    @Test fun `failed save is reported and not hidden`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0)).also { it.failures["addFact"] = ApiException(ApiError.Server, "Не сохранено") }
        val vm = memory(api)
        val seen = mutableListOf<UiText>()
        watch(vm.messages) { seen += it }
        vm.addFact("Обо мне", "x")
        runCurrent()
        assertEquals(listOf<UiText>(UiText.Raw("Не сохранено")), seen)
        assertFalse(vm.saving.value)
    }

    @Test fun `new note goes to the inbox with a safe name`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        val vm = memory(api)
        vm.createNote("Идея: сайт/кофейня?", "")
        val note = api.notes().first()
        assertEquals("Входящие/Идея сайт кофейня.md", note.path)
        assertEquals(1, api.count("writeNote"))
        assertEquals("# Идея сайт кофейня\n", api.noteText(note.path))
    }

    @Test fun `search waits for typing to stop`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        val vm = memory(api)
        watch(vm.search)
        vm.onQuery("л"); advanceTimeBy(100)
        vm.onQuery("лис"); advanceTimeBy(100)
        assertEquals(0, api.count("search"))
        advanceTimeBy(400); runCurrent()
        assertEquals(1, api.count("search"))
        val s = vm.search.value as SearchState.Found
        assertTrue(s.hits.isNotEmpty())
        vm.onQuery("")
        advanceTimeBy(400); runCurrent()
        assertEquals(SearchState.Idle, vm.search.value)
    }

    @Test fun `search failure is shown`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0)).also { it.failures["search"] = ApiException(ApiError.NoServer, "x") }
        val vm = memory(api)
        watch(vm.search)
        vm.onQuery("что"); advanceTimeBy(400); runCurrent()
        assertTrue(vm.search.value is SearchState.Failed)
    }

    @Test fun `skill detail opens and closes`() = runTest {
        val vm = memory(ScriptedApi(DemoApi(latencyMs = 0)))
        vm.openSkill("mac-volume"); runCurrent()
        assertTrue(vm.skill.value is Load.Ready)
        vm.closeSkill()
        assertNull(vm.skill.value)
    }

    private suspend fun runTestFacts(api: ScriptedApi): List<FactSection> = api.facts()

    // ---------- integrations ----------

    private fun state(enabled: Boolean? = true, errors: Int = 0, lastError: String? = null, canToggle: Boolean = true) =
        IntegrationState("x", enabled, 0, null, errors, lastError, 0.0, canToggle)

    @Test fun `link status rules`() {
        val openai = IntegrationCatalog.by("openai")!!
        val claude = IntegrationCatalog.by("claude_api")!!
        val maps = IntegrationCatalog.by("maps")!!
        val browser = IntegrationCatalog.by("browser")!!
        assertEquals(Link.Disabled, linkOf(browser, state(enabled = false), emptyMap()))
        assertEquals(Link.Working, linkOf(browser, state(), emptyMap()))
        assertEquals(Link.NoKey, linkOf(claude, state(null, canToggle = false), mapOf("SENA_CLAUDE_BRAIN_KEY" to false)))
        assertEquals("OpenAI isn't needed now: no key is no warning", Link.Unused, linkOf(openai, state(null, canToggle = false), mapOf("OPENAI_API_KEY" to false)))
        assertEquals(Link.Part, linkOf(openai, state(null, canToggle = false), mapOf("OPENAI_API_KEY" to true)))
        assertEquals(Link.Working, linkOf(maps, state(), mapOf("TOMTOM_KEY" to false, "GOOGLE_MAPS_KEY" to true)))   // any single one is enough
        assertEquals(Link.NoKey, linkOf(maps, state(), mapOf("TOMTOM_KEY" to false, "GOOGLE_MAPS_KEY" to false)))
        assertEquals(Link.Problem, linkOf(browser, state(errors = 2, lastError = "сбой"), emptyMap()))
        assertEquals(Link.Working, linkOf(browser, null, emptyMap()))
    }

    @Test fun `the server's ready and needed decide what is installed and what is a spare`() {
        val claudeCode = IntegrationCatalog.by("claude")!!
        val openai = IntegrationCatalog.by("openai")!!
        val claude = IntegrationCatalog.by("claude_api")!!
        assertEquals("Claude Code not on this Mac", Link.NotInstalled, linkOf(claudeCode, state().copy(ready = false), emptyMap()))
        assertEquals(Link.Disabled, linkOf(claudeCode, state(enabled = false).copy(ready = false), emptyMap()))   // switched off says so first
        assertEquals("fingerprints moved to the cloud: OpenAI without its key is a fault now", Link.NoKey,
            linkOf(openai, state(null, canToggle = false).copy(needed = true), mapOf("OPENAI_API_KEY" to false)))
        assertEquals(Link.Unused, linkOf(claude, state(null, canToggle = false).copy(needed = false), mapOf("SENA_CLAUDE_BRAIN_KEY" to false)))
    }

    @Test fun `moving a role flips the switch at once and keeps it when the server agrees`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        val vm = viewModelOf { com.tema.ia.ui.screens.analytics.AnalyticsViewModel(api) }
        vm.home.start(); runCurrent()
        val before = vm.home.state.value.valueOrNull!!
        assertEquals("cloud", before.switchable.first { it.role == "compact" }.place)
        vm.move("compact", home = true)
        assertEquals("home", vm.home.state.value.valueOrNull!!.switchable.first { it.role == "compact" }.place)   // optimistic
        runCurrent()
        assertEquals("home", vm.home.state.value.valueOrNull!!.switchable.first { it.role == "compact" }.place)   // and confirmed by a refresh
        vm.home.stop()
    }

    @Test fun `every integration has texts and a known group`() {
        assertEquals(25, IntegrationCatalog.all.size)
        assertEquals(IntegrationCatalog.all.size, IntegrationCatalog.all.map { it.id }.toSet().size)
    }

    @Test fun `integration toggle is optimistic and reverted on refusal`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        val vm = viewModelOf { IntegrationsViewModel(api) }
        val seen = mutableListOf<UiText>()
        watch(vm.messages) { seen += it }
        vm.loader.start()
        vm.toggle("browser", false)
        runCurrent()
        assertEquals(false, vm.loader.state.value.valueOrNull?.states?.get("browser")?.enabled)
        assertTrue(seen.contains(UiText.Res(R.string.int_toggled_off)))
        api.failures["setIntegration"] = ApiException(ApiError.Server, "нельзя")
        vm.toggle("browser", true)
        runCurrent()
        assertEquals(false, vm.loader.state.value.valueOrNull?.states?.get("browser")?.enabled)   // the server refused: stays as it was
        assertTrue(seen.contains(UiText.Raw("нельзя")))
    }

    @Test fun `switching on what connects only at Sena's next start says so`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        val vm = viewModelOf { IntegrationsViewModel(api) }
        val seen = mutableListOf<UiText>()
        watch(vm.messages) { seen += it }
        vm.loader.start(); runCurrent()
        vm.toggle("telegram_account", false); runCurrent()
        vm.toggle("telegram_account", true); runCurrent()
        assertEquals(listOf<UiText>(UiText.Res(R.string.int_toggled_off), UiText.Res(R.string.int_toggled_on_restart)), seen)
        assertEquals("the keys come with the settings, not the whole overview", 0, api.count("overview"))
        vm.loader.stop()
    }

    @Test fun `integration detail selection`() = runTest {
        val vm = viewModelOf { IntegrationsViewModel(ScriptedApi(DemoApi(latencyMs = 0))) }
        vm.open("github"); assertEquals("github", vm.selected.value)
        vm.close(); assertNull(vm.selected.value)
    }

    // ---------- panels ----------

    @Test fun `a panel action shows the server's answer and the panel as it is now`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        val seen = mutableListOf<UiText>()
        val vm = viewModelOf { com.tema.ia.ui.screens.panels.PanelViewModel(api, "goals") }
        watch(vm.messages) { seen += it }
        vm.loader.start()
        runCurrent()
        val before = vm.loader.state.value.valueOrNull!!
        val items = before.blocks.filterIsInstance<com.tema.ia.data.PanelBlock.Items>().first().items
        val stepDone = items.first().actions.first { it.id == "goal_step_done" }
        vm.act(stepDone)
        runCurrent()
        assertEquals(1, api.count("panelAct"))
        assertEquals(1, api.count("panel"))   // the answer carries the fresh panel: no second request
        val after = vm.loader.state.value.valueOrNull!!.blocks.filterIsInstance<com.tema.ia.data.PanelBlock.Items>().first().items.first()
        assertTrue((after.progress ?: 0) > (items.first().progress ?: 0))
        assertTrue(seen.any { it is UiText.Raw && it.text.startsWith("Отмечено") })
        vm.loader.stop()
    }

    @Test fun `a form starts clean only once the server took it`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        val vm = viewModelOf { com.tema.ia.ui.screens.panels.PanelViewModel(api, "goals") }
        vm.loader.start(); runCurrent()
        val form = vm.loader.state.value.valueOrNull!!.blocks.filterIsInstance<com.tema.ia.data.PanelBlock.Form>().single()
        var cleared = 0
        vm.act(form.submit, kotlinx.serialization.json.JsonObject(mapOf("title" to kotlinx.serialization.json.JsonPrimitive("")))) { cleared++ }
        runCurrent()
        assertEquals("a refusal (no title) keeps what was typed", 0, cleared)
        vm.act(form.submit, kotlinx.serialization.json.JsonObject(mapOf("title" to kotlinx.serialization.json.JsonPrimitive("Английский до C1")))) { cleared++ }
        runCurrent()
        assertEquals(1, cleared)
        vm.loader.stop()
    }

    @Test fun `no answer in time for a panel action means still at work, not gone`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        api.failOnce["panelAct"] = ApiException(ApiError.NoServer, "timeout", timedOut = true)
        val seen = mutableListOf<UiText>()
        val vm = viewModelOf { com.tema.ia.ui.screens.panels.PanelViewModel(api, "goals") }
        watch(vm.messages) { seen += it }
        vm.loader.start(); runCurrent()
        val any = vm.loader.state.value.valueOrNull!!.blocks.filterIsInstance<com.tema.ia.data.PanelBlock.Items>().first().items.first().actions.first()
        vm.act(any); runCurrent()
        assertEquals(listOf<UiText>(UiText.Res(R.string.panel_still_running)), seen)
        vm.loader.stop()
    }

    @Test fun `a refused panel action is reported and the panel reloads`() = runTest {
        val api = ScriptedApi(DemoApi(latencyMs = 0))
        api.failOnce["panelAct"] = ApiException(ApiError.Server, "Такого действия на этой панели нет")
        val seen = mutableListOf<UiText>()
        val vm = viewModelOf { com.tema.ia.ui.screens.panels.PanelViewModel(api, "goals") }
        watch(vm.messages) { seen += it }
        vm.loader.start()
        runCurrent()
        val any = vm.loader.state.value.valueOrNull!!.blocks.filterIsInstance<com.tema.ia.data.PanelBlock.Items>().first().items.first().actions.first()
        vm.act(any)
        runCurrent()
        assertEquals(1, seen.size)
        assertNull(vm.busy.value)
        vm.loader.stop()
    }
}
