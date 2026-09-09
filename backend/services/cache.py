"""TTL memory cache utility"""
import time
import functools

_store = {}

def ttl_cache(ttl=30):
    def deco(fn):
        @functools.wraps(fn)
        def wrap(*a, **kw):
            k = f"{fn.__name__}:{str(a)}:{str(sorted(kw.items()))}"
            now = time.time()
            if k in _store and now - _store[k][1] < ttl:
                return _store[k][0]
            v = fn(*a, **kw)
            _store[k] = (v, now)
            return v
        return wrap
    return deco
