package com.tema.ia.data

import kotlinx.coroutines.delay
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.doubleOrNull
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter

/**
 * Fake server: lets you try the app without a Mac and lets on-device tests run without a network.
 * All data is made up; nothing here is personal.
 */
class DemoApi(
    private var coreAlive: Boolean = true,
    private val latencyMs: Long = 250,
    /** Tests construct DemoApi for a small, exact fixture; the app's own demo mode asks for the full one, so every screen has enough to show its design. */
    private val rich: Boolean = false,
    private val clock: () -> Double = { System.currentTimeMillis() / 1000.0 },
) : SenaApi {
    private val now get() = clock()
    private var chat = mutableListOf(
        ChatMessage("d1", true, "Привет! Что у меня сегодня?", stamp(-3600 * 5)),
        ChatMessage("d2", false, "Привет. На сегодня два дела: созвон в 15:00 и подготовить отчёт. Билеты в Лиссабон подешевели до 142 долларов.", stamp(-3600 * 5 + 20)),
        ChatMessage("d3", true, "Напомни про отчёт за час до конца дня.", stamp(-3600 * 4)),
        ChatMessage("d4", false, "Записала: напомню в 17:00.", stamp(-3600 * 4 + 12)),
    ).apply {
        if (rich) {
            addAll(0, listOf(
                ChatMessage("d0a", true, "Доброе утро! Что по плану на сегодня?", stamp(-3600 * 30)),
                ChatMessage("d0b", false, "Доброе утро. Сегодня: созвон в 15:00, подготовить отчёт, забрать документы у Ольги.", stamp(-3600 * 30 + 15)),
            ))
            addAll(listOf(
                ChatMessage("d5", true, "Что нового по вакансиям курьера?", stamp(-1800)),
                ChatMessage(
                    "d6", false,
                    "Нашла три варианта рядом с домом:\n- Яндекс.Еда, свободный график\n- СДЭК, утренние смены\n- Самокат, нужен велосипед\n\n**Самая высокая ставка** у Самоката.",
                    stamp(-1700),
                ),
                ChatMessage("d7", true, "Отлично, спасибо!", stamp(-1600)),
            ))
        }
    }
    private var question: Question? = if (rich) Question("q1", "Можно заказать столик на двоих сегодня в 19:00 в «Таверне»?", false) else null
    private var sends = mutableListOf(PendingSend("s1", "Ольга", "Буду в пятницу, привезу документы.")).apply {
        if (rich) add(PendingSend("s2", "Игорь", "Макет главной страницы готов, гляну сегодня вечером."))
    }
    private val asks = mutableMapOf<String, Pair<String, Long>>()
    private var running = (
        if (!rich) emptyList()
        else listOf(
            TaskItem(
                id = "r1", text = "Собери сводку новостей за утро", start = now - 90, end = null,
                steps = listOf(
                    TaskStep("ag_sena", "Принято, передаю в веб-агента", now - 90),
                    TaskStep("ag_web", "Открываю Hacker News и Hacker Weekly", now - 40),
                ),
                answer = null, comments = emptyList(), service = false, cost = 0.004, outcome = null, running = true,
                next = "Сравню с утренней сводкой за вчера",
            ),
            TaskItem(
                id = "r2", text = "Проверь цены на отель в Порту на выходные", start = now - 20, end = null,
                steps = listOf(TaskStep("ag_sena", "Принято, передаю в веб-агента", now - 20)),
                answer = null, comments = emptyList(), service = false, cost = 0.001, outcome = null, running = true,
                next = "Открою Booking и сравню с прошлым разом",
            ),
        )
    ).toMutableList()
    private var finished = mutableListOf(
        task("t1", "Что сейчас обсуждают на Hacker News? Топ-3.", -3600 * 2, 41, 0.012, "Топ-3: 1. Новый формат сжатия для карт. 2. Как устроена очередь сообщений в SQLite. 3. Спор о длине рабочей недели.", Verdict.Done).copy(rating = 5),
        task("t2", "Проверь цены на билеты в Лиссабон на май.", -3600 * 6, 95, 0.031, "Самый дешёвый перелёт 12 мая за 142 доллара, с одной пересадкой. Сигнал на цену ниже 150 включён.", Verdict.DoneWithRemarks).copy(rating = 4),
        task("t3", "Ночная уборка памяти и навыков", -3600 * 9, 12, 0.010, "Ничего менять не нужно.", Verdict.None, service = true),
        task("t4", "Найди вакансии курьера рядом с домом.", -3600 * 20, 140, 0.054, "Не вышло открыть сайт: он просил войти. Попробую после твоего входа.", Verdict.Failed).copy(rating = 2),
    ).apply {
        if (rich) addAll(listOf(
            task("t5", "Собери статистику по трафику сайта-портфолио за неделю.", -3600 * 27, 63, 0.021, "Заходов 214, из них 38 на страницу контактов. Пик в среду.", Verdict.Unverified),
            task("t6", "Напиши короткое поздравление Игорю с днём рождения.", -3600 * 31, 8, 0.004, "Игорь, с днём рождения! Пусть проект сайта запустится без единого бага.", Verdict.Done).copy(rating = 5),
            task("t7", "Проверь курс биткоина и сравни с вчерашним.", -3600 * 46, 5, 0.002, "90 400 долларов, плюс 1,2% за сутки.", Verdict.Done).copy(rating = 4),
            task("t8", "Забронируй столик в «Таверне» на пятницу.", -3600 * 70, 22, 0.006, "Не нашла свободных мест на сайте ресторана, предложила написать им в Телеграм напрямую.", Verdict.Failed),
        ))
    }
    private var rules = mutableListOf(
        RuleEntry(RuleKind.Rule, "r1", "Каждое утро присылай сводку новостей", true, at = "21.10.2026 09:00", repeat = "daily",
            last = LastRun(now - 3600 * 3, "Главное за ночь: новый формат сжатия карт, спор о длине рабочей недели.")),
        RuleEntry(RuleKind.Rule, "r2", "Когда прошу сайт, сначала собери требования", true, note = "без расписания"),
        RuleEntry(RuleKind.Job, "j1", "Напомнить про отчёт", true, at = "20.10.2026 17:00"),
        RuleEntry(RuleKind.Alert, "a1", "BTC", false, alert = AlertSpec(above = true, value = 90_000.0, everyTime = false)),
    ).apply {
        if (rich) addAll(listOf(
            RuleEntry(RuleKind.Rule, "r3", "Не звони и не пиши в мессенджеры до 10 утра", true, note = "без расписания"),
            RuleEntry(RuleKind.Job, "j2", "Собрать недельную сводку расходов", true, at = "27.10.2026 09:00", repeat = "weekly"),
            RuleEntry(RuleKind.Job, "j3", "Проверить обновления на сайте hh.ru", false, at = "24.10.2026 12:00", repeat = "daily", fails = 10),
            RuleEntry(RuleKind.Alert, "a2", "TP-LIS", true, alert = AlertSpec(above = false, value = 150.0, everyTime = true)),
        ))
    }
    private var sections = mutableListOf(
        FactSection("Обо мне", listOf("Живу в Лиссабоне", "Работаю удалённо, дизайнер", "Не люблю звонки утром")),
        FactSection("Люди", listOf("Ольга — сестра, присылает документы по пятницам", "Игорь — коллега, ведёт проект сайта")),
        FactSection("Проекты", listOf("Сайт-портфолио: обновить раздел с работами", "Поездка в мае")),
    ).apply {
        if (rich) {
            this[0] = this[0].copy(items = this[0].items + "Встаю рано, лучше работаю до обеда")
            this[1] = this[1].copy(items = this[1].items + "Марина — клиент, портфолио для неё")
            this[2] = this[2].copy(items = this[2].items + "Отчёт по расходам — каждый понедельник")
            add(FactSection("Привычки", listOf("Кофе без сахара", "Читаю новости вечером, не утром", "Спортзал по вторникам и четвергам")))
        }
    }
    private var people = (
        listOf(
            Person("p1", "Ольга", "сестра", "Надёжная, отвечает быстро", listOf("Оля"), listOf("Живёт в Порту", "Любит кофе без сахара")),
            Person("p2", "Игорь", "коллега", "", emptyList(), listOf("Ведёт проект сайта")),
        ) + if (!rich) emptyList() else listOf(
            Person("p3", "Марина", "клиент", "Платит вовремя, любит подробные отчёты", emptyList(), listOf("Заказала обновление портфолио")),
            Person("p4", "Саша", "друг", "", listOf("Александр"), listOf("Собираемся в Лиссабон вместе в мае")),
        )
    ).toMutableList()
    private var notes = mutableListOf(
        NoteInfo("Идеи/Сайт.md", "Сайт", "Идеи", now - 3600 * 3, "Обновить раздел с работами, добавить три новых кейса и страницу контактов."),
        NoteInfo("Дневник/2026-10-19.md", "2026-10-19", "Дневник", now - 3600 * 20, "Задач: 12. Главное: билеты, сводка новостей, вакансии."),
        NoteInfo("Проекты/Поездка.md", "Поездка", "Проекты", now - 86400 * 3, "Лиссабон, май. Билеты, жильё рядом с центром, маршрут по району Алфама."),
    ).apply {
        if (rich) {
            add(0, NoteInfo("Дневник/2026-10-23.md", "2026-10-23", "Дневник", now - 1800, "Задач: 4. Главное: отчёт, созвон, документы от Ольги."))
            add(NoteInfo("Проекты/Портфолио.md", "Портфолио", "Проекты", now - 86400 * 9, "Три новых кейса, страница контактов, обновить шрифты."))
        }
    }
    private val noteTexts = mutableMapOf(
        "Идеи/Сайт.md" to "# Сайт\n\nОбновить раздел с работами, добавить три новых кейса и страницу контактов.\n\n- Кейс 1: приложение для заметок\n- Кейс 2: фирменный стиль кофейни",
        "Дневник/2026-10-19.md" to "# 2026-10-19\n\nЗадач: 12. Главное: билеты, сводка новостей, вакансии.",
        "Проекты/Поездка.md" to "# Поездка\n\nЛиссабон, май. Билеты, жильё рядом с центром, маршрут по району Алфама.",
    ).apply {
        if (rich) {
            put("Дневник/2026-10-23.md", "# 2026-10-23\n\nЗадач: 4. Главное: отчёт, созвон, документы от Ольги.")
            put("Проекты/Портфолио.md", "# Портфолио\n\nТри новых кейса, страница контактов, обновить шрифты.")
        }
    }
    private var skills = (
        listOf(
            SkillInfo("mac-volume", "Громкость звука на Mac: узнать или поменять.", false, now - 86400 * 5),
            SkillInfo("youtube-play", "Включить музыку или видео на YouTube и убедиться, что играет.", true, now - 86400),
        ) + if (!rich) emptyList() else listOf(
            SkillInfo("hh-apply", "Откликнуться на вакансию на hh.ru и приложить сопроводительное письмо.", true, now - 86400 * 2),
            SkillInfo("calendar-add", "Добавить встречу в календарь Mac с напоминанием.", false, now - 86400 * 12),
            SkillInfo("telegram-forward", "Переслать сообщение из Телеграма нужному человеку.", true, now - 86400 * 30),
        )
    ).toMutableList()
    private val skillRecipes = mutableMapOf<String, String>()
    private var secrets = mutableListOf(
        SecretInfo("сервер", "вход на сервер KSclay по ssh", listOf("ag_mac"), "confirm", now - 86400 * 3, 1),
        SecretInfo("почта-рабочая", "пароль от рабочей почты", listOf("ag_web"), "confirm", now - 86400 * 20, 0, listOf("mail.google.com")),
    ).apply { if (rich) add(SecretInfo("банк", "приложение банка", emptyList(), "never", now - 86400 * 40, 0)) }
    private var enabled = mutableMapOf("browser" to true, "google" to true, "gemini" to true, "maps" to true, "travel" to true,
        "luma" to true, "site" to true, "github" to true, "hireflow" to false, "youtube" to true, "claude" to true, "telegram_account" to true, "hearing" to true,
        "mcp" to true)
    private var settings = ServerSettings("low", "low", 0.0, heartbeat = true, tidy = true, weekly = true, voiceReply = true)

    private fun stamp(offsetSeconds: Long): Double = now + offsetSeconds

    private fun task(id: String, text: String, startOffset: Long, seconds: Int, cost: Double, answer: String, verdict: Verdict, service: Boolean = false): TaskItem {
        val start = now + startOffset
        return TaskItem(
            id = id, text = text, start = start, end = start + seconds,
            steps = listOf(TaskStep("ag_sena", "Принято", start), TaskStep("ag_web", "Открываю страницу", start + 5), TaskStep("ag_sena", "Готово", start + seconds)),
            answer = answer, comments = emptyList(), service = service, cost = cost,
            outcome = if (verdict == Verdict.None) null else Outcome(verdict, ""), running = false,
        )
    }

    private suspend fun pause() = delay(latencyMs)

    // ---------- Home ----------

    override suspend fun overview(): Overview {
        pause()
        return Overview(coreAlive, question, sends.toList(), running.toList(), 5.0, 0.12 + finished.size * 0.01, if (rich) 1 else 0, finished.size + running.size,
            mapOf("SENA_CLAUDE_BRAIN_KEY" to true, "SENA_CLAUDE_AGENTS_KEY" to true, "GEMINI_API_KEY" to true, "OPENAI_API_KEY" to false))
    }

    override suspend fun coreStart(): ActionResult {
        pause(); coreAlive = true
        return ActionResult("Запускаю Сену", handover = true)
    }

    override suspend fun coreStop(): ActionResult {
        pause(); coreAlive = false; running.clear()
        return ActionResult("Останавливаю Сену", handover = true)
    }

    override suspend fun stopAll(): ActionResult {
        pause(); running.clear()
        return ActionResult("Остановила все задачи")
    }

    override suspend fun answer(text: String, id: String?): ActionResult {
        pause()
        if (id != null && question?.id != id) throw ApiException(ApiError.Server, "Сена сейчас ничего не спрашивает")
        question = null
        return ActionResult("Ответ передан Сене")
    }

    override suspend fun confirmSend(id: String): ActionResult {
        pause(); sends.removeAll { it.id == id }
        return ActionResult("Отправлено")
    }

    override suspend fun cancelSend(id: String): ActionResult {
        pause(); sends.removeAll { it.id == id }
        return ActionResult("Отменено")
    }

    // ---------- Chat ----------

    override suspend fun history(): List<ChatMessage> { pause(); return chat.toList() }

    override suspend fun ask(text: String): String {
        pause()
        if (!coreAlive) throw ApiException(ApiError.Server, "Сена выключена: ядро не запущено")
        chat += ChatMessage("u${chat.size}", true, text, stamp(0))
        val id = "ask${asks.size}"
        asks[id] = text to System.currentTimeMillis()
        val t = TaskItem(id = "9${asks.size}", text = text, start = now, end = null, steps = emptyList(), answer = null, comments = emptyList(),
            service = false, cost = 0.0, outcome = null, running = true)
        running += t
        return id
    }

    override suspend fun askStatus(id: String): AskStatus {
        delay(120)
        val (text, started) = asks[id] ?: return AskStatus(emptyList(), "Задача потерялась", done = true, failed = true)
        val age = System.currentTimeMillis() - started
        return when {
            age < 900 -> AskStatus(listOf("Принято"), null, done = false, failed = false)
            age < 1800 -> AskStatus(listOf("Принято", "Ищу ответ"), null, done = false, failed = false)
            else -> {
                val reply = "Готово. Вот что я нашла по запросу «${text.take(40)}»: ничего срочного, всё спокойно."
                if (chat.none { it.text == reply }) chat += ChatMessage("a${chat.size}", false, reply, stamp(0))
                running.removeAll { it.text == text }
                AskStatus(listOf("Принято", "Ищу ответ", "Готово"), reply, done = true, failed = false)
            }
        }
    }

    override suspend fun stopTask(id: String): ActionResult {
        pause(); running.removeAll { it.id == id }
        return ActionResult("Остановила задачу")
    }

    override suspend fun clearHistory(): ActionResult {
        pause(); chat.clear()
        return ActionResult("Переписка очищена")
    }

    // ---------- Tasks ----------

    override suspend fun tasks(): TasksSnapshot { pause(); return TasksSnapshot(now, running.toList(), finished.toList()) }

    override suspend fun task(id: String): TaskItem {
        pause()
        return (running + finished).firstOrNull { it.id == id } ?: throw ApiException(ApiError.Server, "Такой задачи нет")
    }

    override suspend fun comment(id: String, text: String): ActionResult {
        pause()
        finished = finished.map { if (it.id == id) it.copy(comments = it.comments + Comment(text, now)) else it }.toMutableList()
        return ActionResult("Замечание записано")
    }

    override suspend fun rate(id: String, rating: Int): ActionResult {
        pause()
        finished = finished.map { if (it.id == id) it.copy(rating = rating.coerceIn(1, 5)) else it }.toMutableList()
        return ActionResult("Оценка записана")
    }

    // ---------- Rules ----------

    override suspend fun rules(): List<RuleEntry> { pause(); return rules.toList() }

    override suspend fun addRule(text: String): ActionResult {
        pause(); rules.add(0, RuleEntry(RuleKind.Rule, "r${rules.size + 10}", text, true, null, "без расписания"))
        return ActionResult("Правило записано")
    }

    override suspend fun setRuleEnabled(entry: RuleEntry, enabled: Boolean): ActionResult {
        pause(); rules = rules.map { if (it.id == entry.id) it.copy(enabled = enabled) else it }.toMutableList()
        return ActionResult(if (enabled) "Включено" else "Выключено")
    }

    override suspend fun removeRule(entry: RuleEntry): ActionResult {
        pause(); rules.removeAll { it.id == entry.id }
        return ActionResult("Удалено")
    }

    // ---------- Memory ----------

    override suspend fun facts(): List<FactSection> { pause(); return sections.toList() }

    override suspend fun addFact(section: String, text: String): ActionResult {
        pause()
        sections = (if (sections.any { it.title == section }) sections.map { if (it.title == section) it.copy(items = it.items + text) else it }
            else sections + FactSection(section, listOf(text))).toMutableList()
        return ActionResult("Запомнила")
    }

    override suspend fun editFact(section: String, old: String, text: String): ActionResult {
        pause()
        sections = sections.map { s -> if (s.title != section) s else s.copy(items = s.items.map { if (it == old) text else it }) }.toMutableList()
        return ActionResult("Исправила")
    }

    override suspend fun deleteFact(section: String, text: String): ActionResult {
        pause()
        sections = sections.map { s -> if (s.title != section) s else s.copy(items = s.items.toMutableList().also { it.remove(text) }) }.toMutableList()
        return ActionResult("Забыла")
    }

    override suspend fun people(): List<Person> { pause(); return people.toList() }

    override suspend fun addPerson(name: String, aliases: List<String>, relationship: String, opinion: String, facts: List<String>): ActionResult {
        pause()
        val id = "p" + (people.size + 10)
        people.add(0, Person(id, name, relationship, opinion, aliases.filter { it.isNotBlank() }.distinct(), facts))
        return ActionResult("Добавлено")
    }

    override suspend fun updatePerson(id: String, name: String, aliases: List<String>, relationship: String, opinion: String, facts: List<String>, retire: List<String>): ActionResult {
        pause()
        people = people.map { p ->
            if (p.id != id) p else Person(id, name.ifBlank { p.name }, relationship, opinion, aliases, (p.facts + facts).distinct().filterNot { it in retire })
        }.toMutableList()
        return ActionResult("Обновлено")
    }

    override suspend fun deletePerson(id: String): ActionResult {
        pause(); people.removeAll { it.id == id }
        return ActionResult("Удалено")
    }

    override suspend fun notes(): List<NoteInfo> { pause(); return notes.toList() }

    override suspend fun noteText(path: String): String { pause(); return noteTexts[path] ?: throw ApiException(ApiError.Server, "Заметки нет") }

    override suspend fun writeNote(path: String, text: String, mode: String): ActionResult {
        pause()
        if (mode == "replace" && noteTexts.containsKey(path)) {
            noteTexts[path] = text
            notes = notes.map { if (it.path == path) it.copy(preview = text.take(120)) else it }.toMutableList()
            return ActionResult("Заметка заменена")
        }
        notes.add(0, NoteInfo(path, path.substringAfterLast('/').removeSuffix(".md"), path.substringBeforeLast('/', ""), now, text.take(120)))
        noteTexts[path] = text
        return ActionResult("Создана заметка")
    }

    override suspend fun deleteNote(path: String): ActionResult {
        pause(); notes.removeAll { it.path == path }; noteTexts.remove(path)
        return ActionResult("Заметка удалена")
    }

    override suspend fun skills(): List<SkillInfo> { pause(); return skills.toList() }

    override suspend fun skill(name: String): SkillDetail {
        pause()
        val s = skills.firstOrNull { it.name == name } ?: throw ApiException(ApiError.Server, "Навыка нет")
        return SkillDetail(s.name, s.whenText, skillRecipes[name] ?: "1. Открыть нужную программу.\n2. Выполнить действие.\n3. Проверить результат и сообщить.")
    }

    override suspend fun writeSkill(name: String, whenText: String, recipe: String): ActionResult {
        pause()
        skillRecipes[name] = recipe
        skills = if (skills.any { it.name == name }) {
            skills.map { if (it.name == name) it.copy(whenText = whenText, updated = now) else it }.toMutableList()
        } else {
            (skills + SkillInfo(name, whenText, false, now)).toMutableList()
        }
        return ActionResult("Навык сохранён")
    }

    override suspend fun deleteSkill(name: String): ActionResult {
        pause(); skills.removeAll { it.name == name }; skillRecipes.remove(name)
        return ActionResult("Навык удалён")
    }

    // ---------- Secrets (no real values anywhere in the demo) ----------

    override suspend fun registry(): RegistryStats {
        pause()
        val items = linkedMapOf("сообщение" to 1842, "задача" to 236, "факт" to 61, "человек" to 19, "заметка" to 133, "файл" to 48, "услышано" to 410, "навык" to 7, "правило" to 5, "проект" to 3)
        if (rich) items["организация"] = 2
        return RegistryStats(items, vectors = 2330, versions = 57, links = 612, pending = 0, fileMb = 14.6, matrixMb = 4.8, searchMs = 6.2,
                             annVectors = 300_000, annSearchMs = 50.0)
    }

    // ---------- The home and the cloud ----------

    private val places = linkedMapOf("embed" to "home", "hearing" to "home", "ears" to "home", "heartbeat" to "home", "compact" to "cloud", "harvest" to "cloud",
        "router" to "cloud", "rules" to "cloud", "feedback" to "cloud")

    override suspend fun home(): HomeReport {
        pause()
        val titles = mapOf("embed" to "Отпечатки памяти", "hearing" to "Голос владельца в текст", "ears" to "Разбор подслушанного",
            "heartbeat" to "Проверка «есть ли важное»", "compact" to "Сводка старой переписки", "harvest" to "Факты, сказанные мимоходом",
            "router" to "Выбор готового рецепта", "rules" to "Время в правиле (что не разобрал код)", "feedback" to "Замечание к прошлому ответу")
        val homeModels = mapOf("embed" to "embeddinggemma-300M-Q8_0.gguf", "hearing" to "ggml-large-v3-turbo-q5_0.bin")
        val cloudModels = mapOf("embed" to "text-embedding-3-small", "hearing" to "gemini-3.5-transcribe")
        val switchable = places.map { (role, place) ->
            val learning = role in setOf("compact", "harvest", "router", "rules", "feedback")
            RoleInfo(role, titles.getValue(role), place, if (place == "home") homeModels[role] ?: "Qwen3.5-4B-Q4_K_M.gguf" else cloudModels[role] ?: "claude-haiku-5-5",
                if (place == "home") "работает на этом Mac" else "ждёт, пока домашняя модель догонит", true, learning, if (learning) 12 else null)
        }
        val fixed = listOf(
            RoleInfo(null, "Мозг: понимает, решает, поручает, проверяет", "cloud", "claude-opus-5-5", "нужен самый большой ум", false, false, null),
            RoleInfo(null, "Агенты: браузер и компьютер", "cloud", "claude-haiku-5-5", "скриншоты, длинные страницы, много шагов", false, false, null),
            RoleInfo(null, "Голос Сены", "cloud", "gemini-3.8-flash-tts · Kore", "один голос везде: Телеграм, телефон, Mac", false, false, null),
            RoleInfo(null, "Чтение документов и фото", "home", "macOS Vision, PDFKit", "текст PDF, сканов и снимков читается на Mac", false, false, null),
        )
        return HomeReport(switchable + fixed,
            homeToday = 148, homeByService = mapOf("embeddings" to 96, "chat" to 9, "transcribe" to 31, "read" to 12), outToday = 23, usdToday = 0.41, minExamples = 40)
    }

    override suspend fun moveRole(role: String, place: String): ActionResult {
        pause()
        if (role !in places || place !in setOf("home", "cloud")) throw ApiException(ApiError.Server, "Нет такой роли")
        places[role] = place
        return ActionResult("Готово: теперь ${if (place == "home") "дома" else "в облаке"}")
    }

    // ---------- Panels: in demo mode the app draws the same kind of panels the server describes ----------

    private class DemoGoal(val id: String, val title: String, val deadline: String, val steps: MutableList<Pair<String, Boolean>>, var note: String?, var open: Boolean = true)
    private val demoGoals = mutableListOf(
        DemoGoal("g1", "Английский до B2", "01.05.2027", mutableListOf("Записаться на курс" to true, "Разговорный клуб по средам" to false, "Пробный тест IELTS" to false), "был на первом клубе"),
        DemoGoal("g2", "Запустить каталог KSclay", "к декабрю", mutableListOf("Снять фото изделий" to false, "Собрать карточки товаров" to false), null),
    )
    private val demoLessons = mutableListOf(
        Triple("l1", "На hh.ru вход во вложенном окне: кнопка «Войти» внутри него", "hh.ru · подтверждён 3 раза · разбор"),
        Triple("l2", "Фильтр «удалённо» на hh.ru сбрасывается при смене города", "hh.ru · черновик · сам агент"),
    )

    private fun act(id: String, label: String, vararg args: Pair<String, String>, confirm: String? = null, danger: Boolean = false, input: PanelInput? = null) =
        PanelAction(id, label, kotlinx.serialization.json.JsonObject(args.associate { (k, v) -> k to kotlinx.serialization.json.JsonPrimitive(v) }), confirm, danger, input)

    private fun progress(g: DemoGoal) = if (g.steps.isEmpty()) 0 else 100 * g.steps.count { it.second } / g.steps.size

    private fun goalsPanel(): Panel {
        val open = demoGoals.filter { it.open }
        val items = open.map { g ->
            val next = g.steps.firstOrNull { !it.second }?.first
            PanelItem(g.id, g.title, "ход ${progress(g)}%" + (next?.let { ", следующий шаг: $it" } ?: "") + (g.note?.let { " · $it" } ?: ""), "срок ${g.deadline}", progress(g), null,
                listOfNotNull(next?.let { act("goal_step_done", "Сделано: $it", "id" to g.id) },
                    act("goal_note", "Как идёт", "id" to g.id, input = PanelInput("Как идёт? Своими словами", "multiline", "")),
                    act("goal_close", "Достигнута", "id" to g.id, "status" to "done")))
        }
        val form = PanelBlock.Form("goal_add", "Новая цель", listOf(
            PanelField("title", "Цель", "text", "", emptyList(), "Английский до B2"),
            PanelField("deadline", "Срок", "text", "", emptyList(), "01.05.2027 или «к маю»"),
            PanelField("steps", "Первые шаги, по строке на шаг", "multiline", "", emptyList(), null)), act("goal_add", "Добавить"))
        return Panel("goals", "Цели", "то, к чему ты идёшь неделями: Сена напоминает следующий шаг",
            listOf(PanelBlock.Stats(listOf(PanelStat("в работе", open.size.toString(), null), PanelStat("достигнуто", demoGoals.count { !it.open }.toString(), "good"))),
                PanelBlock.Items(items, "Целей пока нет"), form))
    }

    private fun lessonsPanel(): Panel = Panel("lessons", "Память агентов", "черновиков ждут разбора: ${demoLessons.count { "черновик" in it.third }}", listOf(
        PanelBlock.Note("Короткие уроки каждого агента: пишет судья после задач, черновики ждут его подтверждения.", "muted"),
        PanelBlock.Section("Браузер (${demoLessons.size})"),
        PanelBlock.Items(demoLessons.map { (id, text, sub) ->
            PanelItem(id, text, sub, null, null, if ("черновик" in sub) "warn" else null,
                listOf(act("lesson_edit", "Исправить", "id" to id, input = PanelInput("Урок", "multiline", text)),
                    act("lesson_retire", "Убрать", "id" to id, confirm = "Убрать урок? Он уйдёт в архив агента", danger = true)))
        }, "Уроков пока нет")))

    private fun examPanel(): Panel = Panel("exam", "Экзамен ума", "проверка на настоящих просьбах", listOf(
        PanelBlock.Stats(listOf(PanelStat("сдано", "48 из 49", null), PanelStat("стоил", "0,94 $", null), PanelStat("когда", "24.10.2026", null))),
        PanelBlock.Items(listOf(PanelItem("мир", "мир", "8 из 9", null, 89, "warn", emptyList()), PanelItem("сообщения", "сообщения", "4 из 4", null, 100, null, emptyList())), ""),
        PanelBlock.Toggle("Раз в неделю, после недельного разбора", true, act("exam_weekly", "Раз в неделю")),
        PanelBlock.Buttons(listOf(act("exam_run", "Запустить экзамен", confirm = "Экзамен на настоящей модели: около 1 $. Запустить?")))))

    override suspend fun panels(): List<PanelInfo> {
        pause()
        return listOf(PanelInfo("goals", "Цели", 0), PanelInfo("lessons", "Память агентов", demoLessons.count { "черновик" in it.third }), PanelInfo("exam", "Экзамен ума", 0))
    }

    override suspend fun panel(id: String): Panel {
        pause()
        return when (id) {
            "goals" -> goalsPanel()
            "lessons" -> lessonsPanel()
            "exam" -> examPanel()
            else -> throw ApiException(ApiError.Server, "Такой панели нет")
        }
    }

    override suspend fun panelAct(panel: String, action: PanelAction, values: kotlinx.serialization.json.JsonObject): PanelResult {
        pause()
        val id = action.args.str("id")
        val message = when (action.id) {
            "goal_step_done" -> demoGoals.firstOrNull { it.id == id }?.let { g ->
                val i = g.steps.indexOfFirst { !it.second }
                if (i >= 0) g.steps[i] = g.steps[i].first to true
                "Отмечено: ход ${progress(g)}%"
            } ?: "Цели нет"
            "goal_note" -> { demoGoals.firstOrNull { it.id == id }?.note = values.str("value"); "Записала" }
            "goal_close" -> { demoGoals.firstOrNull { it.id == id }?.open = false; "Цель достигнута" }
            "goal_add" -> {
                val title = values.str("title").trim()
                if (title.isEmpty()) throw ApiException(ApiError.Server, "Назови цель одной фразой")
                demoGoals += DemoGoal("g${demoGoals.size + 1}", title, values.str("deadline").ifBlank { "без срока" },
                    values.str("steps").lines().map { it.trim() }.filter { it.isNotEmpty() }.map { it to false }.toMutableList(), null)
                "Цель записана"
            }
            "lesson_retire" -> { demoLessons.removeAll { it.first == id }; "Урок убран" }
            "lesson_edit" -> {
                val i = demoLessons.indexOfFirst { it.first == id }
                if (i >= 0) demoLessons[i] = Triple(id, values.str("value"), "hh.ru · Тема")
                "Урок исправлен"
            }
            "exam_run" -> "В демо экзамен не запускается"
            "exam_weekly" -> "Раз в неделю: " + if (values.bool("value")) "включено" else "выключено"
            else -> throw ApiException(ApiError.Server, "Такого действия на этой панели нет")
        }
        return PanelResult(message, panel(panel))
    }

    override suspend fun secrets(): SecretsSnapshot { pause(); return SecretsSnapshot(secrets.toList(), listOf("ag_mac", "ag_web")) }

    override suspend fun secretPut(name: String, value: String, note: String, allow: List<String>, reveal: String, sites: List<String>): ActionResult {
        pause()
        val old = secrets.firstOrNull { it.name == name }
        if (name.isBlank()) throw ApiException(ApiError.Server, "Имя секрета: 2–40 знаков")
        if (old == null && value.isBlank()) throw ApiException(ApiError.Server, "Значение секрета «$name» не задано")
        if (value.isNotBlank() && value.length < 8) throw ApiException(ApiError.Server, "Слишком коротко: от 8 знаков")
        val fresh = SecretInfo(name, note.ifBlank { old?.note.orEmpty() }, allow, reveal, now, (old?.changed ?: 0) + if (old != null && value.isNotBlank()) 1 else 0, sites)
        secrets = (secrets.filterNot { it.name == name } + fresh).sortedBy { it.name }.toMutableList()
        return ActionResult(if (old == null) "Секрет «$name» сохранён" else "Секрет «$name» обновлён")
    }

    override suspend fun secretRemove(name: String): ActionResult {
        pause()
        if (!secrets.removeAll { it.name == name }) throw ApiException(ApiError.Server, "Секрета «$name» нет")
        return ActionResult("Секрет «$name» убран")
    }

    override suspend fun secretReveal(name: String): String {
        pause()
        val s = secrets.firstOrNull { it.name == name } ?: throw ApiException(ApiError.Server, "Секрета «$name» нет")
        if (s.reveal == "never") throw ApiException(ApiError.Server, "Секрет «$name» помечен «никогда не показывать»")
        return "demo-value-1234"
    }

    override suspend fun search(query: String): List<SearchHit> {
        pause()
        val q = query.lowercase()
        val facts = sections.flatMap { s -> s.items.map { SearchHit("факт", it, null) } }
        val notesHits = notes.map { SearchHit("заметка", it.preview, it.updated, ref = it.path + "#0", path = it.path, title = it.title) }
        val peopleHits = people.map { SearchHit("человек", (listOf(it.name) + it.facts).joinToString("; "), null, ref = it.id) }
        val taskHits = finished.filterNot { it.service }.map { SearchHit("задача", "«${it.text}» → ${it.answer.orEmpty()}", it.start, ref = it.id) }
        return (facts + notesHits + peopleHits + taskHits).filter { q in it.text.lowercase() || q in it.title.orEmpty().lowercase() }
    }

    // ---------- Integrations ----------

    override suspend fun integrations(): Map<String, IntegrationState> {
        pause()
        val ids = listOf("telegram_bot", "telegram_account", "app", "phone", "hearing", "scheduler", "memory", "home", "obsidian", "sources", "claude_api", "openai",
            "browser", "mac", "claude", "google", "gemini", "maps", "travel", "luma", "mcp", "site", "github", "hireflow", "youtube")
        return ids.associateWith { id ->
            val toggle = id in enabled
            IntegrationState(id, if (toggle) enabled[id] else null, if (id == "claude_api") 14 else if (toggle && enabled[id] == true) 3 else 0,
                if (id == "claude_api") now - 600 else null, if (id == "hireflow") 1 else 0, if (id == "hireflow") "Не открылся сайт hh.ru" else null,
                if (id == "claude_api") 0.09 else 0.0, toggle, restart = id == "telegram_account", needed = id != "openai")
        }
    }

    override suspend fun setIntegration(id: String, on: Boolean): ActionResult {
        pause()
        if (id !in enabled) throw ApiException(ApiError.Server, "Эту интеграцию нельзя выключить: она часть самой Сены")
        enabled[id] = on
        return ActionResult(if (on) "Включена" else "Выключена")
    }

    // ---------- Settings ----------

    override suspend fun config(): ConfigInfo {
        pause()
        return ConfigInfo(settings, mapOf("low" to "кратко", "medium" to "подробно"), mapOf("low" to "быстро", "medium" to "глубже"), 1000, "demo", coreAlive,
            keys = mapOf("SENA_CLAUDE_BRAIN_KEY" to true, "SENA_CLAUDE_AGENTS_KEY" to true, "GEMINI_API_KEY" to true, "TELEGRAM_TOKEN" to true, "TG_OWNER_ID" to true,
                "OPENAI_API_KEY" to false, "HIREFLOW_KEY" to false))
    }

    override suspend fun updateSettings(values: Map<String, JsonElement>): SettingsChange {
        pause()
        val rejected = mutableListOf<String>()
        fun text(k: String) = (values[k] as? JsonPrimitive)?.contentOrNull
        fun flag(k: String) = (values[k] as? JsonPrimitive)?.booleanOrNull
        settings = settings.copy(
            verbosity = text("verbosity") ?: settings.verbosity,
            effort = text("effort") ?: settings.effort,
            budgetUsdDay = (values["budget_usd_day"] as? JsonPrimitive)?.doubleOrNull?.coerceIn(0.0, 1000.0) ?: settings.budgetUsdDay,
            heartbeat = flag("heartbeat") ?: settings.heartbeat,
            tidy = flag("tidy") ?: settings.tidy,
            weekly = flag("weekly") ?: settings.weekly,
            voiceReply = flag("voice_reply") ?: settings.voiceReply,
            taskBudgetUsd = (values["task_budget_usd"] as? JsonPrimitive)?.doubleOrNull?.coerceIn(0.0, 100.0) ?: settings.taskBudgetUsd,
        )
        return SettingsChange(settings, rejected)
    }

    // ---------- Analytics ----------

    override suspend fun costsReport(period: String): CostsReport {
        pause()
        val byDay = listOf(-4, -3, -2, -1, 0).map { d ->
            val date = LocalDateTime.now().plusDays(d.toLong()).format(DateTimeFormatter.ISO_LOCAL_DATE)
            CostBit(date, if (d == 0) finished.sumOf { it.cost } else 0.02 + 0.01 * (d + 4), if (d == 0) finished.size else 2)
        }
        return CostsReport(
            title = "7 дней, с " + LocalDateTime.now().minusDays(6).format(DateTimeFormatter.ofPattern("dd.MM")),
            total = byDay.sumOf { it.usd }, calls = byDay.sumOf { it.calls },
            byDay = byDay,
            byModel = listOf(CostBit("claude-opus-5-5", byDay.sumOf { it.usd } * 0.6, byDay.sumOf { it.calls } / 3), CostBit("claude-haiku-5-5", byDay.sumOf { it.usd } * 0.3, byDay.sumOf { it.calls } / 2),
                CostBit("gemini-3.8-flash-tts", byDay.sumOf { it.usd } * 0.1, byDay.sumOf { it.calls } / 6)),
            topTasks = finished.sortedByDescending { it.cost }.map { CostBit(it.text, it.cost, 1) },
        )
    }

    override suspend fun quality(days: Int): QualityReport {
        pause()
        val real = finished.filterNot { it.service }
        val verdicts = real.groupingBy { it.outcome?.verdict ?: Verdict.None }.eachCount()
        val ratings = real.mapNotNull { it.rating }
        return QualityReport(
            days = days, tasks = real.size, verdicts = verdicts,
            ratingAvg = if (ratings.isEmpty()) null else ratings.average(), ratingCount = ratings.size,
            errors = real.count { it.outcome?.verdict == Verdict.Failed },
            repeats = if (real.any { it.outcome?.verdict == Verdict.Failed }) listOf("не вышло открыть сайт: просит войти" to 2) else emptyList(),
            byAgent = mapOf("ag_sena" to verdicts, "ag_web" to mapOf(Verdict.Failed to real.count { it.outcome?.verdict == Verdict.Failed })),
        )
    }
}
