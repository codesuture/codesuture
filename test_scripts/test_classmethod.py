class Database:
    def __init__(self, records):
        self.records = records

    def get_user(self, uid):
        return self.records.get(uid)

class UserService:
    def __init__(self, db):
        self.db = db

    def get_name(self, uid):
        user = self.db.get_user(uid)
        return user['name'].strip()

db = Database({1: {'name': 'Alice'}})
service = UserService(db)
print(service.get_name(99))
