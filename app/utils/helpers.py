def singleton(cls):
    _instance = None

    def wrapper(*args, **kwargs):
        nonlocal _instance
        if _instance is None:
            _instance = cls(*args, **kwargs)
            if not hasattr(_instance, "_initialized"):
                _instance._initialized = True
        return _instance

    return wrapper
