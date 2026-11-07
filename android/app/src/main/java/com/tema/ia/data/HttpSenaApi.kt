package com.tema.ia.data

import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlinx.coroutines.Dispatchers
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import okhttp3.Call
import okhttp3.Callback
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import java.io.IOException
import java.io.InterruptedIOException
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/**
 * Client for the real Sena server on the Mac. The password goes in the Authorization header (Bearer): it never lands in an
 * address, a log on the way or a proxy's history. A header carries only plain ASCII, so a password with other letters still goes
 * as the `t` parameter, which the server accepts too.
 */
class HttpSenaApi(
    baseUrl: String,
    private val token: String,
    private val client: OkHttpClient = defaultClient(),
) : SenaApi {
    private val base: HttpUrl = baseUrl.toHttpUrl()
    private val json = "application/json; charset=utf-8".toMediaType()
    private val inHeader = token.all { it in ' '..'~' }

    // The last task list and conversation as the server signed them ("sig"): asked again with that signature, an unchanged
    // one comes back as {"same": true} — a few bytes instead of hundreds of kilobytes every few seconds
    @Volatile private var tasksSeen: Pair<String, TasksSnapshot>? = null
    @Volatile private var historySeen: Pair<String, List<ChatMessage>>? = null

    // ---------- network ----------

    private fun url(path: String, query: Map<String, String> = emptyMap()): HttpUrl =
        base.newBuilder().addPathSegments(path.trimStart('/')).apply {
            query.forEach { (k, v) -> addQueryParameter(k, v) }
            if (!inHeader) addQueryParameter("t", token)
        }.build()

    private suspend fun Call.await(): Response = suspendCancellableCoroutine { cont ->
        enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                // a timeout means the request left: the Mac may still be doing it (a long confirmed action), it isn't "gone"
                if (cont.isActive) cont.resumeWithException(ApiException(ApiError.NoServer, e.message ?: "Mac не отвечает", timedOut = e is InterruptedIOException))
            }

            override fun onResponse(call: Call, response: Response) {
                if (cont.isActive) cont.resume(response) else response.close()
            }
        })
        cont.invokeOnCancellation { cancel() }
    }

    private suspend fun send(request: Request): JsonElement = withContext(Dispatchers.IO) {
        client.newCall(request).await().use { response ->
            val text = response.body.string()
            if (!response.isSuccessful) throw failure(response.code, text)
            if (text.isBlank()) JsonObject(emptyMap()) else parseJson(text)
        }
    }

    /** A refusal in the server's words. 403 is a wrong password, unless the server says the address is locked for guessing. */
    private fun failure(code: Int, text: String): ApiException {
        val message = runCatching { parseJson(text).obj().str("error") }.getOrDefault("")
        return when {
            code == 403 && "подбор" in message -> ApiException(ApiError.Locked, message, code)
            code == 403 -> ApiException(ApiError.BadToken, message.ifBlank { "Пароль не подошёл" }, code)
            message.isNotBlank() -> ApiException(ApiError.Server, message, code)
            else -> ApiException(ApiError.BadAnswer, "HTTP $code", code)
        }
    }

    // The Sena server speaks HTTP/1.0 and closes the connection after each response: we don't reuse connections
    private fun request(path: String, query: Map<String, String> = emptyMap()): Request.Builder =
        Request.Builder().url(url(path, query)).header("Connection", "close").header("X-Sena-Zone", zone()).apply {
            if (inHeader) header("Authorization", "Bearer $token")
        }

    /** The signature of what the app already has, for a read the server can answer with {"same": true}. */
    private fun sameAs(seen: Pair<String, *>?): Map<String, String> = seen?.let { mapOf("same" to it.first) } ?: emptyMap()

    private suspend fun get(path: String, query: Map<String, String> = emptyMap()): JsonElement = send(request(path, query).get().build())

    private suspend fun post(path: String, body: JsonElement): JsonElement = send(request(path).post(body.toString().toRequestBody(json)).build())

    /** The owner's current UTC offset, "+03:00": the server keeps it so Sena knows what time it is where he is. */
    private fun zone(): String = zoneOffset()

    /** If the server returned `{"error": …}`, turns it into an exception; otherwise returns the same object. */
    private fun JsonObject.failIfError(): JsonObject = apply { strOrNull("error")?.let { throw ApiException(ApiError.Server, it) } }

    /** Remote action: `{"result": …}` means success, `{"error": …}` means a real rejection (we show it as an error). */
    private suspend fun act(name: String, body: JsonObject = JsonObject(emptyMap())): ActionResult {
        val answer = post("desk/api/$name", body).obj().failIfError()
        return ActionResult(answer.str("result"), answer.bool("handover"))
    }

    // ---------- Home ----------

    override suspend fun overview() = parseOverview(get("desk/api/overview"))
    override suspend fun coreStart() = act("core_start")
    override suspend fun coreStop() = act("core_stop")
    override suspend fun stopAll() = act("stop_all")
    override suspend fun answer(text: String, id: String?) = act("answer", buildJsonObject { put("text", text); if (id != null) put("id", id) })

    override suspend fun questionImage(id: String): ByteArray? = withContext(Dispatchers.IO) {
        client.newCall(request("desk/api/question_image", mapOf("id" to id)).get().build()).await().use { response ->
            if (!response.isSuccessful) throw failure(response.code, response.body.string())
            response.body.bytes().takeIf { it.isNotEmpty() }
        }
    }

    override suspend fun confirmSend(id: String) = act("confirm_send", buildJsonObject { put("id", id) })
    override suspend fun cancelSend(id: String) = act("cancel_send", buildJsonObject { put("id", id) })

    // ---------- Chat ----------

    override suspend fun history(): List<ChatMessage> {
        val seen = historySeen
        val o = get("desk/api/history", mapOf("limit" to "200") + sameAs(seen)).obj()
        if (seen != null && o.bool("same")) return seen.second
        return parseHistory(o).also { messages -> historySeen = o.strOrNull("sig")?.let { it to messages } }
    }

    override suspend fun ask(text: String): String {
        val answer = post("ask", buildJsonObject { put("text", text) }).obj().failIfError()
        return answer.str("id").ifBlank { throw ApiException(ApiError.BadAnswer, "Сервер не назвал номер задачи") }
    }

    override suspend fun askStatus(id: String) = parseAsk(get("task", mapOf("id" to id)).obj().failIfError())

    override suspend fun stopTask(id: String) = act("stop_task", buildJsonObject { put("id", id) })

    override suspend fun clearHistory() = act("history_clear")

    // ---------- Tasks ----------

    // slim: finished rows without their steps and cards (the list doesn't show them, a task's page asks for its own)
    override suspend fun tasks(): TasksSnapshot {
        val seen = tasksSeen
        val o = get("desk/api/tasks", mapOf("limit" to "100", "slim" to "1") + sameAs(seen)).obj()
        if (seen != null && o.bool("same")) return seen.second.copy(now = o.num("now", seen.second.now))
        return parseTasks(o).also { snapshot -> tasksSeen = o.strOrNull("sig")?.let { it to snapshot } }
    }

    // the task alone, without the desk's thousand events of it: the page is polled every 3 s while the task runs
    override suspend fun task(id: String): TaskItem {
        val o = get("tasks/one", mapOf("id" to id)).obj().failIfError()
        return parseTask(o, running = !o.bool("done", true))
    }

    override suspend fun comment(id: String, text: String) = act("comment", buildJsonObject { put("id", id); put("text", text) })

    override suspend fun rate(id: String, rating: Int) =
        act("rate", buildJsonObject { put("id", id); put("rating", rating) })

    // ---------- Rules ----------

    override suspend fun rules() = parseRules(get("desk/api/rules"))

    override suspend fun addRule(text: String) = act("rule_add", buildJsonObject { put("text", text) }).let { ActionResult(it.message) }

    private fun actionOf(kind: RuleKind, what: String) = when (kind) {
        RuleKind.Rule -> "rule_$what"
        RuleKind.Job -> "job_$what"
        RuleKind.Alert -> "alert_$what"
    }

    override suspend fun setRuleEnabled(entry: RuleEntry, enabled: Boolean) =
        act(actionOf(entry.kind, "toggle"), buildJsonObject { put("id", entry.id); put("enabled", enabled) })

    override suspend fun removeRule(entry: RuleEntry) = act(actionOf(entry.kind, "remove"), buildJsonObject { put("id", entry.id) })

    // ---------- Memory ----------

    // only the sections: the memory page also carries the whole archive, the personality and the registry's counts
    override suspend fun facts() = parseSections(get("desk/api/memory", mapOf("fields" to "sections")))

    override suspend fun addFact(section: String, text: String) =
        factAction("fact_add", buildJsonObject { put("section", section); put("text", text) }) { sections ->
            if (sections.any { it.title == section }) sections.map { if (it.title == section) it.copy(items = it.items + text) else it }
            else sections + FactSection(section, listOf(text))
        }

    override suspend fun editFact(section: String, old: String, text: String) =
        factAction("fact_edit", buildJsonObject { put("section", section); put("old", old); put("text", text) }) { sections ->
            // the line keeps the moment Sena wrote it: only the words change
            val stamped = factLine(old).stamp?.let { "$it: $text" } ?: text
            sections.map { s -> if (s.title != section) s else s.copy(items = s.items.map { if (it == old) stamped else it }) }
        }

    override suspend fun deleteFact(section: String, text: String) =
        factAction("fact_delete", buildJsonObject { put("section", section); put("text", text) }) { sections ->
            sections.map { s -> if (s.title != section) s else s.copy(items = s.items.toMutableList().also { it.remove(text) }) }
        }

    /**
     * One fact through the server's own action. A server without these actions (404: no such action) gets the older way:
     * a fresh copy of every section, this one change, and the whole list written back.
     */
    private suspend fun factAction(name: String, body: JsonObject, change: (List<FactSection>) -> List<FactSection>): ActionResult =
        try {
            act(name, body)
        } catch (e: ApiException) {
            if (e.code != 404) throw e
            saveFacts(change(facts()))
        }

    private suspend fun saveFacts(sections: List<FactSection>): ActionResult {
        val body = buildJsonArray {
            sections.forEach { s ->
                add(buildJsonObject { put("title", s.title); putJsonArray("items") { s.items.forEach { add(JsonPrimitive(it)) } } })
            }
        }
        post("memory", body).obj().failIfError()
        return ActionResult("")
    }

    override suspend fun people() = parsePeople(get("desk/api/people"))

    // every other name goes in "aliases"; the first also as "alias" for a server that takes only one
    override suspend fun addPerson(name: String, aliases: List<String>, relationship: String, opinion: String, facts: List<String>) =
        act(
            "person_add",
            buildJsonObject {
                put("name", name); put("alias", aliases.firstOrNull().orEmpty()); put("relationship", relationship); put("opinion", opinion)
                putJsonArray("aliases") { aliases.forEach { add(JsonPrimitive(it)) } }
                putJsonArray("facts") { facts.forEach { add(JsonPrimitive(it)) } }
            },
        )

    override suspend fun updatePerson(id: String, name: String, aliases: List<String>, relationship: String, opinion: String, facts: List<String>, retire: List<String>) =
        act(
            "person",
            buildJsonObject {
                put("id", id); put("name", name); put("relationship", relationship); put("opinion", opinion)
                putJsonArray("aliases") { aliases.forEach { add(JsonPrimitive(it)) } }
                putJsonArray("facts") { facts.forEach { add(JsonPrimitive(it)) } }
                putJsonArray("retire") { retire.forEach { add(JsonPrimitive(it)) } }
            },
        )

    override suspend fun deletePerson(id: String) = act("person_delete", buildJsonObject { put("id", id) })

    override suspend fun notes() = parseNotes(get("desk/api/notes"))

    override suspend fun noteText(path: String): String {
        val o = get("notes/read", mapOf("path" to path)).obj().failIfError()
        return o.str("text")
    }

    override suspend fun writeNote(path: String, text: String, mode: String) =
        act("note_write", buildJsonObject { put("path", path); put("text", text); put("mode", mode) })

    override suspend fun deleteNote(path: String) = act("note_delete", buildJsonObject { put("path", path) })

    override suspend fun skills() = parseSkills(get("desk/api/skills"))

    override suspend fun skill(name: String): SkillDetail {
        val o = get("desk/api/skills", mapOf("name" to name)).obj().failIfError()
        return SkillDetail(o.str("name", name), o.str("when"), o.str("recipe"))
    }

    override suspend fun writeSkill(name: String, whenText: String, recipe: String) =
        act("skill_write", buildJsonObject { put("name", name); put("when", whenText); put("recipe", recipe) })

    override suspend fun deleteSkill(name: String) = act("skill_delete", buildJsonObject { put("name", name) })

    override suspend fun search(query: String) = parseSearch(get("search", mapOf("q" to query)))

    // ---------- Secrets ----------

    // a plain read: listing the names isn't an action worth a journal line every 30 seconds
    override suspend fun secrets() = parseSecrets(get("desk/api/secrets").obj().failIfError())

    override suspend fun secretPut(name: String, value: String, note: String, allow: List<String>, reveal: String, sites: List<String>) =
        act(
            "secret_put",
            buildJsonObject {
                put("name", name); put("value", value); put("note", note); put("reveal", reveal)
                putJsonArray("allow") { allow.forEach { add(JsonPrimitive(it)) } }
                putJsonArray("sites") { sites.forEach { add(JsonPrimitive(it)) } }
            },
        )

    override suspend fun secretRemove(name: String) = act("secret_remove", buildJsonObject { put("name", name) })

    override suspend fun secretReveal(name: String): String =
        post("desk/api/secret_reveal", buildJsonObject { put("name", name) }).obj().failIfError().str("value")

    // ---------- Integrations ----------

    override suspend fun integrations() = parseIntegrations(get("desk/api/integrations"))

    override suspend fun setIntegration(id: String, on: Boolean) = act("integration", buildJsonObject { put("id", id); put("on", on) })

    // ---------- Settings ----------

    override suspend fun config() = parseConfig(get("desk/api/config"))

    override suspend fun updateSettings(values: Map<String, JsonElement>): SettingsChange {
        val answer = post("desk/api/settings", JsonObject(values)).obj().failIfError()
        return parseSettingsChange(answer)
    }

    // the summary only: the journal's rows (limit) aren't shown on the phone
    override suspend fun costsReport(period: String) = parseCostsReport(get("desk/api/costs", mapOf("period" to period, "limit" to "1")))
    override suspend fun quality(days: Int) = parseQuality(get("quality", mapOf("days" to days.toString())))
    override suspend fun registry() = parseRegistry(get("desk/api/memory", mapOf("fields" to "index")))
    override suspend fun home() = parseHome(get("desk/api/home"))
    override suspend fun moveRole(role: String, place: String) = act("role_move", buildJsonObject { put("role", role); put("place", place) })

    // ---------- Panels the server describes ----------

    override suspend fun panels() = parsePanels(get("desk/api/panels"))
    override suspend fun panel(id: String) = parsePanel(get("desk/api/panel", mapOf("id" to id)).obj().failIfError())
    override suspend fun panelAct(panel: String, action: PanelAction, values: JsonObject): PanelResult {
        val answer = post("desk/api/panel_act", buildJsonObject {
            put("panel", panel); put("action", action.id); put("args", action.args); put("values", values)
        }).obj().failIfError()
        return parsePanelResult(answer)
    }

    /** The live voice of the "talk" button: the same Mac, its own port, plain WebSocket — so only inside your own network (Ф-4). */
    override fun voiceUrl(): String? {
        if (base.isHttps) return null
        val stream = base.newBuilder().port(Address.VOICE_PORT).encodedPath("/ear").query(null)
            .apply { if (!inHeader) addQueryParameter("t", token) }
            .addQueryParameter("device", "phone").addQueryParameter("mode", "talk").build()
        return "ws://" + stream.toString().removePrefix("http://")
    }

    override fun voiceHeaders(): Map<String, String> = if (inHeader) mapOf("Authorization" to "Bearer $token") else emptyMap()

    companion object {
        fun defaultClient(): OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(5, TimeUnit.SECONDS)
            .readTimeout(25, TimeUnit.SECONDS)
            .writeTimeout(15, TimeUnit.SECONDS)
            .callTimeout(40, TimeUnit.SECONDS)
            .retryOnConnectionFailure(false)
            .build()
    }
}

/** The phone's current UTC offset, "+07:00": the server keeps it so Sena knows what time it is where the owner is. */
internal fun zoneOffset(): String = java.time.ZoneId.systemDefault().rules.getOffset(java.time.Instant.now()).id.let { if (it == "Z") "+00:00" else it }
