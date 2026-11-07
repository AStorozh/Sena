"""A copy that leaves the Mac: the night's backup set, encrypted, in the owner's iCloud Drive (Sena/), so a lost or broken Mac
doesn't take Sena's memory with it. One file per night (AES-GCM, the newest KEEP kept); inside — the records database, memory,
personality, settings, skills, expenses, the history of versions, the owner's secrets with their key. Only one key opens it:
the copy key, kept in the Mac's Keychain (core/keychain.py). The owner keeps it himself as a recovery key:
    python -m sena.core.offsite key                      the recovery key, to save in a password manager
    python -m sena.core.offsite restore <copy> <folder>  [--key <hex>]  unpacks a copy into a folder (never over data/)
Nothing here sends anything anywhere: iCloud Drive syncs the folder on its own, the content is already encrypted."""
import io
import os
import sys
import time
import zipfile
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from sena import config
from sena.core import events, keychain, store

MAGIC = b"SENA-COPY-1\n"
KEEP = 14


def _key():
    """The copy key: from the Keychain, or a file in data/state when the Keychain is off (tests). Made once."""
    if keychain.enabled():
        found = keychain.get(keychain.COPIES)
        if found:
            return bytes.fromhex(found)
        key = os.urandom(32)
        if keychain.put(keychain.COPIES, key.hex()) and keychain.get(keychain.COPIES) == key.hex():
            return key
        raise RuntimeError("Связка ключей не приняла ключ копий: копия вне Mac не сделана")
    path = config.STATE_DIR / "offsite.key"
    if not path.exists():
        store.write_bytes(path, os.urandom(32), mode=0o600)
    return path.read_bytes()


def recovery_key():
    events.log("secret", "показан ключ восстановления копий", name="offsite")   # the fact, never the value
    return _key().hex()


def folder():
    return None if str(config.OFFSITE_DIR) == "off" else Path(config.OFFSITE_DIR)


def make(files, extra=None):
    """Pack files (and extra {name: bytes}) into one encrypted copy in the off-site folder; the oldest beyond KEEP go.
    Returns the copy's path, or None when there is no off-site folder (iCloud Drive is off on this Mac)."""
    target_dir = folder()
    if target_dir is None or not target_dir.parent.exists():
        return None
    target_dir.mkdir(parents=True, exist_ok=True)
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as z:
        for path in files:
            path = Path(path)
            if path.is_file():
                z.write(path, arcname=path.name)
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    nonce = os.urandom(12)
    blob = MAGIC + nonce + AESGCM(_key()).encrypt(nonce, raw.getvalue(), MAGIC)
    target = target_dir / f"sena-{time.strftime('%Y%m%d-%H%M%S')}.copy"
    store.write_bytes(target, blob, mode=0o600)
    for old in sorted(target_dir.glob("sena-*.copy"))[:-KEEP]:
        old.unlink()   # only copies go, never an original
    events.log("backup", f"копия вне Mac: {target.name}, {len(blob) // 1024} КБ", path=str(target), bytes=len(blob))
    return target


def restore(copy, into, key_hex=None):
    """Unpack a copy into a folder of the owner's choice: the files as they were that night, for him to look at and put back."""
    blob = Path(copy).read_bytes()
    if not blob.startswith(MAGIC):
        raise ValueError("это не копия Сены")
    body = blob[len(MAGIC):]
    key = bytes.fromhex(key_hex) if key_hex else _key()
    data = AESGCM(key).decrypt(body[:12], body[12:], MAGIC)
    into = Path(into)
    if into.resolve() == Path(config.DATA).resolve() or Path(config.DATA).resolve() in into.resolve().parents:
        raise ValueError("копия разворачивается рядом, не поверх data/: так ничего не затрётся")
    into.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(into)
        return z.namelist()


def status():
    """For the apps: where the copies go, how many, the newest one."""
    target_dir = folder()
    copies = sorted(target_dir.glob("sena-*.copy")) if target_dir and target_dir.exists() else []
    newest = copies[-1] if copies else None
    return {"folder": str(target_dir) if target_dir else "", "available": bool(target_dir and target_dir.parent.exists()), "count": len(copies),
            "newest": newest.name if newest else "", "time": newest.stat().st_mtime if newest else 0, "kb": newest.stat().st_size // 1024 if newest else 0}


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["key"]:
        print(recovery_key())
    elif args[:1] == ["restore"] and len(args) >= 3:
        key = args[args.index("--key") + 1] if "--key" in args else None
        names = restore(args[1], args[2], key)
        print(f"Развёрнуто в {args[2]}: {', '.join(names)}")
    else:
        print(__doc__)
