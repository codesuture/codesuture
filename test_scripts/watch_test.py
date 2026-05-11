class Profile:
    def __init__(self, bio): self.bio = bio
class User:
    def __init__(self, name, profile):
        self.name = name
        self.profile = profile
def get_bio(user):
    return user.profile.bio.strip()
user = User("Bob", None)
print(get_bio(user))
print("Done.")
