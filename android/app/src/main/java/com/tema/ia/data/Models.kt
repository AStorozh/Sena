package com.tema.ia.data

// App data models. The app has no data logic of its own: all of this comes from the Sena server on the Mac.

/** Sena's question to the owner: the oldest one waiting; [count] — how many wait in all; [hasImage] — it comes with a picture. */
data class Question(val id: String, val text: String, val hasImage: Boolean, val count: Int = 1)

/** Something prepared that waits for the owner's "yes": [name] says what it is ("Ольга", "правка сайта"), [text] what exactly. */
data class PendingSend(val id: String, val name: String, val text: String)

data class TaskStep(val who: String, val text: String, val time: Double?)

data class Comment(val text: String, val time: Double?)

enum class Verdict { Done, DoneWithRemarks, Failed, Unverified, None }

data class Outcome(val verdict: Verdict, val reason: String)

data class TaskItem(
    val id: String,          // the task's entry in the logs (tid): the same while it runs and after it ends
    val text: String,
    val start: Double,
    val end: Double?,        // empty while the task is running
    val steps: List<TaskStep>,
    val answer: String?,
    val comments: List<Comment>,
    val service: Boolean,    // a background task of Sena's own (cleanup, review)
    val cost: Double,
    val outcome: Outcome?,   // the judge's own verdict: done / with remarks / failed / unverified (honestly "not enough facts")
    val running: Boolean,
    val rating: Int? = null,   // Tema's own 1 (very bad) to 5 (very good) score, separate from the judge's verdict
    val next: String? = null,  // while running: the agent's own guess at what it'll do next, if it already knows
    val thread: String? = null,   // while running: the thread it runs in (a server that can't stop by tid stops by this)
    val title: String? = null,    // a short name the server gives the task once it is done («Пауза Red Hot Chili Peppers»)
) {
    /** What a list shows: the short name when there is one, the owner's own words otherwise (they may be just «да» or «делай»). */
    val shown: String get() = title?.takeIf { it.isNotBlank() } ?: text
}

data class TasksSnapshot(val now: Double, val active: List<TaskItem>, val finished: List<TaskItem>)

data class Overview(
    val coreAlive: Boolean,
    val question: Question?,
    val pendingSends: List<PendingSend>,
    val active: List<TaskItem>,
    val budgetLimit: Double,       // 0 means no limit
    val spentToday: Double,
    val errorsToday: Int,
    val tasksToday: Int,
    val keys: Map<String, Boolean>,
) {
    /** His own running work: Sena's service chores (the night's cleanup, the review after a task) aren't his, and "stop all" spares them. */
    val ownWork: List<TaskItem> get() = active.filter { !it.service }
}

/** One row of a cost/quality breakdown: a day, a model, or a task, with what it cost. */
data class CostBit(val key: String, val usd: Double, val calls: Int)

data class CostsReport(
    val title: String,      // the period in words, from the server ("7 дней, с 17.10")
    val total: Double,
    val calls: Int,
    val byDay: List<CostBit>,
    val byModel: List<CostBit>,
    val topTasks: List<CostBit>,   // key is the task's text here, not a model name
)

data class QualityReport(
    val days: Int,
    val tasks: Int,
    val verdicts: Map<Verdict, Int>,
    val ratingAvg: Double?,     // Tema's own average score, 1 to 5; null when nothing is rated yet
    val ratingCount: Int,
    val errors: Int,
    val repeats: List<Pair<String, Int>>,     // recurring error spots: place, how many times
    val byAgent: Map<String, Map<Verdict, Int>>,
)

data class ChatMessage(val id: String, val fromUser: Boolean, val text: String, val time: Double)

/** Status of a chat task: steps are strings shown while Sena is thinking. [tid] and [thread] name the running task, to stop it. */
data class AskStatus(
    val steps: List<String>, val answer: String?, val done: Boolean, val failed: Boolean,
    val tid: String? = null, val thread: String? = null,
)

enum class RuleKind { Rule, Job, Alert }

/** One thing Sena does on her own: a rule (with its scheduled jobs), a job without a rule, or a price alert. The screen builds the
 * words from these codes in the app's own language. */
data class RuleEntry(
    val kind: RuleKind,
    val id: String,
    val text: String,              // the rule or the job in the owner's words; for an alert, the symbol it watches
    val enabled: Boolean,
    val at: String? = null,        // when it fires next, as the server writes it: "21.10.2026 09:00"
    val repeat: String? = null,    // "daily", "weekdays", "weekly"; null — once
    val note: String? = null,      // the server's note on a rule ("без расписания")
    val fails: Int = 0,            // failures in a row; after ten of them the server switches a repeating job off
    val paused: Boolean = false,   // a rule that is on while all its jobs are off (switched off after failures)
    val last: LastRun? = null,     // a repeating job's last answer
    val alert: AlertSpec? = null,  // a price alert's condition
)

data class LastRun(val time: Double?, val text: String)

data class AlertSpec(val above: Boolean, val value: Double, val everyTime: Boolean)

data class FactSection(val title: String, val items: List<String>)

/** A fact as the owner reads it: Sena writes her own with a stamp in front ("24.10.2026 14:03: …"), shown apart from the words. */
data class FactLine(val stamp: String?, val text: String)

data class Person(val id: String, val name: String, val relationship: String, val opinion: String, val aliases: List<String>, val facts: List<String>)

data class NoteInfo(val path: String, val title: String, val folder: String, val updated: Double?, val preview: String)

/**
 * A note as the owner edits it: [auto] — Sena's own block of a note she keeps (people, memory, the diary), up to and with its end
 * marker, shown but never edited; [own] — the owner's part after it (the whole note when there is no block).
 */
data class NoteParts(val auto: String?, val own: String)

data class SkillInfo(val name: String, val whenText: String, val hasCode: Boolean, val updated: Double?)

data class SkillDetail(val name: String, val whenText: String, val recipe: String)

/**
 * A search hit: the registry's [kind] and [ref] say where it lives (a task's id, a person's "id" or "id:fact", a skill's name,
 * a message's "chat:id"); a note (or a project, an organization, a place kept as a note) also comes with its [path] and [title].
 */
data class SearchHit(val kind: String, val text: String, val time: Double?, val ref: String? = null, val path: String? = null, val title: String? = null)

/** A secret as the keeper describes it: never the value, only who may use it and whether the owner may see it. */
data class SecretInfo(
    val name: String, val note: String, val allow: List<String>, val reveal: String, val time: Double?, val changed: Int,
    val sites: List<String> = emptyList(),   // the hosts a password may be typed on; empty — any site
)

data class SecretsSnapshot(val items: List<SecretInfo>, val agents: List<String>)

/** The registry of memory in numbers: records by kind, embeddings, versions, links, and how close it is to needing a faster vector index. */
data class RegistryStats(
    val items: Map<String, Int>,   // kind (the server's word: человек, факт, задача...) -> records
    val vectors: Int,
    val versions: Int,
    val links: Int,
    val pending: Int,              // records still waiting for an embedding
    val fileMb: Double,
    val matrixMb: Double,
    val searchMs: Double,          // the last search
    val annVectors: Int,           // the seam opens past these two numbers
    val annSearchMs: Double,
) {
    val total: Int get() = items.values.sum()
    val headroom: Int get() = annVectors - vectors
    val overThreshold: Boolean get() = vectors > annVectors || searchMs > annSearchMs
}

/** One job of Sena and where it works now: at home (on the Mac, nothing leaves it) or in the cloud. A switchable role the owner can move. */
data class RoleInfo(
    val role: String?,             // null — a fixed job (the brain, agents...): shown, not moved
    val title: String,
    val place: String,             // "home" or "cloud"
    val model: String,
    val why: String,
    val switchable: Boolean,
    val learning: Boolean,         // a cloud job that collects examples to move home once the home model matches it
    val examples: Int?,
) {
    val atHome: Boolean get() = place == "home"
}

/** The home and the cloud in numbers: who does what, how many calls stayed home today. */
data class HomeReport(
    val roles: List<RoleInfo>,
    val homeToday: Int,
    val homeByService: Map<String, Int>,
    val outToday: Int,
    val usdToday: Double,
    val minExamples: Int,
) {
    val switchable: List<RoleInfo> get() = roles.filter { it.switchable }
    val fixed: List<RoleInfo> get() = roles.filter { !it.switchable }
}

data class IntegrationState(
    val id: String,
    val enabled: Boolean?,      // null means it's part of Sena herself and can't be turned off
    val usesToday: Int,
    val lastUsed: Double?,
    val errorsToday: Int,
    val lastError: String?,
    val usdToday: Double,
    val canToggle: Boolean,
    val restart: Boolean = false,   // switching it on takes effect at Sena's next start (the Telegram account)
    val ready: Boolean = true,      // what it needs is on the Mac (the server checks its files and programs): false — not installed
    val needed: Boolean? = null,    // anything of Sena's uses it now; false — without its key it is simply not used. null — the server didn't say
)

/** Sena's settings, as returned by the server. Until the app changes them, the values stay as received. */
data class ServerSettings(
    val verbosity: String,
    val effort: String,
    val budgetUsdDay: Double,       // dollars a day, fractions allowed; 0 — no limit
    val heartbeat: Boolean,
    val tidy: Boolean,
    val weekly: Boolean,
    val voiceReply: Boolean,        // Telegram only: a voice message is answered with voice
    val telegramNotify: Boolean = true,   // her own, unprompted messages to the Telegram bot (night check, reminders, notices)
    val taskBudgetUsd: Double = 5.0,   // one task never spends more than this (the brain sums up and stops); 0 — no brake
)

data class ConfigInfo(
    val settings: ServerSettings,
    val verbosityOptions: Map<String, String>,   // the server's choices, value to its own (Russian) label
    val effortOptions: Map<String, String>,
    val budgetMax: Int,
    val commit: String?,
    val coreAlive: Boolean,
    val keys: Map<String, Boolean> = emptyMap(),   // which keys are set in .env
    val taskBudgetMax: Int = 100,
)

data class SettingsChange(val settings: ServerSettings, val rejected: List<String>)

data class ActionResult(val message: String, val handover: Boolean = false)

// ---------- panels the server describes (sena/phone/panels.py): the app only draws them ----------
data class PanelInfo(val id: String, val title: String, val badge: Int)

data class Panel(val id: String, val title: String, val subtitle: String, val blocks: List<PanelBlock>)

/** One block of a panel. A type this version doesn't know is skipped: a newer server never breaks an older app. */
sealed interface PanelBlock {
    data class Stats(val items: List<PanelStat>) : PanelBlock
    data class Note(val text: String, val tone: String?) : PanelBlock
    data class Section(val title: String) : PanelBlock
    data class Items(val items: List<PanelItem>, val empty: String) : PanelBlock
    data class Form(val id: String, val title: String, val fields: List<PanelField>, val submit: PanelAction) : PanelBlock
    data class Toggle(val label: String, val value: Boolean, val action: PanelAction) : PanelBlock
    data class Buttons(val items: List<PanelAction>) : PanelBlock
}

data class PanelStat(val label: String, val value: String, val tone: String?)

data class PanelItem(
    val id: String,
    val title: String,
    val subtitle: String?,
    val meta: String?,
    val progress: Int?,          // 0–100 or none
    val tone: String?,           // "muted" | "good" | "warn" | "bad"
    val actions: List<PanelAction>,
)

data class PanelField(
    val name: String,
    val label: String,
    val kind: String,            // "text" | "multiline" | "number" | "choice"
    val value: String,
    val options: List<Pair<String, String>>,   // value to label, for "choice"
    val placeholder: String?,
)

/** One value an action asks for first; [optional] — an empty answer is a real answer too ("resume with no note", "no tool asks"). */
data class PanelInput(val label: String, val kind: String, val value: String, val optional: Boolean = false) {
    fun accepts(text: String): Boolean = optional || text.isNotBlank()
}

data class PanelAction(
    val id: String,
    val label: String,
    val args: kotlinx.serialization.json.JsonObject,
    val confirm: String?,        // a question the app asks before doing it
    val danger: Boolean,
    val input: PanelInput?,      // one value the app asks for first
)

data class PanelResult(val message: String, val panel: Panel)
