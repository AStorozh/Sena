package com.tema.ia.data

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.longOrNull

// Parses server responses into models. Every function tolerates empty, extra, and malformed-looking input.

fun verdictOf(text: String?): Verdict = when (text) {
    "done" -> Verdict.Done
    "done_with_remarks" -> Verdict.DoneWithRemarks
    "failed" -> Verdict.Failed
    "unverified" -> Verdict.Unverified
    else -> Verdict.None
}

private fun stepOf(e: JsonElement): TaskStep? = when (e) {
    is JsonObject -> TaskStep(e.str("who"), e.str("text"), e.numOrNull("time"))
    is JsonPrimitive -> e.takeIf { it !is JsonNull }?.let { TaskStep("", it.content, null) }
    else -> null
}

/**
 * A task from a list or from its own page. A running task in a list carries two numbers: "id" — the thread it runs in,
 * "tid" — its entry in the logs, which stays the same after it ends. Its own page names them "id" (the entry) and "thread".
 * The app opens a task by its entry: polled by the thread, a finished task can't be found, and a reused thread is someone else's.
 */
fun parseTask(o: JsonObject, running: Boolean): TaskItem {
    val outcome = o.obj("outcome").takeIf { it.isNotEmpty() }?.let { Outcome(verdictOf(it.strOrNull("verdict")), it.str("reason")) }
    val rating = o.obj("rating").takeIf { it.isNotEmpty() }?.intOrNull("value")
    val listed = "tid" in o
    val thread = if (listed) o.strOrNull("id") else o.strOrNull("thread")
    return TaskItem(
        id = (if (listed) o.strOrNull("tid") else null) ?: o.str("id"),
        thread = if (running) thread else null,
        text = o.str("text"),
        start = o.num("start"),
        end = if (running) null else o.numOrNull("end"),
        steps = o.arr("steps").mapNotNull(::stepOf),
        answer = o.strOrNull("answer"),
        comments = o.arr("comments").objects().map { Comment(it.str("text"), it.numOrNull("time")) },
        service = o.bool("service"),
        cost = o.num("cost"),
        outcome = outcome,
        rating = rating,
        running = running,
        next = if (running) o.strOrNull("next") else null,
        title = o.strOrNull("title"),
    )
}

fun parseTasks(root: JsonElement): TasksSnapshot {
    val o = root.obj()
    return TasksSnapshot(
        now = o.num("now"),
        active = o.arr("active").objects().map { parseTask(it, running = true) },
        finished = o.arr("finished").objects().map { parseTask(it, running = false) },
    )
}

fun parseOverview(root: JsonElement): Overview {
    val o = root.obj()
    val q = o.obj("question").takeIf { it.isNotEmpty() }
    val today = o.obj("today")
    val budget = o.obj("budget")
    return Overview(
        coreAlive = o.obj("core").bool("alive"),
        question = q?.let { Question(it.str("id"), it.str("question"), it.bool("has_image"), it.int("count", 1)) },
        pendingSends = o.arr("pending_sends").objects().map { PendingSend(it.str("id"), it.str("name"), it.str("text")) },
        active = o.arr("active").objects().map { parseTask(it, running = true) },
        budgetLimit = budget.num("limit"),
        spentToday = budget.num("spent", today.num("usd")),
        errorsToday = today.int("errors"),
        tasksToday = today.int("tasks"),
        keys = keyFlags(o.obj("accounts").obj("keys")),
    )
}

private fun keyFlags(o: JsonObject): Map<String, Boolean> = o.mapValues { (_, v) -> v.bool() }

private fun JsonElement?.int(): Int = (this as? JsonPrimitive)?.longOrNull?.toInt() ?: 0
private fun JsonElement?.bool(): Boolean = (this as? JsonPrimitive)?.booleanOrNull ?: false

/**
 * The server sends these breakdowns as [[key, {usd, calls}], ...] (a Python list of tuples turns into nested JSON arrays).
 * A task's row is keyed by its id and carries its words in "task": the words are what the owner reads.
 */
private fun costBits(arr: JsonArray): List<CostBit> = arr.mapNotNull { el ->
    val pair = el as? JsonArray ?: return@mapNotNull null
    val key = (pair.getOrNull(0) as? JsonPrimitive)?.takeIf { it !is JsonNull }?.content ?: return@mapNotNull null
    val o = pair.getOrNull(1).obj()
    CostBit(o.strOrNull("task") ?: key, o.num("usd"), o.int("calls"))
}

fun parseCostsReport(root: JsonElement): CostsReport {
    val s = root.obj().obj("summary")
    return CostsReport(
        title = s.str("title"), total = s.num("total"), calls = s.int("calls"),
        byDay = costBits(s.arr("by_day")), byModel = costBits(s.arr("by_model")), topTasks = costBits(s.arr("top_tasks")),
    )
}

fun parseQuality(root: JsonElement): QualityReport {
    val o = root.obj()
    fun verdictCounts(v: JsonObject) = v.entries.associate { (k, n) -> verdictOf(k) to n.int() }
    val repeats = o.arr("repeats").mapNotNull { el ->
        val pair = el as? JsonArray ?: return@mapNotNull null
        val place = (pair.getOrNull(0) as? JsonPrimitive)?.content ?: return@mapNotNull null
        place to pair.getOrNull(1).int()
    }
    val ratingCount = o.obj("ratings").entries.sumOf { it.value.int() }
    return QualityReport(
        days = o.int("days"), tasks = o.int("tasks"), verdicts = verdictCounts(o.obj("verdicts")),
        ratingAvg = o.numOrNull("rating_avg"), ratingCount = ratingCount,
        errors = o.int("errors"), repeats = repeats,
        byAgent = o.obj("by_agent").entries.associate { (agent, v) -> agent to verdictCounts(v.obj()) },
    )
}

fun parseHistory(root: JsonElement): List<ChatMessage> {
    val rows = if (root is JsonArray) root else root.obj().arr("messages")
    return rows.objects().mapNotNull {
        val text = it.str("content")
        if (text.isBlank()) null else ChatMessage(it.str("id"), it.str("role") == "user", text, it.num("time"))
    }
}

fun parseAsk(root: JsonElement): AskStatus {
    val o = root.obj()
    return AskStatus(
        steps = o.arr("steps").mapNotNull { stepOf(it)?.text },
        answer = o.strOrNull("answer"),
        done = o.bool("done"),
        failed = o.bool("failed"),
        tid = o.strOrNull("tid"),
        thread = o.strOrNull("thread"),
    )
}

private fun lastRun(job: JsonObject): LastRun? = job.obj("last").takeIf { it.isNotEmpty() }?.let { l -> l.strOrNull("text")?.let { LastRun(l.numOrNull("t"), it) } }

/** A single list: rules (with their jobs), jobs without a rule, and price alerts. Times and repeats stay codes: the screen words them. */
fun parseRules(root: JsonElement): List<RuleEntry> {
    val o = root.obj()
    val out = mutableListOf<RuleEntry>()
    for (r in o.arr("rules").objects()) {
        val jobs = r.arr("jobs").objects()
        val next = jobs.firstOrNull { it.bool("enabled", true) && it.strOrNull("at") != null }
        out += RuleEntry(
            RuleKind.Rule, r.str("id"), r.str("text"), r.bool("enabled", true),
            at = next?.strOrNull("at"), repeat = next?.repeatCode(), note = r.strOrNull("note"),
            fails = jobs.maxOfOrNull { it.int("fails") } ?: 0,
            paused = jobs.isNotEmpty() && jobs.none { it.bool("enabled", true) },
            last = jobs.mapNotNull(::lastRun).maxByOrNull { it.time ?: 0.0 },
        )
    }
    for (j in o.arr("jobs").objects()) {
        out += RuleEntry(RuleKind.Job, j.str("id"), j.str("text"), j.bool("enabled", true), at = j.strOrNull("at"), repeat = j.repeatCode(),
            fails = j.int("fails"), last = lastRun(j))
    }
    for (a in o.arr("alerts").objects()) {
        out += RuleEntry(RuleKind.Alert, a.str("id"), a.str("symbol"), a.bool("enabled", true),
            alert = AlertSpec(above = a.str("condition") == "above", value = a.num("value"), everyTime = !a.bool("once", true)))
    }
    return out.filter { it.id.isNotBlank() }
}

private fun JsonObject.repeatCode(): String? = str("repeat", "once").takeIf { it != "once" && it.isNotBlank() }

fun parseSections(root: JsonElement): List<FactSection> {
    val rows = if (root is JsonArray) root else root.obj().arr("sections")
    return rows.objects().map { FactSection(it.str("title"), it.arr("items").strings()) }
}

private val stampRegex = Regex("""^(\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}): (.*)$""", RegexOption.DOT_MATCHES_ALL)

/** A memory line as the server keeps it: Sena's own lines start with the moment she wrote them ("24.10.2026 14:03: …"). */
fun factLine(item: String): FactLine = stampRegex.matchEntire(item)?.let { FactLine(it.groupValues[1], it.groupValues[2]) } ?: FactLine(null, item)

/** The registry's numbers from the memory page (desk/api/memory → index.stats and index.ann_threshold). */
fun parseRegistry(root: JsonElement): RegistryStats {
    val index = root.obj().obj("index")
    val s = index.obj("stats")
    val threshold = index.obj("ann_threshold")
    return RegistryStats(
        items = s.obj("items").entries.associate { (kind, n) -> kind to n.int() },
        vectors = s.int("vectors"), versions = s.int("versions"), links = s.int("links"), pending = s.int("pending"),
        fileMb = s.num("file_mb"), matrixMb = s.num("matrix_mb"), searchMs = s.num("search_ms"),
        annVectors = threshold.int("vectors", 300_000), annSearchMs = threshold.num("search_ms", 50.0),
    )
}

/** desk/api/home: the roles table, today's home and cloud calls. */
fun parseHome(root: JsonElement): HomeReport {
    val o = root.obj()
    val today = o.obj("today")
    return HomeReport(
        roles = o.arr("roles").objects().map {
            RoleInfo(it.strOrNull("role"), it.str("title"), it.str("place"), it.str("model"), it.str("why"), it.bool("switchable"), it.bool("learning"), it.intOrNull("examples"))
        },
        homeToday = today.int("home"), homeByService = today.obj("home_by_service").entries.associate { (k, v) -> k to v.int() },
        outToday = today.int("out"), usdToday = today.num("usd"), minExamples = o.obj("learning").int("min_examples", 40),
    )
}

fun parsePeople(root: JsonElement): List<Person> = root.arr().objects().map {
    Person(it.str("id"), it.str("name"), it.str("relationship"), it.str("opinion"), it.arr("aliases").strings(), factTexts(it.arr("facts")))
}

/** A person's facts: the server sends their texts; an older one sent whole records, of which only those still true count. */
private fun factTexts(facts: JsonArray): List<String> = facts.mapNotNull { f ->
    when (f) {
        is JsonObject -> f.strOrNull("text")?.takeIf { f["valid_to"].let { to -> to == null || to is JsonNull } }
        is JsonPrimitive -> f.takeIf { it !is JsonNull }?.content
        else -> null
    }
}

fun parseNotes(root: JsonElement): List<NoteInfo> = root.obj().arr("notes").objects().map {
    NoteInfo(it.str("path"), it.str("title"), it.str("folder"), it.numOrNull("updated"), it.str("preview"))
}

/** The markers of Sena's automatic block in a note (sena/memory/vault.py BEGIN and END): the server keeps everything up to END itself. */
const val NOTE_BEGIN = "<!-- sena:begin -->"
const val NOTE_END = "<!-- sena:end -->"

/**
 * Splits a note like the server does when it is written back (vault.write_note, mode replace): with both markers, the block is
 * Sena's and only what follows the end marker is the owner's; editing the whole file let one deleted marker wipe his lines or
 * turn her block into "his" facts. Without the markers the whole note is his.
 */
fun noteParts(text: String): NoteParts {
    val begin = text.indexOf(NOTE_BEGIN)
    val end = text.indexOf(NOTE_END)
    if (begin < 0 || end < begin) return NoteParts(null, text)
    val cut = end + NOTE_END.length
    return NoteParts(text.substring(0, cut), text.substring(cut).trim())
}

fun parseSkills(root: JsonElement): List<SkillInfo> = root.arr().objects().map {
    SkillInfo(it.str("name"), it.str("when"), it.bool("has_code"), it.numOrNull("updated"))
}

fun parseSearch(root: JsonElement): List<SearchHit> = root.arr().objects().map {
    SearchHit(it.str("kind"), it.str("text"), it.numOrNull("time"), it.strOrNull("ref"), it.strOrNull("path"), it.strOrNull("title"))
}

fun parseSecrets(root: JsonElement): SecretsSnapshot {
    val o = root.obj()
    val items = o.arr("items").objects().map {
        SecretInfo(it.str("name"), it.str("note"), it.arr("allow").strings(), it.str("reveal", "confirm"), it.numOrNull("t"), it.int("changed"), it.arr("sites").strings())
    }
    return SecretsSnapshot(items, o.arr("agents").strings())
}

fun parseIntegrations(root: JsonElement): Map<String, IntegrationState> = root.obj().mapNotNull { (id, v) ->
    val o = v as? JsonObject ?: return@mapNotNull null
    id to IntegrationState(
        id = id,
        enabled = o.boolOrNull("enabled"),
        usesToday = o.int("uses_today"),
        lastUsed = o.numOrNull("last_used"),
        errorsToday = o.int("errors_today"),
        lastError = o.strOrNull("last_error"),
        usdToday = o.num("usd_today"),
        canToggle = o.strOrNull("flag") != null,
        restart = o.bool("restart"),
        ready = o.bool("ready", true),
        needed = o.boolOrNull("needed"),
    )
}.toMap()

fun parseSettings(o: JsonObject): ServerSettings = ServerSettings(
    verbosity = o.str("verbosity", "low"),
    effort = o.str("effort", "low"),
    budgetUsdDay = o.num("budget_usd_day"),
    heartbeat = o.bool("heartbeat", true),
    tidy = o.bool("tidy", true),
    weekly = o.bool("weekly", true),
    voiceReply = o.bool("voice_reply", true),
    telegramNotify = o.bool("telegram_notify", true),
    taskBudgetUsd = o.num("task_budget_usd", 5.0),
)

fun parseConfig(root: JsonElement): ConfigInfo {
    val o = root.obj()
    val options = o.obj("options")
    val labels = { key: String -> options.obj(key).mapValues { (_, v) -> (v as? JsonPrimitive)?.content ?: "" } }
    // a range's upper bound (settings.RANGES on the server): [low, high]
    val most = { key: String, default: Int -> o.obj("ranges").arr(key).let { r -> (r.lastOrNull() as? JsonPrimitive)?.content?.toDoubleOrNull()?.toInt() } ?: default }
    return ConfigInfo(
        settings = parseSettings(o.obj("settings")),
        verbosityOptions = labels("verbosity"),
        effortOptions = labels("effort"),
        budgetMax = most("budget_usd_day", 1000),
        commit = o.obj("process").obj("git").strOrNull("commit"),
        coreAlive = o.obj("core").bool("alive"),
        keys = keyFlags(o.obj("keys")),
        taskBudgetMax = most("task_budget_usd", 100),
    )
}

fun parseSettingsChange(root: JsonElement): SettingsChange {
    val o = root.obj()
    val rejected = o["rejected"].let { r ->
        when (r) {
            is JsonObject -> r.map { (k, v) -> "$k: ${(v as? JsonPrimitive)?.content ?: ""}" }
            is JsonArray -> r.strings()
            else -> emptyList()
        }
    }
    return SettingsChange(parseSettings(o.obj("settings")), rejected)
}


// ---------- panels (desk/api/panels, desk/api/panel, desk/api/panel_act) ----------
fun parsePanels(root: JsonElement): List<PanelInfo> = root.obj().arr("items").objects().map {
    PanelInfo(it.str("id"), it.str("title"), it.int("badge"))
}

private fun panelAction(o: JsonObject): PanelAction {
    val input = o["input"] as? JsonObject
    return PanelAction(
        id = o.str("id"), label = o.str("label"), args = o.obj("args"), confirm = o.strOrNull("confirm"), danger = o.bool("danger"),
        input = input?.let { PanelInput(it.str("label"), it.str("kind", "text"), it.str("value"), it.bool("optional")) },
    )
}

private fun panelBlock(b: JsonObject): PanelBlock? = when (b.str("type")) {
    "stats" -> PanelBlock.Stats(b.arr("items").objects().map { PanelStat(it.str("label"), it.str("value"), it.strOrNull("tone")) })
    "text" -> PanelBlock.Note(b.str("text"), b.strOrNull("tone"))
    "section" -> PanelBlock.Section(b.str("title"))
    "items" -> PanelBlock.Items(
        b.arr("items").objects().map {
            PanelItem(it.str("id"), it.str("title"), it.strOrNull("subtitle"), it.strOrNull("meta"), it.intOrNull("progress"), it.strOrNull("tone"),
                it.arr("actions").objects().map(::panelAction))
        },
        b.str("empty"),
    )
    "form" -> PanelBlock.Form(
        b.str("id"), b.str("title"),
        b.arr("fields").objects().map { f ->
            PanelField(f.str("name"), f.str("label"), f.str("kind", "text"), f.str("value"),
                f.arr("options").objects().map { it.str("value") to it.str("label") }, f.strOrNull("placeholder"))
        },
        panelAction(b.obj("submit")),
    )
    "switch" -> PanelBlock.Toggle(b.str("label"), b.bool("value"), panelAction(b.obj("action")))
    "buttons" -> PanelBlock.Buttons(b.arr("items").objects().map(::panelAction))
    else -> null   // a block this version can't draw: skipped, the rest of the panel stays usable
}

fun parsePanel(root: JsonElement): Panel {
    val o = root.obj()
    return Panel(o.str("id"), o.str("title"), o.str("subtitle"), o.arr("blocks").objects().mapNotNull(::panelBlock))
}

fun parsePanelResult(root: JsonElement): PanelResult {
    val o = root.obj()
    return PanelResult(o.str("result"), parsePanel(o.obj("panel")))
}
