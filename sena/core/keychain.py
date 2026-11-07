"""Sena's two keys in the Mac's login Keychain instead of files in data/: the key to the owner's secrets and the key to the copies
that leave the Mac. A copied data folder then holds no key at all. Reached through the system `security` tool: an item it creates
trusts only that tool, so reading it later asks nobody, and no other program gets it without the owner's permission.
(iCloud Keychain would carry the keys to a new Mac, but it is closed to a program without Apple's signature; the copy key can
be shown to the owner once from the app instead, to keep in his own password manager.) Tests and SENA_KEYCHAIN=off keep the
keys as files in data/state, as before."""
import subprocess

from sena import config
from sena.core import events

ACCOUNT = "sena"
SECRETS = "Sena: ключ секретов"
COPIES = "Sena: ключ копий вне Mac"
TOOL = "/usr/bin/security"
NOT_FOUND = 44   # what `security` exits with when there is no such item (errSecItemNotFound)


class Unavailable(RuntimeError):
    """The Keychain could not be read right now: locked, the prompt cancelled, `security` failed or hung. Not "there is no key"."""


def enabled():
    return config.KEYCHAIN


def get(service):
    """The key as hex text, or None if the Keychain has no such item (or is off). Raises Unavailable when it can't be read:
    a caller that took a locked Keychain for "no key yet" would make a new key and replace the real one for good."""
    if not enabled():
        return None
    try:
        done = subprocess.run([TOOL, "find-generic-password", "-a", ACCOUNT, "-s", service, "-w"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Unavailable(f"Связка ключей не ответила: {type(e).__name__}") from e
    if done.returncode == NOT_FOUND:
        return None
    if done.returncode:
        raise Unavailable(f"Связка ключей не открылась (код {done.returncode}): {(done.stderr or done.stdout).strip()[:200]}")
    return done.stdout.strip() or None


def _word(text):
    # one word of a command for `security -i`: in double quotes, \ and " escaped (its reader splits a line the way a shell does)
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _held(service, value):
    try:
        return get(service) == value
    except Unavailable:
        return False


def put(service, value):
    """Store (or replace) a key; True when the Keychain gives it back. The key goes to `security` on its standard input (its
    interactive mode, -i), not on its command line, which any process on the Mac can read while it runs (ps). Only if the Keychain
    doesn't hold it after that is the command line tried, as before, and the journal says so."""
    if not enabled():
        return False
    command = ["add-generic-password", "-U", "-a", ACCOUNT, "-s", service, "-w", value, "-T", TOOL]
    try:
        done = subprocess.run([TOOL, "-i"], input=" ".join(map(_word, command)) + "\n", capture_output=True, text=True, timeout=30)
        failed = done.returncode != 0 or not _held(service, value)
    except (OSError, subprocess.TimeoutExpired):
        failed = True
    if failed:
        try:
            done = subprocess.run([TOOL, *command], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as e:
            events.error("Связка ключей", e)
            return False
        if done.returncode or not _held(service, value):
            events.error("Связка ключей", RuntimeError((done.stderr or done.stdout).strip()[:200] or "ключ не сохранился"))
            return False
        events.error("Связка ключей", RuntimeError("ключ не принят через стандартный ввод (security -i): записан через командную строку"))
    events.log("secret", f"ключ в Связке ключей: {service}", name=service)   # the fact, never the value
    return True
