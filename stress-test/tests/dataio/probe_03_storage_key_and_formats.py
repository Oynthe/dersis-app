"""Storage / persistence: Risks 4, 7, 10 + encrypt/decrypt timing & size.

R4 (High):  a short/corrupt key.bin is silently backed up & regenerated,
            orphaning every previously-saved .egu file (storage.py:183-202).
R7 (Medium):legacy plain-JSON ASCII files fail to load because they are
            mis-detected as Fernet tokens (storage.py:385).
R10:        is the encryption a hardcoded/derivable key, or a real random key?
            Where does the key live relative to the data?
"""
import os, sys, tempfile, json, time, importlib

_sb = tempfile.mkdtemp(prefix="dersis_probe03_")
os.environ["HOME"] = _sb
os.environ["USERPROFILE"] = _sb
sys.path.insert(0, r"C:\dev\dersis-app")

import scheduler_app.storage.storage as storage  # submodule exposes _privates

sys.path.insert(0, r"C:\dev\dersis-app\stress-test\tests")
from _fixtures.dataset_gen import make_preset

# ─────────────────────────────────────────────────────────────────────────
print("=" * 75)
print("R4: truncated key.bin -> silent regeneration -> orphaned data")
print("=" * 75)
save_path = os.path.join(storage.sub_dir(storage.SAVES_DIR), "victim.egu")
state = make_preset("small")
storage.save_encrypted(state, save_path)
key_file = storage._key_path()
with open(key_file, "rb") as f:
    good_key = f.read()
print(f"   saved state ({len(state['classes'])} classes); key.bin = {len(good_key)} bytes")

# reload works normally (fresh process simulation: clear cache)
storage._cached_key = None
back = storage.load_encrypted(save_path)
print("   sanity reload OK, classes =", len(back["classes"]))

# Now truncate key.bin to 20 bytes (e.g. partial write / disk corruption)
with open(key_file, "wb") as f:
    f.write(good_key[:20])
storage._cached_key = None
backups_before = set(os.listdir(storage.sub_dir(storage.BACKUPS_DIR)))
try:
    data = storage.load_encrypted(save_path)
    print("   load after truncation: SUCCESS classes =", len(data["classes"]))
except Exception as e:
    print(f"   *** load after truncation FAILED: {type(e).__name__}: {e} ***")
new_key = open(key_file, "rb").read()
backups_after = set(os.listdir(storage.sub_dir(storage.BACKUPS_DIR)))
regenerated = (len(new_key) == 32 and new_key != good_key)
print(f"   key.bin now {len(new_key)} bytes; regenerated={regenerated}; "
      f"old key backed up: {sorted(backups_after - backups_before)}")
if regenerated:
    print("   *** CONFIRMED: key silently regenerated -> ALL prior .egu now undecryptable ***")

# ─────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 75)
print("R7: legacy plain-JSON files (ASCII vs non-ASCII)")
print("=" * 75)
storage._cached_key = None
# ASCII-only JSON (no Turkish chars in first 80 bytes)
ascii_path = os.path.join(_sb, "legacy_ascii.json")
with open(ascii_path, "w", encoding="utf-8") as f:
    json.dump({"classes": [], "days": ["monday"], "note": "plain legacy file"}, f)
try:
    d = storage.load_encrypted(ascii_path)
    print("   ASCII plain-JSON load: SUCCESS ->", type(d).__name__)
except Exception as e:
    print(f"   *** ASCII plain-JSON load FAILED: {type(e).__name__}: {e} ***")

# Non-ASCII JSON (Turkish char early)
utf_path = os.path.join(_sb, "legacy_utf.json")
with open(utf_path, "w", encoding="utf-8") as f:
    json.dump({"ğşıİ_note": "türkçe", "classes": []}, f, ensure_ascii=False)
try:
    d = storage.load_encrypted(utf_path)
    print("   non-ASCII plain-JSON load: SUCCESS ->", type(d).__name__)
except Exception as e:
    print(f"   non-ASCII plain-JSON load FAILED: {type(e).__name__}: {e}")

# ─────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 75)
print("R10: encryption key nature & location")
print("=" * 75)
# Fresh sandbox key
sb2 = tempfile.mkdtemp(prefix="dersis_probe03b_")
os.environ["HOME"] = sb2
os.environ["USERPROFILE"] = sb2
importlib.reload(storage)
storage._cached_key = None
p = os.path.join(storage.sub_dir(storage.SAVES_DIR), "x.egu")
storage.save_encrypted({"classes": [], "secret": "hi"}, p)
k1 = open(storage._key_path(), "rb").read()
print(f"   key length = {len(k1)} bytes ({len(k1)*8} bits)")
print(f"   key stored at: {storage._key_path()}")
print(f"   data stored at: {os.path.dirname(p)}")
print(f"   key lives INSIDE the same Documents/Dersis tree as data: "
      f"{os.path.commonpath([storage._key_path(), p]) == storage.root_dir()}")
# Is the key derivable/hardcoded? Regenerate on a second fresh install:
sb3 = tempfile.mkdtemp(prefix="dersis_probe03c_")
os.environ["HOME"] = sb3
os.environ["USERPROFILE"] = sb3
importlib.reload(storage)
storage._cached_key = None
storage.save_encrypted({"classes": []}, os.path.join(storage.sub_dir(storage.SAVES_DIR), "y.egu"))
k2 = open(storage._key_path(), "rb").read()
print(f"   two fresh installs produce DIFFERENT keys: {k1 != k2} "
      f"(=> not hardcoded; random per-install)")
print("   NOTE: key file is plaintext, unprotected, colocated with ciphertext.")

# ─────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 75)
print("Encrypt/decrypt time & file size vs state size")
print("=" * 75)
importlib.reload(storage)
storage._cached_key = None
for preset in ("tiny", "small", "normal", "large", "very_large"):
    st = make_preset(preset)
    pth = os.path.join(storage.sub_dir(storage.SAVES_DIR), f"{preset}.egu")
    t0 = time.perf_counter()
    storage.save_encrypted(st, pth)
    t1 = time.perf_counter()
    storage._cached_key = storage._cached_key  # keep cache
    t2 = time.perf_counter()
    _ = storage.load_encrypted(pth)
    t3 = time.perf_counter()
    raw = len(json.dumps(st, ensure_ascii=False).encode("utf-8"))
    disk = os.path.getsize(pth)
    print(f"   {preset:11s} classes={len(st['classes']):5d} "
          f"json={raw/1024:8.1f}KB egu={disk/1024:8.1f}KB "
          f"save={ (t1-t0)*1000:7.2f}ms load={(t3-t2)*1000:7.2f}ms")

print("\nSandbox:", _sb)
