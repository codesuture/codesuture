# Real-world pattern: instance method on a class with None attribute
class Database:
    def __init__(self, records):
        self.records = records  # dict or None

    def get_user(self, uid):
        return self.records.get(uid)  # records may be None

class UserService:
    def __init__(self, db):
        self.db = db

    def get_name(self, uid):
        user = self.db.get_user(uid)
        return user['name'].strip()  # user may be None

db = Database({1: {'name': 'Alice'}})
service = UserService(db)
print(service.get_name(99))  # triggers crash: user is None
