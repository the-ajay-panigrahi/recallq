"""Offline MiniLM indexing. Does not call Gemini, OpenAI, or an LLM service."""

import asyncio
import hashlib
import json
from pathlib import Path
import time
from uuid import NAMESPACE_URL, uuid5

from backend import core
from backend.embeddings import MODEL, REVISION, DIMENSION, CHUNK_VERSION, encode, passages, document_text
from scripts.files import atomic_json

STAGE = core.ROOT / "work/minilm"
CORPUS = "tutorial-minilm-2026-11-14-v1"


async def main():
    started = time.monotonic()
    model_seconds = 0
    manifest_file = STAGE / "manifest.json"
    manifest = json.loads(manifest_file.read_text()) if manifest_file.exists() else core.manifest()
    if manifest.get("revision") != CORPUS:
        for video in manifest["videos"]:
            if video["status"] != "excluded":
                video["status"] = "pending"
                video.pop("chunks", None)
    manifest.update(revision=CORPUS, embedding_model=MODEL, embedding_revision=REVISION,
                    embedding_dimension=DIMENSION, chunk_version=CHUNK_VERSION)
    cache = core.ROOT / "data/embedding-cache/minilm"
    cache.mkdir(parents=True, exist_ok=True)
    by_id = {v["video_id"]: v for v in manifest["videos"]}
    files = sorted((core.ROOT / "data/transcripts").glob("*.json"))
    for file in files:
        data = json.loads(file.read_text())
        vid = data["video_id"]
        if vid not in by_id or by_id[vid]["status"] == "excluded":
            continue
        digest = hashlib.sha256(file.read_bytes()).hexdigest()
        if by_id[vid].get("status") == "indexed" and by_id[vid].get("transcript_hash") == digest:
            continue
        parts = list(passages(data))
        points, missing = [], []
        for index, part in enumerate(parts):
            identity = f"{CORPUS}:{vid}:{digest}:{CHUNK_VERSION}:{REVISION}:{index}"
            pid = str(uuid5(NAMESPACE_URL, identity))
            cached = cache / f"{pid}.json"
            payload = {**part, "source_id": pid, "video_id": vid, "video_title": data["title"],
                       "transcript_hash": digest, "corpus_revision": CORPUS,
                       "transcript_source": "tutorial faster-whisper large-v3; automatic transcript",
                       "embedding_model": MODEL, "embedding_revision": REVISION,
                       "embedding_dimension": DIMENSION, "chunk_version": CHUNK_VERSION}
            point = {"id": pid, "payload": payload}
            points.append(point)
            if cached.exists():
                point["vector"] = core.storage_vector(json.loads(cached.read_text()))
            else:
                missing.append((point, cached, document_text(data["title"], part["text"])))
        for offset in range(0, len(missing), 16):
            batch = missing[offset:offset + 16]
            before = time.monotonic()
            vectors = encode([text for _, _, text in batch])
            model_seconds += time.monotonic() - before
            for (point, cached, _), vector in zip(batch, vectors):
                atomic_json(cached, vector)
                point["vector"] = core.storage_vector(vector)
        for offset in range(0, len(points), 32):
            await core.qdrant("/points?wait=true", {"points": points[offset:offset + 32]}, method="PUT", write=True)
        by_id[vid].update(status="indexed", chunks=len(points), transcript_hash=digest)
        manifest["videos"] = list(by_id.values())
        manifest["quality_status"] = "Automatic tutorial transcripts; recognition errors remain possible. Timestamps identify approximate passages."
        atomic_json(manifest_file, manifest)
        count = sum(v["status"] == "indexed" for v in manifest["videos"])
        print(f"Indexed {count}/121: {vid}, {len(points)} passages", flush=True)
    expected = sum(v.get("chunks", 0) for v in manifest["videos"] if v["status"] == "indexed")
    result = await core.qdrant("/points/count", {"filter": {"must": [{"key": "corpus_revision", "match": {"value": CORPUS}}]}, "exact": True})
    actual = result["result"]["count"]
    if actual != expected:
        raise RuntimeError(f"Index count mismatch: expected {expected}, got {actual}")
    report = {"corpus_revision": CORPUS, "indexed_videos": sum(v["status"] == "indexed" for v in manifest["videos"]),
              "passages": actual, "elapsed_seconds": round(time.monotonic() - started, 2),
              "embedding_seconds": round(model_seconds, 2), "embedding_model": MODEL,
              "embedding_revision": REVISION, "external_embedding_api_calls": 0}
    atomic_json(STAGE / "result.json", report)
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except core.ServiceError as exc:
        print("Stopped safely:", exc.code, exc.message, flush=True)
        raise SystemExit(1)
