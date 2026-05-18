"""
LivePatch Manual Verification Suite
Run: python livepatch_verify.py [--path C:/path/to/livepatch]
Tests all three bug fixes + persistence + regression.
"""

import subprocess
import sys
import os
import shutil
import argparse
import textwrap
import time

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

user = User("Bob", None)  # profile is None — depth-2 chain crash
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

# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────

def header(title):
    bar = "─" * 60
    print(f"\n{CYAN}{bar}{RESET}")
    print(f"{BOLD}  {title}{RESET}")
    print(f"{CYAN}{bar}{RESET}")

def run_livepatch(script_path, cwd, timeout=30):
    """Run livepatch run <script> and capture full output."""
    cmd = ["livepatch", "run", script_path]
    try:
        result = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout
        )
        combined = result.stdout + result.stderr
        return combined, result.returncode
    except FileNotFoundError:
        return None, -999
    except subprocess.TimeoutExpired:
        return "TIMEOUT", -998

def check(label, output, must_contain=None, must_not_contain=None, rc_ok=None):
    """Assert output conditions and record result."""
    passed = True
    reasons = []

    if output is None:
        results.append((label, False, ["livepatch command not found on PATH"]))
        print(f"  {FAIL}  {label}")
        print(f"         {RED}livepatch not found — is it installed and on PATH?{RESET}")
        return False

    if output == "TIMEOUT":
        results.append((label, False, ["timed out after 30s"]))
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

    results.append((label, passed, reasons))

    if passed:
        print(f"  {PASS}  {label}")
    else:
        print(f"  {FAIL}  {label}")
        for r in reasons:
            print(f"         {RED}↳ {r}{RESET}")
        if output.strip():
            preview = output.strip()[-600:]
            print(f"         {DIM}--- output tail ---{RESET}")
            for line in preview.splitlines():
                print(f"         {DIM}{line}{RESET}")

    return passed

def write_test(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(textwrap.dedent(content))

def clear_patches(cwd):
    """Remove any persisted patch files so tests start clean."""
    for name in [".livepatch_store", ".livepatch", "livepatch_patches", ".livepatch_cache", ".livepatch_knowledge", ".livepatch_fingerprints"]:
        target = os.path.join(cwd, name)
        if os.path.isdir(target):
            shutil.rmtree(target)
        elif os.path.isfile(target):
            os.remove(target)

# ─────────────────────────────────────────────────────────────────
# Test suites
# ─────────────────────────────────────────────────────────────────

def test_install_check():
    header("0. Install check")
    result = subprocess.run(
        ["livepatch", "--version"], capture_output=True, text=True
    )
    ok = result.returncode == 0 or "livepatch" in (result.stdout + result.stderr).lower()
    status = PASS if ok else FAIL
    print(f"  {status}  livepatch is on PATH")
    results.append(("livepatch on PATH", ok, []))
    return ok


def test_bug1_chain_depth(workdir):
    header("1. Bug 1 — Chain depth resolver (profile guard, NOT user guard)")
    clear_patches(workdir)
    script = os.path.join(workdir, "bug1_test.py")
    write_test(script, TEST_BUG1)

    print(f"  {DIM}Running: livepatch run bug1_test.py{RESET}")
    output, _ = run_livepatch("bug1_test.py", workdir)

    check("Guards 'profile', not 'user'",      output,
          must_contain=["null_guard on 'profile'"],
          must_not_contain=["null_guard on 'user'"])

    check("No secondary 'cannot access local variable' crash", output,
          must_not_contain=["cannot access local variable"])

    check("Patch is applied (not rejected)",   output,
          must_contain=["patch applied"],
          must_not_contain=["patch rejected"])


def test_bug2_validator(workdir):
    header("2. Bug 2 — Frame validator (bad patches rejected)")

    # We verify indirectly: after Bug 1 fix, the chain is correct so the
    # validator never fires for a legitimate patch. We check the validator
    # path by confirming there is no phantom LOAD_FAST in any applied patch.
    # Direct validator rejection is tested by a synthesized bad bytecode call
    # if livepatch exposes a --dry-run or internal test; otherwise we check
    # the negative: no "cannot access local variable" on any valid test run.

    clear_patches(workdir)
    script = os.path.join(workdir, "bug2_test.py")
    write_test(script, TEST_BUG1)

    output, _ = run_livepatch("bug2_test.py", workdir)

    check("Valid patch passes validator (no rejection log)", output,
          must_not_contain=["patch rejected", "not in co_varnames"])

    check("No phantom local variable error after patch", output,
          must_not_contain=["cannot access local variable",
                            "unboundlocalerror"])

    # Second run — validator should not reject persisted patches either
    print(f"  {DIM}Second run (persistence check for validator)...{RESET}")
    output2, _ = run_livepatch("bug2_test.py", workdir)

    check("Persisted patch loads without validator error on re-run", output2,
          must_contain=["already healed"],
          must_not_contain=["patch rejected", "cannot access local variable"])


def test_bug3_dedup(workdir):
    header("3. Bug 3 — Cross-function deduplication (patches ≤ 2, not 3)")
    clear_patches(workdir)
    script = os.path.join(workdir, "bug3_hard_test.py")
    write_test(script, TEST_BUG3_FULL)

    print(f"  {DIM}Running: livepatch run bug3_hard_test.py (first run)...{RESET}")
    output, _ = run_livepatch("bug3_hard_test.py", workdir)

    # Check patch count — must be 1 or 2, definitely NOT 3
    import re
    count_match = re.search(r"patches applied[:\s]+(\d+)", output, re.IGNORECASE)
    patch_count = int(count_match.group(1)) if count_match else -1

    dedup_ok = 0 < patch_count <= 2
    label = f"Patches applied: {patch_count} (expected ≤ 2)"
    results.append((label, dedup_ok, [] if dedup_ok else [f"got {patch_count}, need ≤ 2"]))
    status = PASS if dedup_ok else FAIL
    print(f"  {status}  {label}")

    check("Script finishes without secondary crash", output,
          must_not_contain=["cannot access local variable",
                            "traceback (most recent call last)"])

    check("Guards 'profile' (correct chain target)", output,
          must_contain=["profile"],
          must_not_contain=["null_guard on 'user'"])


def test_persistence(workdir):
    header("4. Persistence — second run shows 0 new patches")
    # workdir still has patches from Bug 3 test — run same file again
    script = "bug3_hard_test.py"
    print(f"  {DIM}Running: livepatch run bug3_hard_test.py (second run)...{RESET}")
    output, _ = run_livepatch(script, workdir)

    check("'Already healed' for all functions",   output,
          must_contain=["already healed"])

    check("Patches applied: 0 on second run",      output,
          must_contain=["patches applied: 0"])

    check("Script completes successfully",         output,
          must_not_contain=["traceback (most recent call last)",
                            "error"])


def test_simple_regression(workdir):
    header("5. Regression — original simple test still works")
    clear_patches(workdir)
    script = os.path.join(workdir, "simple_reg_test.py")
    write_test(script, TEST_SIMPLE)

    print(f"  {DIM}Running: livepatch run simple_reg_test.py...{RESET}")
    output, _ = run_livepatch("simple_reg_test.py", workdir)

    check("Simple single-level null guard still applies", output,
          must_contain=["patch applied"])

    check("No secondary crash on simple test", output,
          must_not_contain=["cannot access local variable",
                            "traceback (most recent call last)"])


def test_pytest_regression(project_path):
    header("6. pytest — existing test suite, zero regressions")
    if not project_path:
        print(f"  {SKIP}  --path not provided, skipping pytest run")
        results.append(("pytest regression suite", None, ["skipped — no --path"]))
        return

    tests_dir = os.path.join(project_path, "tests")
    if not os.path.isdir(tests_dir):
        print(f"  {SKIP}  No 'tests/' directory found at {project_path}")
        results.append(("pytest regression suite", None, ["no tests/ dir"]))
        return

    result = subprocess.run(
        ["pytest", "tests/", "-v", "--tb=short"],
        cwd=project_path, capture_output=True, text=True, timeout=60
    )
    output = result.stdout + result.stderr
    passed = result.returncode == 0

    label = "pytest: all tests pass"
    results.append((label, passed, [] if passed else ["see pytest output"]))
    status = PASS if passed else FAIL
    print(f"  {status}  {label}")

    if not passed:
        for line in output.splitlines():
            if "FAILED" in line or "ERROR" in line or "error" in line.lower():
                print(f"         {RED}{line}{RESET}")

# ─────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────

def summary():
    header("VERIFICATION SUMMARY")
    total  = len(results)
    passed = sum(1 for _, ok, _ in results if ok is True)
    failed = sum(1 for _, ok, _ in results if ok is False)
    skipped= sum(1 for _, ok, _ in results if ok is None)

    for label, ok, reasons in results:
        if ok is True:
            print(f"  {GREEN}✓{RESET}  {label}")
        elif ok is False:
            print(f"  {RED}✗{RESET}  {label}")
            for r in reasons:
                print(f"      {RED}↳ {r}{RESET}")
        else:
            print(f"  {YELLOW}–{RESET}  {label}  {DIM}(skipped){RESET}")

    print()
    if failed == 0:
        print(f"{BOLD}{GREEN}ALL {passed}/{total} CHECKS PASSED.{RESET} LivePatch bugs are verified fixed.")
    else:
        print(f"{BOLD}{RED}{failed} CHECK(S) FAILED{RESET} ({passed} passed, {skipped} skipped)")
        print(f"Run with {CYAN}--verbose{RESET} for full output details.")

# ─────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="LivePatch bug-fix verification suite"
    )
    parser.add_argument(
        "--path", "-p",
        help="Absolute path to your LivePatch project root (for pytest regression)",
        default=None
    )
    parser.add_argument(
        "--workdir", "-w",
        help="Directory to write temp test files (default: current dir)",
        default="."
    )
    args = parser.parse_args()

    workdir = os.path.abspath(args.workdir)
    os.makedirs(workdir, exist_ok=True)

    print(f"\n{BOLD}LivePatch Verification Suite{RESET}")
    print(f"{DIM}workdir : {workdir}{RESET}")
    print(f"{DIM}project : {args.path or 'not provided (pytest will be skipped)'}{RESET}")

    if not test_install_check():
        print(f"\n{RED}Cannot continue — livepatch not found on PATH.{RESET}")
        print("Make sure you've run: pip install -e . (or pip install livepatch)")
        sys.exit(1)

    test_bug1_chain_depth(workdir)
    test_bug2_validator(workdir)
    test_bug3_dedup(workdir)
    test_persistence(workdir)
    test_simple_regression(workdir)
    test_pytest_regression(args.path)

    summary()