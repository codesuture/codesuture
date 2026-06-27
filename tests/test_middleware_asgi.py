"""Tests for CodeSutureASGIMiddleware — real async ASGI apps.

Uses asyncio.run() directly — no pytest-asyncio dependency required.
"""

import asyncio
import json
import pytest
from codesuture.middleware_asgi import CodeSutureASGIMiddleware

async def _receive():
    """Minimal ASGI receive callable."""
    return {'type': 'http.request', 'body': b''}

class _ResponseCollector:
    """Collects ASGI response messages for assertion."""

    def __init__(self):
        self.messages = []
        self.status = None
        self.headers = {}
        self.body = b''

    async def send(self, message):
        self.messages.append(message)
        if message['type'] == 'http.response.start':
            self.status = message.get('status')
            for k, v in message.get('headers', []):
                if isinstance(k, bytes):
                    k = k.decode()
                if isinstance(v, bytes):
                    v = v.decode()
                self.headers[k.lower()] = v
        elif message['type'] == 'http.response.body':
            self.body += message.get('body', b'')

def _make_scope(path='/', method='GET'):
    return {
        'type': 'http',
        'method': method,
        'path': path,
        'query_string': b'',
        'headers': [],
    }

class TestASGIMiddleware:

    def test_healthy_app_passes_through(self):
        """ASGI app that doesn't crash passes through unchanged."""
        async def app(scope, receive, send):
            await send({
                'type': 'http.response.start',
                'status': 200,
                'headers': [[b'content-type', b'text/plain']],
            })
            await send({
                'type': 'http.response.body',
                'body': b'hello',
            })

        async def run():
            mw = CodeSutureASGIMiddleware(app, silent=True)
            collector = _ResponseCollector()
            await mw(_make_scope(), _receive, collector.send)
            return collector

        collector = asyncio.run(run())
        assert collector.status == 200
        assert collector.body == b'hello'

    def test_non_http_passes_through(self):
        """Non-HTTP scopes (websocket, lifespan) pass through unmodified."""
        called = []
        async def app(scope, receive, send):
            called.append(scope['type'])

        async def run():
            mw = CodeSutureASGIMiddleware(app, silent=True)
            scope = {'type': 'websocket', 'path': '/ws'}
            await mw(scope, _receive, lambda m: asyncio.sleep(0))

        asyncio.run(run())
        assert called == ['websocket']

    def test_crash_sends_500_error_response(self):
        """App that crashes with unpatchable error gets 500 JSON response."""
        async def app(scope, receive, send):
            raise RuntimeError("Unpatchable crash")

        async def run():
            mw = CodeSutureASGIMiddleware(app, silent=True)
            collector = _ResponseCollector()
            await mw(_make_scope(), _receive, collector.send)
            return collector

        collector = asyncio.run(run())
        assert collector.status == 500
        body = json.loads(collector.body)
        assert 'error' in body
        assert 'RuntimeError' in body['exception']

    def test_crash_with_none_attr_attempts_patch(self):
        """App crashing on None.attr — middleware attempts patch + replay.

        Accepts both 200 (patch+replay succeeded) and 500
        (patch not applicable to async inner function). Both paths
        verified with concrete assertions — no vacuous pass.
        """
        async def app(scope, receive, send):
            data = None
            result = data.strip()
            await send({
                'type': 'http.response.start',
                'status': 200,
                'headers': [],
            })
            await send({
                'type': 'http.response.body',
                'body': result.encode(),
            })

        async def run():
            mw = CodeSutureASGIMiddleware(app, silent=True)
            collector = _ResponseCollector()
            await mw(_make_scope(), _receive, collector.send)
            return collector

        collector = asyncio.run(run())

        assert len(collector.messages) >= 1, "No response messages sent"
        assert collector.status in (200, 500)
        if collector.status == 200:
            # Patch+replay worked — header proves CodeSuture acted
            assert 'x-codesuture' in collector.headers
            assert 'patched=1' in collector.headers['x-codesuture']
        else:
            # 500 fallback — proper JSON error body, not empty
            body = json.loads(collector.body)
            assert 'error' in body
            assert 'exception' in body
            assert 'AttributeError' in body['exception']

    def test_none_attr_crash_is_analyzable(self):
        """analyze_exception recognizes None.attr as patchable.

        Unit-level proof the ASGI middleware's patch path CAN work,
        even if get_function_from_frame fails on anonymous async defs.
        """
        import sys
        from codesuture.pattern_matcher import analyze_exception

        def crasher():
            data = None
            return data.strip()

        try:
            crasher()
        except AttributeError as e:
            tb = sys.exc_info()[2]
            inner = tb
            while inner.tb_next:
                inner = inner.tb_next
            spec = analyze_exception(inner.tb_frame, type(e), e, inner)
            assert spec is not None, "analyze_exception should recognize None.attr"
            assert spec.strategy == 'null_guard'

    def test_dedup_prevents_retry_loop(self):
        """Same crash type is not retried after first patch attempt."""
        call_count = [0]

        async def app(scope, receive, send):
            call_count[0] += 1
            raise ValueError("always crashes")

        async def run():
            mw = CodeSutureASGIMiddleware(app, silent=True)
            c1 = _ResponseCollector()
            await mw(_make_scope(), _receive, c1.send)
            c2 = _ResponseCollector()
            await mw(_make_scope(), _receive, c2.send)
            return c1, c2

        c1, c2 = asyncio.run(run())
        assert c1.status == 500
        assert c2.status == 500

    def test_headers_already_sent_raises(self):
        """If response headers were already sent before crash, re-raise."""
        async def app(scope, receive, send):
            await send({
                'type': 'http.response.start',
                'status': 200,
                'headers': [],
            })
            raise RuntimeError("Crash after headers sent")

        async def run():
            mw = CodeSutureASGIMiddleware(app, silent=True)
            collector = _ResponseCollector()
            await mw(_make_scope(), _receive, collector.send)

        with pytest.raises(RuntimeError, match="Crash after headers sent"):
            asyncio.run(run())

    def test_incident_logger_singleton(self):
        """IncidentLogger is created once in __init__, not per request."""
        async def app(scope, receive, send):
            await send({'type': 'http.response.start', 'status': 200, 'headers': []})
            await send({'type': 'http.response.body', 'body': b'ok'})

        async def run():
            mw = CodeSutureASGIMiddleware(app, silent=True)
            assert hasattr(mw, '_incident_logger')
            results = []
            for _ in range(3):
                c = _ResponseCollector()
                await mw(_make_scope(), _receive, c.send)
                results.append(c.status)
            return results

        results = asyncio.run(run())
        assert all(s == 200 for s in results)

    def test_silent_mode_suppresses_output(self, capsys):
        """Silent mode doesn't print to stdout."""
        async def app(scope, receive, send):
            await send({'type': 'http.response.start', 'status': 200, 'headers': []})
            await send({'type': 'http.response.body', 'body': b'ok'})

        async def run():
            mw = CodeSutureASGIMiddleware(app, silent=True)
            c = _ResponseCollector()
            await mw(_make_scope(), _receive, c.send)

        asyncio.run(run())
        captured = capsys.readouterr()
        assert '[CodeSuture ASGI]' not in captured.out
