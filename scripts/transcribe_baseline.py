"""One-video faster-whisper baseline. No custom decoder or public indexing."""

import argparse
import importlib.metadata
import json
import signal
import time

from scripts import transcribe as t


def completed(path, config):
    data = t.read_json(path)
    return data if data and data.get("provenance", {}).get("settings") == config else None


def model_directory():
    pointer = t.HOME / "faster-whisper-model-path.txt"
    if pointer.exists():
        return pointer.read_text().strip()
    from faster_whisper.utils import download_model
    folder = download_model("large-v3")
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(str(folder))
    return str(folder)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=t.Path, required=True)
    parser.add_argument("--audio", type=t.Path, required=True)
    parser.add_argument("--video", required=True)
    parser.add_argument("--duration", type=float, required=True)
    parser.add_argument("--title", default="Lecture 4: Two Pointers")
    parser.add_argument("--sample-seconds", type=int)
    parser.add_argument("--device", choices=["cpu", "mps"], default="cpu")
    parser.add_argument("--compute-type", choices=["int8", "int8_float16", "float16"], default="int8")
    args = parser.parse_args()
    home = args.home.resolve()
    with t.exclusive_lock(home):
        t.free_space(12)
        model_dir = model_directory()
        config = {"engine": "faster-whisper", "version": importlib.metadata.version("faster-whisper"),
                  "model": "large-v3", "revision": t.Path(model_dir).name,
                  "device": args.device, "compute_type": args.compute_type, "cpu_threads": 4,
                  "beam_size": 5, "language": "hi", "task": "transcribe",
                  "vad_filter": True, "word_timestamps": False,
                  "condition_on_previous_text": False,
                  "pipeline": "BatchedInferencePipeline", "batch_size": 1, "chunk_length": 15,
                  "sample_seconds": args.sample_seconds,
                  "audio_sha256": t.audio_digest(args.audio)}
        target = home / "transcripts" / f"{args.video}.json"
        if completed(target, config):
            t.log("Completed transcript reused; no model loaded or ASR repeated.")
            return
        source = args.audio.resolve()
        duration = args.duration
        if args.sample_seconds:
            duration = min(duration, args.sample_seconds)
            source = home / "sample.wav"
            t.extract_audio_section(str(args.audio.resolve()), source, 0, duration)
        from faster_whisper import WhisperModel, BatchedInferencePipeline
        began = time.monotonic()
        t.log(f"Loading full large-v3 on {args.device} {args.compute_type}, four threads, beam 5.")
        model = WhisperModel(model_dir, device=args.device, compute_type=args.compute_type,
                             cpu_threads=4, num_workers=1)
        actual_compute = str(getattr(model.model, "compute_type", "int8"))
        loaded = time.monotonic()
        pipeline = BatchedInferencePipeline(model=model)
        segments, info = pipeline.transcribe(str(source), language="hi", task="transcribe",
            beam_size=5, vad_filter=True, word_timestamps=False, condition_on_previous_text=False,
            batch_size=1, chunk_length=15)
        saved = []
        # This is an audit log, not a resume checkpoint. Only final files are reused.
        with (home / "partial.jsonl").open("w") as partial:
            for segment in segments:
                item = {"start": float(segment.start), "end": float(segment.end), "text": segment.text,
                        "words": [{"start": float(w.start), "end": float(w.end),
                                   "word": w.word, "probability": float(w.probability)}
                                  for w in segment.words or []]}
                partial.write(json.dumps(item, ensure_ascii=False) + "\n")
                partial.flush()
                saved.append(item)
                t.atomic_json(home / "status.json", {"video_id": args.video, "stage": "transcribing",
                    "through_seconds": item["end"], "duration": duration,
                    "elapsed_seconds": time.monotonic() - began})
                t.log(f"Saved speech through {item['end']:.1f}s / {duration:.1f}s")
                if "repeated_phrase_loop" in t.quality_flags({"text": item["text"]}):
                    raise RuntimeError("Repeated speech detected; final transcript not accepted.")
        elapsed = time.monotonic() - began
        convert = t.romanizer()
        raw_segments = saved
        roman = [{**s, "raw_text": s["text"], "text": convert(s["text"])} for s in saved]
        if not roman:
            raise RuntimeError("No speech detected; not accepting an empty transcript.")
        t.atomic_json(home / "raw-transcript.json", {"segments": raw_segments, "settings": config})
        t.atomic_json(target, {"video_id": args.video, "title": args.title, "duration": duration,
            "language": "roman-hinglish", "model": "large-v3", "segments": roman,
            "provenance": {"source": "local audio; faster-whisper large-v3 CPU INT8; uroman",
                "settings": config, "processing_seconds": elapsed, "load_seconds": loaded - began,
                "actual_compute_type": actual_compute,
                "vad_speech_seconds": info.duration_after_vad, "llm_rewritten": False},
            "review_required": True})
        t.atomic_json(home / "status.json", {"video_id": args.video, "stage": "complete",
            "duration": duration, "processing_seconds": elapsed,
            "processing_per_audio_second": elapsed / duration})
        t.log(f"Complete. Audio={duration:.1f}s; processing={elapsed:.1f}s; load={loaded-began:.1f}s.")


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        main()
    except KeyboardInterrupt:
        t.log("Stopped safely. Completed videos are cached; an interrupted video restarts.")
        raise SystemExit(130)
