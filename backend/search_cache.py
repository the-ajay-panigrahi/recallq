"""Small, bounded cache per warm worker. Never caches credentials or summaries."""
import asyncio
from collections import OrderedDict
from copy import deepcopy
from time import monotonic

from backend import core
from backend.embeddings import MODEL, REVISION

TTL_SECONDS = 300
MAX_ENTRIES = 128
_cache = OrderedDict()
_pending = {}


async def search(query, limit):
    # Match exact trimmed text, not guessed semantic equivalents. Index and
    # model changes must never reuse a previous corpus's results.
    query = query.strip()
    key = (query, limit, core.manifest()["revision"], MODEL, REVISION,
           core.config("MATCH_THRESHOLD", ".30"))
    now = monotonic()
    for old in list(_cache):
        if _cache[old][0] <= now:
            del _cache[old]
    if key in _cache:
        _cache.move_to_end(key)
        return deepcopy(_cache[key][1])

    async def load():
        try:
            result = await core.search(query, limit, "")
            _cache[key] = (monotonic() + TTL_SECONDS, deepcopy(result))
            while len(_cache) > MAX_ENTRIES:
                _cache.popitem(last=False)
            return result
        finally:
            _pending.pop(key, None)

    if key not in _pending:
        _pending[key] = asyncio.create_task(load())
        # A disconnected caller must not leave an unobserved failed task.
        _pending[key].add_done_callback(lambda task: None if task.cancelled() else task.exception())
    return deepcopy(await asyncio.shield(_pending[key]))
