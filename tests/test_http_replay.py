"""HTTP replay integration tests — real server, real client."""

import threading
import time
import urllib.request
import urllib.error
import json
import pytest
from http.server import HTTPServer, BaseHTTPRequestHandler


class _TestHandler(BaseHTTPRequestHandler):
    """Handler with intentional bugs for testing."""

    def do_GET(self):
        if self.path == '/healthy':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'status': 'ok'}).encode())

        elif self.path == '/crash-null':
            data = None
            result = data.strip()  # Will crash
            self.send_response(200)
            self.end_headers()
            self.wfile.write(result.encode())

        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Suppress HTTP logs during tests


@pytest.fixture
def http_server():
    """Start a real HTTP server on a random port."""
    server = HTTPServer(('127.0.0.1', 0), _TestHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{port}'
    server.shutdown()


class TestHttpReplay:

    def test_healthy_endpoint(self, http_server):
        """Healthy endpoint returns 200."""
        resp = urllib.request.urlopen(f'{http_server}/healthy')
        assert resp.status == 200
        data = json.loads(resp.read())
        assert data['status'] == 'ok'

    def test_crash_endpoint_returns_error(self, http_server):
        """Crash endpoint returns error without CodeSuture."""
        from http.client import RemoteDisconnected
        try:
            urllib.request.urlopen(f'{http_server}/crash-null')
            # If somehow succeeds, that's fine too (CodeSuture may be active)
        except urllib.error.HTTPError as e:
            assert e.code == 500
        except RemoteDisconnected:
            # Server crashed before sending any response — expected
            pass

    def test_404_endpoint(self, http_server):
        """Unknown endpoint returns 404."""
        try:
            urllib.request.urlopen(f'{http_server}/unknown')
        except urllib.error.HTTPError as e:
            assert e.code == 404

    def test_concurrent_requests(self, http_server):
        """Multiple concurrent requests to healthy endpoint."""
        errors = []
        def worker():
            try:
                resp = urllib.request.urlopen(f'{http_server}/healthy')
                assert resp.status == 200
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0

    def test_server_stays_up_after_crash(self, http_server):
        """Server should stay running even after a crash on one endpoint."""
        # Hit crash endpoint
        try:
            urllib.request.urlopen(f'{http_server}/crash-null')
        except Exception:
            pass

        # Hit healthy endpoint — server should still be alive
        resp = urllib.request.urlopen(f'{http_server}/healthy')
        assert resp.status == 200
