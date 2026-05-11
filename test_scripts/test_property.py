class Profile:
    def __init__(self, meta):
        self.meta = meta

    @property
    def display_name(self):
        return self.meta['display'].strip()

p = Profile(None)
print(p.display_name)
