class User:
    def __init__(self, name):
        self.name = name

def get_name(user):
    return user.name.strip()

# Call it 10 times to trigger CPython 3.11+ specialization
for i in range(10):
    try:
        get_name(User(f"user{i}"))
    except Exception:
        pass

# This crashes on specialized bytecode if de-specialization doesn't work
get_name(None)
print("HEALED")
