# Real-world pattern: crash inside a property getter
class Profile:
    def __init__(self, meta):
        self.meta = meta  # may be None

    @property
    def display_name(self):
        return self.meta['display'].strip()  # meta may be None

p = Profile(None)
print(p.display_name)
