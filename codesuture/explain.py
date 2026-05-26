
import os
import json
import sys
from datetime import datetime, timezone
from codesuture.utils import find_patch_store, load_all_patches


def run_explain(func_name=None):

    store = find_patch_store()

    if not store:
        print("[CodeSuture] No active patches.")
        return

    patches = load_all_patches(store)

    if not patches:
        print("[CodeSuture] No active patches.")
        return

    if func_name:
        patches = [p for p in patches if func_name.lower() in p.get("func_name", "").lower()]
        if not patches:
            print(f"[CodeSuture] No patches found for '{func_name}'.")
            return

    now = datetime.now(timezone.utc)

    try:
        "│".encode(sys.stdout.encoding or "ascii")
        HAS_UNICODE = True
    except Exception:
        HAS_UNICODE = False

    col_func    = max(12, max(len(p.get("func_name", "?")) for p in patches) + 2)
    col_guard   = max(12, max(len(p.get("guard_type", "?")) for p in patches) + 2)
    col_target  = max(10, max(len(str(p.get("target", "?"))) for p in patches) + 2)
    col_default = max(10, max(len(repr(p.get("default_value", "?"))[:15]) for p in patches) + 2)
    col_age     = 12
    col_safe    = 9

    v = "│" if HAS_UNICODE else "|"

    def row(f, g, t, d, a, s):
        return (f"{v} {f:<{col_func}} {v} {g:<{col_guard}} {v} "
                f"{t:<{col_target}} {v} {d:<{col_default}} {v} "
                f"{a:<{col_age}} {v} {s:<{col_safe}} {v}")

    if HAS_UNICODE:
        sep = (f"├─{'─'*col_func}─┼─{'─'*col_guard}─┼─"
               f"{'─'*col_target}─┼─{'─'*col_default}─┼─{'─'*col_age}─┼─{'─'*col_safe}─┤")
        top = (f"┌─{'─'*col_func}─┬─{'─'*col_guard}─┬─"
               f"{'─'*col_target}─┬─{'─'*col_default}─┬─{'─'*col_age}─┬─{'─'*col_safe}─┐")
        bot = (f"└─{'─'*col_func}─┴─{'─'*col_guard}─┴─"
               f"{'─'*col_target}─┴─{'─'*col_default}─┴─{'─'*col_age}─┴─{'─'*col_safe}─┘")
    else:
        sep = (f"+-{'-'*col_func}-+-{'-'*col_guard}-+-"
               f"{'-'*col_target}-+-{'-'*col_default}-+-{'-'*col_age}-+-{'-'*col_safe}-+")
        top = sep
        bot = sep

    print()
    print("  CodeSuture Explain - Active Patches")
    print()
    print(top)
    print(row("Function", "Guard type", "Target", "Default value", "Age (days)", "Safe?"))
    print(sep)

    for p in patches:
        func    = p.get("func_name", "?")
        guard   = p.get("guard_type", "?")
        target  = str(p.get("target", "?"))
        default = repr(p.get("default_value", "?"))[:15]
        age_str = "?"
        if "patched_at" in p:
            try:
                dt = datetime.fromisoformat(p["patched_at"])
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                days = (now - dt).days
                age_str = f"{days}d ago (patched)"
            except Exception:
                pass
        safe = _assess_safety(p)
        print(row(func, guard, target, default, age_str, safe))

    print(bot)
    print()
    print(f"  Total: {len(patches)} active patch(es).")
    print()

def _assess_safety(patch_data):

    default = patch_data.get("default_value")
    guard_type = patch_data.get("guard_type", "")
    target = str(patch_data.get("target", ""))

    string_methods = {"strip", "upper", "lower", "title", "capitalize",
                      "casefold", "swapcase", "encode", "replace", "split"}

    is_string_downstream = any(m in target.lower() for m in string_methods)

    if default == "" and (guard_type == "null_guard" or is_string_downstream):
        return "LIKELY"

    if default == 0 and is_string_downstream:
        return "RISKY"

    if default == "" and guard_type in ("null_guard", "subscript_guard", "key_guard"):
        return "LIKELY"

    return "UNKNOWN"