import sys
from types import SimpleNamespace

import codesuture.code_replacer as code_replacer
import codesuture.fingerprint as fingerprint
import codesuture.persistence as persistence
import codesuture.tracer as tracer_mod
from codesuture.pattern_matcher import PatchSpec


def test_active_shield_reuses_two_argument_synthesized_code(monkeypatch):
    def target(value):
        return value.strip()

    def patched(value):
        return ""

    try:
        target(None)
    except AttributeError as exc:
        exc_type = type(exc)
        exc_value = exc
        exc_tb = sys.exc_info()[2]
        leaf_tb = exc_tb
        while leaf_tb.tb_next:
            leaf_tb = leaf_tb.tb_next
        frame = leaf_tb.tb_frame
    else:
        raise AssertionError("target should raise")

    spec = PatchSpec("null_guard", "value", "")
    calls = []

    def synthesize(original_code, patch_spec):
        calls.append((original_code, patch_spec))
        return SimpleNamespace(to_code=lambda: patched.__code__)

    tracer = tracer_mod.CodeSutureTracer()
    monkeypatch.setattr(tracer_mod, "analyze_exception", lambda *args: spec)
    monkeypatch.setattr(tracer_mod, "synthesize_guarded_code", synthesize)
    monkeypatch.setattr(
        tracer_mod,
        "replace_function_code",
        lambda func, code: setattr(func, "__code__", code),
    )
    monkeypatch.setattr(tracer_mod, "_force_despecialize", lambda func: None)
    monkeypatch.setattr(
        tracer_mod, "rewind_frame_to_start", lambda frame_arg, code_arg: True
    )
    monkeypatch.setattr(
        code_replacer, "get_function_from_frame", lambda frame_arg: target
    )
    monkeypatch.setattr(fingerprint, "compute_fingerprint", lambda *args: "fp")
    monkeypatch.setattr(fingerprint, "lookup", lambda fp: None)
    monkeypatch.setattr(fingerprint, "record", lambda *args: None)
    monkeypatch.setattr(persistence, "HEALED_FUNCTIONS", set())
    monkeypatch.setattr(persistence, "_heal_key", lambda *args: "missing")
    monkeypatch.setattr(persistence, "save_patch", lambda *args, **kwargs: None)
    monkeypatch.setattr(tracer, "_persist_patch", lambda *args: None)

    def fail_fallback(*args):
        raise AssertionError("Active Shield should not enter transaction fallback")

    monkeypatch.setattr(tracer, "_try_transaction_fallback", fail_fallback)

    tracer._handle_exception(frame, exc_type, exc_value, exc_tb)

    assert calls == [(frame.f_code, spec)]
    assert target.__code__ is patched.__code__
    assert id(exc_value) in tracer._rewound_exc_ids
