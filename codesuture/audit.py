import os
import json
import sys
from datetime import datetime, timezone
from codesuture.utils import find_patch_store, load_all_patches


def run_audit(patch_store_path: str = None):

    store = find_patch_store(patch_store_path)

    if not store:
        print("[CodeSuture] No patch store found. Nothing has been patched yet.")
        return

    patches = load_all_patches(store)

    if not patches:
        print("[CodeSuture] Patch store exists but is empty.")
        return

    now = datetime.now(timezone.utc)

    col_func    = max(18, max(len(p.get("func_name","?")) for p in patches) + 2)
    col_guard   = 12
    col_target  = 10
    col_default = 10
    col_age     = 7

    try:
        "│".encode(sys.stdout.encoding or 'ascii')
        HAS_UNICODE = True
    except Exception:
        HAS_UNICODE = False

    def row(f, g, t, d, a):
        v = "│" if HAS_UNICODE else "|"
        return (f"{v} {f:<{col_func}} {v} {g:<{col_guard}} {v} "
                f"{t:<{col_target}} {v} {d:<{col_default}} {v} {a:<{col_age}} {v}")

    if HAS_UNICODE:
        sep = f"├─{'─'*col_func}─┼─{'─'*col_guard}─┼─{'─'*col_target}─┼─{'─'*col_default}─┼─{'─'*col_age}─┤"
        top = f"┌─{'─'*col_func}─┬─{'─'*col_guard}─┬─{'─'*col_target}─┬─{'─'*col_default}─┬─{'─'*col_age}─┐"
        bot = f"└─{'─'*col_func}─┴─{'─'*col_guard}─┴─{'─'*col_target}─┴─{'─'*col_default}─┴─{'─'*col_age}─┘"
    else:
        sep = (f"|-{'*'*col_func}-+-{'*'*col_guard}-+-"
               f"{'*'*col_target}-+-{'*'*col_default}-+-{'*'*col_age}-|")
        top = sep
        bot = sep

    print()
    print("  CodeSuture Audit Report")
    print()
    print(top)
    print(row("Function", "Guard", "Target", "Default", "Age"))
    print(sep)

    oldest_days = 0
    expired = 0
    for p in patches:
        func    = p.get("func_name", "?")
        guard   = p.get("guard_type", "?")
        target  = p.get("target",     "?")
        default = repr(p.get("default_value", "?"))[:col_default]
        age_str = "?"
        ttl_days = p.get("ttl_days", 7)
        if "patched_at" in p:
            try:
                dt = datetime.fromisoformat(p["patched_at"])
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                days = (now - dt).days
                oldest_days = max(oldest_days, days)
                age_str = f"{days}d"
                if days > ttl_days:
                    age_str += " [WARN]"
                    expired += 1
            except Exception:
                pass
        print(row(func, guard, target, default, age_str))

    print(bot)
    print()
    print(f"  Total: {len(patches)} active patch(es). "
          f"Oldest: {oldest_days}d. "
          f"{'[WARN] ' + str(expired) + ' expired - run codesuture rollback to clear.' if expired else 'All within TTL.'}")
    print()
    print("  Run 'codesuture rollback <function_name>' to remove a patch.")
    print("  Run 'codesuture rollback --all' to clear everything.")
    print()
