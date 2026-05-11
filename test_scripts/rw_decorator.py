# Real-world pattern: patching through a decorator wrapper
import functools

def log_call(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return fn(*args, **kwargs)
    return wrapper

class Config:
    def __init__(self, data):
        self.data = data  # may be None

@log_call
def get_setting(config, key):
    return config.data[key]  # config.data may be None

cfg = Config(None)
print(get_setting(cfg, 'debug'))
