def with_retries(fn, attempts: int = 3):
    for attempt in range(attempts):
        try:
            return fn()
        except TimeoutError:
            if attempt == attempts - 1:
                raise
    return None
