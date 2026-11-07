package com.tema.ia.data

import kotlinx.serialization.json.JsonElement

/**
 * A controllable fake server for tests: by default answers like [DemoApi] with no delay,
 * records calls, and can be told to fail a chosen call with an error.
 */
class ScriptedApi(private val base: SenaApi = DemoApi(latencyMs = 0)) : SenaApi {
    val calls = mutableListOf<String>()
    val failures = mutableMapOf<String, Throwable>()
    /** An error that ONLY the next call with this name will receive. */
    val failOnce = mutableMapOf<String, Throwable>()
    /** A custom answer for a call (to check that the screen shows what the server returned). */
    val answers = mutableMapOf<String, suspend () -> Any?>()

    fun count(name: String) = calls.count { it == name }

    @Suppress("UNCHECKED_CAST")
    private suspend fun <T> gate(name: String, block: suspend () -> T): T {
        calls += name
        failOnce.remove(name)?.let { throw it }
        failures[name]?.let { throw it }
        answers[name]?.let { return it() as T }
        return block()
    }

    override suspend fun overview(): Overview = gate("overview") { base.overview() }
    override suspend fun coreStart(): ActionResult = gate("coreStart") { base.coreStart() }
    override suspend fun coreStop(): ActionResult = gate("coreStop") { base.coreStop() }
    override suspend fun stopAll(): ActionResult = gate("stopAll") { base.stopAll() }
    override suspend fun answer(text: String, id: String?): ActionResult = gate("answer") { base.answer(text, id) }
    override suspend fun questionImage(id: String): ByteArray? = gate("questionImage") { base.questionImage(id) }
    override suspend fun confirmSend(id: String): ActionResult = gate("confirmSend") { base.confirmSend(id) }
    override suspend fun cancelSend(id: String): ActionResult = gate("cancelSend") { base.cancelSend(id) }
    override suspend fun history(): List<ChatMessage> = gate("history") { base.history() }
    override suspend fun ask(text: String): String = gate("ask") { base.ask(text) }
    override suspend fun askStatus(id: String): AskStatus = gate("askStatus") { base.askStatus(id) }
    override suspend fun stopTask(id: String): ActionResult = gate("stopTask") { base.stopTask(id) }
    override suspend fun clearHistory(): ActionResult = gate("clearHistory") { base.clearHistory() }
    override suspend fun tasks(): TasksSnapshot = gate("tasks") { base.tasks() }
    override suspend fun task(id: String): TaskItem = gate("task") { base.task(id) }
    override suspend fun comment(id: String, text: String): ActionResult = gate("comment") { base.comment(id, text) }
    override suspend fun rate(id: String, rating: Int): ActionResult = gate("rate") { base.rate(id, rating) }
    override suspend fun rules(): List<RuleEntry> = gate("rules") { base.rules() }
    override suspend fun addRule(text: String): ActionResult = gate("addRule") { base.addRule(text) }
    override suspend fun setRuleEnabled(entry: RuleEntry, enabled: Boolean): ActionResult = gate("setRuleEnabled") { base.setRuleEnabled(entry, enabled) }
    override suspend fun removeRule(entry: RuleEntry): ActionResult = gate("removeRule") { base.removeRule(entry) }
    override suspend fun facts(): List<FactSection> = gate("facts") { base.facts() }
    override suspend fun registry(): RegistryStats = gate("registry") { base.registry() }
    override suspend fun home(): HomeReport = gate("home") { base.home() }
    override suspend fun moveRole(role: String, place: String): ActionResult = gate("moveRole") { base.moveRole(role, place) }
    override suspend fun panels(): List<PanelInfo> = gate("panels") { base.panels() }
    override suspend fun panel(id: String): Panel = gate("panel") { base.panel(id) }
    override suspend fun panelAct(panel: String, action: PanelAction, values: kotlinx.serialization.json.JsonObject): PanelResult =
        gate("panelAct") { base.panelAct(panel, action, values) }
    override suspend fun addFact(section: String, text: String): ActionResult = gate("addFact") { base.addFact(section, text) }
    override suspend fun editFact(section: String, old: String, text: String): ActionResult = gate("editFact") { base.editFact(section, old, text) }
    override suspend fun deleteFact(section: String, text: String): ActionResult = gate("deleteFact") { base.deleteFact(section, text) }
    override suspend fun people(): List<Person> = gate("people") { base.people() }
    override suspend fun addPerson(name: String, aliases: List<String>, relationship: String, opinion: String, facts: List<String>): ActionResult =
        gate("addPerson") { base.addPerson(name, aliases, relationship, opinion, facts) }
    override suspend fun updatePerson(id: String, name: String, aliases: List<String>, relationship: String, opinion: String, facts: List<String>, retire: List<String>): ActionResult =
        gate("updatePerson") { base.updatePerson(id, name, aliases, relationship, opinion, facts, retire) }
    override suspend fun deletePerson(id: String): ActionResult = gate("deletePerson") { base.deletePerson(id) }
    override suspend fun notes(): List<NoteInfo> = gate("notes") { base.notes() }
    override suspend fun noteText(path: String): String = gate("noteText") { base.noteText(path) }
    override suspend fun writeNote(path: String, text: String, mode: String): ActionResult = gate("writeNote") { base.writeNote(path, text, mode) }
    override suspend fun deleteNote(path: String): ActionResult = gate("deleteNote") { base.deleteNote(path) }
    override suspend fun skills(): List<SkillInfo> = gate("skills") { base.skills() }
    override suspend fun skill(name: String): SkillDetail = gate("skill") { base.skill(name) }
    override suspend fun writeSkill(name: String, whenText: String, recipe: String): ActionResult = gate("writeSkill") { base.writeSkill(name, whenText, recipe) }
    override suspend fun deleteSkill(name: String): ActionResult = gate("deleteSkill") { base.deleteSkill(name) }
    override suspend fun search(query: String): List<SearchHit> = gate("search") { base.search(query) }
    override suspend fun secrets(): SecretsSnapshot = gate("secrets") { base.secrets() }
    override suspend fun secretPut(name: String, value: String, note: String, allow: List<String>, reveal: String, sites: List<String>): ActionResult =
        gate("secretPut") { base.secretPut(name, value, note, allow, reveal, sites) }
    override suspend fun secretRemove(name: String): ActionResult = gate("secretRemove") { base.secretRemove(name) }
    override suspend fun secretReveal(name: String): String = gate("secretReveal") { base.secretReveal(name) }
    override suspend fun integrations(): Map<String, IntegrationState> = gate("integrations") { base.integrations() }
    override suspend fun setIntegration(id: String, on: Boolean): ActionResult = gate("setIntegration") { base.setIntegration(id, on) }
    override suspend fun config(): ConfigInfo = gate("config") { base.config() }
    override suspend fun updateSettings(values: Map<String, JsonElement>): SettingsChange = gate("updateSettings") { base.updateSettings(values) }
    override suspend fun costsReport(period: String): CostsReport = gate("costsReport") { base.costsReport(period) }
    override suspend fun quality(days: Int): QualityReport = gate("quality") { base.quality(days) }
    override fun voiceUrl(): String? = base.voiceUrl()
    override fun voiceHeaders(): Map<String, String> = base.voiceHeaders()
}
