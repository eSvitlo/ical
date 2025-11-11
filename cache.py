import os
from functools import wraps

from flask import request

if os.getenv("SERVER_SOFTWARE"):
    from google.appengine.api.memcache import Client

    def cache_route(timeout=60):
        def decorator(f):
            @wraps(f)
            def wrapped(*args, **kwargs):
                memcache = Client()
                key = request.path
                cached = memcache.get(key)
                if cached:
                    return cached
                result = f(*args, **kwargs)
                memcache.set(key, result, time=timeout)
                return result

            return wrapped

        return decorator

else:
    def cache_route(timeout=60):
        def decorator(f):
            return f

        return decorator
