def process(data):
    try:
        result = data["key"].strip().upper()
        return result
    except ValueError as e:
        return f"value error: {e}"

# Test 1: NoneType subscript inside try block
r = process(None)
print(f"Result: '{r}'")
assert r is not None, "Should not be None"

# Test 2: ensure the except clause still works after patching
r2 = process({"key": "hello"})
print(f"Normal: '{r2}'")
assert r2 == "HELLO", f"Expected 'HELLO', got '{r2}'"

# Test 3: Verify no UnboundLocalError from corrupted exception table
def process2(data):
    try:
        val = data["nested"]["deep"]
        return val
    except KeyError as e:
        return f"missing: {e}"
    except TypeError as e:
        return f"type error: {e}"

r3 = process2(None)
print(f"Process2: '{r3}'")

r4 = process2({"nested": {"deep": "found"}})
print(f"Process2 normal: '{r4}'")
assert r4 == "found"

print("COMPLETED_OK")
