import math

import pytest

from backend import core
from scripts import ingest


async def test_document_batches_preserve_order_and_normalize_vectors(monkeypatch):
    async def request(method, url, *, headers, body):
        assert url.endswith(":batchEmbedContents")
        assert [r["content"]["parts"][0]["text"] for r in body["requests"]] == ["first", "second"]
        assert all(r["taskType"] == "RETRIEVAL_DOCUMENT" for r in body["requests"])
        return {"embeddings": [{"values": [3, 4]}, {"values": [0, 2]}]}

    monkeypatch.setenv("EMBEDDING_DIMENSION", "2")
    monkeypatch.setattr(core, "request_json", request)
    assert await ingest.embed_documents(["first", "second"]) == [[.6, .8], [0, 1]]


@pytest.mark.parametrize("embeddings", [[], [{"values": [0, 0]}], [{"values": [math.nan, 1]}]])
async def test_incomplete_or_invalid_batches_are_rejected(monkeypatch, embeddings):
    async def request(*args, **kwargs):
        return {"embeddings": embeddings}

    monkeypatch.setenv("EMBEDDING_DIMENSION", "2")
    monkeypatch.setattr(core, "request_json", request)
    with pytest.raises(core.ServiceError):
        await ingest.embed_documents(["first"])
