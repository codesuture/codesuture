
import os
import gc
import json
import shutil
import types
from datetime import datetime, timezone

from codesuture.persistence import CACHE_DIR


def rollback_runtime(name):
    """Restore original code for a function at runtime."""
    restored = False

    # Try from in-memory _ORIGINAL_CODES first
    try:
        from codesuture.tracer import _ORIGINAL_CODES
    except ImportError:
        _ORIGINAL_CODES = {}

    for func_key, original_code in list(_ORIGINAL_CODES.items()):
        if func_key.endswith(f":{name}"):
            # Search for functions whose current code has the same co_name
            # The patched code replaced the original, so we look for functions
            # with a code object named `name`
            import sys
            for mod in list(sys.modules.values()):
                if mod is None:
                    continue
                for attr_name in dir(mod):
                    try:
                        obj = getattr(mod, attr_name)
                    except Exception:
                        continue
                    if isinstance(obj, types.FunctionType) and obj.__code__.co_name == name:
                        obj.__code__ = original_code
                        print(f"[CodeSuture] Runtime code restored for {name}")
                        restored = True
                        break
                if restored:
                    break

            if not restored:
                # Also try gc.get_referrers on all code objects to find the patched one
                for obj in gc.get_objects():
                    if isinstance(obj, types.FunctionType) and obj.__code__.co_name == name and obj.__code__ is not original_code:
                        obj.__code__ = original_code
                        print(f"[CodeSuture] Runtime code restored for {name}")
                        restored = True
                        break

            if restored:
                del _ORIGINAL_CODES[func_key]
                break

    # If not found in _ORIGINAL_CODES, try loading from .orig.code file in the store
    if not restored and os.path.isdir(CACHE_DIR):
        import marshal
        for fname in os.listdir(CACHE_DIR):
            if fname.endswith(".orig.code") and name in fname:
                path = os.path.join(CACHE_DIR, fname)
                try:
                    with open(path, "rb") as f:
                        original_code = marshal.load(f)
                    for obj in gc.get_objects():
                        if isinstance(obj, types.FunctionType) and obj.__code__.co_name == name:
                            obj.__code__ = original_code
                            print(f"[CodeSuture] Runtime code restored for {name}")
                            restored = True
                            break
                except Exception:
                    pass
                if restored:
                    break

    if not restored:
        print(f"[CodeSuture] No live function found to restore for '{name}'.")

    return restored


def rollback_function(name):

    # Restore runtime code first, before deleting persisted files
    rollback_runtime(name)

    if not os.path.isdir(CACHE_DIR):
        print("[CodeSuture] Nothing to roll back.")
        return

    removed = 0
    for fname in list(os.listdir(CACHE_DIR)):

        # Strip known extensions to get the base name
        base = fname
        for ext in ('.orig.code', '.code', '.json'):
            if fname.endswith(ext):
                base = fname[:-len(ext)]
                break
        func_part = base.split(".", 1)[-1] if "." in base else base

        if func_part == name or base == name or func_part.endswith(name):
            path = os.path.join(CACHE_DIR, fname)
            os.remove(path)
            removed += 1

    if removed > 0:
        print(f"[CodeSuture] Rolled back patch for '{name}'. "
              f"Run your script again to re-patch if needed.")
    else:
        print(f"[CodeSuture] No patch found matching '{name}'.")

def rollback_all():

    count = 0
    if os.path.isdir(CACHE_DIR):
        count = len(os.listdir(CACHE_DIR))
        if count == 0:
            print("[CodeSuture] Nothing to roll back.")
            return
        shutil.rmtree(CACHE_DIR)
    else:
        print("[CodeSuture] Nothing to roll back.")
        return

    fp = ".codesuture_fingerprints"
    if os.path.isfile(fp):
        os.remove(fp)

    print(f"[CodeSuture] Cleared {count} patch file(s) and fingerprint registry.")

def rollback_dry_run():

    if not os.path.isdir(CACHE_DIR):
        print("[CodeSuture] Nothing to roll back. Store does not exist.")
        return

    json_files = [f for f in os.listdir(CACHE_DIR) if f.endswith(".json")]
    if not json_files:
        print("[CodeSuture] Nothing to roll back. No patches found.")
        return

    now = datetime.now(timezone.utc)
    print()
    print("  [CodeSuture DRY-RUN] Would remove the following patches:")
    print()
    for jf in json_files:
        path = os.path.join(CACHE_DIR, jf)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            func = data.get("func_name", "?")
            guard = data.get("guard_type", "?")
            age = "?"
            if "patched_at" in data:
                dt = datetime.fromisoformat(data["patched_at"])
                age = f"{(now - dt).days}d"
            print(f"    - {func}  guard={guard}  age={age}")
        except Exception:
            print(f"    - {jf}  (could not read metadata)")

    fp = ".codesuture_fingerprints"
    if os.path.isfile(fp):
        print(f"    - .codesuture_fingerprints (fingerprint registry)")
    print()
    print("  Run 'codesuture rollback --all' to actually remove them.")