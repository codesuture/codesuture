class Profile:
    def __init__(self, bio): self.bio = bio
class User:
    def __init__(self, name, profile):
        self.name = name
        self.profile = profile
def fetch_user(uid):
    return User("Bob", None) if uid == 2 else User("Alice", Profile("eng"))
def get_bio(user):
    return user.profile.bio.strip()
print(get_bio(fetch_user(2)))
