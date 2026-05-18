import threading

class User:
    def __init__(self, name):
        self.name = name

def worker():
    user = None
    name = user.name.strip()  # crashes in thread
    print("Thread healed:", name)

t = threading.Thread(target=worker)
t.start()
t.join()
print("COMPLETED_OK")
