class Profile:
    def __init__(self, bio): self.bio = bio
class User:
    def __init__(self, name, profile):
        self.name = name
        self.profile = profile
def fetch_user(uid):
    users = {
        1: User("Alice", Profile("Engineer")),
        2: User("Bob", None),
    }
    return users.get(uid)
def get_bio(user):
    return user.profile.bio.strip()
def format_user(user):
    return f"{user.name.upper()} - {get_bio(user)}"
def process_users():
    results = []
    for uid in [1, 2, 3]:
        user = fetch_user(uid)
        results.append(format_user(user))
    return results
def main():
    print("Starting hard test...")
    results = process_users()
    print("Results:", results)
if __name__ == "__main__":
    main()
