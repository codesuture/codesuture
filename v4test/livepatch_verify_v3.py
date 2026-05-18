"""
LivePatch Verification Suite v3.0
Sections:
  A  — Core engine (original 16 checks)
  B  — Dark upgrades D1-D6 (28 checks)
  C  — v0.4 release layer: rollback, 3 new guards, dry-run, PyPI, README, CHANGELOG

Run all:        python livepatch_verify_v3.py --path C:\path\to\livepatch
Run section C:  python livepatch_verify_v3.py --path C:\path\to\livepatch --section C
Run A+B:        python livepatch_verify_v3.py --path C:\path\to\livepatch --section AB
"""

import subprocess, sys, os, shutil, argparse, textwrap, json, re
from datetime import datetime, timedelta

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"

PASS = f"{GREEN}PASS{RESET}"
FAIL = f"{RED}FAIL{RESET}"
SKIP = f"{YELLOW}SKIP{RESET}"

results = []

TEST_BUG1 = """\
class Profile:
    def __init__(self, bio): self.bio = bio
class User:
    def __init__(self, name, profile):
        self.name = name
        self.profile = profile
def get_bio(user):
    return user.profile.bio.strip()
user = User("Bob", None)
print(get_bio(user))
"""

TEST_BUG3_FULL = """\
class Profile:
    def __init__(self, bio): self.bio = bio
class User:
    def __init__(self, name, profile):
        self.name = name
        self.profile = profile
def fetch_user(uid):
    users = {
        1: User("Alice", Profile("Engineer")),
        2: User("Bob", None),
    }
    return users.get(uid)
def get_bio(user):
    return user.profile.bio.strip()
def format_user(user):
    return f"{user.name.upper()} - {get_bio(user)}"
def process_users():
    results = []
    for uid in [1, 2, 3]:
        user = fetch_user(uid)
        results.append(format_user(user))
    return results
def main():
    print("Starting hard test...")
    results = process_users()
    print("Results:", results)
if __name__ == "__main__":
    main()
"""

TEST_SIMPLE = """\
class User:
    def __init__(self, name): self.name = name
def get_user(uid):
    return None if uid != 1 else User("Alice")
def process(uid):
    user = get_user(uid)
    name = user.name.strip()
    print("Processed:", name)
process(2)
"""

TEST_CLOSURE = """\
class User:
    def __init__(self, n): self.name = n
def get_user(uid):
    return None if uid != 1 else User("Alice")
def make_processor(get_user_fn):
    def process(uid):
        user = get_user_fn(uid)
        return user.name.strip()
    return process
process = make_processor(get_user)
print(process(2))
"""

TEST_SHADOW = """\
class Profile:
    def __init__(self, bio): self.bio = bio
class User:
    def __init__(self, name, profile):
        self.name = name
        self.profile = profile
def fetch_user(uid):
    return User("Bob", None) if uid == 2 else User("Alice", Profile("eng"))
def get_bio(user):
    return user.profile.bio.strip()
print(get_bio(fetch_user(2)))
"""

TEST_TYPE_ERROR = """\
def process(val):
    return int(val) * 2
print(process("not_a_number"))
"""

TEST_INDEX_ERROR = """\
def get_item(items, n):
    return items[n].strip()
print(get_item(["a", "b"], 10))
"""

TEST_KEY_ERROR = """\
def get_config(cfg):
    return cfg["timeout"] * 1000
print(get_config({"host": "localhost"}))
"""

def header(title, subtitle=""):
    bar = "─" * 60
    print(f"\n{CYAN}{bar}{RESET}")
    print(f"{BOLD}  {title}{RESET}")
    if subtitle:
        print(f"  {DIM}{subtitle}{RESET}")
    print(f"{CYAN}{bar}{RESET}")

def run_livepatch(script_path, cwd, timeout=30, extra_args=None):
    cmd = ["livepatch", "run"] + (extra_args or []) + [script_path]
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return (r.stdout or "") + (r.stderr or ""), r.returncode
    except FileNotFoundError:
        return None, -999
    except subprocess.TimeoutExpired:
        return "TIMEOUT", -998

def run_cmd(args, cwd, timeout=15):
    cmd = ["livepatch"] + args
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return (r.stdout or "") + (r.stderr or ""), r.returncode
    except FileNotFoundError:
        return None, -999
    except subprocess.TimeoutExpired:
        return "TIMEOUT", -998

def check(label, output, must_contain=None, must_not_contain=None, section=None):
    passed = True
    reasons = []
    if output is None:
        results.append((label, False, ["livepatch not found on PATH"], section))
        print(f"  {FAIL}  {label}")
        return False
    if output == "TIMEOUT":
        results.append((label, False, ["timed out"], section))
        print(f"  {FAIL}  {label}  {DIM}(timeout){RESET}")
        return False
    if must_contain:
        for p in must_contain:
            if p.lower() not in output.lower():
                passed = False
                reasons.append(f"missing: '{p}'")
    if must_not_contain:
        for p in must_not_contain:
            if p.lower() in output.lower():
                passed = False
                reasons.append(f"must NOT contain: '{p}'")
    results.append((label, passed, reasons, section))
    if passed:
        print(f"  {PASS}  {label}")
    else:
        print(f"  {FAIL}  {label}")
        for r in reasons:
            print(f"         {RED}↳ {r}{RESET}")
        if output.strip():
            for line in output.strip().splitlines()[-6:]:
                print(f"         {DIM}{line}{RESET}")
    return passed

def check_bool(label, value, fail_reason="", section=None):
    results.append((label, value, [] if value else [fail_reason], section))
    print(f"  {PASS if value else FAIL}  {label}")
    if not value and fail_reason:
        print(f"         {RED}↳ {fail_reason}{RESET}")
    return value

def write_test(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(textwrap.dedent(content))

def clear_patches(cwd):
    for name in [".livepatch_cache", ".livepatch_knowledge",
                 ".livepatch_store", ".livepatch", "livepatch_patches",
                 ".livepatch_fingerprints"]:
        t = os.path.join(cwd, name)
        if os.path.isdir(t):    shutil.rmtree(t)
        elif os.path.isfile(t): os.remove(t)

def find_patch_store(cwd):
    for name in [".livepatch_cache", ".livepatch_store",
                 ".livepatch", "livepatch_patches"]:
        p = os.path.join(cwd, name)
        if os.path.exists(p): return p
    return None

def count_patch_files(cwd):
    store = find_patch_store(cwd)
    if not store: return 0
    if os.path.isdir(store): return len(os.listdir(store))
    return 1 if os.path.isfile(store) else 0

def patch_store_is_empty(cwd):
    store = find_patch_store(cwd)
    if not store: return True
    if os.path.isdir(store): return len(os.listdir(store)) == 0
    return False

def find_all_json_patches(cwd):
    store = find_patch_store(cwd)
    if not store: return []
    files = []
    if os.path.isdir(store):
        for f in os.listdir(store):
            if f.endswith(".json"):
                files.append(os.path.join(store, f))
    elif os.path.isfile(store) and store.endswith(".json"):
        files.append(store)
    return files

def backdate_patch(cwd, days=8):
    backdated = 0
    for jpath in find_all_json_patches(cwd):
        try:
            with open(jpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            modified = False
            if isinstance(data, dict) and "patched_at" in data:
                data["patched_at"] = (datetime.utcnow() - timedelta(days=days)).isoformat()
                modified = True
            elif isinstance(data, list):
                for e in data:
                    if isinstance(e, dict) and "patched_at" in e:
                        e["patched_at"] = (datetime.utcnow() - timedelta(days=days)).isoformat()
                        modified = True
            if modified:
                with open(jpath, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                backdated += 1
        except Exception:
            pass
    return backdated

# ─────────────────────────────────────────────────────────────────
# SECTION A
# ─────────────────────────────────────────────────────────────────

def run_section_A(workdir, project_path):
    header("0. Install check", "section A")
    r = subprocess.run(["livepatch", "--version"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    ok = r.returncode == 0 or "livepatch" in ((r.stdout or "") + (r.stderr or "")).lower()
    results.append(("livepatch on PATH", ok, [], "A"))
    print(f"  {PASS if ok else FAIL}  livepatch is on PATH")
    if not ok:
        print(f"\n{RED}Cannot continue.{RESET}")
        sys.exit(1)

    header("1. Bug 1 — Chain depth resolver", "section A")
    clear_patches(workdir)
    write_test(os.path.join(workdir, "bug1_test.py"), TEST_BUG1)
    out, _ = run_livepatch("bug1_test.py", workdir)
    check("Guards 'profile', not 'user'", out,
          must_contain=["null_guard on 'profile'"],
          must_not_contain=["null_guard on 'user'"], section="A")
    check("No 'cannot access local variable' crash", out,
          must_not_contain=["cannot access local variable"], section="A")
    check("Patch applied", out, must_contain=["patch applied"],
          must_not_contain=["patch rejected"], section="A")

    header("2. Bug 2 — Frame validator", "section A")
    clear_patches(workdir)
    write_test(os.path.join(workdir, "bug2_test.py"), TEST_BUG1)
    out, _ = run_livepatch("bug2_test.py", workdir)
    check("Valid patch passes validator", out,
          must_not_contain=["patch rejected", "not in co_varnames"], section="A")
    check("No phantom local variable error", out,
          must_not_contain=["cannot access local variable",
                            "unboundlocalerror"], section="A")
    out2, _ = run_livepatch("bug2_test.py", workdir)
    check("Persisted patch loads clean on re-run", out2,
          must_contain=["already healed"],
          must_not_contain=["patch rejected",
                            "cannot access local variable"], section="A")

    header("3. Bug 3 — Cross-function deduplication", "section A")
    clear_patches(workdir)
    write_test(os.path.join(workdir, "bug3_hard_test.py"), TEST_BUG3_FULL)
    out, _ = run_livepatch("bug3_hard_test.py", workdir)
    m = re.search(r"patches applied[:\s]+(\d+)", out, re.IGNORECASE)
    pc = int(m.group(1)) if m else -1
    ok = 0 < pc <= 2
    check_bool(f"Patches applied: {pc} (expected <= 2)", ok,
               f"got {pc}, need <= 2", "A")
    check("No secondary crash", out,
          must_not_contain=["cannot access local variable",
                            "traceback (most recent call last)"], section="A")
    check("Guards 'profile'", out, must_contain=["profile"],
          must_not_contain=["null_guard on 'user'"], section="A")

    header("4. Persistence — second run 0 patches", "section A")
    out, _ = run_livepatch("bug3_hard_test.py", workdir)
    check("Already healed", out, must_contain=["already healed"], section="A")
    check("Patches applied: 0", out, must_contain=["patches applied: 0"], section="A")
    check("Script completes", out,
          must_not_contain=["traceback (most recent call last)"], section="A")

    header("5. Regression — simple test", "section A")
    clear_patches(workdir)
    write_test(os.path.join(workdir, "simple_reg_test.py"), TEST_SIMPLE)
    out, _ = run_livepatch("simple_reg_test.py", workdir)
    check("Single-level null guard applies", out,
          must_contain=["patch applied"], section="A")
    check("No secondary crash", out,
          must_not_contain=["cannot access local variable",
                            "traceback (most recent call last)"], section="A")

    header("6. pytest regression", "section A")
    if not project_path:
        print(f"  {SKIP}  --path not provided")
        results.append(("pytest: all tests pass", None, ["skipped"], "A"))
        return
    tests_dir = os.path.join(project_path, "tests")
    if not os.path.isdir(tests_dir):
        results.append(("pytest: all tests pass", None, ["no tests/ dir"], "A"))
        print(f"  {SKIP}  no tests/ dir")
        return
    r = subprocess.run(["pytest", "tests/", "-v", "--tb=short", "-x"],
                       cwd=project_path, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    ok = r.returncode == 0
    results.append(("pytest: all tests pass", ok, [], "A"))
    print(f"  {PASS if ok else FAIL}  pytest: all tests pass")
    if not ok:
        for line in (r.stdout + r.stderr).splitlines():
            if "FAILED" in line or "ERROR" in line:
                print(f"         {RED}{line}{RESET}")

# ─────────────────────────────────────────────────────────────────
# SECTION B
# ─────────────────────────────────────────────────────────────────

def run_section_B(workdir):
    header("D1 — Semantic diff safety gate", "section B")
    clear_patches(workdir)
    write_test(os.path.join(workdir, "d1_test.py"), TEST_BUG1)
    out, _ = run_livepatch("d1_test.py", workdir, extra_args=["--verbose"])
    check("--verbose shows diff stats", out, must_contain=["diff:"], section="B-D1")
    check("Valid patch not rejected", out,
          must_not_contain=["semantic diff too large",
                            "patch rejected: semantic diff"], section="B-D1")
    check("No secondary crash", out,
          must_not_contain=["cannot access local variable"], section="B-D1")
    has_fmt = bool(re.search(r"diff.*\+\d+.*-\d+|diff.*instructions", out, re.IGNORECASE))
    check_bool("Diff log shows +N -N counts", has_fmt,
               "expected '+N -N instructions' in output", "B-D1")

    header("D2 — Caller-aware patch propagation", "section B")
    clear_patches(workdir)
    write_test(os.path.join(workdir, "d2_closure_test.py"), TEST_CLOSURE)
    out, _ = run_livepatch("d2_closure_test.py", workdir)
    check("Patch propagates into closure", out,
          must_contain=["propagated patch"], section="B-D2")
    check("Closure test no crash", out,
          must_not_contain=["cannot access local variable",
                            "traceback (most recent call last)"], section="B-D2")
    out2, _ = run_livepatch("d2_closure_test.py", workdir)
    check("Propagated patch persists", out2,
          must_contain=["already healed"], section="B-D2")

    header("D3 — Shadow execution mode", "section B")
    clear_patches(workdir)
    write_test(os.path.join(workdir, "d3_shadow_test.py"), TEST_SHADOW)
    out, _ = run_livepatch("d3_shadow_test.py", workdir, extra_args=["--shadow"])
    check("--shadow flag accepted", out,
          must_not_contain=["unrecognized argument", "error: argument"], section="B-D3")
    check("Shadow warning fires", out, must_contain=["livepatch shadow"], section="B-D3")
    check("Shadow warning mentions sentinel", out, must_contain=["sentinel"], section="B-D3")
    clear_patches(workdir)
    out_ns, _ = run_livepatch("d3_shadow_test.py", workdir)
    check("No shadow warnings without --shadow", out_ns,
          must_not_contain=["livepatch shadow"], section="B-D3")

    header("D4 — Patch expiry TTL", "section B")
    write_test(os.path.join(workdir, "d4_test.py"), TEST_BUG1)
    clear_patches(workdir)
    run_livepatch("d4_test.py", workdir)
    jfiles = find_all_json_patches(workdir)
    check_bool(f"Sidecar .json files exist ({len(jfiles)} found)",
               len(jfiles) > 0, "no .json in patch store", "B-D4")
    has_pa = False
    if jfiles:
        try:
            with open(jfiles[0], "r", encoding="utf-8") as f:
                d = json.load(f)
            has_pa = ("patched_at" in d) if isinstance(d, dict) else \
                     (bool(d) and "patched_at" in d[0])
        except Exception: pass
    check_bool("JSON contains 'patched_at'", has_pa, "", "B-D4")
    if jfiles:
        n = backdate_patch(workdir, 8)
        if n > 0:
            print(f"  {DIM}Backdated {n} patch(es) to 8 days ago...{RESET}")
            out, _ = run_livepatch("d4_test.py", workdir)
            check("TTL warning fires", out, must_contain=["day"], section="B-D4")
            has_w = any(kw in out.lower() for kw in
                        ["old", "expired", "ttl", "day(s)"])
            check_bool("TTL warning references age/TTL", has_w,
                       "expected 'days old' or 'TTL' in output", "B-D4")

    header("D5 — Bytecode fingerprint registry", "section B")
    clear_patches(workdir)
    write_test(os.path.join(workdir, "d5_test.py"), TEST_BUG3_FULL)
    out1, _ = run_livepatch("d5_test.py", workdir)
    check("First run: no cache hit", out1,
          must_not_contain=["known crash pattern", "applying cached"], section="B-D5")
    fp_file = os.path.join(workdir, ".livepatch_fingerprints")
    check_bool(".livepatch_fingerprints created", os.path.isfile(fp_file),
               "file not found", "B-D5")
    if os.path.isfile(fp_file):
        try:
            with open(fp_file, "r", encoding="utf-8") as f:
                fp_data = json.load(f)
            check_bool(f"Registry has entries ({len(fp_data)} hash(es))",
                       len(fp_data) > 0, "registry empty", "B-D5")
        except Exception as e:
            check_bool("Registry is valid JSON", False, str(e), "B-D5")
    for name in [".livepatch_cache", ".livepatch_store", ".livepatch", "livepatch_patches"]:
        t = os.path.join(workdir, name)
        if os.path.isdir(t): shutil.rmtree(t)
        elif os.path.isfile(t): os.remove(t)
    out2, _ = run_livepatch("d5_test.py", workdir)
    check("Second run: cache hit detected", out2,
          must_contain=["known crash pattern"], section="B-D5")
    check("Second run: 'applying cached' logged", out2,
          must_contain=["cached"], section="B-D5")

    header("D6 — livepatch audit command", "section B")
    write_test(os.path.join(workdir, "d6_test.py"), TEST_BUG3_FULL)
    clear_patches(workdir)
    run_livepatch("d6_test.py", workdir)
    out, _ = run_cmd(["audit"], workdir)
    check("audit runs without error", out,
          must_not_contain=["traceback", "unrecognized argument"], section="B-D6")
    check("Shows function name", out, must_contain=["get_bio"], section="B-D6")
    check("Shows guard type", out, must_contain=["null_guard"], section="B-D6")
    check("Shows patch target", out, must_contain=["profile"], section="B-D6")
    check("Shows total count", out, must_contain=["total"], section="B-D6")
    check("Shows age in days", out, must_contain=["0d"], section="B-D6")
    check("Mentions rollback", out, must_contain=["rollback"], section="B-D6")
    clear_patches(workdir)
    out_e, _ = run_cmd(["audit"], workdir)
    check("audit empty store: no crash", out_e,
          must_not_contain=["traceback", "keyerror", "attributeerror"], section="B-D6")

# ─────────────────────────────────────────────────────────────────
# SECTION C
# ─────────────────────────────────────────────────────────────────

def run_section_C(workdir, project_path):

    header("C1 — livepatch rollback command", "section C: v0.4")
    write_test(os.path.join(workdir, "rollback_test.py"), TEST_BUG3_FULL)
    clear_patches(workdir)
    run_livepatch("rollback_test.py", workdir)
    count_before = count_patch_files(workdir)
    print(f"  {DIM}Patch store: {count_before} file(s){RESET}")

    print(f"  {DIM}Running: livepatch rollback get_bio...{RESET}")
    out, _ = run_cmd(["rollback", "get_bio"], workdir)
    check("rollback get_bio: success message", out,
          must_contain=["rolled back"],
          must_not_contain=["traceback", "exception"], section="C1")
    count_after = count_patch_files(workdir)
    check_bool(
        f"rollback removes only targeted patch ({count_before}->{count_after})",
        count_after < count_before and count_after > 0,
        f"before={count_before} after={count_after}", "C1"
    )

    print(f"  {DIM}Running: livepatch rollback --dry-run...{RESET}")
    count_pre = count_patch_files(workdir)
    out_dry, _ = run_cmd(["rollback", "--dry-run"], workdir)
    check("rollback --dry-run shows what would be removed", out_dry,
          must_contain=["would"],
          must_not_contain=["traceback"], section="C1")
    check_bool("rollback --dry-run does NOT delete files",
               count_patch_files(workdir) == count_pre,
               f"file count changed", "C1")

    print(f"  {DIM}Running: livepatch rollback --all...{RESET}")
    out_all, _ = run_cmd(["rollback", "--all"], workdir)
    check("rollback --all: cleared message", out_all,
          must_contain=["cleared"],
          must_not_contain=["traceback"], section="C1")
    check_bool("rollback --all empties store",
               patch_store_is_empty(workdir), "store not empty", "C1")
    check_bool("rollback --all removes fingerprints",
               not os.path.isfile(os.path.join(workdir, ".livepatch_fingerprints")),
               ".livepatch_fingerprints still exists", "C1")

    print(f"  {DIM}Running: livepatch rollback --all (empty store)...{RESET}")
    out_e, _ = run_cmd(["rollback", "--all"], workdir)
    check("rollback --all empty store: clean message", out_e,
          must_contain=["nothing"],
          must_not_contain=["traceback", "exception", "keyerror"], section="C1")

    print(f"  {DIM}Running: livepatch rollback nonexistent_fn...{RESET}")
    out_m, _ = run_cmd(["rollback", "nonexistent_fn"], workdir)
    check("rollback unknown function: no crash", out_m,
          must_not_contain=["traceback", "exception", "keyerror"], section="C1")

    header("C2 — Guard: type_coercion_guard (TypeError)", "section C: v0.4")
    clear_patches(workdir)
    write_test(os.path.join(workdir, "type_test.py"), TEST_TYPE_ERROR)
    print(f"  {DIM}Running: livepatch run type_test.py...{RESET}")
    out, _ = run_livepatch("type_test.py", workdir)
    check("type_coercion_guard fires on TypeError", out,
          must_contain=["type_coercion_guard"],
          must_not_contain=["unhandled", "no guard available"], section="C2")
    check("TypeError: patch applied", out, must_contain=["patch applied"], section="C2")
    check("TypeError: no secondary crash", out,
          must_not_contain=["traceback (most recent call last)",
                            "cannot access local variable"], section="C2")
    out2, _ = run_livepatch("type_test.py", workdir)
    check("TypeError: healed on second run", out2,
          must_contain=["already healed"], section="C2")

    header("C3 — Guard: index_guard (IndexError)", "section C: v0.4")
    clear_patches(workdir)
    write_test(os.path.join(workdir, "index_test.py"), TEST_INDEX_ERROR)
    print(f"  {DIM}Running: livepatch run index_test.py...{RESET}")
    out, _ = run_livepatch("index_test.py", workdir)
    check("index_guard fires on IndexError", out,
          must_contain=["index_guard"],
          must_not_contain=["unhandled", "no guard available"], section="C3")
    check("IndexError: patch applied", out, must_contain=["patch applied"], section="C3")
    check("IndexError: no secondary crash", out,
          must_not_contain=["traceback (most recent call last)",
                            "cannot access local variable"], section="C3")
    out2, _ = run_livepatch("index_test.py", workdir)
    check("IndexError: healed on second run", out2,
          must_contain=["already healed"], section="C3")

    header("C4 — Guard: key_guard (KeyError)", "section C: v0.4")
    clear_patches(workdir)
    write_test(os.path.join(workdir, "key_test.py"), TEST_KEY_ERROR)
    print(f"  {DIM}Running: livepatch run key_test.py...{RESET}")
    out, _ = run_livepatch("key_test.py", workdir)
    check("key_guard fires on KeyError", out,
          must_contain=["key_guard"],
          must_not_contain=["unhandled", "no guard available"], section="C4")
    check("KeyError: patch applied", out, must_contain=["patch applied"], section="C4")
    check("KeyError: no secondary crash", out,
          must_not_contain=["traceback (most recent call last)",
                            "cannot access local variable"], section="C4")
    out2, _ = run_livepatch("key_test.py", workdir)
    check("KeyError: healed on second run", out2,
          must_contain=["already healed"], section="C4")

    header("C5 — livepatch run --dry-run mode", "section C: v0.4")
    clear_patches(workdir)
    write_test(os.path.join(workdir, "dryrun_test.py"), TEST_BUG3_FULL)
    print(f"  {DIM}Running: livepatch run --dry-run dryrun_test.py...{RESET}")
    out, _ = run_livepatch("dryrun_test.py", workdir, extra_args=["--dry-run"])
    check("--dry-run flag accepted", out,
          must_not_contain=["unrecognized argument", "error: argument"], section="C5")
    check("--dry-run shows 'Would apply'", out,
          must_contain=["would apply"], section="C5")
    check("--dry-run shows DRY-RUN label", out,
          must_contain=["dry-run"], section="C5")
    check("--dry-run shows 'No patches applied'", out,
          must_contain=["no patches applied"], section="C5")
    check("--dry-run shows guard type", out,
          must_contain=["null_guard"], section="C5")
    has_confidence = any(kw in out.upper() for kw in ["HIGH", "MEDIUM", "LOW"])
    check_bool("--dry-run shows confidence level (HIGH/MEDIUM/LOW)",
               has_confidence, "none of HIGH/MEDIUM/LOW in output", "C5")
    check_bool("--dry-run does NOT write patch store",
               patch_store_is_empty(workdir) or not find_patch_store(workdir),
               "patch store was modified", "C5")
    check_bool("--dry-run does NOT write fingerprints",
               not os.path.isfile(os.path.join(workdir, ".livepatch_fingerprints")),
               ".livepatch_fingerprints written during dry-run", "C5")

    header("C6 — pytest: test_new_guards.py", "section C: v0.4")
    if not project_path:
        print(f"  {SKIP}  --path not provided")
        results.append(("pytest: test_new_guards.py", None, ["skipped"], "C6"))
    else:
        ng = os.path.join(project_path, "tests", "test_new_guards.py")
        if not os.path.isfile(ng):
            print(f"  {SKIP}  tests/test_new_guards.py not found")
            results.append(("pytest: test_new_guards.py", None, ["not found"], "C6"))
        else:
            r = subprocess.run(
                ["pytest", "tests/test_new_guards.py", "-v", "--tb=short"],
                cwd=project_path, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=60
            )
            ok = r.returncode == 0
            results.append(("pytest: test_new_guards.py passes", ok, [], "C6"))
            print(f"  {PASS if ok else FAIL}  pytest: test_new_guards.py passes")
            if not ok:
                for line in (r.stdout + r.stderr).splitlines():
                    if "FAILED" in line or "ERROR" in line:
                        print(f"         {RED}{line}{RESET}")

    header("C7 — Version 0.4.0 confirmed", "section C: v0.4")
    out, _ = run_cmd(["--version"], workdir)
    check_bool("livepatch --version shows 0.4.0",
               "0.4.0" in (out or ""),
               f"got: {(out or '').strip()[:80]}", "C7")
    if project_path:
        r2 = subprocess.run(
            ["python", "-c", "import livepatch; print(livepatch.__version__)"],
            cwd=project_path, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=10
        )
        v = (r2.stdout or "").strip()
        check_bool("livepatch.__version__ == '0.4.0'",
                   "0.4.0" in v, f"got: {v}", "C7")

    header("C8 — README.md completeness", "section C: v0.4")
    if not project_path:
        print(f"  {SKIP}  --path not provided")
        results.append(("README completeness", None, ["skipped"], "C8"))
    else:
        readme = os.path.join(project_path, "README.md")
        if not os.path.isfile(readme):
            check_bool("README.md exists", False, "not found", "C8")
        else:
            with open(readme, "r", encoding="utf-8") as f:
                content = f.read().lower()
            for label, kw in [
                ("Quick start",           "quick start"),
                ("How it works",          "how it works"),
                ("CLI reference",         "cli"),
                ("Guard types table",     "guard type"),
                ("Limitations",           "limitation"),
                ("livepatch run",         "livepatch run"),
                ("livepatch audit",       "livepatch audit"),
                ("livepatch rollback",    "livepatch rollback"),
                ("--shadow documented",   "--shadow"),
                ("--dry-run documented",  "--dry-run"),
            ]:
                check_bool(f"README: {label}", kw in content,
                           f"'{kw}' not found", "C8")

    header("C9 — CHANGELOG.md completeness", "section C: v0.4")
    if not project_path:
        print(f"  {SKIP}  --path not provided")
        results.append(("CHANGELOG completeness", None, ["skipped"], "C9"))
    else:
        cl = os.path.join(project_path, "CHANGELOG.md")
        if not os.path.isfile(cl):
            check_bool("CHANGELOG.md exists", False, "not found", "C9")
        else:
            with open(cl, "r", encoding="utf-8") as f:
                content = f.read().lower()
            for label, kw in [
                ("[0.4.0] entry",              "0.4.0"),
                ("[0.3.0] entry",              "0.3.0"),
                ("Mentions rollback",          "rollback"),
                ("Mentions type_coercion",     "type_coercion"),
                ("Mentions index_guard",       "index_guard"),
                ("Mentions key_guard",         "key_guard"),
                ("Mentions semantic diff (D1)","semantic diff"),
                ("Mentions caller (D2)",       "caller"),
                ("Mentions shadow (D3)",       "shadow"),
            ]:
                check_bool(f"CHANGELOG: {label}", kw in content,
                           f"'{kw}' not found", "C9")

# ─────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────

def summary():
    header("VERIFICATION SUMMARY — ALL SECTIONS")
    order = ["A","B-D1","B-D2","B-D3","B-D4","B-D5","B-D6",
             "C1","C2","C3","C4","C5","C6","C7","C8","C9"]
    labels = {
        "A":"Core engine (original 16)","B-D1":"D1 Semantic diff",
        "B-D2":"D2 Caller propagation","B-D3":"D3 Shadow mode",
        "B-D4":"D4 Patch TTL","B-D5":"D5 Fingerprint registry",
        "B-D6":"D6 Audit command","C1":"C1 Rollback command",
        "C2":"C2 type_coercion_guard","C3":"C3 index_guard",
        "C4":"C4 key_guard","C5":"C5 Dry-run mode",
        "C6":"C6 pytest new guards","C7":"C7 Version 0.4.0",
        "C8":"C8 README completeness","C9":"C9 CHANGELOG completeness",
    }
    buckets = {k: [] for k in order}
    for label, ok, reasons, sec in results:
        buckets.setdefault(sec, []).append((label, ok, reasons))
    tp = tf = ts = 0
    for sec in order:
        checks = buckets.get(sec, [])
        if not checks: continue
        p = sum(1 for _, ok, _ in checks if ok is True)
        f = sum(1 for _, ok, _ in checks if ok is False)
        s = sum(1 for _, ok, _ in checks if ok is None)
        tp += p; tf += f; ts += s
        icon = f"{GREEN}✓{RESET}" if f == 0 else f"{RED}✗{RESET}"
        print(f"\n  {icon}  {BOLD}{labels.get(sec,sec)}{RESET}"
              f"  {DIM}({p}✓ {f}✗ {s}–){RESET}")
        for label, ok, reasons in checks:
            if ok is True:   print(f"       {GREEN}✓{RESET}  {label}")
            elif ok is False:
                print(f"       {RED}✗{RESET}  {label}")
                for r in reasons: print(f"           {RED}↳ {r}{RESET}")
            else: print(f"       {YELLOW}–{RESET}  {label}  {DIM}(skipped){RESET}")
    total = tp + tf + ts
    print(f"\n{CYAN}{'─'*60}{RESET}")
    if tf == 0:
        print(f"{BOLD}{GREEN}ALL {tp}/{total} CHECKS PASSED.{RESET}")
        print("LivePatch v0.4.0 — engine + dark upgrades + release layer verified.")
    else:
        print(f"{BOLD}{RED}{tf} CHECK(S) FAILED{RESET} "
              f"({tp} passed, {ts} skipped / {total} total)")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="LivePatch verification suite v3.0")
    parser.add_argument("--path", "-p", default=None)
    parser.add_argument("--workdir", "-w", default=".")
    parser.add_argument("--section", "-s",
                        choices=["A","B","AB","C","all"], default="all")
    args = parser.parse_args()
    workdir = os.path.abspath(args.workdir)
    os.makedirs(workdir, exist_ok=True)
    print(f"\n{BOLD}LivePatch Verification Suite v3.0{RESET}")
    print(f"{DIM}workdir : {workdir}{RESET}")
    print(f"{DIM}project : {args.path or 'not provided'}{RESET}")
    print(f"{DIM}section : {args.section}{RESET}")
    if args.section in ("A","AB","all"):  run_section_A(workdir, args.path)
    if args.section in ("B","AB","all"):  run_section_B(workdir)
    if args.section in ("C","all"):       run_section_C(workdir, args.path)
    summary()