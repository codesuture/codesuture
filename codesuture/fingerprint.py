import hashlib
import dis
import json
import os
import threading
from datetime import datetime, timezone

_registry_lock = threading.RLock()

FINGERPRINT_FILE = ".codesuture_fingerprints"

def compute_fingerprint(code_obj, crash_offset: int, exc_type_name: str) -> str:
    instructions = list(dis.get_instructions(code_obj))
    offsets = [i.offset for i in instructions]
    try:
        idx = offsets.index(crash_offset)
    except ValueError:
        idx = len(instructions) - 1
    window = instructions[max(0, idx-2):idx+3]
    key = str([(i.opname, str(i.argval)) for i in window]) + ":" + exc_type_name
    return hashlib.sha256(key.encode()).hexdigest()[:16]

def load_registry() -> dict:
    if not os.path.exists(FINGERPRINT_FILE):
        return {}
    with open(FINGERPRINT_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_registry(registry: dict):
    with _registry_lock:
        with open(FINGERPRINT_FILE, "w", encoding="utf-8") as f:
            json.dump(registry, f, indent=2)

def lookup(fingerprint: str) -> dict | None:
    with _registry_lock:
        return load_registry().get(fingerprint)

def record(fingerprint: str, guard_type: str, target: str,
           func_name: str, error_type: str, default_value=None, key_name=None):
    with _registry_lock:
        registry = load_registry()
        if fingerprint not in registry:
            registry[fingerprint] = {
                "guard_type":  guard_type,
                "target":      target,
                "error_type":  error_type,
                "default_value": default_value,
                "key_name": key_name,
                "first_seen":  datetime.now(timezone.utc).isoformat(),
                "hit_count":   0,
                "functions":   []
            }
        entry = registry[fingerprint]
        entry["hit_count"] += 1
        if func_name not in entry["functions"]:
            entry["functions"].append(func_name)
        save_registry(registry)
        return entry
