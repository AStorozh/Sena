package com.tema.ia.data

import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject

/** Everything the app can ask Sena. The real answer comes from [HttpSenaApi]; the fake one, for browsing without a Mac, from [DemoApi]. */
interface SenaApi {
    suspend fun overview(): Overview
    suspend fun coreStart(): ActionResult
    suspend fun coreStop(): ActionResult
    suspend fun stopAll(): ActionResult
    /** An answer to Sena's question [id] (the one on screen): with two agents asking, the words go to the question he read. */
    suspend fun answer(text: String, id: String? = null): ActionResult
    /** The picture that came with Sena's question [id]; null — there is none. */
    suspend fun questionImage(id: String): ByteArray? = null
    suspend fun confirmSend(id: String): ActionResult
    suspend fun cancelSend(id: String): ActionResult

    suspend fun history(): List<ChatMessage>
    suspend fun ask(text: String): String
    suspend fun askStatus(id: String): AskStatus
    suspend fun stopTask(id: String): ActionResult
    suspend fun clearHistory(): ActionResult

    suspend fun tasks(): TasksSnapshot
    suspend fun task(id: String): TaskItem
    suspend fun comment(id: String, text: String): ActionResult
    suspend fun rate(id: String, rating: Int): ActionResult

    suspend fun rules(): List<RuleEntry>
    suspend fun addRule(text: String): ActionResult
    suspend fun setRuleEnabled(entry: RuleEntry, enabled: Boolean): ActionResult
    suspend fun removeRule(entry: RuleEntry): ActionResult

    // memory facts go one at a time through Sena's own door: she stamps them, refuses passwords, and nothing she wrote meanwhile is lost
    suspend fun facts(): List<FactSection>
    suspend fun addFact(section: String, text: String): ActionResult
    suspend fun editFact(section: String, old: String, text: String): ActionResult
    suspend fun deleteFact(section: String, text: String): ActionResult
    suspend fun people(): List<Person>
    /** A new card; [aliases] — every other name the owner typed. */
    suspend fun addPerson(name: String, aliases: List<String>, relationship: String, opinion: String, facts: List<String>): ActionResult
    /**
     * [aliases] — the whole new list (it replaces the old one); [facts] — only the facts the card doesn't have yet; [retire] — facts
     * the owner took out or changed: they no longer hold. A shortened fact is its old text in [retire] and the new one in [facts].
     */
    suspend fun updatePerson(id: String, name: String, aliases: List<String>, relationship: String, opinion: String, facts: List<String>, retire: List<String> = emptyList()): ActionResult
    suspend fun deletePerson(id: String): ActionResult
    suspend fun notes(): List<NoteInfo>
    suspend fun noteText(path: String): String
    suspend fun writeNote(path: String, text: String, mode: String = "create"): ActionResult
    suspend fun deleteNote(path: String): ActionResult
    suspend fun skills(): List<SkillInfo>
    suspend fun skill(name: String): SkillDetail
    suspend fun writeSkill(name: String, whenText: String, recipe: String): ActionResult
    suspend fun deleteSkill(name: String): ActionResult
    suspend fun search(query: String): List<SearchHit>

    // the keeper of secrets: values go in and come out only here, behind the app's own lock
    suspend fun secrets(): SecretsSnapshot
    suspend fun secretPut(name: String, value: String, note: String, allow: List<String>, reveal: String, sites: List<String>): ActionResult
    suspend fun secretRemove(name: String): ActionResult
    suspend fun secretReveal(name: String): String

    suspend fun integrations(): Map<String, IntegrationState>
    suspend fun setIntegration(id: String, on: Boolean): ActionResult

    suspend fun config(): ConfigInfo
    suspend fun updateSettings(values: Map<String, JsonElement>): SettingsChange

    suspend fun costsReport(period: String = "month"): CostsReport
    suspend fun quality(days: Int = 7): QualityReport
    suspend fun registry(): RegistryStats

    // the home and the cloud: who does what, and moving a switchable job
    suspend fun home(): HomeReport
    suspend fun moveRole(role: String, place: String): ActionResult

    // panels the server describes (goals, the agents' memory, the exam, what waits for a "yes"): the app only draws them
    suspend fun panels(): List<PanelInfo>
    suspend fun panel(id: String): Panel
    suspend fun panelAct(panel: String, action: PanelAction, values: JsonObject = JsonObject(emptyMap())): PanelResult

    /** Where the live voice of the "talk" button streams (see [LiveVoice]); null — no live voice here (the demo, an https address). */
    fun voiceUrl(): String? = null

    /** The headers the live voice's stream carries: the password goes there, never in its address. */
    fun voiceHeaders(): Map<String, String> = emptyMap()
}

/**
 * Stops a running task: by its entry in the logs ([tid]) when known — that names it exactly even after its thread is reused;
 * a server that stops only by thread refuses a tid with 400, and then it goes by the [thread].
 */
suspend fun SenaApi.stopRunning(tid: String?, thread: String?): ActionResult {
    if (tid != null) {
        try {
            return stopTask(tid)
        } catch (e: ApiException) {
            if (e.code != 400 || thread == null || thread == tid) throw e
        }
    }
    return stopTask(thread ?: throw ApiException(ApiError.BadAnswer, "Сервер не назвал задачу"))
}

/** A running task from a list or its page: its entry when the server named one apart from the thread, else the thread itself. */
suspend fun SenaApi.stopRunning(task: TaskItem): ActionResult = stopRunning(task.id.takeIf { it != task.thread }, task.thread ?: task.id)
