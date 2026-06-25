from codesuture.tracer import install

def my_crashing_func(x):
    return x.get('foo')

tracer = install(shadow=True, verbose=True)

try:
    print('First call:', my_crashing_func(None))
except Exception as e:
    print('Failed 1:', type(e), e)

print('Second call:', my_crashing_func(None))
