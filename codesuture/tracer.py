import sys
import types
import os
import json
from datetime import datetime
from codesuture.pattern_matcher import analyze_exception
from codesuture.guard_synthesizer import synthesize_guarded_code, _force_despecialize
from codesuture.code_replacer import replace_function_code, get_function_from_frame
from codesuture.rewind import rewind_frame_to_start

_PYTHON_PREFIX = os.path.normcase(os.path.abspath(sys.prefix)) + os.sep
_PYTHON_BASE_PREFIX = os.path.normcase(os.path.abspath(sys.base_prefix)) + os.sep
_CODESUTURE_ROOT = os.path.normcase(os.path.abspath(os.path.dirname(__file__))) + os.sep

_ORIGINAL_CODES = {}  # {func_key: original_code_object}

def _is_internal_frame(frame):

    co_filename = frame.f_code.co_filename

    if co_filename.startswith('<'):
        return True

    try:
        norm = os.path.normcase(os.path.abspath(co_filename))
        if norm.startswith(_PYTHON_PREFIX) or norm.startswith(_PYTHON_BASE_PREFIX):
            return True
        if norm.startswith(_CODESUTURE_ROOT):
            return True
    except (ValueError, OSError):
        pass
    return False

class _CodeSutureFallbackSuppression(Exception):
    """Internal control signal to quietly abort frame evaluation without console traceback."""
    pass


class CodeSutureTracer:
    def __init__(self, dry_run=False, log_file=None, max_retries=3, autonomous=False, script_path=None, verbose=False, shadow=False, ttl=7, silent=False):
        import threading
        self.dry_run = dry_run
        self.log_file = log_file
        self.max_retries = max_retries
        self.autonomous = autonomous
        self.script_path = script_path
        self.verbose = verbose
        self.shadow_mode = shadow
        self.ttl = ttl
        self.silent = silent
        self._patched_codes = {}  
        self.attempts = {}  
        self.stats = {
            "patched": 0,
            "dry_run_suggestions": 0,
            "self_healed": 0
        }
        self.patched_signatures = {}  
        self._handled_exc_ids = set()
        self._rewound_exc_ids = set()
        self._patch_lock = threading.Lock()
        self._thread_state = threading.local()

    def __call__(self, frame, event, arg):
        if event == 'return' and self.shadow_mode and frame.f_code in self._patched_codes:
            from codesuture.shadow import shadow_check
            func_name = frame.f_code.co_name
            guard_type = self._patched_codes[frame.f_code]
            shadow_check(func_name, arg, guard_type)
            return self

        if event == 'exception':
            exc_type, exc_value, exc_tb = arg
            try:
                self._handle_exception(frame, exc_type, exc_value, exc_tb)
            except _CodeSutureFallbackSuppression:
                import ctypes
                ctypes.pythonapi.PyErr_Clear()
                return None
            return self
        return self

    def _extract_crash_key(self, exc_type, exc_value):

        import re
        if exc_type.__name__ == 'KeyError':
            return str(exc_value).strip("'\"")
        elif exc_type.__name__ == 'AttributeError':
            m = re.search(r"has no attribute '(\w+)'", str(exc_value))
            if m:
                return m.group(1)
        elif exc_type.__name__ == 'TypeError':
            m = re.search(r"'NoneType' object is not subscriptable", str(exc_value))
            if m:
                return '__subscript__'
        return None

    def _handle_exception(self, frame, exc_type, exc_value, exc_tb, thread=None):

        if _is_internal_frame(frame):
            return

        name = getattr(frame.f_code, 'co_qualname', '') or frame.f_code.co_name
        if '<listcomp>' in name or '<genexpr>' in name or \
           '<dictcomp>' in name or '<setcomp>' in name:
            import logging
            logging.getLogger(__name__).debug(
                "[CodeSuture] Skipping %s — "
                "comprehensions are not patchable via __code__", name
            )
            return

        from codesuture.persistence import HEALED_FUNCTIONS, _heal_key
        from codesuture.code_replacer import get_function_from_frame
        try:
            func = get_function_from_frame(frame)
            if func is not None:
                func_name = getattr(func, '__qualname__', func.__name__)
                module_name = getattr(func, '__module__', '__main__')
                crash_key = self._extract_crash_key(exc_type, exc_value)
                if _heal_key(module_name, func_name, crash_key) in HEALED_FUNCTIONS:
                    return
        except Exception:
            pass

        exc_id = id(exc_value)
        if exc_id in self._handled_exc_ids:
            return

        spec = None
        from codesuture.fingerprint import compute_fingerprint, lookup, record
        fp = compute_fingerprint(frame.f_code, frame.f_lasti, exc_type.__name__)
        cached = lookup(fp)
        if cached:
            if not self.silent:
                print(f"[CodeSuture] Known crash pattern #{fp[:8]} -- "
                      f"applying cached {cached['guard_type']} guard directly.")
            from codesuture.pattern_matcher import PatchSpec

            spec = PatchSpec(
                strategy=cached['guard_type'],
                var_name=cached['target'],
                default_value=cached.get('default_value', None),
                key_name=tuple(cached.get('key_name')) if isinstance(cached.get('key_name'), list) else cached.get('key_name', None)
            )

        if spec is None:
            try:
                spec = analyze_exception(frame, exc_type, exc_value, exc_tb)
            except Exception as internal_exc:

                spec = self._self_heal(internal_exc)
                if spec is None:
                    return

                try:
                    spec = analyze_exception(frame, exc_type, exc_value, exc_tb)
                except Exception:
                    return

        if spec is None:

            from codesuture.pattern_matcher import check_learned_rules
            func = get_function_from_frame(frame)
            if func is not None:
                func_name = getattr(func, '__qualname__', func.__name__)
                spec = check_learned_rules(func_name, exc_type.__name__, str(exc_value))

        if spec is None and self.autonomous and func is not None:

            if not self.silent:
                print(f"[CodeSuture] Autonomous mode activated for unknown error: {exc_type.__name__}")
            import traceback
            from codesuture.code_replacer import get_source_from_frame
            from codesuture.plugins.autonomous import propose_fix
            from codesuture.sandbox import test_fix
            from codesuture.knowledge import save_learned_rule
            from codesuture.pattern_matcher import PatchSpec

            tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
            function_source = get_source_from_frame(frame)

            new_source = propose_fix(tb_text, function_source, exc_type.__name__, str(exc_value))

            module_name = getattr(func, '__module__', '__main__')

            if test_fix(self.script_path, module_name, func_name, new_source, exc_type.__name__):
                if not self.silent:
                    print(f"[CodeSuture] LLM fix PASSED sandbox. Learning rule for {func_name}.")
                save_learned_rule(exc_type.__name__, str(exc_value), func_name, new_source)
                spec = PatchSpec(
                    strategy='autonomous_rule',
                    var_name=func_name,
                    default_value=new_source
                )
            else:
                if not self.silent:
                    print("[CodeSuture] LLM fix FAILED sandbox. Skipping autonomous patch.")

        if spec is None:
            return

        key = (id(frame.f_code), frame.f_lasti)
        tries = self.attempts.get(key, 0)
        if tries >= self.max_retries:
            print(f"[CodeSuture] Max retries ({self.max_retries}) reached at "
                  f"{frame.f_code.co_name}:{frame.f_lineno}, giving up.")
            return

        self.attempts[key] = tries + 1

        _thread_name = thread.name if thread is not None else None

        entry = {
            "timestamp": datetime.now().isoformat(),
            "function": frame.f_code.co_name,
            "filename": frame.f_code.co_filename,
            "lineno": frame.f_lineno,
            "exception": f"{exc_type.__name__}: {exc_value}",
            "strategy": spec.strategy,
            "var_name": spec.var_name,
            "default": repr(spec.default_value),
        }
        if _thread_name is not None:
            entry["thread"] = _thread_name

        display_name = spec.var_name
        if spec.key_name:
            display_name = spec.key_name[-1] if isinstance(spec.key_name, tuple) else spec.key_name
        elif spec.strategy == 'null_guard' and exc_type.__name__ == 'AttributeError':
            import re
            m = re.search(r"has no attribute '(\w+)'", str(exc_value))
            if m:
                display_name = m.group(1)

        if self.dry_run:
            entry["action"] = "dry_run"
            from codesuture.fingerprint import lookup as fp_lookup
            fp_hit = fp_lookup(fp) if fp else None
            if fp_hit:
                try:
                    import os as _os
                    fp_file = ".codesuture_fingerprints"
                    if _os.path.isfile(fp_file):
                        with open(fp_file, "r", encoding="utf-8") as fpf:
                            fp_data = json.load(fpf)
                        count = fp_data.get(fp, {}).get("count", 1) if isinstance(fp_data.get(fp), dict) else 1
                    else:
                        count = 0
                except Exception:
                    count = 0
            else:
                count = 0
            if count >= 3:
                confidence = "HIGH"
            elif count >= 1:
                confidence = "MEDIUM"
            else:
                confidence = "LOW"
            confidence_detail = (f"pattern seen {count}x in fingerprint registry" if count > 0
                                 else "new pattern, not in fingerprint registry")
            if not self.silent:
                print(f"[CodeSuture DRY-RUN] Would apply {spec.strategy} on '{display_name}' in {frame.f_code.co_name}()")
                print(f"[CodeSuture DRY-RUN] Confidence: {confidence} ({confidence_detail})")
                print(f"  Default value: {repr(spec.default_value)}")
                print(f"  Guard type: {spec.strategy}")
            self._log(entry)
            self.stats["dry_run_suggestions"] += 1
            return
        else:
            if not self.silent:
                print(f"[CodeSuture] Caught {exc_type.__name__}: {exc_value}")

            sig = (spec.var_name, spec.key_name, spec.strategy, exc_type.__name__)
            is_reuse = sig in self.patched_signatures

            if is_reuse:
                if not self.silent:
                    print(f"[CodeSuture] Reusing existing patch for '{display_name}' in {frame.f_code.co_name}()")
                spec = self.patched_signatures[sig]

            if not cached:
                if not self.silent:
                    print(f"[CodeSuture] Applying {spec.strategy} on '{display_name}' ...")

            try:
                if getattr(spec, 'target_func', None):
                    func = spec.target_func
                    old_code = getattr(func, '__code__', frame.f_code)
                else:
                    func = get_function_from_frame(frame)
                    old_code = frame.f_code

                if func is not None:
                    func_key = f"{frame.f_code.co_filename}:{frame.f_code.co_name}"
                    if func_key not in _ORIGINAL_CODES:
                        _ORIGINAL_CODES[func_key] = func.__code__

                new_bc = synthesize_guarded_code(old_code, spec)
                new_code = new_bc.to_code()
                self._persist_patch(frame, old_code, new_code, func)

                replace_function_code(func, new_code)

                if getattr(spec, 'target_func', None):
                    assert spec.target_func.__code__ is new_code, "Property fget code replacement failed"

                with self._patch_lock:
                    from codesuture.persistence import save_patch
                    save_patch(func, new_code, spec, self.ttl)

                    if self.shadow_mode:
                        self._patched_codes[new_code] = spec.strategy

                if self.verbose:
                    from codesuture.diff_guard import semantic_diff
                    diff = semantic_diff(old_code, new_code, spec.strategy)
                    print(f"[CodeSuture DEBUG] Diff: +{diff.added} -{diff.removed} instructions (allowed <= {diff.allowed})")

                with self._patch_lock:
                    if not is_reuse:
                        self.patched_signatures[sig] = spec

                    if not cached:
                        record(fp, spec.strategy, spec.var_name, getattr(func, '__name__', 'unknown'), exc_type.__name__, spec.default_value, spec.key_name)

                entry["action"] = "applied"
                self._log(entry)
                self.stats["patched"] += 1
                self._handled_exc_ids.add(exc_id)
                if not self.silent:
                    print(f"[CodeSuture] Patch applied to {getattr(func, '__name__', 'unknown')}().")
                if self._arm_http_transaction_replay(frame, func, exc_id, spec, display_name):
                    return
                if func is not None and not self._is_uninvokable(func, frame):
                    with self._patch_lock:
                        try:
                            if self.dry_run:
                                return
                            patched_code = new_code
                            if getattr(func, "__code__", None) is not patched_code:
                                func.__code__ = patched_code
                            self._patched_codes[patched_code] = spec.strategy
                            _force_despecialize(func)
                            if not rewind_frame_to_start(frame, patched_code):
                                self._try_transaction_fallback(frame, func, exc_id)
                                return
                            import ctypes
                            ctypes.pythonapi.PyErr_Clear()
                            self._handled_exc_ids.add(exc_id)
                            self._rewound_exc_ids.add(exc_id)
                            if not self.silent:
                                print(f"[CodeSuture] Active Shield: Native frame rewound for {getattr(func, '__name__', 'unknown')}() successfully.")
                            return None
                        except Exception:
                            self._try_transaction_fallback(frame, func, exc_id)
                    return

            except Exception as e:
                from codesuture.guard_synthesizer import PatchValidationError, PatchRejectedError
                if isinstance(e, PatchValidationError):
                    print(f"[CodeSuture] {e}")
                    entry["action"] = "rejected"
                elif isinstance(e, PatchRejectedError):
                    entry["action"] = "rejected"
                elif isinstance(e, RuntimeError) and old_code.co_flags & 0x100:
                    print(f"[CodeSuture] WARNING: async patch for {old_code.co_name}() "
                          f"raised RuntimeError: {e} -- aborting patch, not persisting.")
                    entry["action"] = "aborted"
                else:
                    import traceback as _tb
                    _tb.print_exc()
                    print(f"[CodeSuture] Patch failed: {e}")
                    entry["action"] = "failed"

                entry["error"] = str(e)
                self._log(entry)
                return

    def _self_heal(self, internal_exc):

        import traceback as tb_mod
        internal_tb = sys.exc_info()[2]
        if internal_tb is None:
            return None
        curr = internal_tb
        while curr.tb_next:
            curr = curr.tb_next
        internal_frame = curr.tb_frame

        if not self.silent:
            print(f"[CodeSuture] ENGINE SELF-HEAL: caught internal {type(internal_exc).__name__}: {internal_exc}")
            print(f"[CodeSuture]   in {internal_frame.f_code.co_name}() at {internal_frame.f_code.co_filename}:{internal_frame.f_lineno}")

        try:
            spec = analyze_exception(
                internal_frame, type(internal_exc), internal_exc, internal_tb
            )
        except Exception:
            if not self.silent:
                print("[CodeSuture]   self-heal analysis failed")
            return None

        if spec is None:
            if not self.silent:
                print("[CodeSuture]   no deterministic patch found for internal error")
            return None

        if not self.silent:
            print(f"[CodeSuture]   Applying {spec.strategy} on '{spec.var_name}' …")
        try:
            func = get_function_from_frame(internal_frame)
            new_bc = synthesize_guarded_code(internal_frame.f_code, spec)
            new_code = new_bc.to_code()
            replace_function_code(func, new_code)
            _force_despecialize(func)
            self.stats["patched"] += 1
            if not self.silent:
                print(f"[CodeSuture]   Self-healed {func.__name__}().")

            from codesuture.persistence import save_patch
            save_patch(func, new_code)

            return spec
        except Exception as e:
            print(f"[CodeSuture]   self-heal patch failed: {e}")
            return None

    def _persist_patch(self, frame, old_code, new_code, func=None):
        import gc
        import ctypes
        replaced = False
        propagated_count = 0

        refs = gc.get_referrers(old_code)
        for ref in refs:
            if hasattr(ref, "__code__") and getattr(ref, "__code__", None) is old_code:
                try:
                    ref.__code__ = new_code
                    _force_despecialize(ref)
                    replaced = True
                    propagated_count += 1
                except Exception:
                    pass
            elif hasattr(ref, "__func__"):
                fn = getattr(ref, "__func__", None)
                if hasattr(fn, "__code__") and getattr(fn, "__code__", None) is old_code:
                    try:
                        fn.__code__ = new_code
                        _force_despecialize(fn)
                        replaced = True
                        propagated_count += 1
                    except Exception:
                        pass
            elif isinstance(ref, tuple):
                # ref is likely a co_consts tuple in a parent code object
                # Find functions that own code objects containing this tuple
                for code_ref in gc.get_referrers(ref):
                    if isinstance(code_ref, types.CodeType) and ref == code_ref.co_consts:
                        new_consts = list(ref)
                        for i, item in enumerate(new_consts):
                            if item is old_code:
                                new_consts[i] = new_code
                        new_parent = code_ref.replace(co_consts=tuple(new_consts))
                        for func_ref in gc.get_referrers(code_ref):
                            if isinstance(func_ref, types.FunctionType) and func_ref.__code__ is code_ref:
                                func_ref.__code__ = new_parent
                                try:
                                    _force_despecialize(func_ref)
                                except Exception:
                                    pass
                        replaced = True

        if propagated_count > 0:
            if not self.silent:
                print(f"[CodeSuture] Propagated patch to {propagated_count} additional live reference(s) of {frame.f_code.co_name}.")
        elif replaced:
            if not self.silent:
                print(f"[CodeSuture] In-memory propagated patch applied to {frame.f_code.co_name}.")
        else:
            if func is None:
                func_name = frame.f_code.co_name
                func = frame.f_globals.get(func_name)

            if func and hasattr(func, "__code__") and getattr(func, "__code__", None) is old_code:
                func.__code__ = new_code
                _force_despecialize(func)
                if not self.silent:
                    print(f"[CodeSuture] In-memory propagated patch applied to {func.__name__}().")
            else:
                print("[CodeSuture] Could not find code object in memory to persist.")

    def _log(self, entry):
        if self.log_file:
            with open(self.log_file, 'a', encoding='utf-8') as f:
                json.dump(entry, f, default=str)
                f.write('\n')

    def _is_http_handler_frame(self, frame):
        handler = frame.f_locals.get('self')
        if handler is None or not frame.f_code.co_name.startswith('do_'):
            return False
        return all(hasattr(handler, attr) for attr in ('rfile', 'wfile', 'send_response'))

    def _arm_http_transaction_replay(self, frame, func, exc_id, spec=None, target_name=None):
        if not self._is_http_handler_frame(frame):
            return False
        handler = frame.f_locals.get('self')
        if handler is not None:
            handler._codesuture_patch = spec
            handler._codesuture_patch_target = target_name or getattr(spec, 'target_name', None) or getattr(spec, 'var_name', 'unknown')
            handler.__class__._codesuture_patch = spec
            handler.__class__._codesuture_patch_target = handler._codesuture_patch_target
        self._thread_state.http_replay_ready = True
        self._thread_state.http_replay_exc_id = exc_id
        self._thread_state.http_replay_func_name = getattr(func, '__name__', 'unknown')
        self._thread_state.http_replay_patch_spec = spec
        self._thread_state.http_replay_patch_target = target_name
        self._rewound_exc_ids.add(exc_id)
        if not self.silent:
            print(f"[CodeSuture] Transaction replay armed for {getattr(func, '__name__', 'unknown')}().")
        return True

    def _should_replay_http_transaction(self, exc):
        return bool(getattr(self._thread_state, 'http_replay_ready', False))

    def _clear_http_transaction_replay(self):
        self._thread_state.http_replay_ready = False
        self._thread_state.http_replay_exc_id = None
        self._thread_state.http_replay_func_name = None
        self._thread_state.http_replay_patch_spec = None
        self._thread_state.http_replay_patch_target = None

    def _leaf_application_traceback(self, exc_tb):
        target = None
        curr = exc_tb
        while curr is not None:
            if not _is_internal_frame(curr.tb_frame):
                target = curr
            curr = curr.tb_next
        return target

    def _handle_http_transaction_exception(self, exc):
        tb = getattr(exc, '__traceback__', None)
        leaf_tb = self._leaf_application_traceback(tb)
        if leaf_tb is None:
            return False
        self._handle_exception(leaf_tb.tb_frame, type(exc), exc, leaf_tb)
        return self._should_replay_http_transaction(exc)

    def _is_uninvokable(self, func, frame):
        code = getattr(func, '__code__', None)
        if code is None:
            return True
        if code.co_flags & (0x20 | 0x100 | 0x200):
            return True
        if code.co_name == '__init__':
            return True
        return False

    def _try_transaction_fallback(self, frame, func, exc_id):
        try:
            handler = frame.f_locals.get('self')
            if handler is None:
                return
            sock = getattr(handler, 'connection', None) or getattr(handler, 'request', None)
            if sock is None:
                return
            import json as _json
            body = _json.dumps({'error': 'CodeSuture patched this endpoint. Retry for a healed response.', 'patched': True})
            raw = (
                'HTTP/1.0 500 Internal Server Error\r\n'
                'Content-Type: application/json\r\n'
                f'Content-Length: {len(body)}\r\n'
                'X-CodeSuture: fallback=1\r\n'
                'Connection: close\r\n'
                '\r\n'
                f'{body}'
            )
            sock.sendall(raw.encode())
            self._rewound_exc_ids.add(exc_id)
            if not self.silent:
                print(f"[CodeSuture] Transaction fallback: sent graceful 500 for {getattr(func, '__name__', 'unknown')}().")
            raise _CodeSutureFallbackSuppression()
        except _CodeSutureFallbackSuppression:
            raise
        except Exception:
            pass
        return self

    def report(self):
        if not self.silent:
            print("\n[CodeSuture] Session summary:")
            print(f"  Patches applied: {len(self.patched_signatures)}")
            if self.dry_run:
                print(f"  Dry-run suggestions: {self.stats['dry_run_suggestions']}")
                print(f"[CodeSuture DRY-RUN] No patches applied. Run without --dry-run to apply.")

_original_excepthook = None
_original_http_handle_one_request = None
_original_print = None


def _install_http_transaction_replay(tracer):
    global _original_http_handle_one_request, _original_print
    try:
        import builtins
        from http import HTTPStatus
        from http.server import BaseHTTPRequestHandler
    except Exception:
        return

    if _original_print is None:
        _original_print = builtins.print

        def _codesuture_print(*args, **kwargs):
            if args and isinstance(args[0], str) and \
               "CRITICAL: Server logic crashed!" in args[0] and \
               (getattr(tracer._thread_state, 'http_transaction_active', False) or
                getattr(tracer._thread_state, 'http_replay_ready', False)):
                return
            return _original_print(*args, **kwargs)

        builtins.print = _codesuture_print

    if _original_http_handle_one_request is not None:
        return

    _original_http_handle_one_request = BaseHTTPRequestHandler.handle_one_request

    def _infer_http_patch_from_exception(exc):
        import re
        msg = str(exc)
        if type(exc).__name__ == 'AttributeError' and "'NoneType' object has no attribute" in msg:
            m = re.search(r"has no attribute '([^']+)'", msg)
            return 'null_guard', (m.group(1) if m else 'unknown')
        if type(exc).__name__ == 'KeyError':
            return 'key_guard', msg.strip("'\"")
        if type(exc).__name__ == 'IndexError':
            return 'index_guard', 'index'
        if type(exc).__name__ in ('TypeError', 'ValueError'):
            return 'type_coercion_guard', 'value'
        return 'fallback_guard', 'unknown'

    def _codesuture_handle_one_request(self):
        try:
            self.raw_requestline = self.rfile.readline(65537)
            if len(self.raw_requestline) > 65536:
                self.requestline = ''
                self.request_version = ''
                self.command = ''
                self.send_error(HTTPStatus.REQUEST_URI_TOO_LONG)
                return
            if not self.raw_requestline:
                self.close_connection = True
                return
            if not self.parse_request():
                return
            method_name = 'do_' + self.command
            if not hasattr(self, method_name):
                self.send_error(
                    HTTPStatus.NOT_IMPLEMENTED,
                    "Unsupported method (%r)" % self.command,
                )
                return
            method = getattr(self, method_name)
            sent_response = False
            replayed = False
            original_send_response = self.send_response
            original_end_headers = self.end_headers
            codesuture_header_sent = False

            def _codesuture_send_response(*args, **kwargs):
                nonlocal sent_response
                sent_response = True
                return original_send_response(*args, **kwargs)

            def _codesuture_end_headers(*args, **kwargs):
                nonlocal codesuture_header_sent
                patch = getattr(self, '_codesuture_patch', None)
                if patch is not None and not codesuture_header_sent:
                    target = getattr(self, '_codesuture_patch_target', None) or \
                             getattr(patch, 'target_name', None) or \
                             getattr(patch, 'var_name', 'unknown')
                    self.send_header(
                        "X-CodeSuture",
                        f"patched=1; guard={patch.strategy}; target={target}"
                    )
                    codesuture_header_sent = True
                return original_end_headers(*args, **kwargs)

            self.send_response = _codesuture_send_response
            self.end_headers = _codesuture_end_headers
            try:
                def _send_codesuture_response(guard_type=None, target_name=None):
                    import json as _json
                    patch = getattr(self, '_codesuture_patch', None)
                    guard = guard_type or getattr(patch, 'strategy', 'unknown')
                    target = target_name or getattr(self, '_codesuture_patch_target', None) or \
                             getattr(patch, 'target_name', None) or \
                             getattr(patch, 'var_name', 'unknown')
                    fallback = {
                        'patched': True,
                        'path': getattr(self, 'path', ''),
                        'result': None,
                    }
                    body = _json.dumps(fallback).encode()
                    original_send_response(200)
                    self.send_header("Content-type", "application/json")
                    self.send_header(
                        "X-CodeSuture",
                        f"patched=1; guard={guard}; target={target}"
                    )
                    original_end_headers()
                    self.wfile.write(body)

                try:
                    tracer._thread_state.http_transaction_active = True
                    method()
                except Exception as exc:
                    if (not tracer._should_replay_http_transaction(exc) and
                            not tracer._handle_http_transaction_exception(exc)):
                        guard_type, target_name = _infer_http_patch_from_exception(exc)
                        _send_codesuture_response(guard_type, target_name)
                        return
                    tracer._clear_http_transaction_replay()
                    replayed = True
                    if not tracer.silent:
                        print("[CodeSuture] Transaction replay: retrying patched HTTP handler in-place.")
                    method = getattr(self, method_name)
                    method()
                if not sent_response:
                    _send_codesuture_response()
            finally:
                tracer._thread_state.http_transaction_active = False
                self.send_response = original_send_response
                self.end_headers = original_end_headers
            self.wfile.flush()
        except TimeoutError as exc:
            self.log_error("Request timed out: %r", exc)
            self.close_connection = True

    BaseHTTPRequestHandler.handle_one_request = _codesuture_handle_one_request


def _uninstall_http_transaction_replay():
    global _original_http_handle_one_request, _original_print
    try:
        import builtins
        from http.server import BaseHTTPRequestHandler
        if _original_http_handle_one_request is not None:
            BaseHTTPRequestHandler.handle_one_request = _original_http_handle_one_request
            _original_http_handle_one_request = None
        if _original_print is not None:
            builtins.print = _original_print
            _original_print = None
    except Exception:
        pass

def _codesuture_excepthook(tracer, exc_type, exc_value, exc_tb):
    import threading
    if exc_tb:
        tracer._handle_exception(exc_tb.tb_frame, exc_type, exc_value, exc_tb, thread=threading.current_thread())

    exc_id = id(exc_value)
    if exc_id in tracer._handled_exc_ids:
        if exc_id in tracer._rewound_exc_ids:
            if tracer.silent:
                return
            # Print a structured summary instead of fully suppressing
            func_name = exc_tb.tb_frame.f_code.co_name if exc_tb else exc_type.__name__
            print(f"[CodeSuture] Self-healed: {exc_type.__name__} in {func_name}()")
            print(f"[CodeSuture]   Guard applied, execution rewound successfully")
            print(f"[CodeSuture]   Review: codesuture explain")
            return
        
    if _original_excepthook:
        _original_excepthook(exc_type, exc_value, exc_tb)
    else:
        sys.__excepthook__(exc_type, exc_value, exc_tb)

def _install_trace_on_all_threads(trace_fn):
    """Install trace hook on main thread and
       all currently running threads."""
    import threading
    sys.settrace(trace_fn)
    threading.settrace(trace_fn)
    # For threads already running:
    for thread in threading.enumerate():
        if thread is not threading.current_thread():
            try:
                import ctypes
                ctypes.pythonapi.PyThreadState_SetAsyncExc(
                    ctypes.c_ulong(thread.ident),
                    None  # does not raise, just wakes thread
                )
            except Exception:
                pass

def _install_monitoring_engine(tracer):
    mon = sys.monitoring
    TOOL_ID = mon.DEBUGGER_ID
    mon.use_tool_id(TOOL_ID, 'CodeSuture')

    def _on_raise(code, instruction_offset, exception):
        tb = sys.exc_info()[2]
        frame = tb.tb_frame if tb else None
        if frame is None:
            frame = sys._getframe(1)
            while frame is not None and frame.f_code is not code:
                frame = frame.f_back
        if frame is None:
            return
        tracer._handle_exception(frame, type(exception), exception, tb)

    mon.register_callback(TOOL_ID, mon.events.RAISE, _on_raise)
    mon.set_events(TOOL_ID, mon.events.RAISE)
    if not tracer.silent:
        print('[CodeSuture] Python 3.12+ sys.monitoring active. Zero baseline overhead.')

def install(dry_run=False, log_file=None, max_retries=3, autonomous=False, script_path=None, verbose=False, shadow=False, ttl=7, silent=False):
    global _original_excepthook
    import threading
    tracer = CodeSutureTracer(dry_run, log_file, max_retries, autonomous, script_path, verbose, shadow, ttl, silent=silent)

    if sys.version_info >= (3, 12) and hasattr(sys, 'monitoring'):
        _install_monitoring_engine(tracer)
    else:
        _install_trace_on_all_threads(tracer)
    _install_http_transaction_replay(tracer)

    if getattr(threading, 'excepthook', None) is not None:
        if threading.excepthook != getattr(threading, '__excepthook__', None):
             _original_excepthook = threading.excepthook
        threading.excepthook = lambda args: _codesuture_excepthook(tracer, args.exc_type, args.exc_value, args.exc_traceback)

    return tracer

def uninstall():
    global _original_excepthook
    _uninstall_http_transaction_replay()
    if sys.version_info >= (3, 12) and hasattr(sys, 'monitoring'):
        try:
            mon = sys.monitoring
            TOOL_ID = mon.DEBUGGER_ID
            mon.set_events(TOOL_ID, 0)
            mon.free_tool_id(TOOL_ID)
        except Exception:
            pass
    sys.settrace(None)
    import threading
    threading.settrace(None)
    if getattr(threading, 'excepthook', None) is not None:
        threading.excepthook = _original_excepthook or getattr(threading, '__excepthook__', sys.__excepthook__)
        _original_excepthook = None
