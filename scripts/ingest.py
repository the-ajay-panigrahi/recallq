"""Historical Gemini indexing implementation. Use scripts.index_minilm now."""

import argparse
import asyncio
import hashlib
import json
import math
import shutil
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5
from backend import core
from scripts.files import atomic_json


async def embed_documents(texts):
    """Embed a small synchronous batch; cache callers can resume after quota errors."""
    model = core.config("EMBEDDING_MODEL", "gemini-embedding-001")
    dimension = int(core.config("EMBEDDING_DIMENSION", "768"))
    for attempt in range(4):
        try:
            response = await core.request_json(
                "POST", f"{core.GEMINI}/{model}:batchEmbedContents",
                headers={"x-goog-api-key": core.config("GEMINI_INGEST_KEY")},
                body={"requests": [{
                    "model": f"models/{model}",
                    "content": {"parts": [{"text": text}]},
                    "taskType": "RETRIEVAL_DOCUMENT",
                    "outputDimensionality": dimension,
                } for text in texts]},
            )
            break
        except core.ServiceError as exc:
            if exc.status != 429 or attempt == 3:
                raise
            print("Embedding quota reached; retrying in 65 seconds. Saved work is retained.", flush=True)
            await asyncio.sleep(65)
    embeddings = response.get("embeddings", [])
    if len(embeddings) != len(texts):
        raise core.ServiceError(503, "EMBEDDING_INVALID", "Incomplete embedding batch.")
    vectors = []
    for embedding in embeddings:
        values = embedding.get("values", [])
        norm = math.sqrt(sum(v * v for v in values))
        if len(values) != dimension or not math.isfinite(norm) or norm == 0:
            raise core.ServiceError(503, "EMBEDDING_INVALID", "Invalid embedding batch vector.")
        vectors.append([v / norm for v in values])
    return vectors


def chunks(data, window=75, overlap=15):
    if window <= 0 or not 0 <= overlap < window:
        raise ValueError("Require a positive window and overlap smaller than the window")
    segments = data["segments"]
    previous = -1
    for s in segments:
        if (
            not all(math.isfinite(float(s[k])) for k in ("start", "end"))
            or s["start"] < 0
            or s["end"] <= s["start"]
            or s["start"] < previous
        ):
            raise ValueError("Invalid segment timing")
        previous = s["start"]
    index = 0
    while index < len(segments):
        end = index + 1
        while (
            end < len(segments)
            and segments[end]["end"] - segments[index]["start"] <= window
        ):
            end += 1
        group = segments[index:end]
        text = " ".join(s["text"].strip() for s in group).strip()
        if text:
            yield {
                "start_sec": group[0]["start"],
                "end_sec": group[-1]["end"],
                "text": text,
                "segments": group,
            }
        if end == len(segments):
            break
        next_index = end
        while (
            next_index > index + 1
            and segments[next_index - 1]["end"] > group[-1]["end"] - overlap
        ):
            next_index -= 1
        index = next_index


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--import-only", action="store_true")
    parser.add_argument("--manifest", type=Path, default=core.ROOT / "data/manifest.json",
                        help="Use a separate manifest to stage a new index without changing the live corpus")
    parser.add_argument("--revision", help="Corpus revision for a separately staged manifest")
    parser.add_argument("--batch-size", type=int, default=1, choices=range(1, 101),
                        metavar="1-100", help="Synchronous embedding batch size")
    args = parser.parse_args()
    destination = core.ROOT / "data/transcripts"
    destination.mkdir(exist_ok=True)
    # Initial seed lessons cover three separate topics; no transcript text is rewritten.
    preferred = ["N8vJ8RyQEes", "ICggOtnixBk"]
    files = sorted(args.source.glob("*.json"))
    sliding = next(
        (
            p.stem
            for p in files
            if "introduction to sliding" in json.loads(p.read_text())["title"].lower()
        ),
        None,
    )
    if sliding:
        preferred.insert(0, sliding)
    if args.limit < 0:
        parser.error("--limit must be zero (all files) or positive")
    selected = sorted(
        files,
        key=lambda p: (preferred.index(p.stem) if p.stem in preferred else 999, p.name),
    )
    if args.limit:
        selected = selected[:args.limit]
    if not selected:
        parser.error("No transcript files found")
    manifest = json.loads(args.manifest.read_text()) if args.manifest.exists() else core.manifest()
    if args.revision:
        if args.manifest.resolve() == (core.ROOT / "data/manifest.json").resolve():
            parser.error("Use --manifest with a separate path when staging a new --revision")
        if not args.manifest.exists():
            for video in manifest["videos"]:
                if video["status"] != "excluded":
                    video["status"] = "pending"
                    video.pop("chunks", None)
        elif manifest["revision"] != args.revision:
            parser.error("Staging manifest already belongs to a different revision")
        manifest["revision"] = args.revision
    by_id = {v["video_id"]: v for v in manifest["videos"]}
    cache = core.ROOT / "data/embedding-cache"
    cache.mkdir(exist_ok=True)
    for path in selected:
        data = json.loads(path.read_text())
        passages = list(chunks(data))
        target = destination / path.name
        if path.resolve() != target.resolve():
            if target.exists() and target.read_bytes() != path.read_bytes():
                backup = core.ROOT / "data/tutorial-backup" / hashlib.sha256(target.read_bytes()).hexdigest()
                backup.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup / target.name)
            atomic_json(target, data)
        vid = data["video_id"]
        revision = hashlib.sha256(path.read_bytes()).hexdigest()
        entry = {
            **by_id.get(vid, {}),
            "video_id": vid,
            "title": data["title"],
            "status": "pending",
            "transcript_hash": revision,
            "audio_verified": False,
        }
        if args.import_only:
            by_id[vid] = entry
            continue
        revision_key = hashlib.sha256(manifest["revision"].encode()).hexdigest()[:16]
        checkpoint = cache / f"{vid}-{revision_key}-checkpoint.json"
        legacy = cache / f"{vid}-checkpoint.json"
        if manifest["revision"] == "initial" and not checkpoint.exists() and legacy.exists():
            shutil.copy2(legacy, checkpoint)
        previous = json.loads(checkpoint.read_text()) if checkpoint.exists() else []
        pending = checkpoint.with_suffix(".pending.json")
        if pending.exists():
            previous = list(set(previous) | set(json.loads(pending.read_text())))
        points = []
        missing = []
        model = core.config("EMBEDDING_MODEL", "gemini-embedding-001")
        dimension = int(core.config("EMBEDDING_DIMENSION", "768"))
        for passage in passages:
            identity = (
                f"{manifest['revision']}:{vid}:{revision}:{passage['start_sec']}:75-15-v2:{model}:{dimension}"
            )
            cached = cache / f"{uuid5(NAMESPACE_URL, identity)}.json"
            if not cached.exists():
                missing.append((cached, data["title"] + "\n\n" + passage["text"]))
        if args.batch_size > 1:
            for start in range(0, len(missing), args.batch_size):
                batch = missing[start:start + args.batch_size]
                vectors = await embed_documents([text for _, text in batch])
                for (cached, _), vector in zip(batch, vectors):
                    atomic_json(cached, vector)
                await asyncio.sleep(1)
        for passage in passages:
            model = core.config("EMBEDDING_MODEL", "gemini-embedding-001")
            dimension = int(core.config("EMBEDDING_DIMENSION", "768"))
            identity = (
                f"{manifest['revision']}:{vid}:{revision}:{passage['start_sec']}:75-15-v2:{model}:{dimension}"
            )
            pid = str(uuid5(NAMESPACE_URL, identity))
            cached = cache / f"{pid}.json"
            if cached.exists():
                vector = json.loads(cached.read_text())
            else:
                vector = await core.embed(
                    data["title"] + "\n\n" + passage["text"],
                    core.config("GEMINI_INGEST_KEY"),
                    document=True,
                )
                atomic_json(cached, vector)
                await asyncio.sleep(0.65)
            payload = {
                **passage,
                "source_id": pid,
                "video_id": vid,
                "video_title": data["title"],
                "transcript_hash": revision,
                "transcript_source": data.get("provenance", {}).get(
                    "source", "tutorial faster-whisper large-v3; audio unverified"),
                "chunk_version": "75-15-v2",
                "embedding_model": model,
                "embedding_dimension": dimension,
                "corpus_revision": manifest["revision"],
            }
            points.append({"id": pid, "vector": vector, "payload": payload})
        # Journal all IDs before the first remote write, including interrupted attempts.
        atomic_json(pending, list(set(previous) | {p["id"] for p in points}))
        for start in range(0, len(points), 32):
            await core.qdrant(
                "/points?wait=true",
                {"points": points[start : start + 32]},
                method="PUT",
                write=True,
            )
        current = [p["id"] for p in points]
        stale = list(set(previous) - set(current))
        if stale:
            await core.qdrant("/points/delete?wait=true", {"points": stale}, write=True)
        atomic_json(checkpoint, current)
        pending.unlink(missing_ok=True)
        entry["status"] = "indexed"
        entry["chunks"] = len(points)
        by_id[vid] = entry
        manifest["videos"] = list(by_id.values())
        manifest["quality_status"] = (
            "Automatic transcripts; audio accuracy is not certified. Timestamps identify approximate passages."
        )
        atomic_json(args.manifest, manifest)
        print("Indexed", vid, len(points), "passages", flush=True)
    manifest["videos"] = list(by_id.values())
    manifest["quality_status"] = (
        "Automatic transcripts; audio accuracy is not certified. Timestamps identify approximate passages."
    )
    atomic_json(args.manifest, manifest)
    print("Manifest saved; audio quality has not been certified.")


if __name__ == "__main__":
    raise SystemExit("Gemini ingestion is retired. Run: python -m scripts.index_minilm")
