def get_config(cfg):
    return cfg["timeout"] * 1000
print(get_config({"host": "localhost"}))
