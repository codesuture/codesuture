class Order:
    def __init__(self, id, customer_id):
        self.id = id
        self._customer_id = customer_id
        self._customer_cache = None

    @property
    def customer(self):
        if self._customer_cache is None:
            customers = {1: {"name": "Alice Corp", "tier": "premium"}}
            self._customer_cache = customers.get(self._customer_id)
        return self._customer_cache

class InvoiceGenerator:
    def generate(self, order):
        name = order.customer["name"]
        tier = order.customer["tier"].upper()
        return f"Invoice for {name} ({tier})"

order = Order(id=100, customer_id=999)
print(InvoiceGenerator().generate(order))
print("COMPLETED_OK")
