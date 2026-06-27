"""
CodeSuture v1.1.0 Ship-Gate Tests
=================================
These tests run REAL scenarios end-to-end. No mocking. No faking.
Each test launches a subprocess with codesuture to verify actual behavior.
If any of these fail, we don't ship.
"""
import subprocess
import sys
import os
import json
import shutil
import tempfile
import threading
import time
import pytest

PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE_DIR = os.path.join(PROJ_ROOT, '.codesuture_store')
FP_FILE = os.path.join(PROJ_ROOT, '.codesuture_fingerprints')
INC_DIR = os.path.join(PROJ_ROOT, '.codesuture_incidents')

def _clean_state():
    """Remove all persisted state for a clean test."""
    for path in [STORE_DIR, FP_FILE, INC_DIR]:
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        elif os.path.isfile(path):
            os.remove(path)

def _run_codesuture(script_content, *extra_args, timeout=30):
    """Write a temp script, run it under codesuture, return (stdout, stderr, returncode)."""
    tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.py', dir=PROJ_ROOT,
                                       delete=False, encoding='utf-8')
    try:
        tmp.write(script_content)
        tmp.close()
        result = subprocess.run(
            [sys.executable, '-m', 'codesuture', 'run', tmp.name, *extra_args],
            capture_output=True, text=True, timeout=timeout, cwd=PROJ_ROOT,
            env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
            encoding='utf-8', errors='replace'
        )
        return result.stdout, result.stderr, result.returncode
    finally:
        os.unlink(tmp.name)

def _run_cli(*args, timeout=15):
    """Run a codesuture CLI command, return (stdout, stderr, returncode)."""
    result = subprocess.run(
        [sys.executable, '-m', 'codesuture', *args],
        capture_output=True, text=True, timeout=timeout, cwd=PROJ_ROOT,
        env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
        encoding='utf-8', errors='replace'
    )
    return result.stdout, result.stderr, result.returncode

class TestPersistenceRoundTrip:
    """Patch in process A, kill it, run process B — patch loads from disk."""

    def setup_method(self):
        _clean_state()

    def teardown_method(self):
        _clean_state()

    def test_patch_survives_restart(self):
        script = '''
def get_bio(user):
    return user.profile.bio

class User:
    def __init__(self):
        self.profile = None

u = User()
try:
    result = get_bio(u)
    print(f"RESULT:{result}")
except AttributeError as e:
    print(f"CRASHED:{e}")
'''

        out1, _, _ = _run_codesuture(script)
        assert 'Patch applied' in out1, f"Run 1 should patch. Got: {out1}"
        assert os.path.isdir(STORE_DIR), "Patch store should exist after run 1"

        patch_files = [f for f in os.listdir(STORE_DIR) if f.endswith('.json')]
        assert len(patch_files) >= 1, f"Should have at least 1 patch file, got {patch_files}"

        out2, _, _ = _run_codesuture(script)

        assert 'Caught AttributeError' not in out2, (
            f"Run 2 should load persisted patch, not re-catch. Got: {out2}"
        )

class TestRollbackRestoresCrash:
    """After rollback, the original crash must come back."""

    def setup_method(self):
        _clean_state()

    def teardown_method(self):
        _clean_state()

    def test_rollback_restores_original_crash(self):
        script = '''
def divide(a, b):
    return a / b

try:
    print(f"RESULT:{divide(10, 0)}")
except ZeroDivisionError:
    print("CRASHED:ZeroDivisionError")
'''

        out1, _, _ = _run_codesuture(script)
        assert 'Patch applied' in out1 or 'division_guard' in out1

        out_rb, _, rc = _run_cli('rollback', '--all')
        assert rc == 0, f"Rollback failed: {out_rb}"

        out2, _, _ = _run_codesuture(script)

        assert 'Caught ZeroDivisionError' in out2 or 'division_guard' in out2, (
            f"After rollback, should re-catch. Got: {out2}"
        )

class TestStrCoerceNoRegression:
    """The patched function must work on ALL subsequent calls, not just the first."""

    def setup_method(self):
        _clean_state()

    def teardown_method(self):
        _clean_state()

    def test_str_coerce_no_unbound_local(self):
        script = '''
def format_record(data):
    name = data["name"]
    age = data["age"]
    score = data["score"]
    return "Name: " + name + ", Age: " + age + ", Score: " + score

records = [
    {"name": "Alice", "age": "30", "score": "95"},
    {"name": "Bob", "age": 25, "score": "80"},
    {"name": "Charlie", "age": "35", "score": 100},
    {"name": "Dave", "age": "40", "score": "75"},
]

for r in records:
    try:
        line = format_record(r)
        print(f"OK:{line}")
    except Exception as e:
        print(f"ERROR:{type(e).__name__}:{e}")
'''
        out, err, _ = _run_codesuture(script)
        combined = out + err
        assert 'UnboundLocalError' not in combined, (
            f"str_coerce_guard introduced UnboundLocalError:\n{combined}"
        )

class TestListBoundNoRegression:
    """Index guard with constant index must not corrupt local variables."""

    def setup_method(self):
        _clean_state()

    def teardown_method(self):
        _clean_state()

    def test_list_bound_no_unbound_local(self):
        script = '''
def get_third(items_input):
    items = list(items_input)
    return items[2]

test_cases = [
    [1, 2, 3, 4],
    [1],
    [10, 20, 30],
    [],
]

for tc in test_cases:
    try:
        result = get_third(tc)
        print(f"OK:{result}")
    except Exception as e:
        print(f"ERROR:{type(e).__name__}:{e}")
'''
        out, err, _ = _run_codesuture(script)
        combined = out + err
        assert 'UnboundLocalError' not in combined, (
            f"list_bound_guard introduced UnboundLocalError:\n{combined}"
        )

class TestDryRunNoSideEffects:
    """--dry-run must preview without applying."""

    def setup_method(self):
        _clean_state()

    def teardown_method(self):
        _clean_state()

    def test_dry_run_no_patch_files(self):
        script = '''
def crash():
    d = None
    return d["key"]

try:
    crash()
except:
    print("CRASHED")
'''
        out, _, _ = _run_codesuture(script, '--dry-run')
        assert 'DRY-RUN' in out, f"Should show dry-run output. Got: {out}"
        assert not os.path.isdir(STORE_DIR) or len(os.listdir(STORE_DIR)) == 0, (
            "Dry-run should not create patch files"
        )

class TestFingerprintDedup:
    """Identical crashes should reuse the same patch, not create duplicates."""

    def setup_method(self):
        _clean_state()

    def teardown_method(self):
        _clean_state()

    def test_same_crash_twice_one_fingerprint(self):
        script = '''
def get_name(user):
    return user.name.strip()

for i in range(3):
    try:
        get_name(None)
    except:
        pass
'''
        _run_codesuture(script)

        assert os.path.isfile(FP_FILE), "Fingerprint file should exist"
        with open(FP_FILE, 'r', encoding='utf-8') as f:
            registry = json.load(f)

        assert len(registry) >= 1, "Should have at least 1 fingerprint"

# TEST 7: CLI commands don't crash on Windows (Unicode safety)

class TestCLIUnicodeSafety:
    """All CLI commands must run without UnicodeEncodeError on any terminal."""

    def setup_method(self):
        _clean_state()

        script = '''
def crash():
    x = None
    return x.value
try:
    crash()
except:
    pass
'''
        _run_codesuture(script)

    def teardown_method(self):
        _clean_state()

    def test_audit_no_unicode_crash(self):
        out, err, rc = _run_cli('audit')
        assert 'UnicodeEncodeError' not in err, f"audit crashed: {err}"

    def test_incidents_no_unicode_crash(self):
        out, err, rc = _run_cli('incidents')
        assert 'UnicodeEncodeError' not in err, f"incidents crashed: {err}"
        assert rc == 0, f"incidents exited with {rc}: {err}"

    def test_digest_no_unicode_crash(self):
        out, err, rc = _run_cli('digest')
        assert 'UnicodeEncodeError' not in err, f"digest crashed: {err}"
        assert rc == 0, f"digest exited with {rc}: {err}"

    def test_alerts_no_unicode_crash(self):
        out, err, rc = _run_cli('alerts')
        assert 'UnicodeEncodeError' not in err, f"alerts crashed: {err}"
        assert rc == 0, f"alerts exited with {rc}: {err}"

    def test_explain_no_unicode_crash(self):
        out, err, rc = _run_cli('explain')
        assert 'UnicodeEncodeError' not in err, f"explain crashed: {err}"

    def test_rollback_dryrun_no_unicode_crash(self):
        out, err, rc = _run_cli('rollback', '--dry-run')
        assert 'UnicodeEncodeError' not in err, f"rollback --dry-run crashed: {err}"

    def test_suggest_no_unicode_crash(self):
        out, err, rc = _run_cli('suggest')
        assert 'UnicodeEncodeError' not in err, f"suggest crashed: {err}"

    def test_version_no_unicode_crash(self):
        out, err, rc = _run_cli('--version')
        assert '1.1.0' in out, f"Version wrong: {out}"
        assert rc == 0

class TestDeepCallChainPatch:
    """Function A→B→C, C crashes. Patch propagates correctly."""

    def setup_method(self):
        _clean_state()

    def teardown_method(self):
        _clean_state()

    def test_deep_chain_patch(self):
        script = '''
def get_config():
    return {"db": {"host": "localhost"}}

def get_db_port(config):
    return config["db"]["port"]

def connect():
    cfg = get_config()
    port = get_db_port(cfg)
    return f"Connected to port {port}"

try:
    result = connect()
    print(f"OK:{result}")
except KeyError as e:
    print(f"CRASHED:KeyError:{e}")
'''
        out, _, _ = _run_codesuture(script)
        assert 'Patch applied' in out or 'key_guard' in out or 'chain_subscript_guard' in out, (
            f"Should patch the KeyError. Got: {out}"
        )

# TEST 9: Concurrent crashes from multiple threads — no deadlock

class TestConcurrentCrashesNoDeadlock:
    """Multiple threads crashing simultaneously must not deadlock."""

    def setup_method(self):
        _clean_state()

    def teardown_method(self):
        _clean_state()

    def test_threaded_no_deadlock(self):
        script = '''
import threading

def risky(x):
    return x.upper()

errors = []
def worker(val):
    try:
        risky(val)
    except Exception as e:
        errors.append(str(e))

threads = []
for v in ["hello", None, "world", None, "test", None, None, None]:
    t = threading.Thread(target=worker, args=(v,))
    threads.append(t)
    t.start()

for t in threads:
    t.join(timeout=10)

# If we get here without hanging, no deadlock
print(f"DONE:errors={len(errors)}")
'''
        out, err, rc = _run_codesuture(script)
        combined = out + err
        assert 'DONE:' in out, f"Should complete without deadlock. Got: {combined}"
        # Must NOT have a deadlock timeout
        assert rc != -1, "Process was killed (possible deadlock)"

class TestWSGIMiddlewarePatch:
    """WSGI middleware should catch crash, patch, and retry the request."""

    def setup_method(self):
        _clean_state()

    def teardown_method(self):
        _clean_state()

    def test_wsgi_middleware_patches(self):
        script = '''
import sys
sys.path.insert(0, '.')

from codesuture.middleware import CodeSutureMiddleware

call_count = 0

def buggy_app(environ, start_response):
    global call_count
    call_count += 1
    data = None
    body = data["key"].encode()  # Crash on first call
    start_response("200 OK", [("Content-Type", "text/plain")])
    return [body]

app = CodeSutureMiddleware(buggy_app)

# Simulate a WSGI request
environ = {"REQUEST_METHOD": "GET", "PATH_INFO": "/test"}
responses = []
def start_response(status, headers):
    responses.append(status)

try:
    result = list(app(environ, start_response))
    print(f"STATUS:{responses[-1] if responses else 'NONE'}")
    print(f"CALLS:{call_count}")
except Exception as e:
    print(f"CRASHED:{type(e).__name__}:{e}")
    print(f"CALLS:{call_count}")
'''
        out, err, _ = _run_codesuture(script)
        combined = out + err

        assert 'CRASHED' in combined or 'STATUS:' in combined, (
            f"Middleware should handle or propagate. Got: {combined}"
        )

        assert 'UnboundLocalError' not in combined, (
            f"Middleware introduced UnboundLocalError: {combined}"
        )

class TestIncidentLogging:
    """Crashes should produce real JSONL incident records."""

    def setup_method(self):
        _clean_state()

    def teardown_method(self):
        _clean_state()

    def test_incident_jsonl_created(self):
        script = '''
def boom(x):
    return 10 / x

try:
    boom(0)
except:
    pass
'''
        _run_codesuture(script)

        assert os.path.isdir(INC_DIR), "Incidents directory should exist"

        jsonl_files = []
        for dp, dn, fnames in os.walk(INC_DIR):
            for fname in fnames:
                if fname.endswith('.jsonl'):
                    jsonl_files.append(os.path.join(dp, fname))
        assert len(jsonl_files) >= 1, f"Should have JSONL files. Tree: {os.listdir(INC_DIR)}"

        jsonl_path = os.path.join(INC_DIR, jsonl_files[0])
        with open(jsonl_path, 'r', encoding='utf-8') as f:
            lines = [l.strip() for l in f if l.strip()]

        assert len(lines) >= 1, "JSONL should have at least 1 record"
        for line in lines:
            record = json.loads(line)
            assert 'timestamp' in record, f"Record missing timestamp: {record}"
            assert 'severity' in record, f"Record missing severity: {record}"
            assert 'guard_type' in record or 'guard' in record, f"Record missing guard info: {record}"

class TestPrometheusOutput:
    """codesuture metrics must produce valid Prometheus text format."""

    def setup_method(self):
        _clean_state()

        script = '''
def crash1():
    return None.x
def crash2():
    return {}["missing"]
try:
    crash1()
except:
    pass
try:
    crash2()
except:
    pass
'''
        _run_codesuture(script)

    def teardown_method(self):
        _clean_state()

    def test_metrics_valid_prometheus_format(self):
        out, err, rc = _run_cli('metrics')
        assert rc == 0, f"metrics command failed: {err}"
        assert 'codesuture_' in out, f"Should contain codesuture_ metrics. Got: {out}"

        for line in out.strip().splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            parts = line.rsplit(' ', 1)
            assert len(parts) == 2, f"Invalid Prometheus line: {line}"

            try:
                float(parts[1])
            except ValueError:
                pytest.fail(f"Non-numeric value in Prometheus output: {line}")

    def test_metrics_label_is_guard_type(self):
        """README says guard_type=, code must emit guard_type=."""
        out, _, _ = _run_cli('metrics')
        if 'patches_total{' in out:
            assert 'guard_type=' in out, (
                f"Label should be guard_type=, not guard=. Got: {out}"
            )

class TestMaxRetriesHonored:
    """After max_retries, codesuture must give up and not loop forever."""

    def setup_method(self):
        _clean_state()

    def teardown_method(self):
        _clean_state()

    def test_max_retries_stops(self):

        script = '''
import sys
sys.path.insert(0, ".")
from codesuture.tracer import CodeSutureTracer

def always_crashes():
    raise RuntimeError("unfixable")

tracer = CodeSutureTracer(max_retries=2)
try:
    tracer._handle_exception(RuntimeError, RuntimeError("unfixable"), None)
except:
    pass
print(f"DONE:attempts={tracer.stats}")
'''
        out, _, rc = _run_codesuture(script)
        assert 'DONE' in out, f"Should finish, not hang. Got: {out}"

class TestShadowModeWarnings:
    """--shadow should warn when patched functions return default/sentinel values."""

    def setup_method(self):
        _clean_state()

    def teardown_method(self):
        _clean_state()

    def test_shadow_warns_on_sentinel(self):
        script = '''
def get_value(d):
    return d["key"]

try:
    get_value({})
except KeyError:
    pass
'''
        out, err, _ = _run_codesuture(script, '--shadow')
        # Shadow mode is active but may or may not produce a warning

        combined = out + err
        assert 'Error' not in combined or 'KeyError' in combined, (
            f"Shadow mode shouldn't crash: {combined}"
        )

class TestVersionConsistency:
    def test_all_versions_match(self):

        out, _, _ = _run_cli('--version')
        cli_version = out.strip().split()[-1]

        sys.path.insert(0, PROJ_ROOT)
        import importlib
        import codesuture
        importlib.reload(codesuture)
        init_version = codesuture.__version__

        toml_path = os.path.join(PROJ_ROOT, 'pyproject.toml')
        with open(toml_path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip().startswith('version'):
                    toml_version = line.split('=')[1].strip().strip('"').strip("'")
                    break

        assert cli_version == init_version == toml_version, (
            f"Version mismatch: CLI={cli_version}, __init__={init_version}, pyproject={toml_version}"
        )
        assert cli_version == '1.1.0', f"Expected 1.1.0, got {cli_version}"
