"""
LivePatch Verification Suite v4.0
Sections:
  A  — Core engine (16 checks)
  B  — Dark upgrades D1-D6 (28 checks)
  C  — v0.4 release layer (40 checks)
  D  — v0.5: async, watch, explain, middleware, real-world patterns (35 checks)

Run all:        python livepatch_verify_v4.py --path .
Run v0.5 only:  python livepatch_verify_v4.py --path . --section D
Run C+D:        python livepatch_verify_v4.py --path . --section CD
"""

from pkg_resources import run_script
import subprocess, sys, os, shutil, argparse, textwrap, json, re, time
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

# ─────────────────────────────────────────────────────────────────
# All test file contents
# ─────────────────────────────────────────────────────────────────

# ── Inherited from v3 (sections A/B/C) ───────────────────────────
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

# ── v0.5 test files ───────────────────────────────────────────────

TEST_ASYNC = """\
import asyncio

class Profile:
    def __init__(self, bio): self.bio = bio

class User:
    def __init__(self, name, profile):
        self.name = name
        self.profile = profile

async def get_bio(user):
    return user.profile.bio.strip()

async def main():
    user = User("Bob", None)
    result = await get_bio(user)
    print("Async result:", result)

asyncio.run(main())
"""

TEST_WATCH = """\
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
print("Done.")
"""

TEST_EXPLAIN_SETUP = """\
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

TEST_MIDDLEWARE = """\
from livepatch.middleware import LivePatchMiddleware

def buggy_app(environ, start_response):
    data = environ.get("livepatch.test_data")
    result = data.strip()  # crashes when data is None
    start_response("200 OK", [])
    return [result.encode()]

patched_app = LivePatchMiddleware(buggy_app)
env = {"livepatch.test_data": None}
resp_holder = []

def fake_start(status, headers):
    resp_holder.append(status)
    resp_holder.extend([h for h in headers])

body = list(patched_app(env, fake_start))
assert resp_holder[0] == "200 OK", f"Expected 200 OK, got: {resp_holder[0]}"
print("Middleware test passed.")
"""

# ── Real-world pattern tests (anti-template validation) ──────────

TEST_REALWORLD_CLASSMETHOD = """\
# Real-world pattern: instance method on a class with None attribute
class Database:
    def __init__(self, records):
        self.records = records  # dict or None

    def get_user(self, uid):
        return self.records.get(uid)  # records may be None

class UserService:
    def __init__(self, db):
        self.db = db

    def get_name(self, uid):
        user = self.db.get_user(uid)
        return user['name'].strip()  # user may be None

db = Database({1: {'name': 'Alice'}})
service = UserService(db)
print(service.get_name(99))  # triggers crash: user is None
"""

TEST_REALWORLD_KWARGS = """\
# Real-world pattern: **kwargs access with None value
def build_response(**kwargs):
    profile = kwargs.get('profile')
    title = profile.title.strip()  # profile may be None
    return {'title': title}

result = build_response(user_id=1, profile=None)
print(result)
"""

TEST_REALWORLD_DECORATOR = """\
# Real-world pattern: patching through a decorator wrapper
import functools

def log_call(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return fn(*args, **kwargs)
    return wrapper

class Config:
    def __init__(self, data):
        self.data = data  # may be None

@log_call
def get_setting(config, key):
    return config.data[key]  # config.data may be None

cfg = Config(None)
print(get_setting(cfg, 'debug'))
"""

TEST_REALWORLD_PROPERTY = """\
# Real-world pattern: crash inside a property getter
class Profile:
    def __init__(self, meta):
        self.meta = meta  # may be None

    @property
    def display_name(self):
        return self.meta['display'].strip()  # meta may be None

p = Profile(None)
print(p.display_name)
"""

TEST_REALWORLD_NESTED = """\
# Real-world pattern: 3-level deep chain
class Address:
    def __init__(self, city): self.city = city

class Contact:
    def __init__(self, address): self.address = address  # may be None

class Person:
    def __init__(self, contact): self.contact = contact

def get_city(person):
    return person.contact.address.city.strip()  # contact.address may be None

p = Person(Contact(None))
print(get_city(p))
"""

# ─────────────────────────────────────────────────────────────────
# Helpers (identical to v3 + watch-specific)
# ─────────────────────────────────────────────────────────────────

def header(title, subtitle=""):
    bar = "─" * 60
    print(f"\n{CYAN}{bar}{RESET}")
    print(f"{BOLD}  {title}{RESET}")
    if subtitle: print(f"  {DIM}{subtitle}{RESET}")
    print(f"{CYAN}{bar}{RESET}")

def run_livepatch(script_path, cwd, timeout=30, extra_args=None):
    cmd = ["livepatch", "run"] + (extra_args or []) + [script_path]
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return (r.stdout or "") + (r.stderr or ""), r.returncode
    except FileNotFoundError: return None, -999
    except subprocess.TimeoutExpired: return "TIMEOUT", -998

def run_watch(script_path, cwd, extra_args=None, timeout=45):
    cmd = ["livepatch", "watch"] + (extra_args or []) + [script_path]
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return (r.stdout or "") + (r.stderr or ""), r.returncode
    except FileNotFoundError: return None, -999
    except subprocess.TimeoutExpired: return "TIMEOUT", -998

def run_cmd(args, cwd, timeout=15):
    cmd = ["livepatch"] + args
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return (r.stdout or "") + (r.stderr or ""), r.returncode
    except FileNotFoundError: return None, -999
    except subprocess.TimeoutExpired: return "TIMEOUT", -998

def run_python(script_path, cwd, timeout=20):
    try:
        r = subprocess.run(["python", script_path], cwd=cwd,
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return (r.stdout or "") + (r.stderr or ""), r.returncode
    except FileNotFoundError: return None, -999
    except subprocess.TimeoutExpired: return "TIMEOUT", -998

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
                passed = False; reasons.append(f"missing: '{p}'")
    if must_not_contain:
        for p in must_not_contain:
            if p.lower() in output.lower():
                passed = False; reasons.append(f"must NOT contain: '{p}'")
    results.append((label, passed, reasons, section))
    if passed: print(f"  {PASS}  {label}")
    else:
        print(f"  {FAIL}  {label}")
        for r in reasons: print(f"         {RED}↳ {r}{RESET}")
        if output.strip():
            for line in output.strip().splitlines()[-5:]:
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
        if os.path.isdir(t): shutil.rmtree(t)
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
            if f.endswith(".json"): files.append(os.path.join(store, f))
    elif os.path.isfile(store) and store.endswith(".json"):
        files.append(store)
    return files

def backdate_patch(cwd, days=8):
    backdated = 0
    for jpath in find_all_json_patches(cwd):
        try:
            with open(jpath, "r", encoding="utf-8") as f: data = json.load(f)
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
                with open(jpath, "w", encoding="utf-8") as f: json.dump(data, f, indent=2)
                backdated += 1
        except Exception: pass
    return backdated

# ─────────────────────────────────────────────────────────────────
# SECTION A (16 checks — identical to v3)
# ─────────────────────────────────────────────────────────────────

def run_section_A(workdir, project_path):
    header("0. Install check", "section A")
    r = subprocess.run(["livepatch", "--version"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    ok = r.returncode == 0 or "livepatch" in ((r.stdout or "") + (r.stderr or "")).lower()
    results.append(("livepatch on PATH", ok, [], "A"))
    print(f"  {PASS if ok else FAIL}  livepatch is on PATH")
    if not ok: print(f"\n{RED}Cannot continue.{RESET}"); sys.exit(1)

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
          must_not_contain=["cannot access local variable", "unboundlocalerror"], section="A")
    out2, _ = run_livepatch("bug2_test.py", workdir)
    check("Persisted patch loads clean on re-run", out2,
          must_contain=["already healed"],
          must_not_contain=["patch rejected", "cannot access local variable"], section="A")

    header("3. Bug 3 — Deduplication", "section A")
    clear_patches(workdir)
    write_test(os.path.join(workdir, "bug3_hard_test.py"), TEST_BUG3_FULL)
    out, _ = run_livepatch("bug3_hard_test.py", workdir)
    m = re.search(r"patches applied[:\s]+(\d+)", out, re.IGNORECASE)
    pc = int(m.group(1)) if m else -1
    check_bool(f"Patches applied: {pc} (expected <= 2)", 0 < pc <= 2, f"got {pc}", "A")
    check("No secondary crash", out, must_not_contain=["cannot access local variable",
          "traceback (most recent call last)"], section="A")
    check("Guards 'profile'", out, must_contain=["profile"],
          must_not_contain=["null_guard on 'user'"], section="A")

    header("4. Persistence — second run 0 patches", "section A")
    out, _ = run_livepatch("bug3_hard_test.py", workdir)
    check("Already healed", out, must_contain=["already healed"], section="A")
    check("Patches applied: 0", out, must_contain=["patches applied: 0"], section="A")
    check("Completes cleanly", out, must_not_contain=["traceback (most recent call last)"], section="A")

    header("5. Regression — simple test", "section A")
    clear_patches(workdir)
    write_test(os.path.join(workdir, "simple_reg_test.py"), TEST_SIMPLE)
    out, _ = run_livepatch("simple_reg_test.py", workdir)
    check("Single-level null guard applies", out, must_contain=["patch applied"], section="A")
    check("No secondary crash", out, must_not_contain=["cannot access local variable",
          "traceback (most recent call last)"], section="A")

    header("6. pytest regression", "section A")
    if not project_path:
        print(f"  {SKIP}  --path not provided")
        results.append(("pytest: all tests pass", None, ["skipped"], "A")); return
    tests_dir = os.path.join(project_path, "tests")
    if not os.path.isdir(tests_dir):
        results.append(("pytest: all tests pass", None, ["no tests/"], "A"))
        print(f"  {SKIP}  no tests/ dir"); return
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
# SECTION B (D1-D6 — identical to v3)
# ─────────────────────────────────────────────────────────────────

def run_section_B(workdir):
    header("D1 — Semantic diff", "section B")
    clear_patches(workdir)
    write_test(os.path.join(workdir, "d1_test.py"), TEST_BUG1)
    out, _ = run_livepatch("d1_test.py", workdir, extra_args=["--verbose"])
    check("--verbose shows diff stats", out, must_contain=["diff:"], section="B-D1")
    check("Valid patch not rejected", out, must_not_contain=["semantic diff too large",
          "patch rejected: semantic diff"], section="B-D1")
    check("No secondary crash", out, must_not_contain=["cannot access local variable"], section="B-D1")
    has_fmt = bool(re.search(r"diff.*\+\d+.*-\d+|diff.*instructions", out, re.IGNORECASE))
    check_bool("Diff shows +N -N counts", has_fmt, "expected '+N -N instructions' in output", "B-D1")

    header("D2 — Caller propagation", "section B")
    clear_patches(workdir)
    write_test(os.path.join(workdir, "d2_closure_test.py"), TEST_CLOSURE)
    out, _ = run_livepatch("d2_closure_test.py", workdir)
    check("Propagates into closure", out, must_contain=["propagated patch"], section="B-D2")
    check("Closure no crash", out, must_not_contain=["cannot access local variable",
          "traceback (most recent call last)"], section="B-D2")
    out2, _ = run_livepatch("d2_closure_test.py", workdir)
    check("Propagated patch persists", out2, must_contain=["already healed"], section="B-D2")

    header("D3 — Shadow mode", "section B")
    clear_patches(workdir)
    write_test(os.path.join(workdir, "d3_shadow_test.py"), TEST_SHADOW)
    out, _ = run_livepatch("d3_shadow_test.py", workdir, extra_args=["--shadow"])
    check("--shadow accepted", out, must_not_contain=["unrecognized argument"], section="B-D3")
    check("Shadow warning fires", out, must_contain=["livepatch shadow"], section="B-D3")
    check("Mentions sentinel", out, must_contain=["sentinel"], section="B-D3")
    clear_patches(workdir)
    out_ns, _ = run_livepatch("d3_shadow_test.py", workdir)
    check("No shadow without --shadow", out_ns, must_not_contain=["livepatch shadow"], section="B-D3")

    header("D4 — TTL", "section B")
    write_test(os.path.join(workdir, "d4_test.py"), TEST_BUG1)
    clear_patches(workdir); run_livepatch("d4_test.py", workdir)
    jfiles = find_all_json_patches(workdir)
    check_bool(f"Sidecar .json exists ({len(jfiles)})", len(jfiles) > 0, "no .json", "B-D4")
    has_pa = False
    if jfiles:
        try:
            with open(jfiles[0], "r", encoding="utf-8") as f: d = json.load(f)
            has_pa = ("patched_at" in d) if isinstance(d, dict) else (bool(d) and "patched_at" in d[0])
        except Exception: pass
    check_bool("JSON has 'patched_at'", has_pa, "", "B-D4")
    if jfiles:
        n = backdate_patch(workdir, 8)
        if n > 0:
            print(f"  {DIM}Backdated {n} patch(es)...{RESET}")
            out, _ = run_livepatch("d4_test.py", workdir)
            check("TTL warning fires", out, must_contain=["day"], section="B-D4")
            check_bool("TTL references age", any(kw in out.lower() for kw in
                       ["old", "expired", "ttl", "day(s)"]), "expected age/TTL in output", "B-D4")

    header("D5 — Fingerprint registry", "section B")
    clear_patches(workdir)
    write_test(os.path.join(workdir, "d5_test.py"), TEST_BUG3_FULL)
    out1, _ = run_livepatch("d5_test.py", workdir)
    check("First run: no cache hit", out1, must_not_contain=["known crash pattern", "applying cached"], section="B-D5")
    fp = os.path.join(workdir, ".livepatch_fingerprints")
    check_bool(".livepatch_fingerprints created", os.path.isfile(fp), "not found", "B-D5")
    if os.path.isfile(fp):
        try:
            with open(fp, "r", encoding="utf-8") as f: fp_data = json.load(f)
            check_bool(f"Registry has entries ({len(fp_data)})", len(fp_data) > 0, "empty", "B-D5")
        except Exception as e: check_bool("Registry valid JSON", False, str(e), "B-D5")
    for name in [".livepatch_cache", ".livepatch_store", ".livepatch", "livepatch_patches"]:
        t = os.path.join(workdir, name)
        if os.path.isdir(t): shutil.rmtree(t)
        elif os.path.isfile(t): os.remove(t)
    out2, _ = run_livepatch("d5_test.py", workdir)
    check("Second run: cache hit", out2, must_contain=["known crash pattern"], section="B-D5")
    check("Second run: applying cached", out2, must_contain=["cached"], section="B-D5")

    header("D6 — Audit command", "section B")
    write_test(os.path.join(workdir, "d6_test.py"), TEST_BUG3_FULL)
    clear_patches(workdir); run_livepatch("d6_test.py", workdir)
    out, _ = run_cmd(["audit"], workdir)
    check("audit no error", out, must_not_contain=["traceback", "unrecognized argument"], section="B-D6")
    check("Shows function name", out, must_contain=["get_bio"], section="B-D6")
    check("Shows guard type", out, must_contain=["null_guard"], section="B-D6")
    check("Shows target", out, must_contain=["profile"], section="B-D6")
    check("Shows total", out, must_contain=["total"], section="B-D6")
    check("Shows age", out, must_contain=["0d"], section="B-D6")
    check("Mentions rollback", out, must_contain=["rollback"], section="B-D6")
    clear_patches(workdir)
    out_e, _ = run_cmd(["audit"], workdir)
    check("Audit empty store: no crash", out_e, must_not_contain=["traceback", "keyerror"], section="B-D6")

# ─────────────────────────────────────────────────────────────────
# SECTION C (v0.4 — identical to v3)
# ─────────────────────────────────────────────────────────────────

def run_section_C(workdir, project_path):
    header("C1 — Rollback command", "section C: v0.4")
    write_test(os.path.join(workdir, "rollback_test.py"), TEST_BUG3_FULL)
    clear_patches(workdir); run_livepatch("rollback_test.py", workdir)
    before = count_patch_files(workdir)
    out, _ = run_cmd(["rollback", "get_bio"], workdir)
    check("rollback get_bio: success", out, must_contain=["rolled back"],
          must_not_contain=["traceback"], section="C1")
    after = count_patch_files(workdir)
    check_bool(f"Removes only target ({before}->{after})",
               after < before and after > 0, f"before={before} after={after}", "C1")
    pre = count_patch_files(workdir)
    out_dry, _ = run_cmd(["rollback", "--dry-run"], workdir)
    check("--dry-run shows 'would'", out_dry, must_contain=["would"], section="C1")
    check_bool("--dry-run no delete", count_patch_files(workdir) == pre, "files changed", "C1")
    out_all, _ = run_cmd(["rollback", "--all"], workdir)
    check("rollback --all: cleared", out_all, must_contain=["cleared"], section="C1")
    check_bool("Store empty after --all", patch_store_is_empty(workdir), "store not empty", "C1")
    check_bool("Fingerprints removed",
               not os.path.isfile(os.path.join(workdir, ".livepatch_fingerprints")), "fp still exists", "C1")
    out_e, _ = run_cmd(["rollback", "--all"], workdir)
    check("--all empty: clean message", out_e, must_contain=["nothing"],
          must_not_contain=["traceback", "keyerror"], section="C1")
    out_m, _ = run_cmd(["rollback", "nonexistent_fn"], workdir)
    check("Unknown fn: no crash", out_m, must_not_contain=["traceback", "exception"], section="C1")

    header("C2 — type_coercion_guard", "section C: v0.4")
    clear_patches(workdir)
    write_test(os.path.join(workdir, "type_test.py"), TEST_TYPE_ERROR)
    out, _ = run_livepatch("type_test.py", workdir)
    check("type_coercion_guard fires", out, must_contain=["type_coercion_guard"],
          must_not_contain=["unhandled"], section="C2")
    check("Patch applied", out, must_contain=["patch applied"], section="C2")
    check("No secondary crash", out, must_not_contain=["traceback (most recent call last)",
          "cannot access local variable"], section="C2")
    out2, _ = run_livepatch("type_test.py", workdir)
    check("Healed second run", out2, must_contain=["already healed"], section="C2")

    header("C3 — index_guard", "section C: v0.4")
    clear_patches(workdir)
    write_test(os.path.join(workdir, "index_test.py"), TEST_INDEX_ERROR)
    out, _ = run_livepatch("index_test.py", workdir)
    check("index_guard fires", out, must_contain=["index_guard"],
          must_not_contain=["unhandled"], section="C3")
    check("Patch applied", out, must_contain=["patch applied"], section="C3")
    check("No secondary crash", out, must_not_contain=["traceback (most recent call last)",
          "cannot access local variable"], section="C3")
    out2, _ = run_livepatch("index_test.py", workdir)
    check("Healed second run", out2, must_contain=["already healed"], section="C3")

    header("C4 — key_guard", "section C: v0.4")
    clear_patches(workdir)
    write_test(os.path.join(workdir, "key_test.py"), TEST_KEY_ERROR)
    out, _ = run_livepatch("key_test.py", workdir)
    check("key_guard fires", out, must_contain=["key_guard"],
          must_not_contain=["unhandled"], section="C4")
    check("Patch applied", out, must_contain=["patch applied"], section="C4")
    check("No secondary crash", out, must_not_contain=["traceback (most recent call last)",
          "cannot access local variable"], section="C4")
    out2, _ = run_livepatch("key_test.py", workdir)
    check("Healed second run", out2, must_contain=["already healed"], section="C4")

    header("C5 — Dry-run mode", "section C: v0.4")
    clear_patches(workdir)
    write_test(os.path.join(workdir, "dryrun_test.py"), TEST_BUG3_FULL)
    out, _ = run_livepatch("dryrun_test.py", workdir, extra_args=["--dry-run"])
    check("--dry-run accepted", out, must_not_contain=["unrecognized argument"], section="C5")
    check("Shows 'Would apply'", out, must_contain=["would apply"], section="C5")
    check("Shows DRY-RUN label", out, must_contain=["dry-run"], section="C5")
    check("Shows 'No patches applied'", out, must_contain=["no patches applied"], section="C5")
    check("Shows guard type", out, must_contain=["null_guard"], section="C5")
    check_bool("Shows confidence level",
               any(kw in out.upper() for kw in ["HIGH", "MEDIUM", "LOW"]),
               "none of HIGH/MEDIUM/LOW in output", "C5")
    check_bool("No patch store written",
               patch_store_is_empty(workdir) or not find_patch_store(workdir),
               "store modified", "C5")
    check_bool("No fingerprints written",
               not os.path.isfile(os.path.join(workdir, ".livepatch_fingerprints")),
               "fingerprints written", "C5")

    header("C6 — pytest new guards", "section C: v0.4")
    if not project_path:
        print(f"  {SKIP}  --path not provided")
        results.append(("pytest: test_new_guards.py", None, ["skipped"], "C6")); return
    ng = os.path.join(project_path, "tests", "test_new_guards.py")
    if not os.path.isfile(ng):
        results.append(("pytest: test_new_guards.py", None, ["not found"], "C6"))
        print(f"  {SKIP}  test_new_guards.py not found")
    else:
        r = subprocess.run(["pytest", "tests/test_new_guards.py", "-v", "--tb=short"],
                           cwd=project_path, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
        ok = r.returncode == 0
        results.append(("pytest: test_new_guards.py passes", ok, [], "C6"))
        print(f"  {PASS if ok else FAIL}  pytest: test_new_guards.py passes")

    header("C7 — Version 0.5.0", "section C: v0.4")
    out, _ = run_cmd(["--version"], workdir)
    check_bool("livepatch --version shows 0.5.0", "0.5.0" in (out or ""),
               f"got: {(out or '').strip()[:80]}", "C7")
    if project_path:
        r2 = subprocess.run(["python", "-c", "import livepatch; print(livepatch.__version__)"],
                            cwd=project_path, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=10)
        v = (r2.stdout or "").strip()
        check_bool("__version__ == '0.5.0'", "0.5.0" in v, f"got: {v}", "C7")

    header("C8 — README completeness", "section C: v0.4")
    if not project_path:
        print(f"  {SKIP}  --path not provided")
        results.append(("README", None, ["skipped"], "C8")); return
    readme = os.path.join(project_path, "README.md")
    if not os.path.isfile(readme):
        check_bool("README.md exists", False, "not found", "C8"); return
    with open(readme, "r", encoding="utf-8") as f: content = f.read().lower()
    for label, kw in [("Quick start","quick start"),("How it works","how it works"),
                       ("CLI reference","cli"),("Guard types table","guard type"),
                       ("Limitations","limitation"),("livepatch run","livepatch run"),
                       ("livepatch audit","livepatch audit"),("livepatch rollback","livepatch rollback"),
                       ("--shadow","--shadow"),("--dry-run","--dry-run")]:
        check_bool(f"README: {label}", kw in content, f"'{kw}' not found", "C8")

    header("C9 — CHANGELOG completeness", "section C: v0.4")
    if not project_path:
        print(f"  {SKIP}  --path not provided")
        results.append(("CHANGELOG", None, ["skipped"], "C9")); return
    cl = os.path.join(project_path, "CHANGELOG.md")
    if not os.path.isfile(cl):
        check_bool("CHANGELOG.md exists", False, "not found", "C9"); return
    with open(cl, "r", encoding="utf-8") as f: content = f.read().lower()
    for label, kw in [("[0.5.0] entry","0.5.0"),("[0.3.0] entry","0.3.0"),
                       ("rollback","rollback"),("type_coercion","type_coercion"),
                       ("index_guard","index_guard"),("key_guard","key_guard"),
                       ("semantic diff","semantic diff"),("caller","caller"),("shadow","shadow")]:
        check_bool(f"CHANGELOG: {label}", kw in content, f"'{kw}' not found", "C9")

# ─────────────────────────────────────────────────────────────────
# SECTION D — v0.5 new features + real-world patterns
# ─────────────────────────────────────────────────────────────────

def run_section_D(workdir, project_path):

    # ── D1: Async/await support ───────────────────────────────────
    header("D1 — Async/await support", "section D: v0.5")
    clear_patches(workdir)
    write_test(os.path.join(workdir, "async_test.py"), TEST_ASYNC)
    print(f"  {DIM}Running: livepatch run async_test.py...{RESET}")
    out, _ = run_livepatch("async_test.py", workdir, timeout=30)

    check("Async function patched (no RuntimeError)", out,
          must_not_contain=["runtimeerror", "syntaxerror",
                            "coroutine was never awaited"], section="D1")
    check("Patch applied to async function", out,
          must_contain=["patch applied"], section="D1")
    check("Async test: no secondary crash", out,
          must_not_contain=["traceback (most recent call last)",
                            "cannot access local variable"], section="D1")
    # Second run: healed
    out2, _ = run_livepatch("async_test.py", workdir, timeout=30)
    check("Async test: healed on second run", out2,
          must_contain=["already healed"], section="D1")

    # ── D2: Watch mode ────────────────────────────────────────────
    header("D2 — Watch mode (livepatch watch)", "section D: v0.5")
    clear_patches(workdir)
    write_test(os.path.join(workdir, "watch_test.py"), TEST_WATCH)
    print(f"  {DIM}Running: livepatch watch watch_test.py --max-restarts 3...{RESET}")
    out, rc = run_watch("watch_test.py", workdir,
                        extra_args=["--max-restarts", "3"], timeout=45)

    check("watch command accepted", out,
          must_not_contain=["unrecognized argument", "error: argument"], section="D2")
    check("Watch detects crash and patches", out,
          must_contain=["watch"], section="D2")
    check("Watch reports clean exit", out,
          must_contain=["exited cleanly"], section="D2")
    check_bool("Watch exits 0 on success", rc == 0,
               f"exit code was {rc}", "D2")
    check("Watch does not loop forever", out,
          must_not_contain=["max restarts"], section="D2")

    # ── D3: Explain command ───────────────────────────────────────
    header("D3 — livepatch explain command", "section D: v0.5")
    clear_patches(workdir)
    write_test(os.path.join(workdir, "explain_setup.py"), TEST_EXPLAIN_SETUP)
    run_livepatch("explain_setup.py", workdir)  # create patches

    print(f"  {DIM}Running: livepatch explain...{RESET}")
    out, _ = run_cmd(["explain"], workdir)
    check("explain runs without error", out,
          must_not_contain=["traceback", "unrecognized argument",
                            "error:"], section="D3")
    check("explain shows function name", out, must_contain=["get_bio"], section="D3")
    check("explain shows guard type", out, must_contain=["null_guard"], section="D3")
    check("explain shows default value", out, must_contain=["default"], section="D3")
    check("explain shows Safe? assessment", out, must_contain=["safe"], section="D3")
    check("explain shows age", out,
          must_contain_any := any(kw in out.lower() for kw in ["day", "ago", "applied"]),
          section="D3") if False else None
    check_bool("explain shows age/timestamp",
               any(kw in out.lower() for kw in ["day", "ago", "applied", "patched"]),
               "no age/timestamp in explain output", "D3")

    # Explain with no patches — must not crash
    clear_patches(workdir)
    out_e, _ = run_cmd(["explain"], workdir)
    check("explain empty store: no crash", out_e,
          must_not_contain=["traceback", "keyerror", "attributeerror"], section="D3")

    # Explain single function
    run_livepatch("explain_setup.py", workdir)
    out_fn, _ = run_cmd(["explain", "get_bio"], workdir)
    check("explain get_bio: no crash", out_fn,
          must_not_contain=["traceback"], section="D3")
    check("explain get_bio: shows function", out_fn,
          must_contain=["get_bio"], section="D3")

    # ── D4: WSGI Middleware ───────────────────────────────────────
    header("D4 — WSGI Middleware integration", "section D: v0.5")
    clear_patches(workdir)
    write_test(os.path.join(workdir, "test_middleware.py"), TEST_MIDDLEWARE)
    print(f"  {DIM}Running: python test_middleware.py...{RESET}")
    out, rc = run_python("test_middleware.py", workdir, timeout=20)

    check("Middleware import works", out,
          must_not_contain=["importerror", "modulenotfounderror"], section="D4")
    check("Middleware test passes", out,
          must_contain=["middleware test passed"], section="D4")
    check("No crash in middleware", out,
          must_not_contain=["assertionerror", "traceback (most recent call last)"], section="D4")
    check_bool("Middleware exits 0", rc == 0, f"exit code was {rc}", "D4")

    # ── D5: Real-world patterns (anti-template) ───────────────────
    header("D5 — Real-world patterns (anti-template validation)", "section D: v0.5")

    rw_tests = [
        ("rw_classmethod.py", TEST_REALWORLD_CLASSMETHOD,
         "Class method: instance with None attribute",
         ["patch applied"], ["traceback (most recent call last)",
                             "cannot access local variable"]),
        ("rw_kwargs.py", TEST_REALWORLD_KWARGS,
         "kwargs: None value in **kwargs",
         ["patch applied"], ["traceback (most recent call last)"]),
        ("rw_decorator.py", TEST_REALWORLD_DECORATOR,
         "Decorator: patch through functools.wraps wrapper",
         ["patch applied"], ["traceback (most recent call last)",
                             "cannot access local variable"]),
        ("rw_property.py", TEST_REALWORLD_PROPERTY,
         "Property getter: crash inside @property",
         ["patch applied"], ["traceback (most recent call last)"]),
        ("rw_nested.py", TEST_REALWORLD_NESTED,
         "3-level deep chain: a.b.c.d",
         ["patch applied"], ["traceback (most recent call last)",
                             "cannot access local variable"]),
    ]

    for fname, content, label, must_have, must_not in rw_tests:
        clear_patches(workdir)
        write_test(os.path.join(workdir, fname), content)
        print(f"  {DIM}Running: livepatch run {fname}...{RESET}")
        out, _ = run_livepatch(fname, workdir, timeout=20)
        check(f"Real-world: {label}", out,
              must_contain=must_have, must_not_contain=must_not, section="D5")
        # Second run healed
        out2, _ = run_livepatch(fname, workdir, timeout=20)
        check(f"Real-world healed: {label}", out2,
              must_contain=["already healed"], section="D5")

    # ── D6: Version 0.5.0 ────────────────────────────────────────
    header("D6 — Version 0.5.0 confirmed", "section D: v0.5")
    out, _ = run_cmd(["--version"], workdir)
    check_bool("livepatch --version shows 0.5.0",
               "0.5.0" in (out or ""),
               f"got: {(out or '').strip()[:80]}", "D6")
    if project_path:
        r2 = subprocess.run(
            ["python", "-c", "import livepatch; print(livepatch.__version__)"],
            cwd=project_path, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=10)
        v = (r2.stdout or "").strip()
        check_bool("livepatch.__version__ == '0.5.0'", "0.5.0" in v, f"got: {v}", "D6")

# ─────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────

def summary():
    header("VERIFICATION SUMMARY — ALL SECTIONS")
    order = ["A","B-D1","B-D2","B-D3","B-D4","B-D5","B-D6",
             "C1","C2","C3","C4","C5","C6","C7","C8","C9",
             "D1","D2","D3","D4","D5","D6"]
    labels = {
        "A":"Core engine (16)","B-D1":"D1 Semantic diff","B-D2":"D2 Caller propagation",
        "B-D3":"D3 Shadow mode","B-D4":"D4 Patch TTL","B-D5":"D5 Fingerprint registry",
        "B-D6":"D6 Audit command","C1":"C1 Rollback","C2":"C2 type_coercion_guard",
        "C3":"C3 index_guard","C4":"C4 key_guard","C5":"C5 Dry-run",
        "C6":"C6 pytest new guards","C7":"C7 Version 0.5.0","C8":"C8 README",
        "C9":"C9 CHANGELOG","D1":"D1 Async/await","D2":"D2 Watch mode",
        "D3":"D3 Explain command","D4":"D4 WSGI Middleware",
        "D5":"D5 Real-world patterns","D6":"D6 Version 0.5.0",
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
        print(f"\n  {icon}  {BOLD}{labels.get(sec,sec)}{RESET}  {DIM}({p}✓ {f}✗ {s}–){RESET}")
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
        print("LivePatch v0.5.0 — production-ready.")
    else:
        print(f"{BOLD}{RED}{tf} CHECK(S) FAILED{RESET} ({tp} passed, {ts} skipped / {total})")

# ─────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LivePatch verification suite v4.0")
    parser.add_argument("--path", "-p", default=None)
    parser.add_argument("--workdir", "-w", default=".")
    parser.add_argument("--section", "-s",
                        choices=["A","B","AB","C","D","CD","ABC","all"], default="all")
    args = parser.parse_args()
    workdir = os.path.abspath(args.workdir)
    os.makedirs(workdir, exist_ok=True)
    print(f"\n{BOLD}LivePatch Verification Suite v4.0{RESET}")
    print(f"{DIM}workdir : {workdir}{RESET}")
    print(f"{DIM}project : {args.path or 'not provided'}{RESET}")
    print(f"{DIM}section : {args.section}{RESET}")
    if args.section in ("A","AB","ABC","all"):   run_section_A(workdir, args.path)
    if args.section in ("B","AB","ABC","all"):   run_section_B(workdir)
    if args.section in ("C","CD","ABC","all"):   run_section_C(workdir, args.path)
    if args.section in ("D","CD","all"):         run_section_D(workdir, args.path)
    summary()