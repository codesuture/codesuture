"""ASGI middleware for CodeSuture.

Supports FastAPI, Starlette, Django Channels, and any ASGI 3.0 app.
Catches exceptions in HTTP request handlers, applies bytecode guards,
and replays the request transparently.
"""

import sys
import json
import traceback
import threading
from datetime import datetime, timezone


class CodeSutureASGIMiddleware:
    """ASGI middleware that catches crashes and patches handlers.

    Usage with FastAPI:
        app = FastAPI()
        app.add_middleware(CodeSutureASGIMiddleware)

    Usage with Starlette:
        app = Starlette(middleware=[Middleware(CodeSutureASGIMiddleware)])

    Usage standalone:
        app = CodeSutureASGIMiddleware(app)
    """

    def __init__(self, app, max_retries: int = 2, silent: bool = False):
        self.app = app
        self.max_retries = max_retries
        self.silent = silent
        self._patched_ids = set()
        self._lock = threading.Lock()
        # Singleton logger — avoid makedirs on every request
        self._incident_logger = None
        try:
            from codesuture.incidents.incident_log import IncidentLogger
            self._incident_logger = IncidentLogger()
        except Exception:
            pass

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return

        response_started = False
        original_send = send

        async def tracked_send(message):
            nonlocal response_started
            if message['type'] == 'http.response.start':
                response_started = True
            await original_send(message)

        try:
            await self.app(scope, receive, tracked_send)
        except Exception as exc:
            await self._handle_exception(exc, scope, receive, original_send, response_started)

    async def _handle_exception(self, exc, scope, receive, send, already_sent: bool):
        """Attempt to patch the failing function and replay."""
        if already_sent:
            # Headers already sent, can't replay
            raise exc

        exc_type = type(exc)
        tb = sys.exc_info()[2]
        if tb is None:
            raise exc

        # Walk to the innermost application frame
        inner_tb = tb
        while inner_tb.tb_next:
            inner_tb = inner_tb.tb_next
        frame = inner_tb.tb_frame

        # Dedup: don't retry same crash twice (per-line, matching tracer granularity)
        exc_id = (exc_type.__name__, frame.f_code.co_filename, frame.f_code.co_name, frame.f_lineno)
        with self._lock:
            if exc_id in self._patched_ids:
                await self._send_error_response(send, exc, scope)
                return

        try:
            from codesuture.pattern_matcher import analyze_exception
            from codesuture.guard_synthesizer import synthesize_guarded_code
            from codesuture.code_replacer import replace_function_code, get_function_from_frame

            spec = analyze_exception(frame, exc_type, exc, tb)
            if spec is None:
                await self._send_error_response(send, exc, scope)
                return

            func = get_function_from_frame(frame)
            if func is None:
                await self._send_error_response(send, exc, scope)
                return

            # Apply patch
            new_bc = synthesize_guarded_code(frame.f_code, spec)
            new_code = new_bc.to_code()
            replace_function_code(func, new_code)

            guard_type = spec.strategy
            target = spec.var_name
            if spec.key_name:
                target = spec.key_name if isinstance(spec.key_name, str) else spec.key_name[-1]

            with self._lock:
                self._patched_ids.add(exc_id)

            if not self.silent:
                print(f"[CodeSuture ASGI] Patched {frame.f_code.co_name}() "
                      f"with {guard_type} on '{target}'")

            # Log incident
            try:
                from codesuture.incidents.incident import IncidentRecord, IncidentStatus
                from codesuture.incidents.severity import classify_severity

                method = scope.get('method', '')
                severity = classify_severity(
                    guard_type=guard_type,
                    module=getattr(func, '__module__', ''),
                    function=frame.f_code.co_name,
                    http_method=method,
                )
                incident = IncidentRecord(
                    exception_type=exc_type.__name__,
                    exception_message=str(exc),
                    module=getattr(func, '__module__', ''),
                    function=frame.f_code.co_name,
                    line_number=frame.f_lineno,
                    file_path=frame.f_code.co_filename,
                    severity=severity,
                    status=IncidentStatus.REPLAYED,
                    guard_type=guard_type,
                    target_variable=target,
                    default_value=spec.default_value,
                )
                if self._incident_logger is not None:
                    self._incident_logger.log_incident(incident)
            except Exception:
                pass

            # Replay request
            try:
                response_started = False
                codesuture_header = f"patched=1;guard={guard_type};target={target}"

                async def patched_send(message):
                    nonlocal response_started
                    if message['type'] == 'http.response.start':
                        response_started = True
                        headers = list(message.get('headers', []))
                        headers.append([b'x-codesuture', codesuture_header.encode()])
                        message = {**message, 'headers': headers}
                    await send(message)

                await self.app(scope, receive, patched_send)

                if not self.silent:
                    print(f"[CodeSuture ASGI] Replay successful for {frame.f_code.co_name}()")

            except Exception:
                await self._send_error_response(send, exc, scope)

        except Exception:
            await self._send_error_response(send, exc, scope)

    async def _send_error_response(self, send, exc, scope):
        """Send a JSON error response when patching fails."""
        path = scope.get('path', '/')
        body = json.dumps({
            'error': 'CodeSuture: patch failed, original exception propagated',
            'exception': f"{type(exc).__name__}: {exc}",
            'path': path,
        }).encode()

        await send({
            'type': 'http.response.start',
            'status': 500,
            'headers': [
                [b'content-type', b'application/json'],
                [b'x-codesuture', b'patched=0;status=failed'],
            ],
        })
        await send({
            'type': 'http.response.body',
            'body': body,
        })
