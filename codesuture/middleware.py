
import sys
import threading
import traceback

class CodeSutureMiddleware:

    def __init__(self, app):
        self.app = app
        self._retried_exc_ids = set()
        self._lock = threading.Lock()

    def __call__(self, environ, start_response):

        try:
            return self.app(environ, start_response)
        except Exception as exc:
            return self._handle_exception(exc, environ, start_response)

    def _handle_exception(self, exc, environ, start_response):

        exc_type = type(exc)

        tb = sys.exc_info()[2]
        if tb is None:
            raise exc

        inner_tb = tb
        while inner_tb.tb_next:
            inner_tb = inner_tb.tb_next
        frame = inner_tb.tb_frame

        exc_id = (exc_type, frame.f_code.co_filename, frame.f_code.co_name)
        with self._lock:
            if exc_id in self._retried_exc_ids:
                raise exc

        try:
            from codesuture.pattern_matcher import analyze_exception
            from codesuture.guard_synthesizer import synthesize_guarded_code
            from codesuture.code_replacer import replace_function_code, get_function_from_frame

            spec = analyze_exception(frame, exc_type, exc, tb)
            if spec is None:
                raise exc

            func = get_function_from_frame(frame)
            if func is None:
                raise exc

            new_bc = synthesize_guarded_code(frame.f_code, spec)
            new_code = new_bc.to_code()
            replace_function_code(func, new_code)

            guard_type = spec.strategy
            target_name = spec.var_name
            if spec.key_name:
                target_name = spec.key_name if isinstance(spec.key_name, str) else spec.key_name[-1]

            with self._lock:
                self._retried_exc_ids.add(exc_id)

            try:
                patched_start_called = []

                def patched_start_response(status, headers, exc_info=None):

                    headers = list(headers) + [
                        ("X-CodeSuture", f"patched=1;guard={guard_type};target={target_name}")
                    ]
                    patched_start_called.append(True)
                    return start_response(status, headers, exc_info)

                result = self.app(environ, patched_start_response)

                if not patched_start_called:
                    start_response("200 OK", [
                        ("X-CodeSuture", f"patched=1;guard={guard_type};target={target_name}")
                    ])

                return result

            except Exception:

                raise exc

        except Exception as inner:
            if inner is exc:
                raise

            raise exc