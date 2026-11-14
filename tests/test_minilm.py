import json
from pathlib import Path

import numpy as np
import pytest

from backend import core, embeddings


ASSETS = Path(__file__).resolve().parents[1] / "backend/assets/minilm/model.onnx"


def test_exact_problem_keywords_break_close_semantic_ties():
    heap = {"score": .5797, "payload": {"video_title": "Merge K Sorted Lists HEAP", "text": "Similar to two pointers. Result array is sorted."}}
    lesson = {"score": .567, "payload": {"video_title": "2 Pointer Technique", "text": "Remove duplicates from sorted array."}}
    query = "Remove duplicates from a sorted array using two pointers"
    assert core.rerank(query, [heap, lesson])[0] is lesson
    heap["score"] = .9
    assert core.rerank(query, [heap, lesson])[0] is heap


def test_zero_padding_preserves_cosine_similarity(monkeypatch):
    monkeypatch.setenv("QDRANT_VECTOR_DIMENSION", "768")
    a, b = np.random.default_rng(42).normal(size=(2, 384))
    cosine = lambda x, y: np.dot(x, y) / (np.linalg.norm(x) * np.linalg.norm(y))
    assert cosine(a, b) == pytest.approx(cosine(core.storage_vector(a.tolist()), core.storage_vector(b.tolist())))


@pytest.mark.skipif(not ASSETS.exists(), reason="Run python -m scripts.prepare_embeddings")
def test_embedding_is_normalized_and_unaffected_by_batch_padding():
    alone = embeddings.encode(["Remove duplicates from a sorted array"])[0]
    together = embeddings.encode(["Remove duplicates from a sorted array", "longer input " * 60])[0]
    assert len(alone) == 384
    assert np.linalg.norm(alone) == pytest.approx(1)
    assert together == pytest.approx(alone, abs=2e-6)


@pytest.mark.skipif(not ASSETS.exists(), reason="Run python -m scripts.prepare_embeddings")
def test_long_segment_is_preserved_and_never_silently_truncated():
    text = " ".join(f"word{i}" for i in range(600))
    data = {"title": "Long title " * 50, "segments": [{"start": 5, "end": 95, "text": text}]}
    parts = list(embeddings.passages(data))
    assert len(parts) > 1
    assert " ".join(p["text"] for p in parts).split() == text.split()
    for p in parts:
        assert p["start_sec"] == 5 and p["end_sec"] == 95
        assert embeddings.token_count(embeddings.document_text(data["title"], p["text"])) + 2 <= 256


async def test_search_uses_local_embeddings_and_rejects_other_model_vectors(monkeypatch):
    monkeypatch.setattr(embeddings, "encode", lambda texts: [[1.] + [0.] * 383 for _ in texts])
    monkeypatch.setattr(embeddings, "token_count", lambda text: 10)
    monkeypatch.setattr(core, "manifest", lambda: {"revision": "minilm", "videos": [{"status": "indexed"}]})
    payload = {"source_id": "id", "video_id": "N8vJ8RyQEes", "video_title": "Kadane", "text": "sum",
               "start_sec": 0, "end_sec": 60, "embedding_model": embeddings.MODEL,
               "embedding_revision": embeddings.REVISION, "embedding_dimension": 384}

    async def qdrant(path, body):
        assert len(body["query"]) == 768
        assert body["query"][384:] == [0.] * 384
        assert body["filter"]["must"][0]["match"]["value"] == "minilm"
        return {"result": {"points": [{"score": .8, "payload": payload}]}}

    async def remote(*args, **kwargs):
        pytest.fail("Search must not call an embedding API")

    monkeypatch.setattr(core, "qdrant", qdrant)
    monkeypatch.setattr(core, "request_json", remote)
    assert (await core.search("Kadane", 3))["results"][0]["video_title"] == "Kadane"
    payload["embedding_model"] = "gemini-embedding-001"
    with pytest.raises(core.ServiceError) as exc:
        await core.search("Kadane", 3)
    assert exc.value.code == "INDEX_INCOMPATIBLE"


@pytest.mark.asyncio
async def test_relevance_filter_rejects_unrelated_and_weak_candidates(monkeypatch):
    monkeypatch.setattr(core, "embed", lambda *args: fake_embedding())
    monkeypatch.setattr(core, "manifest", lambda: {"revision": "test", "videos": [{"status": "indexed"}]})
    def payload(title, text, start):
        return {"source_id": str(start), "video_id": "N8vJ8RyQEes", "video_title": title,
                "text": text, "start_sec": start, "end_sec": start + 30,
                "embedding_model": embeddings.MODEL, "embedding_revision": embeddings.REVISION,
                "embedding_dimension": 384}
    hits = [{"score": .40, "payload": payload("Combination Sum", "Best explanation of recursion using chocolate as an analogy", 0)}]
    async def points(*args):
        return {"result": {"points": hits}}
    monkeypatch.setattr(core, "qdrant", points)
    rejected = await core.search("What is the best chocolate cake recipe?", 5)
    assert rejected["match_state"] == "none"
    assert rejected["results"] == []
    hits[:] = [{"score": .60, "payload": payload("Kadane", "maximum subarray sum", 0)},
               {"score": .32, "payload": payload("Kadane", "maximum subarray sum", 80)}]
    assert len((await core.search("Kadane maximum subarray", 5))["results"]) == 1
    hits[:] = [{"score": .34, "payload": payload("Sliding window", "shrink window", 0)}]
    assert len((await core.search("sliding window kab shrink kare", 5))["results"]) == 1


async def fake_embedding():
    return [1.] + [0.] * 383


def test_hinglish_query_normalization_preserves_topic_and_negation():
    assert core.retrieval_query("HashMap kab use karna hai?") == "when should I use hash map"
    assert core.retrieval_query("HashMap kab nahi use karna hai?") == "when should I not use hash map"
    assert core.retrieval_query("HashMap kya hai?") == "hash map"
    assert core.retrieval_query("Binary search without recursion") == "binary search without recursion"
    assert "without" in core.retrieval_query("recursion bina loop kaise kare")


async def test_search_embeds_normalized_text_but_returns_original_question(monkeypatch):
    seen=[]
    async def embed(text,key):
        seen.append(text)
        return [1.] + [0.] * 383
    async def points(*args):
        return {"result":{"points":[]}}
    monkeypatch.setattr(core,"embed",embed)
    monkeypatch.setattr(core,"qdrant",points)
    monkeypatch.setattr(core,"manifest",lambda:{"revision":"test","videos":[{"status":"indexed"}]})
    result=await core.search("HashMap kab use karna hai?",5)
    assert seen == ["when should I use hash map"]
    assert result["query"] == "HashMap kab use karna hai?"
