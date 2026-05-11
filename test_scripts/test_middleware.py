from codesuture.middleware import CodeSutureMiddleware

def buggy_app(environ, start_response):
    data = environ.get("codesuture.test_data")
    result = data.strip()  # crashes when data is None
    start_response("200 OK", [])
    return [result.encode()]

patched_app = CodeSutureMiddleware(buggy_app)
env = {"codesuture.test_data": None}
resp_holder = []

def fake_start(status, headers):
    resp_holder.append(status)
    resp_holder.extend([h for h in headers])

body = list(patched_app(env, fake_start))
assert resp_holder[0] == "200 OK", f"Expected 200 OK, got: {resp_holder[0]}"
print("Middleware test passed.")
