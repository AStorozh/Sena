#!/bin/zsh
# Starts Sena and keeps her up. A crash is restarted in 3 seconds; one that keeps falling waits longer each time, and after five
# falls in a row only the screen is left (the apps work, «Запустить Сену» there tries again). A start that can't work (code 78:
# keys missing, the port held by another Sena) isn't restarted either: the screen comes back. One run.sh per Mac. The log:
# logs/bot.log in the data folder.
cd "$(dirname "$0")"
PYTHON=${PYTHON:-/Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13}
# the data folder is the core's own (config.DATA: SENA_DATA from the environment or .env, data/ by default): the stop request, the
# start mark and the update record are only met where the core and the apps write them
DATA=$("$PYTHON" -c 'from sena import config; print(config.DATA)' 2>/dev/null)
DATA=${DATA:-data}
STATE=$DATA/state
LOG=$DATA/logs/bot.log
mkdir -p "$DATA/logs" "$STATE"
chmod -R go-rwx "$DATA" .env 2>/dev/null   # personal data and keys are closed to other users of the Mac (Telegram session, tokens, memory)
[ -t 1 ] || exec >/dev/null                # from launchd and the app the output goes only into bot.log: no second log nobody trims
tell() {   # a line to the owner in Telegram, in the background: Telegram out of reach (ten tries of a minute) never holds a start
  "$PYTHON" -c 'import sys; from sena.telegram import api; api.send_text(sys.argv[1])' "$1" >/dev/null 2>&1 &
}
leave_screen() {   # the screen without the core: the apps work and say she is off, «Запустить Сену» is there
  rm -f "$STATE/core_starting"
  "$PYTHON" -u -m sena.phone 2>&1 | tee -a "$LOG"
  exit 0
}
# one run.sh per Mac: a second one («Запустить», launchd on top of the one the app started) waits for the first to hand over, then leaves
zmodload zsh/system
: >> "$STATE/run.lock"
if ! zsystem flock -t 15 "$STATE/run.lock" 2>/dev/null; then
  echo "Сена уже запущена другим run.sh: этот выходит" | tee -a "$LOG"
  exit 0
fi
rotate() {   # over 50 MB: a copy in bot.log.old (the previous one is overwritten), the log starts afresh; tee goes on writing to it
  [ -f "$LOG" ] && [ "$(stat -f%z "$LOG")" -gt 52428800 ] && cp "$LOG" "$LOG.old" && : > "$LOG"
}
( while kill -0 $$ 2>/dev/null; do rotate; sleep 600; done ) >/dev/null 2>&1 &   # also while the core runs for weeks, not only between restarts
ROTATE=$!
trap 'pkill -P $ROTATE 2>/dev/null; kill $ROTATE 2>/dev/null' EXIT   # leaving, run.sh takes the loop with it, and the loop's sleep
# caffeinate -s: on power the Mac doesn't sleep while Sena works (or the night jobs and the reminders would sleep through)
CAFF=(); command -v caffeinate >/dev/null && CAFF=(caffeinate -s)
pause=3 falls=0
while true; do
  date +%s > "$STATE/core_starting"   # the app doesn't raise its own screen while the core comes up: the core removes the mark once it holds the port
  start=$(date +%s)
  "${CAFF[@]}" "$PYTHON" -u -m sena 2>&1 | tee -a "$LOG"
  code=${pipestatus[1]}
  uptime=$(( $(date +%s) - start ))
  said=$(tail -n 1 "$LOG")   # the core's last line: on 78 it is the reason
  if [ -f "$STATE/stop_requested" ]; then   # stopped from the apps: the core isn't raised again, the screen stays
    rm -f "$STATE/stop_requested"
    echo "Сена остановлена по команде; остаётся экран без ядра" | tee -a "$LOG"
    leave_screen
  fi
  # a change to her own code was applied (ag_code writes update_pending, the commit before it first) and the new code fell or
  # couldn't start within two minutes: back to that commit, and the old code is started. A stop (0) or a restart asked for (3) is
  # no verdict on the new code: the record waits for the next run. `git reset --keep` moves only the files the update changed
  # and refuses when one of them holds changes nobody committed: the owner's work in the tree is never wiped
  if [ -f "$STATE/update_pending" ] && [ "$uptime" -lt 120 ] && [ "$(stat -f%m "$STATE/update_pending")" -lt "$start" ] \
      && [ "$code" -ne 0 ] && [ "$code" -ne 3 ]; then
    from=$(cut -d' ' -f1 "$STATE/update_pending")
    rm -f "$STATE/update_pending"   # one verdict per update: a refused rollback isn't tried, and told, again on every fall
    if git reset -q --keep "$from" >> "$LOG" 2>&1; then
      echo "Обновление не прижилось (прожила $uptime с, код $code): откатилась на $from, запускаю прежний код" | tee -a "$LOG"
      tell "Обновление своего кода не прижилось: откатилась на прежний код и запускаюсь на нём. Подробности в bot.log"
    else
      echo "Обновление не прижилось (код $code), а откатиться на $from git не дал (обычно это незакоммиченные правки в тех же файлах): ничего не трогаю" | tee -a "$LOG"
      tell "Обновление своего кода не прижилось, а откатиться я не смогла: git отказал — обычно это незакоммиченные правки в изменённых им файлах. Ничего не трогала; подробности в bot.log"
    fi
  elif [ "$code" -eq 78 ]; then   # keys missing or the port held by another Sena: a restart can't help (app.NO_RESTART)
    echo "Сена не запустилась (причина строкой выше), перезапускать не буду; остаётся экран" | tee -a "$LOG"
    tell "Я не запустилась: $said. Перезапускать не буду: поправь это и нажми «Запустить Сену» в приложении."
    leave_screen
  fi
  [ "$uptime" -ge 120 ] && { pause=3; falls=0; }   # she lived long: a chance failure, quick again
  case $code in 0|3) ;; *) falls=$(( falls + 1 )) ;; esac
  if [ "$falls" -ge 5 ]; then   # falling again and again: a restart doesn't help — the screen stays, «Запустить» in the app tries again
    echo "Сена упала $falls раз подряд, не прожив и двух минут: оставляю экран без ядра, причина — в логе выше" | tee -a "$LOG"
    tell "Я падаю раз за разом и не поднимаюсь: оставила только экран. Причина — в bot.log; «Запустить Сену» в приложении попробует снова."
    leave_screen
  fi
  echo "Сена выключилась (код $code), перезапуск через $pause с" | tee -a "$LOG"
  date +%s > "$STATE/core_starting"
  sleep $pause
  case $code in   # 0 and 3 are a restart asked for (from the app, after a change to her own code): the pause doesn't grow
    0|3) ;;
    *) pause=$(( pause * 2 > 60 ? 60 : pause * 2 )) ;;
  esac
done
