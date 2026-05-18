"""
LivePatch Verification Suite v2.0
Tests: 16 core checks + 6 dark upgrade verifications
"""

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
# Test file contents
# ─────────────────────────────────────────────────────────────────

TEST_BUG1 = """\
class Profile:
    def __init__(self, bio):
        self.bio = bio

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
    def __init__(self, bio):
        self.bio = bio

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
    def __init__(self, name):
        self.name = name

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
    def __init__(self, n):
        self.name = n

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
    def __init__(self, bio):
        self.bio = bio

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

# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────

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
        result = subprocess.run(cmd, cwd=cwd, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=timeout)
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        return stdout + stderr, result.returncode
    except FileNotFoundError:
        return None, -999
    except subprocess.TimeoutExpired:
        return "TIMEOUT", -998

def run_livepatch_cmd(args, cwd, timeout=15):
    """Run any livepatch subcommand e.g. ['audit']"""
    cmd = ["livepatch"] + args
    try:
        result = subprocess.run(cmd, cwd=cwd, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=timeout)
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        return stdout + stderr, result.returncode
    except FileNotFoundError:
        return None, -999
    except subprocess.TimeoutExpired:
        return "TIMEOUT", -998

def check(label, output, must_contain=None, must_not_contain=None,
          section=None):
    passed = True
    reasons = []

    if output is None:
        results.append((label, False, ["livepatch not found on PATH"], section))
        print(f"  {FAIL}  {label}")
        print(f"         {RED}livepatch not found{RESET}")
        return False

    if output == "TIMEOUT":
        results.append((label, False, ["timed out after 30s"], section))
        print(f"  {FAIL}  {label}  {DIM}(timeout){RESET}")
        return False

    if must_contain:
        for pattern in must_contain:
            if pattern.lower() not in output.lower():
                passed = False
                reasons.append(f"missing: '{pattern}'")

    if must_not_contain:
        for pattern in must_not_contain:
            if pattern.lower() in output.lower():
                passed = False
                reasons.append(f"should NOT contain: '{pattern}'")

    results.append((label, passed, reasons, section))

    if passed:
        print(f"  {PASS}  {label}")
    else:
        print(f"  {FAIL}  {label}")
        for r in reasons:
            print(f"         {RED}↳ {r}{RESET}")
        if output.strip():
            preview = output.strip()[-500:]
            for line in preview.splitlines()[-8:]:
                print(f"         {DIM}{line}{RESET}")

    return passed

def write_test(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(textwrap.dedent(content))

def clear_patches(cwd):
    for name in [".livepatch_cache", ".livepatch_knowledge",
                 ".livepatch_store", ".livepatch", "livepatch_patches",
                 ".livepatch_fingerprints"]:
        target = os.path.join(cwd, name)
        if os.path.isdir(target):
            shutil.rmtree(target)
        elif os.path.isfile(target):
            os.remove(target)

def find_patch_store(cwd):
    for name in [".livepatch_cache", ".livepatch_store",
                 ".livepatch", "livepatch_patches"]:
        p = os.path.join(cwd, name)
        if os.path.exists(p):
            return p
    return None

def find_all_json_patches(cwd):
    """Find all .json sidecar patch files in the store."""
    store = find_patch_store(cwd)
    if not store:
        return []
    files = []
    if os.path.isdir(store):
        for fname in os.listdir(store):
            if fname.endswith(".json"):
                files.append(os.path.join(store, fname))
    elif os.path.isfile(store):
        files.append(store)
    return files

def backdate_patch(cwd, days=8):
    """Modify a patch's patched_at to simulate expiry."""
    backdated = 0
    for jpath in find_all_json_patches(cwd):
        try:
            with open(jpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            modified = False
            if isinstance(data, dict) and "patched_at" in data:
                old = datetime.utcnow() - timedelta(days=days)
                data["patched_at"] = old.isoformat()
                modified = True
            elif isinstance(data, list):
                for entry in data:
                    if isinstance(entry, dict) and "patched_at" in entry:
                        old = datetime.utcnow() - timedelta(days=days)
                        entry["patched_at"] = old.isoformat()
                        modified = True
            if modified:
                with open(jpath, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                backdated += 1
        except Exception as e:
            print(f"         {DIM}backdate failed on {jpath}: {e}{RESET}")
    return backdated

# ─────────────────────────────────────────────────────────────────
# ── SECTION A: Original 16 checks ─────────────────────────────────
# ─────────────────────────────────────────────────────────────────

def test_install_check():
    header("0. Install check", "section A")
    result = subprocess.run(["livepatch", "--version"],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    stdout = result.stdout or ""
    stderr = result.stderr or ""
    ok = result.returncode == 0 or \
         "livepatch" in (stdout + stderr).lower()
    results.append(("livepatch on PATH", ok, [], "A"))
    print(f"  {PASS if ok else FAIL}  livepatch is on PATH")
    return ok


def test_bug1_chain_depth(workdir):
    header("1. Bug 1 — Chain depth resolver", "section A")
    clear_patches(workdir)
    write_test(os.path.join(workdir, "bug1_test.py"), TEST_BUG1)
    output, _ = run_livepatch("bug1_test.py", workdir)
    check("Guards 'profile', not 'user'", output,
          must_contain=["null_guard on 'profile'"],
          must_not_contain=["null_guard on 'user'"], section="A")
    check("No secondary 'cannot access local variable' crash", output,
          must_not_contain=["cannot access local variable"], section="A")
    check("Patch is applied (not rejected)", output,
          must_contain=["patch applied"],
          must_not_contain=["patch rejected"], section="A")


def test_bug2_validator(workdir):
    header("2. Bug 2 — Frame validator", "section A")
    clear_patches(workdir)
    write_test(os.path.join(workdir, "bug2_test.py"), TEST_BUG1)
    output, _ = run_livepatch("bug2_test.py", workdir)
    check("Valid patch passes validator (no rejection log)", output,
          must_not_contain=["patch rejected", "not in co_varnames"],
          section="A")
    check("No phantom local variable error after patch", output,
          must_not_contain=["cannot access local variable",
                            "unboundlocalerror"], section="A")
    output2, _ = run_livepatch("bug2_test.py", workdir)
    check("Persisted patch loads without validator error on re-run", output2,
          must_contain=["already healed"],
          must_not_contain=["patch rejected", "cannot access local variable"],
          section="A")


def test_bug3_dedup(workdir):
    header("3. Bug 3 — Cross-function deduplication", "section A")
    clear_patches(workdir)
    write_test(os.path.join(workdir, "bug3_hard_test.py"), TEST_BUG3_FULL)
    output, _ = run_livepatch("bug3_hard_test.py", workdir)
    count_match = re.search(r"patches applied[:\s]+(\d+)", output, re.IGNORECASE)
    patch_count = int(count_match.group(1)) if count_match else -1
    dedup_ok = 0 < patch_count <= 2
    label = f"Patches applied: {patch_count} (expected ≤ 2)"
    results.append((label, dedup_ok, [] if dedup_ok else
                    [f"got {patch_count}"], "A"))
    print(f"  {PASS if dedup_ok else FAIL}  {label}")
    check("Script finishes without secondary crash", output,
          must_not_contain=["cannot access local variable",
                            "traceback (most recent call last)"], section="A")
    check("Guards 'profile' (correct chain target)", output,
          must_contain=["profile"],
          must_not_contain=["null_guard on 'user'"], section="A")


def test_persistence(workdir):
    header("4. Persistence — second run shows 0 new patches", "section A")
    output, _ = run_livepatch("bug3_hard_test.py", workdir)
    check("'Already healed' for all functions", output,
          must_contain=["already healed"], section="A")
    check("Patches applied: 0 on second run", output,
          must_contain=["patches applied: 0"], section="A")
    check("Script completes successfully", output,
          must_not_contain=["traceback (most recent call last)", " error"],
          section="A")


def test_simple_regression(workdir):
    header("5. Regression — simple test still works", "section A")
    clear_patches(workdir)
    write_test(os.path.join(workdir, "simple_reg_test.py"), TEST_SIMPLE)
    output, _ = run_livepatch("simple_reg_test.py", workdir)
    check("Simple single-level null guard still applies", output,
          must_contain=["patch applied"], section="A")
    check("No secondary crash on simple test", output,
          must_not_contain=["cannot access local variable",
                            "traceback (most recent call last)"], section="A")


def test_pytest_regression(project_path, workdir):
    header("6. pytest — existing test suite, zero regressions", "section A")
    if not project_path:
        print(f"  {SKIP}  --path not provided")
        results.append(("pytest regression suite", None, ["skipped"], "A"))
        return
    tests_dir = os.path.join(project_path, "tests")
    if not os.path.isdir(tests_dir):
        print(f"  {SKIP}  No tests/ directory at {project_path}")
        results.append(("pytest regression suite", None, ["no tests/ dir"], "A"))
        return
    result = subprocess.run(
        ["pytest", "tests/", "-v", "--tb=short", "-x"],
        cwd=project_path, capture_output=True, text=True, timeout=120
    )
    output = result.stdout + result.stderr
    passed = result.returncode == 0
    results.append(("pytest: all tests pass", passed,
                    [] if passed else ["see output"], "A"))
    print(f"  {PASS if passed else FAIL}  pytest: all tests pass")
    if not passed:
        for line in output.splitlines():
            if "FAILED" in line or "ERROR" in line:
                print(f"         {RED}{line}{RESET}")


# ─────────────────────────────────────────────────────────────────
# ── SECTION B: Dark upgrade verifications ─────────────────────────
# ─────────────────────────────────────────────────────────────────

def test_d1_semantic_diff(workdir):
    header("D1 — Semantic diff safety gate",
           "section B: dark upgrades")
    clear_patches(workdir)
    write_test(os.path.join(workdir, "d1_test.py"), TEST_BUG1)

    print(f"  {DIM}Running with --verbose to check diff stats...{RESET}")
    output, _ = run_livepatch("d1_test.py", workdir, extra_args=["--verbose"])

    check("--verbose shows diff instruction count", output,
          must_contain=["diff:"], section="B-D1")

    check("Patch not rejected (valid guard is within delta)", output,
          must_not_contain=["semantic diff too large",
                            "patch rejected: semantic diff"], section="B-D1")

    check("No secondary crash after diff-gated patch", output,
          must_not_contain=["cannot access local variable"], section="B-D1")

    # Confirm the diff line format: should contain + and - counts
    has_diff_format = bool(re.search(
        r"diff.*\+\d+.*-\d+|diff.*instructions", output, re.IGNORECASE
    ))
    results.append(("Diff log shows +N -N instruction counts",
                    has_diff_format, [] if has_diff_format else
                    ["expected '+N -N instructions' pattern in output"], "B-D1"))
    print(f"  {PASS if has_diff_format else FAIL}  "
          f"Diff log shows +N -N instruction counts")


def test_d2_caller_propagation(workdir):
    header("D2 — Caller-aware patch propagation",
           "section B: dark upgrades")
    clear_patches(workdir)
    write_test(os.path.join(workdir, "d2_closure_test.py"), TEST_CLOSURE)

    print(f"  {DIM}Running closure test...{RESET}")
    output, _ = run_livepatch("d2_closure_test.py", workdir)

    check("Patch propagates into closure (log confirms)", output,
          must_contain=["propagated patch"], section="B-D2")

    check("Closure test completes without crash", output,
          must_not_contain=["cannot access local variable",
                            "traceback (most recent call last)"], section="B-D2")

    # Second run — persisted patch should load for closure too
    print(f"  {DIM}Second run (persistence of propagated patch)...{RESET}")
    output2, _ = run_livepatch("d2_closure_test.py", workdir)
    check("Propagated patch persists across runs", output2,
          must_contain=["already healed"], section="B-D2")


def test_d3_shadow_mode(workdir):
    header("D3 — Shadow execution mode (--shadow flag)",
           "section B: dark upgrades")
    clear_patches(workdir)
    write_test(os.path.join(workdir, "d3_shadow_test.py"), TEST_SHADOW)

    print(f"  {DIM}Running: livepatch run --shadow d3_shadow_test.py...{RESET}")
    output, _ = run_livepatch("d3_shadow_test.py", workdir,
                              extra_args=["--shadow"])

    check("--shadow flag accepted (no 'unrecognized argument' error)", output,
          must_not_contain=["unrecognized argument",
                            "error: argument"], section="B-D3")

    check("Shadow warning fires for sentinel return value", output,
          must_contain=["livepatch shadow"], section="B-D3")

    check("Shadow warning mentions sentinel value", output,
          must_contain=["sentinel"], section="B-D3")

    # Run WITHOUT --shadow — should be no shadow lines
    clear_patches(workdir)
    output_no_shadow, _ = run_livepatch("d3_shadow_test.py", workdir)
    check("No shadow warnings without --shadow flag", output_no_shadow,
          must_not_contain=["livepatch shadow"], section="B-D3")


def test_d4_patch_ttl(workdir):
    header("D4 — Patch expiry TTL",
           "section B: dark upgrades")

    # Ensure patches exist from prior tests — run hard test
    write_test(os.path.join(workdir, "d4_test.py"), TEST_BUG1)
    clear_patches(workdir)
    run_livepatch("d4_test.py", workdir)  # create fresh patches

    # Verify JSON sidecar files exist with patched_at field
    json_files = find_all_json_patches(workdir)
    has_sidecars = len(json_files) > 0
    results.append(("Sidecar .json patch metadata files exist",
                    has_sidecars, [] if has_sidecars else
                    ["no .json files found in patch store"], "B-D4"))
    print(f"  {PASS if has_sidecars else FAIL}  "
          f"Sidecar .json patch metadata files exist "
          f"{DIM}({len(json_files)} found){RESET}")

    has_patched_at = False
    if json_files:
        try:
            with open(json_files[0], "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                has_patched_at = "patched_at" in data
            elif isinstance(data, list) and data:
                has_patched_at = "patched_at" in data[0]
        except Exception:
            pass
    results.append(("JSON metadata contains 'patched_at' field",
                    has_patched_at, [], "B-D4"))
    print(f"  {PASS if has_patched_at else FAIL}  "
          f"JSON metadata contains 'patched_at' field")

    # Backdate and verify warning fires
    if has_sidecars:
        backdated = backdate_patch(workdir, days=8)
        if backdated > 0:
            print(f"  {DIM}Backdated {backdated} patch(es) to 8 days ago...{RESET}")
            output, _ = run_livepatch("d4_test.py", workdir)
            check("TTL expiry warning fires for 8-day-old patch", output,
                  must_contain=["day"], section="B-D4")
            # Should contain either "8 day" or "ttl" or "expired" or "old"
            has_ttl_warn = any(kw in output.lower() for kw in
                               ["old", "expired", "ttl", "day(s)"])
            results.append(("TTL warning message references age/TTL",
                            has_ttl_warn, [] if has_ttl_warn else
                            ["expected 'days old' or 'TTL' in output"], "B-D4"))
            print(f"  {PASS if has_ttl_warn else FAIL}  "
                  f"TTL warning message references age/TTL")
        else:
            print(f"  {SKIP}  Could not backdate patches (no patched_at field?)")
            results.append(("TTL expiry warning fires", None, ["skipped"], "B-D4"))


def test_d5_fingerprint_registry(workdir):
    header("D5 — Bytecode fingerprint registry",
           "section B: dark upgrades")

    # Clean start
    clear_patches(workdir)
    write_test(os.path.join(workdir, "d5_test.py"), TEST_BUG3_FULL)

    print(f"  {DIM}First run — no fingerprint match expected...{RESET}")
    output1, _ = run_livepatch("d5_test.py", workdir)
    check("First run: normal analysis (no cached pattern)", output1,
          must_not_contain=["known crash pattern",
                            "applying cached"], section="B-D5")

    # Check fingerprint file was created
    fp_file = os.path.join(workdir, ".livepatch_fingerprints")
    fp_exists = os.path.isfile(fp_file)
    results.append((".livepatch_fingerprints file created after first run",
                    fp_exists, [] if fp_exists else
                    [".livepatch_fingerprints not found"], "B-D5"))
    print(f"  {PASS if fp_exists else FAIL}  "
          f".livepatch_fingerprints file created after first run")

    if fp_exists:
        try:
            with open(fp_file, "r", encoding="utf-8") as f:
                fp_data = json.load(f)
            has_entries = len(fp_data) > 0
            results.append(("Fingerprint registry contains entries",
                            has_entries, [], "B-D5"))
            print(f"  {PASS if has_entries else FAIL}  "
                  f"Fingerprint registry contains entries "
                  f"{DIM}({len(fp_data)} hash(es)){RESET}")
        except Exception as e:
            results.append(("Fingerprint registry is valid JSON",
                            False, [str(e)], "B-D5"))
            print(f"  {FAIL}  Fingerprint registry is valid JSON: {e}")

    # Clear patches only (keep fingerprints), second run should hit cache
    for name in [".livepatch_cache", ".livepatch_store",
                 ".livepatch", "livepatch_patches"]:
        target = os.path.join(workdir, name)
        if os.path.isdir(target):
            shutil.rmtree(target)
        elif os.path.isfile(target):
            os.remove(target)

    print(f"  {DIM}Second run (patches cleared, fingerprints kept)...{RESET}")
    output2, _ = run_livepatch("d5_test.py", workdir)
    check("Second run: fingerprint cache hit detected", output2,
          must_contain=["known crash pattern"], section="B-D5")
    check("Second run: 'applying cached' guard logged", output2,
          must_contain=["cached"], section="B-D5")


def test_d6_audit_command(workdir):
    header("D6 — livepatch audit command",
           "section B: dark upgrades")

    # Ensure patches exist
    write_test(os.path.join(workdir, "d6_test.py"), TEST_BUG3_FULL)
    clear_patches(workdir)
    run_livepatch("d6_test.py", workdir)

    print(f"  {DIM}Running: livepatch audit...{RESET}")
    output, rc = run_livepatch_cmd(["audit"], workdir)

    check("'livepatch audit' command runs without error", output,
          must_not_contain=["error", "traceback",
                            "unrecognized argument"], section="B-D6")

    check("Audit table shows function name", output,
          must_contain=["get_bio"], section="B-D6")

    check("Audit table shows guard type", output,
          must_contain=["null_guard"], section="B-D6")

    check("Audit table shows patch target (profile)", output,
          must_contain=["profile"], section="B-D6")

    check("Audit table shows total count", output,
          must_contain=["total"], section="B-D6")

    check("Audit table shows age in days", output,
          must_contain=["0d"], section="B-D6")

    # Test with no patches — should not crash
    clear_patches(workdir)
    output_empty, _ = run_livepatch_cmd(["audit"], workdir)
    check("'livepatch audit' with no patches: clean message, no crash",
          output_empty,
          must_not_contain=["traceback", "keyerror", "attributeerror"],
          section="B-D6")

    # Verify rollback hint present
    check("Audit mentions rollback command", output,
          must_contain=["rollback"], section="B-D6")


# ─────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────

def summary():
    header("VERIFICATION SUMMARY — all sections")

    sections = {"A": [], "B-D1": [], "B-D2": [], "B-D3": [],
                "B-D4": [], "B-D5": [], "B-D6": []}
    section_labels = {
        "A":    "Core (original 16)",
        "B-D1": "D1 Semantic diff",
        "B-D2": "D2 Caller propagation",
        "B-D3": "D3 Shadow mode",
        "B-D4": "D4 Patch TTL",
        "B-D5": "D5 Fingerprint registry",
        "B-D6": "D6 Audit command",
    }

    for label, ok, reasons, sec in results:
        sections.setdefault(sec, []).append((label, ok, reasons))

    total_pass = total_fail = total_skip = 0
    for sec_key, checks in sections.items():
        if not checks:
            continue
        p = sum(1 for _, ok, _ in checks if ok is True)
        f = sum(1 for _, ok, _ in checks if ok is False)
        s = sum(1 for _, ok, _ in checks if ok is None)
        total_pass += p
        total_fail += f
        total_skip += s
        status = f"{GREEN}✓{RESET}" if f == 0 else f"{RED}✗{RESET}"
        print(f"\n  {status}  {BOLD}{section_labels.get(sec_key, sec_key)}{RESET}"
              f"  {DIM}({p} pass, {f} fail, {s} skip){RESET}")
        for label, ok, reasons in checks:
            if ok is True:
                print(f"       {GREEN}✓{RESET}  {label}")
            elif ok is False:
                print(f"       {RED}✗{RESET}  {label}")
                for r in reasons:
                    print(f"           {RED}↳ {r}{RESET}")
            else:
                print(f"       {YELLOW}–{RESET}  {label}  {DIM}(skipped){RESET}")

    total = total_pass + total_fail + total_skip
    print()
    print(f"{CYAN}{'─'*60}{RESET}")
    if total_fail == 0:
        print(f"{BOLD}{GREEN}ALL {total_pass}/{total} CHECKS PASSED.{RESET}")
        print(f"LivePatch v0.3.0 — core + all 6 dark upgrades verified.")
    else:
        print(f"{BOLD}{RED}{total_fail} CHECK(S) FAILED{RESET} "
              f"({total_pass} passed, {total_skip} skipped out of {total})")

# ─────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="LivePatch v2 verification suite — core + dark upgrades"
    )
    parser.add_argument("--path", "-p",
                        help="Path to livepatch project root (for pytest)",
                        default=None)
    parser.add_argument("--workdir", "-w",
                        help="Directory for temp test files (default: .)",
                        default=".")
    parser.add_argument("--section", "-s",
                        choices=["A", "B", "all"],
                        default="all",
                        help="Run only section A (core), B (dark upgrades), or all")
    args = parser.parse_args()

    workdir = os.path.abspath(args.workdir)
    os.makedirs(workdir, exist_ok=True)

    print(f"\n{BOLD}LivePatch Verification Suite v2.0{RESET}")
    print(f"{DIM}workdir : {workdir}{RESET}")
    print(f"{DIM}project : {args.path or 'not provided (pytest skipped)'}{RESET}")
    print(f"{DIM}section : {args.section}{RESET}")

    if not test_install_check():
        print(f"\n{RED}Cannot continue — livepatch not found on PATH.{RESET}")
        sys.exit(1)

    run_a = args.section in ("A", "all")
    run_b = args.section in ("B", "all")

    if run_a:
        test_bug1_chain_depth(workdir)
        test_bug2_validator(workdir)
        test_bug3_dedup(workdir)
        test_persistence(workdir)
        test_simple_regression(workdir)
        test_pytest_regression(args.path, workdir)

    if run_b:
        test_d1_semantic_diff(workdir)
        test_d2_caller_propagation(workdir)
        test_d3_shadow_mode(workdir)
        test_d4_patch_ttl(workdir)
        test_d5_fingerprint_registry(workdir)
        test_d6_audit_command(workdir)

    summary()