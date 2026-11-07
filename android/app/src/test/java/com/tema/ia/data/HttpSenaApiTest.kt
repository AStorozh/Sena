package com.tema.ia.data

import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonPrimitive
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.SocketPolicy
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test

/** The client against a fake server: what it sends and how it interprets every kind of answer and error. */
class HttpSenaApiTest {
    private lateinit var server: MockWebServer
    private lateinit var api: HttpSenaApi

    @Before fun setUp() {
        server = MockWebServer().also { it.start() }
        api = HttpSenaApi(server.url("/").toString().trimEnd('/'), "secret pass")
    }

    @After fun tearDown() = server.shutdown()

    private fun reply(body: String, code: Int = 200) = server.enqueue(MockResponse().setResponseCode(code).setBody(body).addHeader("Content-Type", "application/json"))

    private fun failure(block: suspend () -> Any?): ApiException {
        try {
            runBlocking { block() }
        } catch (e: ApiException) {
            return e
        }
        fail("Ожидали ApiException")
        throw IllegalStateException()
    }

    @Test fun `the password goes in the Authorization header, never in the address`() {
        reply("{}")
        runBlocking { api.overview() }
        val request = server.takeRequest()
        assertEquals("/desk/api/overview", request.requestUrl?.encodedPath)
        assertEquals("Bearer secret pass", request.getHeader("Authorization"))   // the server's own reading (server.given)
        assertEquals(null, request.requestUrl?.queryParameter("t"))
        assertTrue(!request.path!!.contains("secret"))
    }

    @Test fun `a password a header can't carry still goes, as the t parameter`() {
        val cyrillic = HttpSenaApi(server.url("/").toString().trimEnd('/'), "пароль 12")
        reply("{}")
        runBlocking { cyrillic.overview() }
        val request = server.takeRequest()
        assertEquals("пароль 12", request.requestUrl?.queryParameter("t"))
        assertEquals(null, request.getHeader("Authorization"))
    }

    @Test fun `an unchanged task list comes back as same and the last one is shown`() {
        reply("""{"now":10.0,"active":[],"finished":[{"id":"t1","text":"Погода","start":1.0,"end":2.0}],"sig":"s1"}""")
        reply("""{"now":15.0,"same":true,"sig":"s1"}""")
        val first = runBlocking { api.tasks() }
        val second = runBlocking { api.tasks() }
        val asked = server.takeRequest().requestUrl!!
        assertEquals("1", asked.queryParameter("slim"))   // finished rows without steps and cards
        assertEquals(null, asked.queryParameter("same"))
        assertEquals("s1", server.takeRequest().requestUrl!!.queryParameter("same"))
        assertEquals(first.finished, second.finished)
        assertEquals(15.0, second.now, 0.0)
    }

    @Test fun `an unchanged conversation comes back as same`() {
        reply("""{"messages":[{"id":"m1","role":"user","content":"привет","time":1.0}],"total":1,"sig":"h1"}""")
        reply("""{"same":true,"sig":"h1"}""")
        reply("""{"messages":[],"total":0}""")   // an older server: no signature, nothing kept
        assertEquals(listOf("привет"), runBlocking { api.history() }.map { it.text })
        assertEquals(listOf("привет"), runBlocking { api.history() }.map { it.text })
        assertEquals(emptyList<ChatMessage>(), runBlocking { api.history() })
        server.takeRequest()
        assertEquals("h1", server.takeRequest().requestUrl!!.queryParameter("same"))
    }

    @Test fun `memory reads ask only for the part the screen shows`() {
        reply("""{"sections":[{"title":"Обо мне","items":["a"]}]}""")
        reply("""{"index":{"stats":{"vectors":3}}}""")
        reply("""{"summary":{"title":"неделя","total":0.5}}""")
        runBlocking { api.facts(); api.registry(); api.costsReport("week") }
        assertEquals("sections", server.takeRequest().requestUrl!!.queryParameter("fields"))
        assertEquals("index", server.takeRequest().requestUrl!!.queryParameter("fields"))
        assertEquals("1", server.takeRequest().requestUrl!!.queryParameter("limit"))   // the journal's rows aren't shown on the phone
    }

    @Test fun `the answer names the question it answers`() {
        reply("""{"result":"Ответ передан Сене"}""")
        runBlocking { api.answer("1234", "q7") }
        assertEquals("""{"text":"1234","id":"q7"}""", server.takeRequest().body.readUtf8())
    }

    @Test fun `a new person goes with every other name`() {
        reply("""{"result":"Добавлено","id":"p9"}""")
        runBlocking { api.addPerson("Ольга", listOf("Оля", "Олечка"), "сестра", "", emptyList()) }
        val body = server.takeRequest().body.readUtf8()
        assertTrue(body, body.contains("\"aliases\":[\"Оля\",\"Олечка\"]") && body.contains("\"alias\":\"Оля\""))
    }

    @Test fun `actions post json to the action path`() {
        reply("""{"result":"Запускаю","handover":true}""")
        val r = runBlocking { api.coreStart() }
        val request = server.takeRequest()
        assertEquals("POST", request.method)
        assertEquals("/desk/api/core_start", request.requestUrl?.encodedPath)
        assertEquals("Запускаю", r.message)
        assertTrue(r.handover)
    }

    @Test fun `refusal inside a 200 answer becomes a server error`() {
        reply("""{"error":"Ядро и так не запущено"}""")
        val e = failure { api.coreStop() }
        assertEquals(ApiError.Server, e.kind)
        assertEquals("Ядро и так не запущено", e.message)
    }

    @Test fun `wrong password`() {
        reply("""{"error":"нет доступа"}""", 403)   // what the server says to a wrong or missing password (server.deny)
        assertEquals(ApiError.BadToken, failure { api.overview() }.kind)
    }

    @Test fun `locked address`() {
        reply("""{"error":"адрес закрыт за подбор пароля"}""", 403)   // a locked address hears why: even the right password waits
        val e = failure { api.overview() }
        assertEquals(ApiError.Locked, e.kind)
        assertEquals(403, e.code)
    }

    @Test fun `no answer in time is no server, but the request may still be at work`() {
        val slow = HttpSenaApi(server.url("/").toString().trimEnd('/'), "x",
            okhttp3.OkHttpClient.Builder().callTimeout(300, java.util.concurrent.TimeUnit.MILLISECONDS).build())
        server.enqueue(MockResponse().setBody("""{"result":"ok"}""").setHeadersDelay(2, java.util.concurrent.TimeUnit.SECONDS))
        val e = failure { slow.confirmSend("s1") }
        assertEquals(ApiError.NoServer, e.kind)
        assertTrue("a timeout is told apart from a refused connection", e.timedOut)
    }

    @Test fun `server error with an explanation`() {
        reply("""{"error":"Внутренняя ошибка (KeyError): подробности в журнале событий"}""", 500)
        val e = failure { api.tasks() }
        assertEquals(ApiError.Server, e.kind)
        assertTrue(e.message.contains("Внутренняя ошибка"))
    }

    @Test fun `html instead of json`() {
        reply("<html>proxy</html>")
        assertEquals(ApiError.BadAnswer, failure { api.overview() }.kind)
    }

    @Test fun `error code without json body`() {
        reply("Bad Gateway", 502)
        assertEquals(ApiError.BadAnswer, failure { api.overview() }.kind)
    }

    @Test fun `empty body is an empty overview`() {
        reply("")
        assertEquals(false, runBlocking { api.overview() }.coreAlive)
    }

    @Test fun `connection refused is no server`() {
        val port = server.port
        server.shutdown()
        val dead = HttpSenaApi("http://127.0.0.1:$port", "x")
        val e = failure { dead.overview() }
        assertEquals(ApiError.NoServer, e.kind)
        assertTrue(!e.timedOut)
    }

    @Test fun `server that hangs up`() {
        server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AFTER_REQUEST))
        assertEquals(ApiError.NoServer, failure { api.overview() }.kind)
    }

    @Test fun `ask returns the task id and status reads steps`() {
        reply("""{"id":"abc123"}""")
        assertEquals("abc123", runBlocking { api.ask("Привет") })
        val ask = server.takeRequest()
        assertEquals("/ask", ask.requestUrl?.encodedPath)
        assertTrue(ask.body.readUtf8().contains("Привет"))
        reply("""{"steps":["Принято"],"answer":null,"done":false}""")
        val s = runBlocking { api.askStatus("abc123") }
        assertEquals(listOf("Принято"), s.steps)
        assertEquals("abc123", server.takeRequest().requestUrl?.queryParameter("id"))
    }

    @Test fun `ask without an id is a bad answer`() {
        reply("""{}""")
        assertEquals(ApiError.BadAnswer, failure { api.ask("x") }.kind)
    }

    @Test fun `ask refused`() {
        reply("""{"error":"Нужен текст сообщения или файл"}""", 400)
        assertEquals(ApiError.Server, failure { api.ask("") }.kind)
    }

    @Test fun `rule actions use the right names for each kind`() {
        val rule = RuleEntry(RuleKind.Rule, "r1", "t", true, null, null)
        val job = RuleEntry(RuleKind.Job, "j1", "t", true, null, null)
        val alert = RuleEntry(RuleKind.Alert, "a1", "t", true, null, null)
        repeat(6) { reply("""{"result":"ok"}""") }
        runBlocking {
            api.setRuleEnabled(rule, false); api.setRuleEnabled(job, false); api.setRuleEnabled(alert, true)
            api.removeRule(rule); api.removeRule(job); api.removeRule(alert)
        }
        val paths = (1..6).map { server.takeRequest().requestUrl!!.encodedPath }
        assertEquals(listOf("rule_toggle", "job_toggle", "alert_toggle", "rule_remove", "job_remove", "alert_remove").map { "/desk/api/$it" }, paths)
    }

    @Test fun `toggle sends id and enabled flag`() {
        reply("""{"result":"ok"}""")
        runBlocking { api.setRuleEnabled(RuleEntry(RuleKind.Rule, "r1", "t", true, null, null), false) }
        val body = server.takeRequest().body.readUtf8()
        assertTrue(body.contains("\"id\":\"r1\""))
        assertTrue(body.contains("\"enabled\":false"))
    }

    @Test fun `add rule accepts the rule object as success`() {
        reply("""{"id":"abc","text":"t","enabled":true,"jobs":[]}""")
        runBlocking { api.addRule("Каждое утро погода") }
        assertTrue(server.takeRequest().body.readUtf8().contains("Каждое утро погода"))
    }

    @Test fun `add rule refused`() {
        reply("""{"error":"Слишком коротко: напиши правило словами"}""")
        assertEquals(ApiError.Server, failure { api.addRule("a") }.kind)
    }

    @Test fun `a fact goes through the server's own action`() {
        reply("""{"result":"Запомнила"}""")
        assertEquals("Запомнила", runBlocking { api.addFact("Обо мне", "Люблю чай") }.message)
        val request = server.takeRequest()
        assertEquals("/desk/api/fact_add", request.requestUrl?.encodedPath)
        assertEquals("""{"section":"Обо мне","text":"Люблю чай"}""", request.body.readUtf8())
        reply("""{"result":"ok"}""")
        runBlocking { api.editFact("Обо мне", "24.10.2026 14:03: живу в Порту", "живу в Лиссабоне") }
        assertEquals("""{"section":"Обо мне","old":"24.10.2026 14:03: живу в Порту","text":"живу в Лиссабоне"}""", server.takeRequest().body.readUtf8())
        reply("""{"result":"ok"}""")
        runBlocking { api.deleteFact("Обо мне", "a") }
        assertEquals("/desk/api/fact_delete", server.takeRequest().requestUrl?.encodedPath)
    }

    @Test fun `a server without the fact actions gets a fresh copy of the sections changed and written back`() {
        reply("""{"error":"Нет такого действия"}""", 404)   // desk.act for an unknown action
        reply("""{"sections":[{"title":"Обо мне","items":["24.10.2026 14:03: живу в Порту","b"]},{"title":"Люди","items":["c"]}]}""")
        reply("""{"ok":true}""")
        runBlocking { api.editFact("Обо мне", "24.10.2026 14:03: живу в Порту", "живу в Лиссабоне") }
        assertEquals("/desk/api/fact_edit", server.takeRequest().requestUrl?.encodedPath)
        assertEquals("/desk/api/memory", server.takeRequest().requestUrl?.encodedPath)
        val write = server.takeRequest()
        assertEquals("/memory", write.requestUrl?.encodedPath)
        // the line keeps the moment Sena wrote it; nothing else changes
        assertEquals("""[{"title":"Обо мне","items":["24.10.2026 14:03: живу в Лиссабоне","b"]},{"title":"Люди","items":["c"]}]""", write.body.readUtf8())
    }

    @Test fun `a refused fact is the server's refusal, not the older way`() {
        reply("""{"error":"Пароли, коды и ключи в память не пишу"}""")
        assertEquals("Пароли, коды и ключи в память не пишу", failure { api.addFact("Обо мне", "пароль 12345678") }.message)
        assertEquals(1, server.requestCount)
    }

    @Test fun `the secrets list is a plain read, not an action`() {
        reply("""{"items":[{"name":"сервер","allow":["ag_mac"],"reveal":"confirm"}],"agents":["ag_mac","ag_web"]}""")
        assertEquals(listOf("сервер"), runBlocking { api.secrets() }.items.map { it.name })
        val request = server.takeRequest()
        assertEquals("GET", request.method)
        assertEquals("/desk/api/secrets", request.requestUrl?.encodedPath)
    }

    @Test fun `a refused secret is the server's own words`() {
        reply("""{"error":"Слишком коротко: от 8 знаков"}""")
        assertEquals("Слишком коротко: от 8 знаков", failure { api.secretPut("сервер", "123", "", emptyList(), "confirm", emptyList()) }.message)
        assertTrue("an empty list goes as it is: nobody may use it", server.takeRequest().body.readUtf8().contains("\"allow\":[]"))
    }

    @Test fun `the question's picture comes as bytes`() {
        server.enqueue(MockResponse().setBody(okio.Buffer().write(byteArrayOf(-1, -40, -1))).addHeader("Content-Type", "image/jpeg"))
        assertEquals(3, runBlocking { api.questionImage("q1") }?.size)
        assertEquals("q1", server.takeRequest().requestUrl?.queryParameter("id"))
        server.enqueue(MockResponse().setBody("").addHeader("Content-Type", "image/jpeg"))
        assertEquals(null, runBlocking { api.questionImage("q2") })
    }

    @Test fun `a person's card sends the whole alias list and the facts that no longer hold`() {
        reply("""{"result":"Обновлено: Ольга"}""")
        runBlocking { api.updatePerson("p1", "Ольга", listOf("Оля"), "сестра", "", listOf("живёт в Лиссабоне"), retire = listOf("живёт в Порту")) }
        val body = server.takeRequest().body.readUtf8()
        assertTrue(body, body.contains("\"aliases\":[\"Оля\"]") && body.contains("\"retire\":[\"живёт в Порту\"]"))
    }

    @Test fun `settings update sends only the changed key and returns rejected`() {
        reply("""{"settings":{"verbosity":"medium","budget_usd_day":0},"changed":{"verbosity":"medium"},"rejected":{}}""")
        val c = runBlocking { api.updateSettings(mapOf("verbosity" to JsonPrimitive("medium"))) }
        assertEquals("""{"verbosity":"medium"}""", server.takeRequest().body.readUtf8())
        assertEquals("medium", c.settings.verbosity)
        assertTrue(c.rejected.isEmpty())
    }

    @Test fun `note text and skill detail`() {
        reply("""{"path":"a.md","text":"# Привет"}""")
        assertEquals("# Привет", runBlocking { api.noteText("a.md") })
        assertEquals("a.md", server.takeRequest().requestUrl?.queryParameter("path"))
        reply("""{"error":"Заметки «x.md» нет"}""")
        assertEquals(ApiError.Server, failure { api.noteText("x.md") }.kind)
        reply("""{"name":"mac-volume","when":"Громкость","recipe":"1. Сделать","code":""}""")
        assertEquals("1. Сделать", runBlocking { api.skill("mac-volume") }.recipe)
    }

    @Test fun `note write creates in the given folder`() {
        reply("""{"result":"Создана заметка Входящие/Идея.md"}""")
        runBlocking { api.writeNote("Входящие/Идея.md", "# Идея") }
        val request = server.takeRequest()
        assertEquals("/desk/api/note_write", request.requestUrl?.encodedPath)
        assertTrue(request.body.readUtf8().contains("\"mode\":\"create\""))
    }

    @Test fun `task detail is the task alone, found by its entry, and names its thread while it runs`() {
        reply("""{"id":"ab12cd34","text":"t","start":1.0,"steps":[],"done":false,"thread":"6170939392"}""")
        val running = runBlocking { api.task("ab12cd34") }
        assertTrue(running.running)
        assertEquals("ab12cd34", running.id)
        assertEquals("6170939392", running.thread)
        val request = server.takeRequest()
        assertEquals("/tasks/one", request.requestUrl?.encodedPath)   // not the desk's view with its thousand events
        assertEquals("ab12cd34", request.requestUrl?.queryParameter("id"))
        reply("""{"id":"ab12cd34","text":"t","start":1.0,"end":3.0,"steps":[],"done":true}""")
        val done = runBlocking { api.task("ab12cd34") }
        assertTrue(!done.running)
        assertEquals(null, done.thread)
        reply("""{"error":"Такой задачи нет"}""")
        assertEquals(ApiError.Server, failure { api.task("nope") }.kind)
    }

    @Test fun `rating and comment use the task log id`() {
        reply("""{"result":"Оценка записана"}""")
        reply("""{"result":"ok"}""")
        runBlocking { api.rate("t1", 2); api.comment("t1", "не так") }
        val a = server.takeRequest().body.readUtf8()
        assertTrue(a.contains("\"rating\":2") && a.contains("\"id\":\"t1\""))
        assertTrue(server.takeRequest().body.readUtf8().contains("не так"))
    }

    @Test fun `stopping goes by the task's entry, and by its thread when the server refuses the entry`() {
        reply("""{"result":"Задача остановлена"}""")
        assertEquals("Задача остановлена", runBlocking { api.stopRunning("ab12cd34", "6170939392") }.message)
        assertTrue(server.takeRequest().body.readUtf8().contains("\"id\":\"ab12cd34\""))
        reply("""{"error":"Поле «id» должно быть номером задачи"}""", 400)   // a server that stops only by thread
        reply("""{"result":"Задача остановлена"}""")
        assertEquals("Задача остановлена", runBlocking { api.stopRunning("ab12cd34", "6170939392") }.message)
        server.takeRequest()
        assertTrue(server.takeRequest().body.readUtf8().contains("\"id\":\"6170939392\""))
    }
}
