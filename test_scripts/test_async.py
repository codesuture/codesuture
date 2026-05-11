import asyncio

class Profile:
    def __init__(self, bio): self.bio = bio

class User:
    def __init__(self, name, profile):
        self.name = name
        self.profile = profile

async def get_bio(user):
    return user.profile.bio.strip()

async def main():
    user = User("Bob", None)
    result = await get_bio(user)
    print("Async result:", result)

asyncio.run(main())
