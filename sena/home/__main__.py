"""python -m sena.home [command] — the models on this Mac.
  (nothing)            what is installed and awake, who does what, how many calls stayed home today
  setup                download the home models into models/ (resumable) and check the programs they need
  bench                search quality with the real fingerprint model: the owner's questions and the paraphrase set
  check                the home model on its jobs: overheard conversations and the hourly check
  eval [role]          the home model on the cloud's examples of a learning role (all of them if none named)
  dataset <role>       the examples as a chat dataset for teaching the home model (mlx-lm LoRA)
  move <role> <home|cloud>   move a role
bench and check run on a temporary data folder: the owner's data is never touched by a measurement."""
import os
import shutil
import sys
import tempfile
import time
import urllib.request

COMMAND = sys.argv[1] if len(sys.argv) > 1 else "status"
if COMMAND in ("bench", "check"):   # before anything reads the data folder: measurements work on a throwaway one
    _tmp = tempfile.mkdtemp(prefix="sena-bench-")
    os.environ["SENA_DATA"] = _tmp
    os.environ["SENA_VAULT"] = os.path.join(_tmp, "vault")

from sena import config  # noqa: E402
from sena.core import costs, local, roles  # noqa: E402

HF = "https://huggingface.co"
DOWNLOADS = [   # file in models/ <- where it comes from; the big ones first in the list are what the roles need
    (config.LOCAL_EMBED, "ggml-org/embeddinggemma-300M-GGUF/resolve/main/embeddinggemma-300M-Q8_0.gguf", "отпечатки памяти, 0,3 ГБ"),
    (config.LOCAL_CHAT, "unsloth/Qwen3.5-4B-GGUF/resolve/main/Qwen3.5-4B-Q4_K_M.gguf", "домашняя модель, 2,7 ГБ"),
    (config.LOCAL_VISION, "unsloth/Qwen3.5-4B-GGUF/resolve/main/mmproj-F16.gguf", "её зрение, 0,7 ГБ"),
    (config.WHISPER_MODEL.name, "ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin", "точный слух, 0,6 ГБ"),
    (config.WHISPER_SMALL.name, "ggerganov/whisper.cpp/resolve/main/ggml-small.bin", "быстрый слух, 0,5 ГБ"),
    (config.VAD_MODEL.name, "ggml-org/whisper-vad/resolve/main/ggml-silero-v5.1.2.bin", "отличает речь от шума, 1 МБ"),
    (config.LOCAL_VOICE, "rhasspy/piper-voices/resolve/main/ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx", "голос Ирина, 63 МБ"),
    (config.LOCAL_VOICE + ".json", "rhasspy/piper-voices/resolve/main/ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx.json", "настройки голоса"),
]
PROGRAMS = [(local.SERVER, "brew install llama.cpp"), (local.WHISPER, "brew install whisper-cpp"), ("ffmpeg", "brew install ffmpeg")]


def _download(name, source):
    target = config.MODELS_DIR / name
    if target.exists():
        return "уже есть"
    part = target.with_name(target.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    request = urllib.request.Request(f"{HF}/{source}", headers={"Range": f"bytes={have}-"} if have else {})
    with urllib.request.urlopen(request, timeout=60) as r, open(part, "ab" if have and r.status == 206 else "wb") as out:
        total = int(r.headers.get("Content-Length") or 0) + (have if r.status == 206 else 0)
        done, shown = (have if r.status == 206 else 0), 0.0
        while True:
            block = r.read(1 << 20)
            if not block:
                break
            out.write(block)
            done += len(block)
            if total and time.time() - shown > 2:
                print(f"\r    {done / 1e6:.0f} из {total / 1e6:.0f} МБ", end="", flush=True)
                shown = time.time()
    part.replace(target)
    print()
    return "скачано"


def setup():
    config.MODELS_DIR.mkdir(exist_ok=True)
    print("Программы:")
    for program, hint in PROGRAMS:
        print(f"  {program}: {'есть' if shutil.which(program) else 'нет — ' + hint}")
    print(f"  чтение документов и фото (macOS Vision): {'есть' if local._vision() else 'нет — pip install pyobjc-framework-Vision pyobjc-framework-Quartz'}")
    print("Модели (в models/):")
    for name, source, what in DOWNLOADS:
        print(f"  {name} — {what}: ", end="", flush=True)
        try:
            print(_download(name, source))
        except OSError as e:
            print(f"не вышло ({e}); повтори команду — докачает с места остановки")
    print("Голос Ирина: набор данных без указанной лицензии — только для личного пользования.")


def status():
    s = local.status()
    print("Дома установлено: " + ", ".join(f"{k} {'да' if v else 'нет'}" for k, v in s["installed"].items()))
    print("Сервер моделей: " + (", ".join(f"{k} — {v}" for k, v in s["models"].items()) if s["server"] else "не запущен (поднимется сам при первой просьбе)"))
    print("\nКто что делает:")
    for row in roles.table():
        place = "дома   " if row["place"] == config.HOME else "облако "
        extra = f", примеров {row['examples']}" if row["learning"] else ""
        print(f"  {place} {row['title']} ({row['model']}{extra}) — {row['why']}")
    today = costs.summary("today")
    print(f"\nСегодня: дома {today['home']['calls']} вызовов, наружу {today['calls']} (на {costs.money(today['total'])})")


def bench():
    from sena.home import bench as b
    from sena.memory import index, recall
    started = time.time()
    ids = b.golden_fill()
    misses = []
    questions = b.golden_questions(ids)
    for question, args, answers in questions:
        rows = recall.find_rows(question, with_events=False, **args).get("rows", [])
        if not b.hit(rows, answers):
            misses.append(question or str(args))
    print(f"Вопросы владельца ({len(questions)}), ответ в пятёрке: {1 - len(misses) / len(questions):.0%}" + (f"; мимо: {misses}" if misses else ""))
    for kind in list(index.KINDS):
        index.drop(kind)
    b.paraphrase_fill()
    whole, meaning = b.paraphrase_score(), b.paraphrase_score(meaning_only=True)
    print(f"Перефразы ({len(b.QUESTIONS)}): поиск целиком — R@1 {whole['r1']:.0%}, R@5 {whole['r5']:.0%}, MRR {whole['mrr']:.2f}; "
          f"только смысл — R@1 {meaning['r1']:.0%}, R@5 {meaning['r5']:.0%}, MRR {meaning['mrr']:.2f}")
    for m in whole["misses"]:
        print("   мимо:", m)
    print(f"Модель отпечатков: {roles.embed_stamp()}; {time.time() - started:.0f} с")


def check():
    from sena.home import bench as b
    for name, run in (("Разбор подслушанного", b.ears_check), ("Проверка «есть ли важное»", b.heartbeat_check)):
        ok, total, each, misses = run()
        print(f"{name}: {ok} из {total}, {each} с на случай")
        for m in misses:
            print("   мимо:", m)


def main():
    args = sys.argv[2:]
    if COMMAND == "setup":
        setup()
    elif COMMAND == "bench":
        bench()
    elif COMMAND == "check":
        check()
    elif COMMAND == "eval":
        for role in args or config.LEARNING:
            r = roles.evaluate(role)
            print(f"{roles.TITLES[role]}: примеров {r['examples']}" + (f", близость к облаку {r['agree']:.0%} на {r['tested']} примерах" if r["tested"] else f" (нужно {roles.MIN_EXAMPLES})"))
    elif COMMAND == "dataset" and args:
        made = roles.dataset(args[0])
        if not made:
            print("Примеров нет: они копятся, пока роль работает в облаке")
        else:
            print(f"Готово: {made['folder']} — обучение {made['train']}, проверка {made['valid']}.\n"
                  "Обучить (нужен pip install mlx-lm): mlx_lm.lora --model Qwen/Qwen3.5-4B --train "
                  f"--data {made['folder']} --batch-size 1 --num-layers 8 --iters 600")
    elif COMMAND == "move" and len(args) == 2:
        print(roles.move(args[0], args[1]))
    elif COMMAND == "status":
        status()
    else:
        print(__doc__)
    local.stop()


if __name__ == "__main__":
    main()
