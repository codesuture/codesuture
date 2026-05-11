class User:
    def __init__(self, name): self.name = name
def get_user(uid):
    return None if uid != 1 else User("Alice")
def process(uid):
    user = get_user(uid)
    name = user.name.strip()
    print("Processed:", name)
process(2)
