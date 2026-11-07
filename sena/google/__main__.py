"""python -m sena.google — log into Google (once); status — check the login; models — image and video models available to the Gemini key."""
import sys

from sena.google import auth

if len(sys.argv) > 1 and sys.argv[1] == "status":
    print({"ok": "вход есть", "none": "входа ещё нет", "expired": "вход устарел, нужен новый"}[auth.state()])
elif len(sys.argv) > 1 and sys.argv[1] == "models":
    from sena.google import media   # checks the Gemini key and that the image/video model names are current
    print("\n".join(media.available_models()))
else:
    scopes = auth.login()
    print("Готово. Выданы права:\n  " + "\n  ".join(s.rsplit("/", 1)[-1] for s in scopes))
