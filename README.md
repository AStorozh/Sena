<div align="center">

# Sena

**A personal AI assistant that lives on a MacBook.**
It runs your computer, browser and code, remembers you, learns from its own tasks and listens to the room.

![macOS](https://img.shields.io/badge/macOS-14%2B-black)
![Python](https://img.shields.io/badge/Python-3.13-blue)
![Android](https://img.shields.io/badge/Android-Kotlin%20%2B%20Compose-green)
![License](https://img.shields.io/badge/license-PolyForm%20Strict%201.0.0-lightgrey)


</div>

---

## What it does

- **Talks everywhere.** Telegram, a native Android app, a native macOS app, and live voice by name ("Sena, ...").
- **Acts, not just answers.** Agents drive a real Chrome, the Mac itself (screen, keyboard, apps) and code in your projects via Claude Code.
- **Remembers.** One record store with hybrid search (meaning + words), people cards, voice prints, skills and versioned facts.
- **Learns.** After every task a judge grades the result on facts and writes lessons; a nightly pass cleans the memory.
- **Keeps private things at home.** Speech, embeddings, reading other people's messages and documents run on local models on the Mac.
- **Improves itself safely.** It edits its own code only in a separate git worktree, runs the whole test suite and waits for the owner's "yes".

## How it works

```
Telegram ─┐                                    ┌─ ag_web    browser (Chrome, patchright)
Android ──┼──>  ag_sena  (the coordinator)  ───┼─ ag_mac    the computer (screen, input, apps)
Mac app ──┤     context: memory, past tasks,   ├─ ag_code   Claude Code in a project
Voice ────┘     schedule, conversation         └─ tools     mail, calendar (via Luma), maps, news, ...
                         │
          background:  ag_judge (grades tasks, writes lessons)
                       ag_night (memory cleanup)
                       ag_ears  (what was overheard, who said it)
```

**Home and cloud.** Code does everything repeatable. Local models handle perception, memory and narrow judgements on private data.
The cloud model is used only where real reasoning is needed. One table, `config.ROLES`, says where each job runs, and the owner
can move a job with a switch in the app.

- **At home:** EmbeddingGemma 300M (memory), whisper.cpp (hearing), Silero VAD, Qwen3.5 4B (private judgements, vision),
  macOS Vision and PDFKit (PDFs, scans, photos), Piper (offline voice).
- **In the cloud:** Claude Opus 5.5 (the brain), Claude Sonnet 5.5 (voice commands), Claude Haiku 5.5 (browser and computer agents,
  narrow jobs), Gemini (speech, live voice, images and video). Models were picked by a measured bench (`tools/model_bench`).
- **No cloud?** Sena answers from memory with the local model and says so. Private data never falls back to the cloud: the job waits.

**Safety by design.**

- Anything irreversible (sending, deleting, paying) waits for the owner's "yes" (`sena/core/confirm.py`).
- Text from outside (mail, web pages, chats) is fenced and marks the task; it can't silently trigger writes.
- Code written by a model runs only in a restricted sandbox; shell commands run under a macOS seatbelt profile without access to data or keys.
- Passwords are encrypted, kept behind the macOS Keychain, and never appear in the model's text: agents get tickets, not values.
- Every paid call is logged with its price (`sena/core/costs.py`); every action goes to an event journal (`sena/core/events.py`).

## Quick start

You may download Sena and use it for yourself for free. You may not modify it, distribute it, present it as your own work or use it commercially ([PolyForm Strict 1.0.0](LICENSE)).

Requirements: macOS 14+ on Apple Silicon, Python 3.13, Homebrew.

```bash
cp .env.example .env              # fill in your keys (see the comments in the file)
pip install -r requirements.txt
brew install ffmpeg whisper-cpp llama.cpp cliclick blueutil
python -m sena.home setup         # once: local models into models/ (~5 GB, resumes where it stopped)
                                  # the "Sena" wake word model is not published: train it with tools/wakeword
python -m sena.google             # once: sign in to Google (mail, Sheets, Drive)
python -m sena.telegram.account   # once: sign in to your Telegram account (to read chats)
./run.sh                          # start, restarted on a crash; log: data/logs/bot.log
```

The Mac app (SwiftUI, no Xcode project needed) starts the server and controls the core:

```bash
cd mac && ./build.sh && open Sena.app
```

The Android app: `android/build.sh` builds `sena.apk`; the phone downloads it from the Mac at `/apk`.

Optional autostart at login:

```bash
sed "s|__SENA_DIR__|$PWD|g" tools/launchd/com.sena.agent.plist > ~/Library/LaunchAgents/com.sena.agent.plist
launchctl load ~/Library/LaunchAgents/com.sena.agent.plist
```

With only `APP_TOKEN` set, `python -m sena.phone` runs just the screen server: the apps open, nothing is spent.

## Project layout

```
sena/
  app.py, config.py       entry point (python -m sena); paths, keys, models: everything configurable
  core/                   the base: agent loop, LLM calls, local models, store, events, costs, sandbox, secrets
  memory/                 what Sena knows: facts, people, skills, rules, goals, the search index
  world/                  outside data: weather, maps, news, finance, travel, GitHub, research, ...
  google/                 Gmail, Sheets, Drive, YouTube, media (voice, images, video)
  agents/                 who does the work: ag_sena, ag_web, ag_mac, ag_code, ag_judge, ag_night, ag_ears
  telegram/               the bot and the owner's account
  phone/                  HTTP and voice servers for the apps; server-described panels
  exam/                   an exam of the brain's behaviour on fixed cases
  home/                   local models: setup, benches, datasets for fine-tuning
mac/                      native macOS app (SwiftUI)
android/                  native Android app (Kotlin, Jetpack Compose, Material 3)
tools/                    model bench, wake word training, launchd job
tests/                    unit and integration tests
data/                     everything Sena accumulates; never in git
```

Layers depend only downwards: `core` ← `memory` / `world` / `google` ← `agents` ← `telegram` / `phone` ← `app`.
`tests/test_structure.py` enforces it.

## Tests

```bash
python -m unittest                                   # Python: models and network are stubbed; browser tests use headless Chrome
cd android && ./gradlew :app:testDebugUnitTest       # Android: JVM tests, no phone needed
cd mac && ./build.sh                                 # Mac: the build; style rules are checked by tests/test_mac_style.py
```

CI runs all three on every push (`.github/workflows/ci.yml`).

## Extending

- **A tool for Sena:** a `tool(...)` description and a function with the same parameters in `sena/agents/ag_sena/tools.py`.
- **A paid service:** call it in one place and log it in `sena/core/costs.py`.
- **A new agent:** `sena/agents/ag_<name>.py` with a role, tools and `run(...)`; wire it in `ag_sena/tools.py`.
- **A new screen in the apps:** a panel in `sena/phone/panels.py`. Android and Mac draw any panel from its description.

## Language

The code and comments are in English; most developer docs (CLAUDE.md, the mac/, site/, tools/ and android/docs/
READMEs, the .env.example comments) are in Russian. What the owner sees (UI strings, Telegram messages)
and the prompts Sena sends to models are in Russian; the apps also ship English strings.

## License

[PolyForm Strict 1.0.0](LICENSE). You may download Sena and use it for yourself for free. You may not modify it, distribute it, present it as your own work or use it commercially.
Third-party components keep their own licenses: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
