import asyncio

import pytest

from backend import core, search_cache as cache


@pytest.fixture(autouse=True)
def clean_cache():
    cache._cache.clear()
    yield
    cache._cache.clear()


@pytest.fixture
def setup(monkeypatch):
    revision = {"revision": "one"}
    calls = []
    clock = [100.0]
    monkeypatch.setattr(core, "manifest", lambda: revision)
    monkeypatch.setattr(cache, "monotonic", lambda: clock[0])
    async def run(query, limit, key):
        calls.append((query, limit, key))
        await asyncio.sleep(0)
        return {"query": query, "results": [{"excerpt": "original"}]}
    monkeypatch.setattr(core, "search", run)
    return calls, revision, clock


async def test_identical_queries_reuse_but_changed_text_and_limits_do_not(setup):
    calls, _, _ = setup
    await cache.search("  sliding window  ", 5)
    await cache.search("sliding window", 5)
    await cache.search("sliding windows", 5)
    await cache.search("Sliding window", 5)
    await cache.search("sliding window", 2)
    assert len(calls) == 4
    assert all(call[2] == "" for call in calls)


async def test_expiration_and_corpus_revision_invalidate_results(setup):
    calls, revision, clock = setup
    await cache.search("DP", 5)
    clock[0] += cache.TTL_SECONDS + 1
    await cache.search("DP", 5)
    revision["revision"] = "two"
    await cache.search("DP", 5)
    assert len(calls) == 3


async def test_simultaneous_duplicates_share_work_and_results_are_isolated(setup):
    calls, _, _ = setup
    answers = await asyncio.gather(*(cache.search("DP", 5) for _ in range(10)))
    assert len(calls) == 1
    answers[0]["results"][0]["excerpt"] = "modified"
    assert answers[1]["results"][0]["excerpt"] == "original"
    assert (await cache.search("DP", 5))["results"][0]["excerpt"] == "original"


async def test_errors_are_not_cached(setup, monkeypatch):
    attempts = []
    async def fail_once(*args):
        attempts.append(1)
        if len(attempts) == 1:
            raise core.ServiceError(503, "TIMEOUT", "retry")
        return {"results": []}
    monkeypatch.setattr(core, "search", fail_once)
    with pytest.raises(core.ServiceError):
        await cache.search("DP", 5)
    assert await cache.search("DP", 5) == {"results": []}
    assert len(attempts) == 2


async def test_cache_has_a_fixed_size(setup, monkeypatch):
    calls, _, _ = setup
    monkeypatch.setattr(cache, "MAX_ENTRIES", 2)
    for q in ["one", "two", "three", "one"]:
        await cache.search(q, 5)
    assert len(cache._cache) == 2
    assert len(calls) == 4

async def test_cancelled_caller_does_not_cancel_shared_search(setup, monkeypatch):
    started, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def slow(*args):
        calls.append(1)
        started.set()
        await release.wait()
        return {"results": []}
    monkeypatch.setattr(core, "search", slow)
    first = asyncio.create_task(cache.search("DP", 5))
    await started.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    second = asyncio.create_task(cache.search("DP", 5))
    await asyncio.sleep(0)
    release.set()
    assert await second == {"results": []}
    assert len(calls) == 1
    assert not cache._pending
