class Address:
    def __init__(self, city): self.city = city

class Contact:
    def __init__(self, address): self.address = address

class Person:
    def __init__(self, contact): self.contact = contact

def get_city(person):
    return person.contact.address.city.strip()

p = Person(Contact(None))
print(get_city(p))
