"""Import tutorial transcripts as data, then index a deliberately bounded subset."""

import argparse
import asyncio
import hashlib
import json
import math
import shutil
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5
from backend import core


def chunks(data, window=75, overlap=15):
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
            and segments[next_index - 1]["start"] >= group[-1]["end"] - overlap
        ):
            next_index -= 1
        index = next_index


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--import-only", action="store_true")
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
    selected = sorted(
        files,
        key=lambda p: (preferred.index(p.stem) if p.stem in preferred else 999, p.name),
    )[: args.limit]
    manifest = core.manifest()
    by_id = {v["video_id"]: v for v in manifest["videos"]}
    cache = core.ROOT / "data/embedding-cache"
    cache.mkdir(exist_ok=True)
    for path in selected:
        data = json.loads(path.read_text())
        passages = list(chunks(data))
        shutil.copy2(path, destination / path.name)
        vid = data["video_id"]
        revision = hashlib.sha256(path.read_bytes()).hexdigest()
        entry = {
            "video_id": vid,
            "title": data["title"],
            "status": "pending",
            "transcript_hash": revision,
            "audio_verified": False,
        }
        by_id[vid] = entry
        if args.import_only:
            continue
        checkpoint = cache / f"{vid}-checkpoint.json"
        previous = json.loads(checkpoint.read_text()) if checkpoint.exists() else []
        points = []
        for passage in passages:
            model = core.config("EMBEDDING_MODEL", "gemini-embedding-001")
            dimension = int(core.config("EMBEDDING_DIMENSION", "768"))
            identity = (
                f"{vid}:{revision}:{passage['start_sec']}:75-15:{model}:{dimension}"
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
                cached.write_text(json.dumps(vector))
                await asyncio.sleep(0.65)
            payload = {
                **passage,
                "source_id": pid,
                "video_id": vid,
                "video_title": data["title"],
                "transcript_hash": revision,
                "transcript_source": "tutorial faster-whisper large-v3; audio unverified",
                "chunk_version": "75-15-v1",
                "embedding_model": model,
                "embedding_dimension": dimension,
                "corpus_revision": manifest["revision"],
            }
            points.append({"id": pid, "vector": vector, "payload": payload})
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
        checkpoint.write_text(json.dumps(current))
        entry["status"] = "indexed"
        entry["chunks"] = len(points)
        manifest["videos"] = list(by_id.values())
        manifest["quality_status"] = (
            "Tutorial transcripts. Audio verification pending; timestamps identify approximate passages."
        )
        (core.ROOT / "data/manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n"
        )
        print("Indexed", vid, len(points), "passages", flush=True)
    manifest["videos"] = list(by_id.values())
    manifest["quality_status"] = (
        "Tutorial transcripts. Audio verification pending; timestamps identify approximate passages."
    )
    (core.ROOT / "data/manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print("Manifest saved; audio quality has not been certified.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except core.ServiceError as exc:
        print("Stopped safely:", exc.code, exc.message)
        raise SystemExit(1)
