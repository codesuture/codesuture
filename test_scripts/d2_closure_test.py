class User:
    def __init__(self, n): self.name = n
def get_user(uid):
    return None if uid != 1 else User("Alice")
def make_processor(get_user_fn):
    def process(uid):
        user = get_user_fn(uid)
        return user.name.strip()
    return process
process = make_processor(get_user)
print(process(2))
