from http.server import BaseHTTPRequestHandler, HTTPServer
import json

# Simulated "External API" or "Database" state
STATE = {
    "api_response": None,         # API went down, returned None
    "config": {"timeout": 30},     # Missing "retry_limit"
    "active_users": ["admin"],     # Index out of bounds if we try users[5]
}

class KillerServer(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            # 1. THE NULL POINTER (AttributeError)
            # Happens when an external API returns None instead of an object.
            if self.path == "/user-data":
                user = STATE["api_response"]
                # CodeSuture should inject a 'null_guard' here.
                # Without it, 'NoneType' has no attribute 'get_profile'
                profile = user.get_profile() 
                response = {"data": profile}

            # 2. THE SCHEMA SHIFT (KeyError)
            # Happens when a JSON payload is missing a required key.
            elif self.path == "/config":
                # CodeSuture should inject a 'key_guard' for "retry_limit".
                # Standard Python will crash instantly.
                retries = STATE["config"]["retry_limit"]
                response = {"retries": retries}

            # 3. THE INVALID DATA (ValueError/TypeError)
            # Happens when a string comes in where a number was expected.
            elif self.path == "/process-payment":
                user_input = "not_a_number" 
                # CodeSuture should inject a 'type_coercion_guard'.
                amount = int(user_input)
                response = {"amount_charged": amount}

            # 4. THE BOUNDARY ERROR (IndexError)
            # Happens when accessing a specific list index that doesn't exist.
            elif self.path == "/latest-user":
                # CodeSuture should inject an 'index_guard'.
                last_user = STATE["active_users"][10]
                response = {"user": last_user}

            else:
                response = {"status": "alive", "msg": "Send requests to crash me!"}

            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())

        except Exception as e:
            print(f"🔥 CRITICAL: Server logic crashed! {type(e).__name__}: {e}")
            raise e # Let CodeSuture catch the unhandled death

if __name__ == "__main__":
    print("Starting Killer Server on port 9000...")
    print("Test endpoints: /user-data, /config, /process-payment, /latest-user")
    HTTPServer(("localhost", 9000), KillerServer).serve_forever()
