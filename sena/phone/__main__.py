"""python -m sena.phone: just the screen — the phone app and the Mac remote — without the bot, schedule, or ambient listening.
This way the remote opens instantly, and Sena herself (the core) is turned on by a button in the remote: the screen hands it the port (desk core_start),
and when the core stops, run.sh brings the screen back up. The screen is Sena switched off (core/power.py): it never calls a paid model,
starts no home model, agent's browser or Claude Code, and refuses what only the core does. Coming up, it sweeps what a stopped or
crashed core may have left running (agents/programs.py) — only once it holds the port, so a core still at work is never touched.
She is off, not gone: the live voice's door says so (the apps heard "the Mac doesn't answer"), and so does Telegram, whose
messages wait for the core. The app password (APP_TOKEN) is needed here too: without it the server won't start."""
import threading

from sena import config
from sena.agents import programs
from sena.core import events, power, safety
from sena.phone import server, voice
from sena.telegram import bot


def main():
    safety.router()   # every print and traceback is masked, stdout and stderr each: a key or a password never reaches bot.log
    power.set_off()
    config.STARTING_FILE.unlink(missing_ok=True)   # the previous core startup failed — the marker shouldn't be left hanging
    httpd = server.make_server()   # holding the port means no core is running: from here on the sweep is safe
    safety.background(programs.stop_all, what="Остатки после остановки")
    voice.start()   # its door refuses while she is off, in words: «Сена выключена…»
    threading.Thread(target=bot.answer_while_off, daemon=True).start()   # one word in Telegram that she is off; the messages wait for her
    events.log("start", f"Экран без ядра запущен, порт {config.APP_PORT}")
    print(f"Экран без ядра: порт {config.APP_PORT} (телефон и приложение Mac)", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
