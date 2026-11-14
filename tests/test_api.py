import asyncio
import json
from uuid import uuid4
import httpx
import pytest
from backend.main import app
from backend import core, search_cache


@pytest.fixture
def client():
    search_cache._cache.clear()
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


async def test_search_needs_no_key_summary_does_and_validation_redacts_input(client, monkeypatch):
    async def search(*args):
        return {"results": []}
    monkeypatch.setattr(core, "search", search)
    async with client:
        assert (
            await client.post("/api/search", json={"query": "DP"})
        ).status_code == 200
        assert (await client.post("/api/summarize", json={"query": "DP", "corpus_revision": "v1", "source_ids": [str(uuid4())]})).status_code == 401
        response = await client.post(
            "/api/search",
            json={"query": " ", "secret": "do-not-echo"},
            headers={"X-Gemini-API-Key": "private-key"},
        )
        assert response.status_code == 422
        assert "private-key" not in response.text and "do-not-echo" not in response.text


async def test_search_caps_legacy_result_requests_at_five(client, monkeypatch):
    async def search(query, limit, key):
        assert limit == 5
        return {"results": []}

    monkeypatch.setattr(core, "search", search)
    async with client:
        assert (await client.post("/api/search", json={"query": "Kadane", "limit": 20})).status_code == 200
        assert (await client.post("/api/search", json={"query": "Kadane"})).status_code == 200


async def test_simultaneous_keys_never_cross_requests(client, monkeypatch):
    async def fake(query, revision, ids, key):
        await asyncio.sleep(0.01)
        assert key == f"key-{query}"
        return {"results": [], "query": query}

    monkeypatch.setattr(core, "summarize", fake)
    async with client:
        responses = await asyncio.gather(
            *[
                client.post(
                    "/api/summarize",
                    json={"query": str(i), "corpus_revision": "v1", "source_ids": [str(uuid4())]},
                    headers={"X-Gemini-API-Key": f"key-{i}"},
                )
                for i in range(10)
            ]
        )
    assert all(r.status_code == 200 for r in responses)


async def test_unknown_and_stale_summary_sources(client, monkeypatch):
    monkeypatch.setattr(core, "manifest", lambda: {"revision": "v1"})

    async def empty(*args, **kwargs):
        return {"result": []}

    monkeypatch.setattr(core, "qdrant", empty)
    async with client:
        body = {"query": "DP", "corpus_revision": "old", "source_ids": [str(uuid4())]}
        assert (
            await client.post(
                "/api/summarize", json=body, headers={"X-Gemini-API-Key": "key"}
            )
        ).status_code == 409
        body["corpus_revision"] = "v1"
        assert (
            await client.post(
                "/api/summarize", json=body, headers={"X-Gemini-API-Key": "key"}
            )
        ).status_code == 404


def test_playback_link_uses_indexed_time_and_validated_id():
    result = core.present(
        {
            "start_sec": 340,
            "end_sec": 410,
            "video_id": "N8vJ8RyQEes",
            "video_title": "Kadane",
            "source_id": "id",
            "text": "excerpt",
        }
    )
    assert result["playback_sec"] == 335
    assert result["watch_url"].endswith("&t=335s")
    with pytest.raises(core.ServiceError):
        core.present({"start_sec": 0, "end_sec": 1, "video_id": "<script>"})


async def test_structured_citations_are_validated_and_resolved(monkeypatch):
    ids = [str(uuid4()), str(uuid4())]
    monkeypatch.setattr(core, "manifest", lambda: {"revision": "v1"})
    payloads = [
        {
            "corpus_revision": "v1",
            "source_id": i,
            "start_sec": 100,
            "end_sec": 160,
            "video_id": "N8vJ8RyQEes",
            "video_title": "Kadane",
            "text": "Best ending sum",
        }
        for i in ids
    ]

    async def points(*args, **kwargs):
        return {"result": [{"id": i, "payload": p} for i, p in zip(ids, payloads)]}

    async def model(*args, **kwargs):
        return {
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {
                        "parts": [{"text": json.dumps({"status":"answered", "sentences":[{"text":"Keep the best ending sum.","sources":[1,2]}]})}]
                    },
                }
            ]
        }

    monkeypatch.setattr(core, "qdrant", points)
    monkeypatch.setattr(core, "request_json", model)
    response = await core.summarize("Kadane", "v1", ids, "private-key")
    assert response["summary"].endswith("[1] [2]")
    assert [c["source_id"] for c in response["citations"]] == ids


def test_chunking_keeps_timestamps_and_makes_forward_progress():
    from scripts.ingest import chunks

    source = {
        "segments": [
            {"start": n, "end": n + 20, "text": f"segment {n}"}
            for n in range(0, 160, 20)
        ]
    }
    result = list(chunks(source))
    assert result[0]["start_sec"] == 0 and result[-1]["end_sec"] == 160
    assert all(a["start_sec"] < b["start_sec"] for a, b in zip(result, result[1:]))
