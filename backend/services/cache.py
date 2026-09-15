"""TTL memory cache utility

PERF-20260915 修复两个既有缺陷：
1) 缓存键原本用 `str(sorted(kw.items()))`，而 FastAPI 端点会把 `db: Session` 作为关键字参数
   传进来，其 repr 含内存地址（`<sqlalchemy.orm.session.Session object at 0x...>`）→
   每个请求都是新键 → 缓存永不命中（/api/metrics/dashboard 因此每次都真跑 150ms）。
   现在只把「不可变简单类型」纳入键，其余（Session/连接/任意对象）统一折叠为占位符。
2) `_store` 只增不减 → 长期运行内存泄漏。现在加了过期清理 + 容量上限。
"""
import time
import functools

_store = {}
_MAX_ENTRIES = 512

_SIMPLE = (str, int, float, bool, bytes, type(None))


def _is_simple(v):
    if isinstance(v, _SIMPLE):
        return True
    if isinstance(v, (tuple, frozenset)):
        return all(_is_simple(x) for x in v)
    return False


def _key_of(fn, args, kwargs):
    parts = [fn.__name__]
    for v in args:
        parts.append(repr(v) if _is_simple(v) else "<obj>")
    for k in sorted(kwargs):
        v = kwargs[k]
        parts.append(f"{k}=" + (repr(v) if _is_simple(v) else "<obj>"))
    return "|".join(parts)


def _evict(now):
    """先清过期，再按容量上限淘汰最旧条目。"""
    dead = [k for k, (_v, ts) in _store.items() if now - ts > 3600]
    for k in dead:
        _store.pop(k, None)
    if len(_store) > _MAX_ENTRIES:
        for k, _ in sorted(_store.items(), key=lambda kv: kv[1][1])[: len(_store) - _MAX_ENTRIES]:
            _store.pop(k, None)


def ttl_cache(ttl=30):
    def deco(fn):
        @functools.wraps(fn)
        def wrap(*a, **kw):
            k = _key_of(fn, a, kw)
            now = time.time()
            hit = _store.get(k)
            if hit is not None and now - hit[1] < ttl:
                return hit[0]
            v = fn(*a, **kw)
            _store[k] = (v, now)
            if len(_store) > _MAX_ENTRIES:
                _evict(now)
            return v
        return wrap
    return deco


def attl_cache(ttl=30):
    """async 版 ttl_cache。

    ⚠ ttl_cache 不能用于 `async def` 端点：其 wrap 是同步函数，`fn(*a, **kw)`
    拿到的是协程对象而不会被 await，接口会直接返回一个 coroutine。
    需要缓存的 async 端点请用本装饰器。
    """
    def deco(fn):
        @functools.wraps(fn)
        async def wrap(*a, **kw):
            k = _key_of(fn, a, kw)
            now = time.time()
            hit = _store.get(k)
            if hit is not None and now - hit[1] < ttl:
                return hit[0]
            v = await fn(*a, **kw)
            _store[k] = (v, now)
            if len(_store) > _MAX_ENTRIES:
                _evict(now)
            return v
        return wrap
    return deco

