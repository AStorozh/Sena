// Integrations: a diagram of how everything connects to Sena, on a three-dimensional surface (SceneKit). The surface is infinite
// and fixed in place: you can move across it (drag with the mouse), zoom (wheel), and rearrange tiles (drag a tile; the layout is
// remembered), but not rotate it: the camera sits at a fixed angle, or, in flat view, straight overhead. On opening and on "Reset
// view" the camera positions itself so the whole diagram is visible at once.
// Data flow left to right: channels (the Telegram bot, the Mac app, the phone) → Sena → agents (ag_web, ag_mac, ag_code) → their
// tools (Chrome, the Mac terminal, Claude Code). Above Sena is what she calls herself (Claude — her brain, Google, Gemini — her voice,
// Maps, Flights, Sources, the Telegram account; OpenAI stays as a spare), below her is memory with Obsidian, the home models, the
// scheduler, and service agents (ag_night, ag_judge).
// A tile is a dark base with a colored lid; the lid has a title, a subtitle, an icon and a status dot drawn on it (nothing floats
// in the air); Sena's tile has a dark lid with her leaf. Links are ribbons on the surface in the theme's ink color, running under
// the bases; while a task is running, ribbons to her brain (Claude) and to busy agents pulse. Clicking a tile selects it: a card on the
// right shows live data (today's uses and spending, last use, errors) and controls — the integration can be turned off and on
// (the server: /desk/api/integrations and integration; a disabled one disappears from the model's tools).
import SceneKit
import SwiftUI

/// Integration state
enum LinkState {
    // idle — ready, opens on the first chore (Chrome); later — connects with the core; disabled — turned off by Tema;
    // unused — nothing of Sena's needs it now and it has no key (the server's "needed"): a spare, not a fault
    case on, idle, off, warn, later, disabled, unused
    var text: String {
        switch self {
        case .on: return L("подключена"); case .off: return L("не подключена"); case .warn: return L("нужен повторный вход")
        case .later: return L("подключится с ядром"); case .disabled: return L("выключена"); case .idle: return L("откроется при первой задаче")
        case .unused: return L("не используется")
        }
    }
    var color: Color {
        switch self {
        case .on, .idle: return Style.Colors.ok; case .off: return Style.Colors.bad; case .warn: return Style.Colors.warn
        case .later: return Style.Colors.info; case .disabled: return Style.Colors.ink; case .unused: return Style.Colors.off
        }
    }
    /// The dot's color on the tile's lid. The lid is drawn once and doesn't depend on the theme, so only fixed colors go here
    /// (dynamic ink on a light lid in dark mode would come out nearly white)
    var dot: NSColor { self == .disabled ? Style.Colors.inkLight : NSColor(color) }
}

/// A service: what it is, what it's for, which keys, how to connect it, how to tell it's connected
struct Integration: Identifiable {
    let id: String
    let name: String
    let role: String
    let agent: String          // who uses it
    let keys: [String]         // variables in .env
    let how: String            // how to connect it, in words
    let command: String?       // the login command, if there is one
    var goto: (String, Screen, Int)? = nil   // a button that jumps to an app section (title, section, tab)
    var focus: String? = nil                  // and what to select there (a panel of «Сена ведёт»)
    let state: (JSON, Bool) -> LinkState   // (overview, core alive) -> connection state

    static func keyed(_ o: JSON, _ keys: [String]) -> Bool { keys.allSatisfy { o["accounts"]["keys"][$0].bool } }

    static let all: [Integration] = [
        Integration(id: "telegram_bot", name: L("Телеграм-бот"), role: L("канал общения: сообщения, голос, фото, файлы"), agent: "ag_sena",
                    keys: ["TELEGRAM_TOKEN", "TG_OWNER_ID"], how: L("Токен бота от @BotFather — в .env как TELEGRAM_TOKEN, свой id в Телеграме — TG_OWNER_ID."),
                    command: nil, state: { o, alive in o["accounts"]["telegram_bot"].bool && keyed(o, ["TELEGRAM_TOKEN", "TG_OWNER_ID"]) ? (alive ? .on : .later) : .off }),
        Integration(id: "telegram_account", name: L("Аккаунт в Телеграме"), role: L("читать чаты и писать людям от твоего имени"), agent: "ag_sena",
                    keys: ["TG_API_ID", "TG_API_HASH"], how: L("Ключи приложения с my.telegram.org — в .env, потом один раз войти по телефону и коду."),
                    command: "python -m sena.telegram.account",
                    state: { o, alive in !keyed(o, ["TG_API_ID", "TG_API_HASH"]) ? .off : o["accounts"]["telegram_account"].bool ? .on : alive ? .off : .later }),
        Integration(id: "app", name: L("Приложение Mac"), role: L("это приложение: чат, задачи, память, контроль"), agent: "ag_sena", keys: ["APP_TOKEN"],
                    how: L("Уже подключено: приложение само поднимает экран и говорит с сервером по токену APP_TOKEN."), command: nil,
                    state: { _, _ in .on }),
        Integration(id: "phone", name: L("Телефон"), role: L("приложение на Android: чат, экраны и разговор голосом по кнопке — тот же сервер, что у этого приложения"), agent: "ag_sena",
                    keys: ["APP_TOKEN"], how: L("Приложение ставится по Wi-Fi со страницы /apk сервера; адрес и токен — в нём же."), command: nil,
                    state: { _, _ in .on }),
        Integration(id: "hearing", name: L("Слух"), role: L("микрофон этого Mac → текст (whisper.cpp, Silero VAD, детектор имени); «Сена, …» — разговор с ней, остальное расшифровывается и хранится для памяти — его разбирает ag_ears"),
                    agent: "ag_ears", keys: [], how: L("Модели и программы ставит одна команда: она скачает модели в models/ и подскажет, что поставить через brew (whisper-cpp, ffmpeg). Включается на полке: «Слушать голос с Mac»."),
                    command: "python -m sena.home setup",
                    state: { _, _ in .on }),   // not installed — the server's "ready" says so (IntegrationsView.states)
        Integration(id: "scheduler", name: L("Расписание"), role: L("запускает твои задания, фоновую проверку, сигналы по цене, ночную уборку и недельный разбор"),
                    agent: "ag_sena", keys: [], how: L("Внутри ядра; что и как часто — в разделе «Правила»."), command: nil,
                    goto: (L("Правила"), .rules, 0), state: { _, alive in alive ? .on : .later }),
        Integration(id: "memory", name: L("Память"), role: L("ядро памяти, архив, люди, навыки, правила, переписка; поиск по смыслу и словам"), agent: "ag_sena",
                    keys: [], how: L("Файлы в data/memory и data/journal; индекс поиска — база SQLite, отпечатки считает домашняя модель на этом Mac."), command: nil,
                    goto: (L("Память"), .memory, 0), state: { _, _ in .on }),
        Integration(id: "home", name: L("Домашние модели"), role: L("модели на этом Mac: отпечатки памяти, слух, чтение документов и фото, разбор чужой речи и сообщений; наружу ничего не уходит"), agent: "ag_sena",
                    keys: [], how: L("Установка одной командой: она скачает модели и проверит программы. Состояние и замеры качества — python -m sena.home, bench, check."), command: "python -m sena.home setup",
                    state: { _, _ in .on }),
        Integration(id: "obsidian", name: "Obsidian", role: L("хранилище заметок: люди, память, навыки и дневник задач становятся заметками"), agent: "ag_sena",
                    keys: [], how: L("Папка ~/Desktop/Obsidian_sena; открой её как хранилище в Obsidian."), command: nil,
                    goto: (L("Заметки"), .memory, 2), state: { _, _ in .on }),
        Integration(id: "sources", name: L("Открытые источники"), role: L("погода Open-Meteo, курсы и крипта, новости из лент, сёрф-прогноз — без ключей"), agent: "ag_sena",
                    keys: [], how: L("Ничего настраивать не нужно."), command: nil, state: { _, _ in .on }),
        Integration(id: "claude_api", name: "Claude", role: L("мозг Сены: координатор, голосовые команды, агенты браузера и компьютера, разбор задач, ночная уборка, характер (Claude API, не код)"), agent: "ag_sena",
                    keys: ["SENA_CLAUDE_BRAIN_KEY"], how: L("Ключ sena-brain из Anthropic Console — в .env как SENA_CLAUDE_BRAIN_KEY; ключ помощников SENA_CLAUDE_AGENTS_KEY — по желанию, без него они берут ключ мозга. Код по-прежнему пишется только по подписке."), command: nil,
                    state: { o, _ in keyed(o, ["SENA_CLAUDE_BRAIN_KEY"]) ? .on : .off }),
        Integration(id: "openai", name: "OpenAI", role: L("запасной: сейчас не используется — думает Claude, отпечатки памяти считаются дома"), agent: "ag_sena",
                    keys: ["OPENAI_API_KEY"], how: L("Ключ с platform.openai.com — в .env как OPENAI_API_KEY. Нужен, только если перенести отпечатки памяти в облако («Журнал» → «Расходы» → «Дом и облако»)."), command: nil,
                    state: { o, _ in keyed(o, ["OPENAI_API_KEY"]) ? .on : .off }),
        Integration(id: "browser", name: "Chrome", role: L("сайты: читать, кликать, заполнять, покупать"), agent: "ag_web", keys: [],
                    how: L("Ничего настраивать не нужно: Chrome открывается сам при первой задаче для браузера."), command: nil,
                    state: { o, alive in o["accounts"]["browser"].bool ? .on : alive ? .idle : .later }),   // the window opens on the first chore: until it's open, that's not "not connected"
        Integration(id: "mac", name: L("Терминал Mac"), role: L("файлы, программы, снимки экрана этого Mac"), agent: "ag_mac", keys: [],
                    how: L("Уже подключено: агент работает прямо на этом Mac."), command: nil, state: { _, _ in .on }),
        Integration(id: "claude", name: "Claude Code", role: L("код проектов и саморазвитие Сены, по подписке"), agent: "ag_code", keys: [],
                    how: L("Работает через подписку Claude в терминале (claude -p); API не используется. Вход — командой."), command: "claude login",
                    state: { _, _ in .on }),
        Integration(id: "google", name: "Google", role: L("почта, Таблицы, Диск"), agent: "ag_sena", keys: [],
                    how: L("Нужен data/state/google_client.json из Google Cloud; вход один раз в браузере."), command: "python -m sena.google",
                    state: { o, _ in let s = o["accounts"]["google"].str; return s == "ok" ? .on : s == "expired" ? .warn : .off }),
        Integration(id: "gemini", name: "Gemini", role: L("голос Сены везде (Kore), живой голос, расшифровка речи, когда дома не вышло, картинки и видео по описанию"), agent: "ag_sena", keys: ["GEMINI_API_KEY"],
                    how: L("Ключ из Google AI Studio — в .env как GEMINI_API_KEY."), command: nil,
                    state: { o, _ in keyed(o, ["GEMINI_API_KEY"]) ? .on : .off }),
        Integration(id: "maps", name: L("Карты"), role: L("TomTom — пробки и маршруты, Google Maps — места и часы работы"), agent: "ag_sena", keys: ["TOMTOM_KEY", "GOOGLE_MAPS_KEY"],
                    how: L("Ключи с developer.tomtom.com и Places API из Google Cloud — в .env как TOMTOM_KEY и GOOGLE_MAPS_KEY; хватает любого одного."), command: nil,
                    state: { o, _ in keyed(o, ["TOMTOM_KEY"]) || keyed(o, ["GOOGLE_MAPS_KEY"]) ? .on : .off }),
        Integration(id: "travel", name: L("Билеты (Авиасейлс)"), role: L("дешёвые билеты по маршруту и месяцу, куда улететь дёшево, сигналы по цене"), agent: "ag_sena", keys: ["TRAVELPAYOUTS_TOKEN"],
                    how: L("Токен из кабинета Travelpayouts — в .env как TRAVELPAYOUTS_TOKEN."), command: nil,
                    state: { o, _ in keyed(o, ["TRAVELPAYOUTS_TOKEN"]) ? .on : .off }),
        Integration(id: "luma", name: "Luma", role: L("твои дела, календарь, привычки, траты и заметки; Сена работает с Luma напрямую через её API"), agent: "ag_sena", keys: [],
                    how: L("Ничего вводить не нужно: Сена сама открывает программу Luma, если она закрыта. Ключ LUMA_KEY в .env — по желанию: в журнале Luma действия видны под его именем. Другой путь к программе — LUMA_APP."), command: nil,
                    state: { _, _ in .on }),
        Integration(id: "mcp", name: "MCP", role: L("твои серверы MCP: программы, которые дают Сене инструменты других приложений; каждый запускается только после твоего «разрешить», в песочнице"),
                    agent: "ag_sena", keys: [], how: L("Серверы добавляются и разрешаются в «Сена ведёт» → «MCP-серверы»."), command: nil,
                    goto: (L("MCP-серверы"), .panels, 0), focus: "mcp", state: { _, alive in alive ? .on : .later }),
        Integration(id: "site", name: L("Сайт-портфолио"), role: L("тексты, работы, ссылки для откликов и статистика посещений твоего сайта через его закрытый API"), agent: "ag_sena",
                    keys: ["SENA_SITE_KEY"], how: L("Ключ из окружения сайта (SENA_SITE_KEY) и адрес сайта (SITE_URL) — в .env Сены."), command: nil,
                    state: { o, _ in keyed(o, ["SENA_SITE_KEY"]) ? .on : .off }),
        Integration(id: "github", name: "GitHub", role: L("репозитории, задачи, pull request, релизы и профиль на GitHub; правки только после твоего «да»"), agent: "ag_sena",
                    keys: ["SENA_GITHUB_TOKEN"], how: L("Fine-grained токен на нужные репозитории — в .env как SENA_GITHUB_TOKEN (GitHub → Settings → Developer settings)."), command: nil,
                    state: { o, _ in keyed(o, ["SENA_GITHUB_TOKEN"]) ? .on : .off }),
        Integration(id: "hireflow", name: "Hire-flow", role: L("поиск работы на hh.ru, оценка вакансий, письма и тексты; письма остаются черновиками, данные о тебе меняются только после твоего «да»"), agent: "ag_sena",
                    keys: ["HIREFLOW_KEY"], how: L("Ключ из папки Hire-flow (python -m app.cli api-key create) — в .env как HIREFLOW_KEY, адрес сервера — HIREFLOW_URL."), command: nil,
                    state: { o, _ in keyed(o, ["HIREFLOW_KEY"]) ? .on : .off }),
        Integration(id: "youtube", name: "YouTube", role: L("твой канал: видео, плейлисты, комментарии и статистика; публикация только после твоего «да»"), agent: "ag_sena",
                    keys: [], how: L("Тот же вход Google: выдай доступ к YouTube и включи YouTube Data API v3 в Google Cloud."), command: "python -m sena.google",
                    state: { o, _ in o["accounts"]["youtube"].bool ? .on : (o["accounts"]["google"].str == "ok" ? .warn : .off) }),
    ]
    static func by(_ id: String) -> Integration? { all.first { $0.id == id } }
}

/// A tile on the surface: a service, an agent, or Sena; where it sits and how big it is (in scene units)
struct Tile {
    let id: String          // "svc:<id>" | "agent:<code>" | "core"
    let title: String
    let subtitle: String
    var icon: String? = nil   // an SF Symbols icon; Sena has none — her leaf goes there instead
    let color: Color
    let x: Double, z: Double
    var w: Double = 2.4, l: Double = 1.6
    static let all: [Tile] = [
        // north: what Sena calls herself
        Tile(id: "svc:telegram_account", title: L("Аккаунт в Телеграме"), subtitle: L("твои чаты"), icon: "person.crop.circle.fill", color: Style.Colors.palette[3], x: -10.4, z: -6.0),
        Tile(id: "svc:google", title: "Google", subtitle: L("почта, таблицы, диск"), icon: "envelope.fill", color: Style.Colors.palette[1], x: -6.9, z: -7.6),
        Tile(id: "svc:gemini", title: "Gemini", subtitle: L("голос, картинки"), icon: "photo.fill", color: Style.Colors.palette[5], x: -3.4, z: -6.6),
        Tile(id: "svc:claude_api", title: "Claude", subtitle: L("мозг Сены"), icon: "sparkles", color: Style.Colors.palette[2], x: 0.0, z: -9.8),
        Tile(id: "svc:openai", title: "OpenAI", subtitle: L("запасной"), icon: "brain.fill", color: Style.Colors.palette[2], x: 0.0, z: -6.6),
        Tile(id: "svc:maps", title: L("Карты"), subtitle: "TomTom, Google Maps", icon: "map.fill", color: Style.Colors.palette[1], x: 3.4, z: -6.6),
        Tile(id: "svc:travel", title: L("Билеты"), subtitle: L("Авиасейлс"), icon: "airplane", color: Style.Colors.palette[5], x: 6.9, z: -7.6),
        Tile(id: "svc:sources", title: L("Источники"), subtitle: L("погода, курсы, новости"), icon: "cloud.sun.fill", color: Style.Colors.palette[4], x: 10.4, z: -6.0),
        // west: entry channels
        Tile(id: "svc:telegram_bot", title: L("Телеграм-бот"), subtitle: L("твои сообщения"), icon: "paperplane.fill", color: Style.Colors.palette[3], x: -5.8, z: -1.2),
        Tile(id: "svc:app", title: L("Приложение Mac"), subtitle: L("это приложение"), icon: "macwindow", color: Style.Colors.palette[3], x: -5.8, z: 1.2),
        Tile(id: "svc:phone", title: L("Телефон"), subtitle: L("чат и голос"), icon: "iphone", color: Style.Colors.palette[3], x: -5.8, z: 3.4),
        Tile(id: "svc:hearing", title: L("Слух"), subtitle: L("микрофон Mac"), icon: "waveform", color: Style.Colors.agent("ag_ears"), x: -9.2, z: 3.4),
        Tile(id: "agent:ag_ears", title: "ag_ears", subtitle: L("разбор разговоров"), icon: "ear", color: Style.Colors.agent("ag_ears"), x: -9.2, z: 6.2),
        // center
        Tile(id: "core", title: L("Сена"), subtitle: L("координатор"), color: Style.Colors.agent("ag_sena"), x: 0.0, z: 0.0, w: 3.0, l: 2.0),
        Tile(id: "svc:memory", title: L("Память"), subtitle: L("ядро, поиск, люди"), icon: "archivebox.fill", color: Style.Colors.palette[0], x: 0.0, z: 4.2),
        Tile(id: "svc:home", title: L("Домашние модели"), subtitle: L("память, слух, чтение"), icon: "house.fill", color: Style.Colors.palette[4], x: -3.0, z: 3.0),
        // east: working agents and their tools
        Tile(id: "agent:ag_web", title: "ag_web", subtitle: L("браузер"), icon: "globe", color: Style.Colors.agent("ag_web"), x: 5.8, z: -1.2),
        Tile(id: "agent:ag_mac", title: "ag_mac", subtitle: L("компьютер"), icon: "desktopcomputer", color: Style.Colors.agent("ag_mac"), x: 5.8, z: 1.2),
        Tile(id: "agent:ag_code", title: "ag_code", subtitle: L("код"), icon: "chevron.left.forwardslash.chevron.right", color: Style.Colors.agent("ag_code"), x: 5.8, z: 3.6),
        Tile(id: "svc:browser", title: "Chrome", subtitle: L("сайты"), icon: "safari.fill", color: Style.Colors.agent("ag_web"), x: 9.4, z: -1.2),
        Tile(id: "svc:mac", title: L("Терминал Mac"), subtitle: L("файлы, программы"), icon: "terminal.fill", color: Style.Colors.agent("ag_mac"), x: 9.4, z: 1.2),
        Tile(id: "svc:claude", title: "Claude Code", subtitle: L("подписка"), icon: "hammer.fill", color: Style.Colors.agent("ag_code"), x: 9.4, z: 3.6),
        Tile(id: "svc:site", title: L("Сайт-портфолио"), subtitle: L("тексты, ссылки, статистика"), icon: "globe", color: Style.Colors.palette[4], x: 6.9, z: -10.4),
        Tile(id: "svc:github", title: "GitHub", subtitle: L("репозитории, задачи"), icon: "chevron.left.forwardslash.chevron.right", color: Style.Colors.palette[0], x: -3.4, z: -9.8),
        Tile(id: "svc:hireflow", title: "Hire-flow", subtitle: L("вакансии, письма"), icon: "briefcase.fill", color: Style.Colors.palette[1], x: 3.4, z: -9.8),
        Tile(id: "svc:youtube", title: "YouTube", subtitle: L("видео, комментарии, статистика"), icon: "play.rectangle.fill", color: Style.Colors.palette[3], x: -6.9, z: -10.4),
        Tile(id: "svc:mcp", title: "MCP", subtitle: L("инструменты программ"), icon: "puzzlepiece.extension.fill", color: Style.Colors.palette[4], x: -10.4, z: -9.0),
        Tile(id: "svc:luma", title: "Luma", subtitle: L("дела, привычки, траты"), icon: "checklist", color: Style.Colors.palette[2], x: 9.4, z: 6.4),
        // south: the scheduler and those who feed memory
        Tile(id: "svc:scheduler", title: L("Расписание"), subtitle: L("задания, проверка, уборка"), icon: "calendar", color: Style.Colors.palette[0], x: -5.8, z: 7.2),
        Tile(id: "agent:ag_night", title: "ag_night", subtitle: L("ночная уборка"), icon: "moon.stars.fill", color: Style.Colors.agent("ag_night"), x: -2.4, z: 6.6),
        Tile(id: "agent:ag_judge", title: "ag_judge", subtitle: L("разбор после задач"), icon: "scalemass.fill", color: Style.Colors.agent("ag_judge"), x: 3.6, z: 6.6),
        Tile(id: "svc:obsidian", title: "Obsidian", subtitle: L("заметки"), icon: "note.text", color: Style.Colors.palette[5], x: 0.6, z: 8.8),
    ]
    /// Links: from → to, by the system's code. Listening → Sena isn't drawn: a named command arrives the same way as chat. Hearing
    /// is this Mac's own microphone (the app streams it): the phone only talks by its button, nothing of it is overheard
    static let links: [(String, String)] = [
        ("svc:telegram_bot", "core"), ("svc:app", "core"), ("svc:phone", "core"), ("svc:scheduler", "core"),
        ("svc:app", "svc:hearing"), ("svc:hearing", "agent:ag_ears"), ("agent:ag_ears", "svc:memory"),
        ("core", "svc:openai"), ("core", "svc:claude_api"), ("core", "svc:site"), ("core", "svc:github"), ("core", "svc:hireflow"), ("core", "svc:youtube"), ("core", "svc:telegram_account"), ("core", "svc:google"), ("core", "svc:gemini"), ("core", "svc:maps"), ("core", "svc:travel"), ("core", "svc:sources"), ("core", "svc:luma"), ("core", "svc:mcp"),
        ("core", "svc:memory"), ("svc:memory", "svc:obsidian"), ("svc:memory", "svc:home"), ("svc:hearing", "svc:home"), ("agent:ag_ears", "svc:home"),
        ("core", "agent:ag_web"), ("core", "agent:ag_mac"), ("core", "agent:ag_code"),
        ("agent:ag_web", "svc:browser"), ("agent:ag_mac", "svc:mac"), ("agent:ag_code", "svc:claude"),
        ("core", "agent:ag_judge"), ("agent:ag_judge", "svc:memory"), ("agent:ag_night", "svc:memory"), ("svc:scheduler", "agent:ag_night"),
    ]
    static func by(_ id: String) -> Tile? { all.first { $0.id == id } }
}

struct IntegrationsView: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.accessibilityReduceMotion) private var still
    @Environment(\.colorScheme) private var scheme
    @State private var selected: String? = "core"
    @State private var resetToken = 0
    @State private var layoutToken = 0
    @AppStorage("integrationsFlat") private var flat = false   // a flat top-down view or a 3D one at an angle
    @State private var info = JSON(nil)   // from the server: whether it's enabled, uses, spending, errors, last use
    private static let refreshSeconds: UInt64 = 5   // how often to ask the server for the counters

    /// State accounting for Tema's manual disabling
    var states: [String: LinkState] {
        func state(_ i: Integration) -> LinkState {
            let d = info[i.id], enabled = d["enabled"]
            if enabled.exists && !enabled.bool { return .disabled }
            if d["ready"].exists && !d["ready"].bool { return .off }   // what it needs isn't on this Mac (the server checks files and programs)
            let s = i.state(model.overview, model.alive)
            return s == .off && d["needed"].exists && !d["needed"].bool ? .unused : s   // no key, and nothing needs it now: a spare
        }
        return Dictionary(Integration.all.map { ($0.id, state($0)) }, uniquingKeysWith: { _, last in last })
    }
    /// What agents are busy with (from the server's reply: a duplicate code shouldn't crash the app)
    var busy: [String: String] {
        Dictionary(model.overview["agents"].array.compactMap { a in a["busy"].string.map { (a["code"].str, $0) } }, uniquingKeysWith: { _, last in last })
    }

    var body: some View {
        let st = states
        AdaptiveRow(fill: true) {
            // the surface itself fills the whole left side, no "plate" block around it; "reset view" and "rearrange" float on top
            BoardScene(states: st, busy: busy, alive: model.alive, active: model.ownTasks.count, selected: $selected, reset: resetToken, layoutReset: layoutToken,
                       flat: flat, dark: scheme == .dark, calm: still)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .frame(minHeight: Style.Size.flowHeight * 3)
                .clipShape(RoundedRectangle(cornerRadius: Style.Radius.all, style: .continuous))
                .overlay(RoundedRectangle(cornerRadius: Style.Radius.all, style: .continuous).strokeBorder(Color.primary.opacity(Style.Size.borderOpacity)))
                .overlay(alignment: .topTrailing) {
                    HStack(spacing: Style.Space.gapSmall) {
                        Button(L("Сбросить вид")) { resetToken += 1 }
                        Button(L("Расставить заново")) { layoutToken += 1 }.buttonStyle(.quiet)
                    }
                    .padding(Style.Space.gapSmall)
                }
            ScrollView {   // the right column scrolls on its own: the diagram stays in place
                VStack(spacing: Style.Space.xl) {   // the summary and the selected tile's details; no need for a service list — the diagram is itself one
                    totals(st)
                    detail(st).id(selected).transition(.asymmetric(insertion: .opacity.combined(with: .offset(x: Style.Space.xl)), removal: .opacity))   // selecting a different tile cross-fades the service card
                }
            }
            .frame(width: model.narrow ? nil : Style.Size.columnWide)
        }
        .motion(value: selected)
        .task { while !Task.isCancelled { await reload(); try? await Task.sleep(nanoseconds: IntegrationsView.refreshSeconds * 1_000_000_000) } }   // its own loop, not Polling: the board doesn't wait for the first reply
        .actions {
            Segments(selection: $flat, options: [(false, "3D"), (true, L("Плоско"))], collapses: true)   // 3D at an angle or a top-down view; an animated transition
            Button(L("Файл .env")) { NSWorkspace.shared.selectFile(model.backend.root.appendingPathComponent(".env").path, inFileViewerRootedAtPath: model.backend.root.path) }
                .help(L("Показать файл с ключами в Finder"))
        }
    }

    /// Counters from the server; the screen redraws only if they've changed
    func reload() async { if let r = try? await model.backend.get("integrations"), !r.isSame(info) { info = r } }

    func totals(_ st: [String: LinkState]) -> some View {
        func count(_ kinds: LinkState...) -> Int { st.values.filter { kinds.contains($0) }.count }
        let on = count(.on, .idle), off = count(.off), warn = count(.warn), disabled = count(.disabled), later = count(.later)
        return Block(L("Сводка"), lead: true) {
            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: Style.Space.m) {   // two columns: labels don't wrap
                Stat(value: "\(on)", label: L("подключено"))
                Stat(value: "\(off)", label: L("не подключено"), bad: off > 0)
                Stat(value: "\(disabled)", label: L("выключено"))
                Stat(value: "\(warn)", label: L("проверить"), bad: warn > 0)
                if later > 0 { Stat(value: "\(later)", label: L("ждут ядра")) }   // while the core is off, some integrations are waiting for it to start
            }
        }
    }

    @ViewBuilder func detail(_ st: [String: LinkState]) -> some View {
        if let sel = selected {
            if sel == "core" { coreCard() }
            else if sel.hasPrefix("agent:") { agentCard(String(sel.dropFirst(6))) }
            else if let i = Integration.by(String(sel.dropFirst(4))) { serviceCard(i, st[i.id] ?? .off) }
        }
    }

    func coreCard() -> some View {
        let o = model.overview, t = o["today"]
        return Block(L("Сена"), lead: true, content: {
            HStack(spacing: Style.Space.s) { StatusDot(ok: model.alive, bad: model.lost); Text(model.stateText).font(Style.Text.heading) }
            HStack(spacing: Style.Space.m) {
                Stat(value: "\(model.ownTasks.count)", label: L("в работе"))
                Stat(value: "\(t["tasks"].int)", label: L("задач сегодня"))
                Stat(value: Fmt.usd(t["usd"].dbl), label: L("расход"))
            }
            HStack(spacing: Style.Space.gapSmall) {
                if model.up { Button(model.alive ? L("Остановить Сену") : L("Запустить Сену")) { model.toggleCore() }.buttonStyle(.primary) }
                if !model.ownTasks.isEmpty { Button(L("Остановить все задачи"), role: .destructive) { model.stopAllAsking() } }
                Button(L("Задачи")) { model.open(.tasks) }.buttonStyle(.quiet)
            }
        }, trailing: { AgentIcon(code: "ag_sena") })
    }

    func agentCard(_ code: String) -> some View {
        let b = busy[code]
        // same as on the server (desk._agents): an agent is busy if it authored one of the task's last three steps
        let mine = model.active.filter { t in t["steps"].array.suffix(3).contains { $0["who"].str.hasPrefix(code) } }
        return Block(code, lead: true, content: {
            Text(agentRole(code)).font(Style.Text.body)
            HStack(spacing: Style.Space.s) { StatusDot(ok: b != nil, bad: b == nil); Text(b ?? L("свободен")).font(Style.Text.small).lineLimit(2) }
            ForEach(mine, id: \.["tid"].str) { t in
                HStack { Text(t["text"].str).font(Style.Text.caption).lineLimit(1); Spacer(); Button(L("Остановить"), role: .destructive) { model.stopTask(AppModel.taskID(t), title: t["text"].str) }.buttonStyle(.quiet) }
            }
            HStack(spacing: Style.Space.gapSmall) {
                Button(L("Задачи")) { model.open(.tasks) }
                if code == "ag_web" || code == "ag_mac" { Button(L("Поручить в чате")) { if model.open(.chat, tab: 0) { model.chatTarget = code } }.buttonStyle(.quiet) }   // chat picks this agent itself (unless leaving the screen with an edit was declined)
            }
        }, trailing: { AgentIcon(code: code) })
    }

    func serviceCard(_ i: Integration, _ s: LinkState) -> some View {
        let d = info[i.id], canToggle = d["flag"].string != nil
        let last = d["last_used"].dbl > 0 ? String(format: L("последний раз: %@"), Fmt.when(d["last_used"]) + ", " + Fmt.ago(d["last_used"].dbl)) : L("последний раз: ещё не использовалась")
        return Block(i.name, lead: true, content: {
            Text(i.role).font(Style.Text.body)
            HStack(spacing: Style.Space.m) {
                AgentIcon(code: i.agent)
                Text(String(format: L("пользуется %@"), agentName(i.agent))).font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText)
                Spacer()
                if canToggle {
                    Toggle(L("Используется"), isOn: Binding(get: { d["enabled"].bool }, set: { on in
                        model.run(on ? L("Включение") : L("Выключение"), { try await self.model.backend.act("integration", ["id": i.id, "on": on]) }, then: { Task { await reload() } })
                    })).font(Style.Text.small)
                }
            }
            HStack(spacing: Style.Space.m) {
                Stat(value: "\(d["uses_today"].int)", label: L("раз сегодня"))
                Stat(value: "\(d["errors_today"].int)", label: L("ошибок сегодня"), bad: d["errors_today"].int > 0)
                if d["usd_today"].dbl > 0 || i.id == "claude_api" || i.id == "gemini" { Stat(value: Fmt.usd(d["usd_today"].dbl), label: L("расход сегодня")) }
            }
            Text(last).font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText)
            if let err = d["last_error"].string { Text(String(format: L("последняя ошибка: %@"), err)).font(Style.Text.caption).foregroundStyle(Style.Colors.badText).lineLimit(2) }
            if !i.keys.isEmpty {
                VStack(alignment: .leading, spacing: Style.Space.xs) {
                    ForEach(i.keys, id: \.self) { k in
                        HStack(spacing: Style.Space.s) {
                            Circle().fill(model.overview["accounts"]["keys"][k].bool ? Style.Colors.ok : Style.Colors.bad).frame(width: Style.Size.lineDot, height: Style.Size.lineDot)
                            Text(k).font(Style.Text.monoSmall)
                            Text(model.overview["accounts"]["keys"][k].bool ? L("есть") : L("нет в .env")).font(Style.Text.caption).foregroundStyle(Style.Colors.secondaryText)
                        }
                    }
                }
            }
            if s != .on && s != .disabled { Text(i.how).font(Style.Text.small).foregroundStyle(Style.Colors.secondaryText) }   // how to connect — only while not connected
            if let cmd = i.command {
                HStack(spacing: Style.Space.m) {
                    Text(cmd).font(Style.Text.mono)
                    Button(L("Скопировать")) { model.copy(cmd, toast: L("Команда скопирована")) }
                }
            }
            if i.id == "memory" {
                let ix = model.overview["index"]
                HStack(spacing: Style.Space.m) { Stat(value: "\(ix["items"].int)", label: L("записей в индексе")); Stat(value: "\(ix["pending"].int)", label: L("без индекса"), bad: ix["pending"].int > 0) }
            }
            if i.id == "scheduler" {
                let sc = model.overview["scheduler"]
                HStack(spacing: Style.Space.m) { Stat(value: "\(sc["jobs_total"].int)", label: L("заданий в расписании")); Stat(value: sc["state"]["heartbeat"].dbl > 0 ? Fmt.ago(sc["state"]["heartbeat"].dbl) : L("не было"), label: L("фоновая проверка")) }
            }
            if let g = i.goto { Button(g.0) { if model.open(g.1, tab: g.2), let f = i.focus { model.focus = f } } }
        }, trailing: { Tag(text: s.text, color: s.color) })
    }
}

// ---- the surface ----

/// Scene positions, sizes and timing — view calculations, not styling; the 3D board has no section of its own in Style.swift,
/// the numbers are gathered here. Tile and ribbon state opacity is styling, that lives in Style.Colors.board*
private enum Look {
    static let fitMargin = 0.06                      // margin around the diagram when fitting the view
    static let zoom: ClosedRange<Double> = 3...26    // the wheel's zoom limit (camera scale)
    static let zoomStep = 0.01                       // how much the scale changes per unit of scroll
    static let flight = 0.9                          // the camera's flight between views, in seconds
    static let settle = 0.25                         // the selected tile's lift, glow, fade
    static let shuffle = 0.4                         // "Rearrange"
    static let pulseHalf = 0.6                       // half the ribbon's blink period
    static let lift = 0.3                            // how much the selected tile is raised
    static let tileTop = 0.44                        // the tile lid's height above the surface (base 0.18 + lid 0.26)
    static let textMin = 0.55                        // how much the lid's label can shrink before it's truncated with an ellipsis
    static let tileCategory = 1, haloCategory = 2    // halos don't take part in tile selection by click
    static let ribbonWidth = 0.12, ribbonY = 0.025, ribbonSteps = 36
}

/// Everything the tiles' and ribbons' appearance depends on: if it hasn't changed, the scene isn't touched
struct BoardInputs: Equatable {
    let states: [String: LinkState]
    let busy: [String: String]
    let alive: Bool
    let active: Int
    let selected: String?
}

/// SCNView: reports size changes (fitting the scale) and the wheel (zooming); there's no camera rotation
final class FitSCNView: SCNView {
    var onLayout: (() -> Void)?
    var onScroll: ((CGFloat) -> Void)?
    override var mouseDownCanMoveWindow: Bool { false }   // dragging on the board moves the board, not the window (otherwise both used to jitter)
    override func layout() { super.layout(); onLayout?() }
    override func scrollWheel(with event: NSEvent) { onScroll?(event.scrollingDeltaY) }
}

/// A SceneKit scene inside SwiftUI: built once, states, pulse and selection update in place; a click selects a tile, dragging moves the board or a tile
struct BoardScene: NSViewRepresentable {
    let states: [String: LinkState]
    let busy: [String: String]
    let alive: Bool
    let active: Int
    @Binding var selected: String?
    let reset: Int
    let layoutReset: Int
    let flat: Bool
    let dark: Bool
    let calm: Bool   // "reduce motion": no ribbon blinking and no camera flights

    /// The scene and its tiles are built once per launch (tile textures are the most expensive part, around half a second); on
    /// later visits to the section the ready one is reused. The app builds it ahead of time while the user is still looking at the splash (prewarm)
    private static var shared: (coordinator: Coordinator, view: FitSCNView)?
    static func prewarm(overview: JSON = JSON(nil), alive: Bool = false) {
        guard shared == nil else { return }
        let c = Coordinator(select: { _ in })
        let view = makeView(c)
        shared = (c, view)
        // tile lids (titles, subtitles, status dots) are drawn on the first layout of states — that's the half second: do it ahead of time
        let states = Dictionary(Integration.all.map { ($0.id, $0.state(overview, alive)) }, uniquingKeysWith: { _, last in last })
        c.apply(BoardInputs(states: states, busy: [:], alive: alive, active: 0, selected: nil))
        let appearance = NSApp.mainWindow?.effectiveAppearance ?? NSApp.effectiveAppearance
        let dark = appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua
        if dark != c.dark { c.setDark(dark) }
        view.prepare([c.scene]) { _ in }   // shaders and textures are prepared in the background
    }
    private static func makeView(_ c: Coordinator) -> FitSCNView {
        let view = FitSCNView()
        view.onLayout = { [weak c] in c?.fit() }
        view.onScroll = { [weak c] d in c?.zoom(d) }
        view.scene = c.scene
        view.pointOfView = c.camera
        view.backgroundColor = .clear
        view.allowsCameraControl = false   // the board is fixed: can't rotate it, moving and zooming use our own gestures
        view.antialiasingMode = .multisampling2X
        view.autoenablesDefaultLighting = false
        view.addGestureRecognizer(NSClickGestureRecognizer(target: c, action: #selector(Coordinator.click(_:))))
        view.addGestureRecognizer(NSPanGestureRecognizer(target: c, action: #selector(Coordinator.pan(_:))))
        c.view = view
        return view
    }
    func makeCoordinator() -> Coordinator {
        BoardScene.prewarm()
        let c = BoardScene.shared!.coordinator
        c.select = { selected = $0 }
        c.lastReset = reset; c.lastLayout = layoutReset   // the screen's counters start from zero again on each visit, while the scene lives for the whole run: otherwise visiting the section after "Reset view" would reset the view and layout
        return c
    }

    func makeNSView(context: Context) -> SCNView { BoardScene.shared!.view }   // the theme and view catch up in updateNSView if they've changed

    func updateNSView(_ view: SCNView, context: Context) {
        let c = context.coordinator
        c.calm = calm
        let first = c.inputs == nil   // first appearance: take the saved view right away, with no flight
        let now = BoardInputs(states: states, busy: busy, alive: alive, active: active, selected: selected)
        if c.inputs != now { c.inputs = now; c.apply(now) }
        if c.lastReset != reset { c.lastReset = reset; c.resetCamera() }
        if c.lastLayout != layoutReset { c.lastLayout = layoutReset; c.resetLayout() }
        if c.flat != flat { c.setFlat(flat, animated: !first) }
        if c.dark != dark { c.setDark(dark) }
    }

    final class Coordinator: NSObject {
        struct P { var x: Double, z: Double }
        let scene = SCNScene()
        let camera = SCNNode()
        weak var view: SCNView?
        var select: (String?) -> Void
        var lastReset = 0, lastLayout = 0
        var inputs: BoardInputs?
        var calm = false
        private var tiles: [String: SCNNode] = [:]
        private var tops: [String: SCNNode] = [:]
        private var faces: [String: String] = [:]      // what's currently drawn on the lid (to avoid redrawing for nothing)
        private var glows: [String: SCNNode] = [:]
        private var links: [String: SCNNode] = [:]     // "from>to"
        private var pulsing: Set<String> = []
        private var ground: SCNNode?
        private var pos: [String: P] = [:]              // where the tiles sit (rearrangeable, stored in UserDefaults)
        private var dragging: String?
        private var panStart: (cam: SCNVector3, tile: P?, ex: P, ey: P)?   // the start of a drag: the camera, the tile and the world shift for one pixel
        private var userZoomed = false
        private var flying = false                      // the camera is mid-flight: fitting the scale to the window size doesn't interrupt it
        private(set) var flat = false
        private(set) var dark = false
        private var ink: NSColor { dark ? Style.Colors.inkDark : Style.Colors.inkLight }   // ink by theme (dynamic colors don't work inside the scene)
        private let center = SCNVector3(0, 0, 0.6)
        private let home = SCNVector3(-7.2, 10.0, 12.4)   // 3D: from above and to the left at roughly a 35° angle, the near corner at the bottom left (Google), the far one at the top right (Chrome)
        private let overhead = SCNVector3(0, 24.0, 0.6)   // flat: straight overhead

        init(select: @escaping (String?) -> Void) {
            self.select = select
            super.init()
            pos = Coordinator.savedLayout()
            build()
        }

        // ---- layout ----
        private static let layoutKey = "integrationTiles.v3"
        static func defaultLayout() -> [String: P] { Dictionary(Tile.all.map { ($0.id, P(x: $0.x, z: $0.z)) }, uniquingKeysWith: { _, last in last }) }
        static func savedLayout() -> [String: P] {
            var out = defaultLayout()
            if let saved = UserDefaults.standard.dictionary(forKey: layoutKey) as? [String: [Double]] {
                for (k, xz) in saved where xz.count == 2 && out[k] != nil && xz.allSatisfy({ $0.isFinite }) { out[k] = P(x: xz[0], z: xz[1]) }
            }
            return out
        }
        private func saveLayout() { UserDefaults.standard.set(pos.mapValues { [$0.x, $0.z] }, forKey: Coordinator.layoutKey) }

        /// "Rearrange": tiles glide smoothly back to their spots, ribbons follow them on every frame
        func resetLayout() {
            let from = pos, target = Coordinator.defaultLayout()
            scene.rootNode.removeAction(forKey: "layout")
            func place(_ k: Double) {
                for t in Tile.all {
                    guard let a = from[t.id], let b = target[t.id], let node = tiles[t.id] else { continue }
                    let p = P(x: a.x + (b.x - a.x) * k, z: a.z + (b.z - a.z) * k)
                    pos[t.id] = p
                    node.position = v(p.x, Double(node.position.y), p.z)
                }
                for key in links.keys { relink(key) }
            }
            if calm { place(1); saveLayout(); return }
            let duration = Look.shuffle
            scene.rootNode.runAction(.customAction(duration: duration) { _, elapsed in
                let t = min(1, Double(elapsed) / duration)
                place(t * t * (3 - 2 * t))
            }, forKey: "layout") { [weak self] in self?.saveLayout() }
        }

        private func ns(_ c: Color) -> NSColor { NSColor(c) }
        private func pastel(_ c: Color, _ f: CGFloat = 0.72) -> NSColor { ns(c).blended(withFraction: f, of: .white) ?? ns(c) }
        private func v(_ x: Double, _ y: Double, _ z: Double) -> SCNVector3 { SCNVector3(CGFloat(x), CGFloat(y), CGFloat(z)) }

        /// The surface's texture: paper with a thin grid (dark in dark mode), repeats infinitely
        private func gridImage() -> NSImage {
            let size = NSSize(width: 512, height: 512)
            let paper = dark ? NSColor(white: 0.13, alpha: 1) : (NSColor(Style.Colors.accent).blended(withFraction: 0.93, of: .white) ?? .white)
            let line = ink.withAlphaComponent(dark ? 0.12 : 0.08)
            return NSImage(size: size, flipped: false) { rect in
                paper.setFill(); rect.fill()
                line.setStroke()
                let path = NSBezierPath(); path.lineWidth = 1
                var x: CGFloat = 0
                while x <= rect.width { path.move(to: NSPoint(x: x, y: 0)); path.line(to: NSPoint(x: x, y: rect.height)); x += 64 }
                var y: CGFloat = 0
                while y <= rect.height { path.move(to: NSPoint(x: 0, y: y)); path.line(to: NSPoint(x: rect.width, y: y)); y += 64 }
                path.stroke()
                return true
            }
        }

        /// A soft round glow under a tile (white: the color comes from multiply)
        private func glowImage() -> NSImage {
            NSImage(size: NSSize(width: 256, height: 256), flipped: false) { rect in
                NSGradient(colors: [NSColor.white, NSColor.white.withAlphaComponent(0)])?.draw(in: NSBezierPath(ovalIn: rect), relativeCenterPosition: .zero)
                return true
            }
        }

        /// An SF Symbols icon in the needed color on a transparent background
        private func symbol(_ name: String, size: CGFloat, color: NSColor) -> NSImage? {
            guard let img = NSImage(systemSymbolName: name, accessibilityDescription: nil)?.withSymbolConfiguration(.init(pointSize: size, weight: .medium)) else { return nil }
            return NSImage(size: img.size, flipped: false) { rect in
                img.draw(in: rect)
                color.set(); rect.fill(using: .sourceAtop)
                return true
            }
        }

        /// A string fit to a width: the font size shrinks (down to Look.textMin of the original), beyond that the end is truncated with an ellipsis
        private func line(_ text: String, size: CGFloat, weight: NSFont.Weight, color: NSColor, width: CGFloat) -> NSAttributedString {
            let style = NSMutableParagraphStyle()
            style.lineBreakMode = .byTruncatingTail
            func make(_ pt: CGFloat) -> NSAttributedString {
                NSAttributedString(string: text, attributes: [.font: NSFont.systemFont(ofSize: pt, weight: weight), .foregroundColor: color, .paragraphStyle: style])
            }
            var pt = size
            while pt > size * Look.textMin, make(pt).size().width > width { pt -= 2 }
            return make(pt)
        }

        /// A tile's lid: everything is drawn on it — title, subtitle, icon, status dot. Sena's is a dark lid with her leaf
        private func faceImage(_ t: Tile, subtitle: String, dot: NSColor) -> NSImage {
            let W: CGFloat = 640, H = (W * CGFloat(t.l / t.w)).rounded()
            let core = t.id == "core"
            let bg: NSColor = core ? Style.Colors.inkLight : pastel(t.color)   // faces are drawn once: colors are fixed, independent of the theme
            let fg: NSColor = core ? .white : Style.Colors.inkLight
            let icon: NSImage? = core ? leafImage(size: 0.42 * H, color: YELLOW, template: false) : t.icon.flatMap { symbol($0, size: 0.3 * H, color: ns(t.color)) }
            return NSImage(size: NSSize(width: W, height: H), flipped: true) { rect in
                bg.setFill(); rect.fill()
                let pad = 0.06 * W, room = W - 2 * pad
                let title = self.line(t.title, size: 0.13 * W, weight: .semibold, color: fg, width: room)
                title.draw(in: NSRect(x: pad, y: pad * 0.9, width: room, height: title.size().height))
                let sub = self.line(subtitle, size: 0.085 * W, weight: .regular, color: fg.withAlphaComponent(0.65), width: room)
                sub.draw(in: NSRect(x: pad, y: pad * 0.9 + 0.17 * W, width: room, height: sub.size().height))
                if let icon = icon {
                    let s = icon.size, r = NSRect(x: W - pad - s.width, y: H - pad - s.height, width: s.width, height: s.height)
                    icon.draw(in: r, from: .zero, operation: .sourceOver, fraction: 1, respectFlipped: true, hints: nil)
                }
                dot.setFill()
                NSBezierPath(ovalIn: NSRect(x: pad, y: H - pad - 0.055 * W, width: 0.055 * W, height: 0.055 * W)).fill()
                return true
            }
        }

        private func build() {
            scene.background.contents = NSColor.clear
            camera.camera = SCNCamera()
            camera.camera?.usesOrthographicProjection = true   // isometric: no perspective distortion
            let (camPos, camRot, extent) = pose(flat: false)
            camera.position = camPos
            camera.orientation = camRot
            camera.camera?.orthographicScale = extent.halfHeight * (1 + Look.fitMargin)   // until the window's first layout; fit() refines it after that
            scene.rootNode.addChildNode(camera)

            let ambient = SCNNode(); ambient.light = SCNLight(); ambient.light?.type = .ambient; ambient.light?.intensity = 650
            let sun = SCNNode(); sun.light = SCNLight(); sun.light?.type = .directional; sun.light?.intensity = 800
            sun.light?.castsShadow = true; sun.light?.shadowRadius = 3; sun.light?.shadowColor = NSColor.black.withAlphaComponent(0.16)
            sun.light?.shadowMode = .deferred; sun.light?.orthographicScale = 20
            sun.eulerAngles = v(-1.0, -0.6, 0)
            scene.rootNode.addChildNode(ambient); scene.rootNode.addChildNode(sun)

            // the infinite, fixed surface
            let g = SCNNode(geometry: SCNPlane(width: 600, height: 600))
            let gm = g.geometry!.firstMaterial!
            gm.diffuse.contents = gridImage()
            gm.diffuse.wrapS = .repeat; gm.diffuse.wrapT = .repeat
            gm.diffuse.contentsTransform = SCNMatrix4MakeScale(75, 75, 1)   // one grid cell equals one scene unit
            gm.lightingModel = .lambert
            g.eulerAngles = v(-.pi / 2, 0, 0)
            scene.rootNode.addChildNode(g)
            ground = g

            let glow = glowImage()
            for t in Tile.all {
                let p = pos[t.id] ?? P(x: t.x, z: t.z)
                let node = SCNNode()
                node.position = v(p.x, 0, p.z)
                node.name = "n:" + t.id
                let halo = SCNNode(geometry: SCNPlane(width: CGFloat(t.w + 2.2), height: CGFloat(t.l + 2.2)))
                halo.geometry?.firstMaterial?.diffuse.contents = glow
                halo.geometry?.firstMaterial?.multiply.contents = ns(t.color)
                halo.geometry?.firstMaterial?.lightingModel = .constant
                halo.geometry?.firstMaterial?.writesToDepthBuffer = false
                halo.renderingOrder = -1
                halo.eulerAngles = v(-.pi / 2, 0, 0)
                halo.position = v(0, 0.01, 0)
                halo.opacity = 0
                halo.isHidden = true                           // while it isn't glowing, the halo is invisible and doesn't catch clicks
                halo.categoryBitMask = Look.haloCategory       // and a glowing one doesn't count as a tile for selection
                node.addChildNode(halo)
                glows[t.id] = halo
                // the dark base
                let base = SCNNode(geometry: SCNBox(width: CGFloat(t.w + 0.12), height: 0.18, length: CGFloat(t.l + 0.12), chamferRadius: 0.1))
                base.geometry?.firstMaterial?.diffuse.contents = Style.Colors.inkLight
                base.geometry?.firstMaterial?.lightingModel = .blinn
                base.position = v(0, 0.09, 0)
                node.addChildNode(base)
                // the lid: colored sides, a drawn face on top
                let lid = SCNNode(geometry: SCNBox(width: CGFloat(t.w), height: 0.26, length: CGFloat(t.l), chamferRadius: 0.06))
                let side = SCNMaterial()
                side.diffuse.contents = t.id == "core" ? Style.Colors.inkLight.blended(withFraction: 0.15, of: .white) : pastel(t.color, 0.55)
                side.lightingModel = .physicallyBased; side.roughness.contents = 0.3; side.metalness.contents = 0.05
                let face = SCNMaterial()
                face.lightingModel = .physicallyBased; face.roughness.contents = 0.25; face.metalness.contents = 0.02
                lid.geometry?.materials = [side, side, side, side, face, side]   // +Z, +X, -Z, -X, +Y (top), -Y
                lid.position = v(0, 0.18 + 0.13, 0)
                node.addChildNode(lid)
                tops[t.id] = lid
                scene.rootNode.addChildNode(node)
                tiles[t.id] = node
            }
            for (from, to) in Tile.links {
                let node = SCNNode(); node.name = "link"
                let body = SCNNode()
                body.geometry = SCNGeometry()
                node.addChildNode(body)
                scene.rootNode.addChildNode(node)
                links[from + ">" + to] = node
                relink(from + ">" + to)
            }
        }

        // ---- connection ribbons ----
        /// Where a ribbon goes from and to: from the source tile's nearest face to the target's nearest face, spread out along the
        /// face; the ends run under the tiles' bases, so the ribbon touches the tile with no gap. Returns the four points of a cubic curve
        private func anchors(_ a: Tile, _ b: Tile) -> (P, P, P, P) {
            let pa = pos[a.id] ?? P(x: a.x, z: a.z), pb = pos[b.id] ?? P(x: b.x, z: b.z)
            let dx = pb.x - pa.x, dz = pb.z - pa.z
            let gapX = abs(dx) - (a.w + b.w) / 2, gapZ = abs(dz) - (a.l + b.l) / 2
            let inset = 0.5   // how far the ribbon runs under the tile
            func clamp(_ x: Double, _ m: Double) -> Double { max(-m, min(m, x)) }
            if gapX >= gapZ {   // horizontally: from a side face to a side face
                let s = dx > 0 ? 1.0 : -1.0
                let start = P(x: pa.x + s * (a.w / 2 - inset), z: pa.z + clamp(dz * 0.3, a.l / 2 - 0.3))
                let end = P(x: pb.x - s * (b.w / 2 - inset), z: pb.z - clamp(dz * 0.15, b.l / 2 - 0.3))
                let k = max(0.8, abs(end.x - start.x) * 0.5)
                return (start, P(x: start.x + s * k, z: start.z), P(x: end.x - s * k, z: end.z), end)
            }
            let s = dz > 0 ? 1.0 : -1.0   // vertically: from the top or bottom face
            let start = P(x: pa.x + clamp(dx * 0.3, a.w / 2 - 0.3), z: pa.z + s * (a.l / 2 - inset))
            let end = P(x: pb.x - clamp(dx * 0.15, b.w / 2 - 0.3), z: pb.z - s * (b.l / 2 - inset))
            let k = max(0.8, abs(end.z - start.z) * 0.5)
            return (start, P(x: start.x, z: start.z + s * k), P(x: end.x, z: end.z - s * k), end)
        }

        private func bezier(_ p0: P, _ p1: P, _ p2: P, _ p3: P, _ n: Int) -> [P] {
            (0...n).map { i in
                let u = Double(i) / Double(n), m = 1 - u
                return P(x: m * m * m * p0.x + 3 * m * m * u * p1.x + 3 * m * u * u * p2.x + u * u * u * p3.x,
                         z: m * m * m * p0.z + 3 * m * m * u * p1.z + 3 * m * u * u * p2.z + u * u * u * p3.z)
            }
        }

        /// A flat ribbon from points: a strip of constant width, lying on the surface
        private func ribbon(_ pts: [P], width: Double, y: Double) -> SCNGeometry {
            var verts: [SCNVector3] = [], normals: [SCNVector3] = [], idx: [Int32] = []
            for i in pts.indices {
                let prev = pts[max(i - 1, 0)], next = pts[min(i + 1, pts.count - 1)]
                var dx = next.x - prev.x, dz = next.z - prev.z
                let len = max(sqrt(dx * dx + dz * dz), 1e-6); dx /= len; dz /= len
                let nx = -dz * width / 2, nz = dx * width / 2
                verts.append(v(pts[i].x + nx, y, pts[i].z + nz)); verts.append(v(pts[i].x - nx, y, pts[i].z - nz))
                normals.append(v(0, 1, 0)); normals.append(v(0, 1, 0))
                idx.append(Int32(i * 2)); idx.append(Int32(i * 2 + 1))
            }
            let element = SCNGeometryElement(indices: idx, primitiveType: .triangleStrip)
            return SCNGeometry(sources: [SCNGeometrySource(vertices: verts), SCNGeometrySource(normals: normals)], elements: [element])
        }

        /// Rebuild a ribbon based on the tiles' current layout
        private func relink(_ key: String) {
            let parts = key.split(separator: ">").map(String.init)
            guard parts.count == 2, let a = Tile.by(parts[0]), let b = Tile.by(parts[1]), let node = links[key], let body = node.childNodes.first else { return }
            let (p0, p1, p2, p3) = anchors(a, b)
            body.geometry = ribbon(bezier(p0, p1, p2, p3, Look.ribbonSteps), width: Look.ribbonWidth, y: Look.ribbonY)
            body.geometry?.firstMaterial?.lightingModel = .constant   // flat color with no highlights: the ribbon reads as a drawing on the surface
            body.geometry?.firstMaterial?.isDoubleSided = true
            body.geometry?.firstMaterial?.diffuse.contents = ink     // all links share one color (the theme's ink)
        }
        private func relink(tile id: String) { for key in links.keys where key.hasPrefix(id + ">") || key.hasSuffix(">" + id) { relink(key) } }

        // ---- camera ----
        /// The diagram's frame in the camera's view plane, computed from the real projection of all tiles' corners (with the base, the
        /// lid, and the selected one's lift) onto the camera's right and up vectors. shift — how far to move the camera within its own
        /// plane so the frame lands centered; halfWidth and halfHeight — half the frame's size, in scene units
        private func frame(of probe: SCNNode) -> (shift: SCNVector3, halfWidth: Double, halfHeight: Double) {
            let r = probe.worldRight, u = probe.worldUp, o = probe.position
            var lo = (u: Double.infinity, v: Double.infinity), hi = (u: -Double.infinity, v: -Double.infinity)
            for t in Tile.all {
                let p = pos[t.id] ?? P(x: t.x, z: t.z)
                for sx in [-1.0, 1.0] {
                    for sz in [-1.0, 1.0] {
                        for y in [0.0, Look.lift + Look.tileTop] {
                            let dx = p.x + sx * (t.w + 0.12) / 2 - Double(o.x), dy = y - Double(o.y), dz = p.z + sz * (t.l + 0.12) / 2 - Double(o.z)
                            let pu = dx * Double(r.x) + dy * Double(r.y) + dz * Double(r.z)
                            let pv = dx * Double(u.x) + dy * Double(u.y) + dz * Double(u.z)
                            lo = (min(lo.u, pu), min(lo.v, pv)); hi = (max(hi.u, pu), max(hi.v, pv))
                        }
                    }
                }
            }
            let cu = (lo.u + hi.u) / 2, cv = (lo.v + hi.v) / 2
            let shift = SCNVector3(r.x * CGFloat(cu) + u.x * CGFloat(cv), r.y * CGFloat(cu) + u.y * CGFloat(cv), r.z * CGFloat(cu) + u.z * CGFloat(cv))
            return (shift, (hi.u - lo.u) / 2, (hi.v - lo.v) / 2)
        }

        /// Camera position and rotation for a mode: 3D at an angle or straight overhead (the top of the screen is the board's far edge).
        /// The camera looks so the diagram's frame lands in the middle of the view; frame is needed for fitting the scale
        private func pose(flat: Bool) -> (SCNVector3, SCNQuaternion, (halfWidth: Double, halfHeight: Double)) {
            let probe = SCNNode()
            probe.position = flat ? overhead : home
            if flat { probe.look(at: center, up: v(0, 0, -1), localFront: v(0, 0, -1)) } else { probe.look(at: center) }
            let f = frame(of: probe)
            let at = SCNVector3(probe.position.x + f.shift.x, probe.position.y + f.shift.y, probe.position.z + f.shift.z)
            return (at, probe.orientation, (f.halfWidth, f.halfHeight))
        }

        /// Switch the view: the camera smoothly flies between positions, shadows and light stay as they are
        func setFlat(_ value: Bool, animated: Bool) {
            flat = value
            let (position, rotation, _) = pose(flat: value)
            let animate = animated && !calm
            flying = animate
            SCNTransaction.begin()
            SCNTransaction.animationDuration = animate ? Look.flight : 0
            SCNTransaction.animationTimingFunction = CAMediaTimingFunction(name: .easeInEaseOut)
            SCNTransaction.completionBlock = { [weak self] in self?.flying = false }
            camera.position = position
            camera.orientation = rotation
            if let s = fitScale(flat: value) { camera.camera?.orthographicScale = s }   // the scale flies along with the camera
            SCNTransaction.commit()
        }

        /// Scene theme: the surface and the ribbons are recolored, the tiles keep their own colors
        func setDark(_ value: Bool) {
            dark = value
            ground?.geometry?.firstMaterial?.diffuse.contents = gridImage()
            for lane in links.values { for c in lane.childNodes { c.geometry?.firstMaterial?.diffuse.contents = ink } }
        }

        /// "Reset view": fit to the window and center the diagram; the scale flies along with the camera, with no jump
        func resetCamera() {
            userZoomed = false
            setFlat(flat, animated: true)
        }
        /// The scale at which the whole diagram is visible with room to spare: based on whichever is bigger, the frame's width or height in the camera's projection
        private func fitScale(flat: Bool) -> Double? {
            guard !userZoomed, let view = view, view.bounds.height > 0, view.bounds.width > 0 else { return nil }
            let aspect = Double(view.bounds.width / view.bounds.height)
            let f = pose(flat: flat).2
            return max(f.halfHeight, f.halfWidth / aspect) * (1 + Look.fitMargin)
        }
        func fit() {
            guard !flying, let s = fitScale(flat: flat) else { return }
            camera.camera?.orthographicScale = s
        }
        func zoom(_ delta: CGFloat) {
            guard let cam = camera.camera else { return }
            userZoomed = true
            cam.orthographicScale = min(Look.zoom.upperBound, max(Look.zoom.lowerBound, cam.orthographicScale * Double(1 - delta * Look.zoomStep)))
        }

        // ---- gestures ----
        private func groundPoint(_ p: CGPoint) -> P? {
            guard let view = view, let ground = ground else { return nil }
            guard let h = view.hitTest(p, options: [.rootNode: ground, .ignoreHiddenNodes: false]).first else { return nil }
            return P(x: Double(h.worldCoordinates.x), z: Double(h.worldCoordinates.z))
        }
        private func tileId(under p: CGPoint) -> String? {
            guard let view = view else { return nil }
            var node = view.hitTest(p, options: [.searchMode: NSNumber(value: SCNHitTestSearchMode.closest.rawValue), .ignoreHiddenNodes: true,
                                                 .categoryBitMask: NSNumber(value: Look.tileCategory)]).first?.node
            while let n = node, !(n.name ?? "").hasPrefix("n:") { node = n.parent }
            return node?.name.map { String($0.dropFirst(2)) }
        }

        @objc func click(_ g: NSClickGestureRecognizer) {
            guard let view = view else { return }
            if let id = tileId(under: g.location(in: view)) { select(id) }
        }

        /// Drag: on a tile — move it (ribbons follow), on the board — pan the camera.
        /// The camera is orthographic, so the world shift per pixel is constant: computed once at the start, then smoothly with no more hitTest
        @objc func pan(_ g: NSPanGestureRecognizer) {
            guard let view = view else { return }
            switch g.state {
            case .began:
                // the gesture starts after a small mouse movement: the press point is the current one minus the distance traveled
                let now = g.location(in: view), moved = g.translation(in: view)
                let p = CGPoint(x: now.x - moved.x, y: now.y - moved.y)
                guard let g0 = groundPoint(p), let gx = groundPoint(CGPoint(x: p.x + 1, y: p.y)), let gy = groundPoint(CGPoint(x: p.x, y: p.y + 1)) else { return }
                dragging = tileId(under: p)
                panStart = (camera.position, dragging.flatMap { pos[$0] }, P(x: gx.x - g0.x, z: gx.z - g0.z), P(x: gy.x - g0.x, z: gy.z - g0.z))
            case .changed:
                guard let s = panStart else { return }
                let tr = g.translation(in: view)
                let dx = Double(tr.x) * s.ex.x + Double(tr.y) * s.ey.x, dz = Double(tr.x) * s.ex.z + Double(tr.y) * s.ey.z
                if let id = dragging, let start = s.tile, let node = tiles[id] {
                    pos[id] = P(x: start.x + dx, z: start.z + dz)
                    node.position = v(start.x + dx, Double(node.position.y), start.z + dz)
                    relink(tile: id)
                } else if dragging == nil {
                    camera.position = SCNVector3(s.cam.x - CGFloat(dx), s.cam.y, s.cam.z - CGFloat(dz))
                }
            case .ended, .cancelled:
                if dragging != nil { saveLayout() }
                dragging = nil; panStart = nil
            default: break
            }
        }

        // ---- states ----
        private func stateOf(_ id: String, _ states: [String: LinkState]) -> LinkState {
            if id.hasPrefix("svc:") { return states[String(id.dropFirst(4))] ?? .off }
            return .on
        }

        /// Bring tiles and ribbons to match the state (called only when something changed); everything animates as one
        func apply(_ i: BoardInputs) {
            let motion = calm ? 0 : Look.settle
            SCNTransaction.begin()
            SCNTransaction.animationDuration = motion
            SCNTransaction.completionBlock = { [weak self] in self?.hideDarkHalos() }
            for t in Tile.all {
                guard let node = tiles[t.id], let halo = glows[t.id], let lid = tops[t.id] else { continue }
                let s = stateOf(t.id, i.states)
                let isCore = t.id == "core", isAgent = t.id.hasPrefix("agent:")
                let code = isAgent ? String(t.id.dropFirst(6)) : ""
                let working = isCore ? (i.alive && i.active > 0) : isAgent ? i.busy[code] != nil : false
                let dim = isCore || isAgent ? false : (s == .off || s == .disabled || s == .unused)
                let dot: NSColor = isCore ? ns(i.alive ? Style.Colors.ok : Style.Colors.bad) : isAgent ? ns(i.busy[code] != nil ? Style.Colors.ok : Style.Colors.off) : s.dot
                let subtitle: String = isCore ? (i.alive ? (i.active > 0 ? "\(i.active) \(Fmt.plural(i.active, "задача", "задачи", "задач")) " + L("в работе") : L("свободна")) : L("выключена"))
                    : isAgent ? (i.busy[code].map { String($0.prefix(22)) } ?? t.subtitle) : (s == .on ? t.subtitle : s.text)
                // the lid is redrawn only when the subtitle or the dot's color changes
                let key = subtitle + "|" + dot.description
                if faces[t.id] != key {
                    faces[t.id] = key
                    lid.geometry?.materials[4].diffuse.contents = faceImage(t, subtitle: subtitle, dot: dot)
                }
                node.opacity = dim ? (s == .disabled || s == .unused ? Style.Colors.boardDimDisabled : Style.Colors.boardDimOff) : 1
                let on = t.id == i.selected
                let glow = on ? Style.Colors.boardHaloSelected : working ? Style.Colors.boardHaloWorking : 0
                if glow > 0 { halo.isHidden = false }
                node.position = v(Double(node.position.x), on ? Look.lift : 0, Double(node.position.z))
                halo.opacity = glow
                lid.geometry?.materials[4].emission.contents = on ? ns(t.color).blended(withFraction: 0.75, of: .black) : NSColor.black
            }
            for (key, lane) in links {
                let parts = key.split(separator: ">").map(String.init)
                let from = parts[0], to = parts[1]
                // a disabled or unconnected tile: its link is just as semi-transparent as the tile itself
                let fade = [from, to].compactMap { id -> CGFloat? in let s = stateOf(id, i.states); return s == .disabled || s == .unused ? Style.Colors.boardDimDisabled : s == .off ? Style.Colors.boardDimOff : nil }.min() ?? 1
                // only ribbons to her brain (Claude) and to busy agents (and from them) pulse; the rest don't blink during a task
                let flow = !calm && i.alive && i.active > 0 && (to == "svc:claude_api" || (to.hasPrefix("agent:") && i.busy[String(to.dropFirst(6))] != nil)
                                                                || (from.hasPrefix("agent:") && i.busy[String(from.dropFirst(6))] != nil))
                if flow && !pulsing.contains(key) {
                    pulsing.insert(key)
                    lane.runAction(.repeatForever(.sequence([.fadeOpacity(to: Style.Colors.boardPulseLow, duration: Look.pulseHalf), .fadeOpacity(to: 1, duration: Look.pulseHalf)])), forKey: "pulse")
                } else if !flow {
                    if pulsing.contains(key) { pulsing.remove(key); lane.removeAction(forKey: "pulse") }
                    lane.opacity = fade
                }
            }
            SCNTransaction.commit()
        }

        /// A faded-out halo is hidden completely: invisible, it shouldn't interfere with tile selection
        private func hideDarkHalos() { for halo in glows.values where halo.opacity == 0 { halo.isHidden = true } }
    }
}
