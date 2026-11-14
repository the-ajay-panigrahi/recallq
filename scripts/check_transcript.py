"""Local checks for a completed one-video audit; no cloud or LLM calls."""

import argparse
import json
import math
from pathlib import Path
import re
import wave

from scripts import transcribe as t


def union(intervals):
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def coverage(speech, transcript):
    """Timestamp overlap is an alignment check, not a word-accuracy score."""
    covered = 0.0
    for start, end in speech:
        covered += sum(max(0, min(end, b) - max(start, a)) for a, b in transcript)
    total = sum(b - a for a, b in speech)
    return {"speech_seconds": total, "covered_seconds": covered,
            "fraction": covered / total if total else None}


def speech_regions(home, duration):
    path = home / "speech-regions.json"
    if path.exists():
        return t.read_json(path)["regions"]
    import numpy as np
    import torch
    from silero_vad import load_silero_vad, get_speech_timestamps
    torch.set_num_threads(1)
    wav = home / "reference.wav"
    t.extract_audio_section(str(home / "reference.webm"), wav, 0, duration)
    with wave.open(str(wav), "rb") as source:
        samples = np.frombuffer(source.readframes(source.getnframes()), dtype=np.int16)
    samples = samples.astype(np.float32) / 32768.0
    regions = get_speech_timestamps(torch.from_numpy(samples), load_silero_vad(),
                                    sampling_rate=16000, return_seconds=True)
    t.atomic_json(path, {"detector": "silero-vad", "regions": regions})
    wav.unlink()
    return regions


# Terms selected from independent audio-only references, not from the tested ASR.
TERMS = {
    180: ["sorted", "duplicate"],
    780: ["array", "unique", "sorted"],
    1050: ["unique", "element", "variable"],
    1560: ["loop", "return", "time complexity"],
    1680: ["maximum", "independent", "time complexity"],
}


def check(home, video_id):
    data = t.read_json(home / "transcripts" / f"{video_id}.json")
    if not data:
        raise RuntimeError("Transcript is incomplete; no quality result can be reported")
    segments, duration = data["segments"], data["duration"]
    invalid = [i for i, s in enumerate(segments)
               if not all(math.isfinite(s[k]) for k in ["start", "end"])
               or s["start"] < 0 or s["end"] <= s["start"] or s["end"] > duration + 1]
    unordered = [i for i in range(1, len(segments)) if segments[i]["start"] < segments[i - 1]["start"]]
    aligned = union((s["start"], s["end"]) for s in segments)
    speech = speech_regions(home, duration)
    regions = union((s["start"], s["end"]) for s in speech)
    keyword_checks = []
    for start, terms in TERMS.items():
        reference = t.read_json(home / f"independent-{start}.json")
        if not reference:
            continue
        excerpt = " ".join(s["text"] for s in segments if s["end"] > start - 2 and s["start"] < start + 32)
        lower = excerpt.lower()
        keyword_checks.append({"start": start, "reference_model": reference["model"],
            "excerpt": excerpt, "terms": {term: bool(re.search(r"\b" + re.escape(term) + r"s?\b", lower))
                                          for term in terms}})
    repeated = []
    for segment in segments:
        words = segment["text"].lower().split()
        for width in [1, 2, 3, 4]:
            for start in range(len(words) - width * 6 + 1):
                phrase = words[start:start + width]
                if all(words[start + k * width:start + (k + 1) * width] == phrase for k in range(6)):
                    repeated.append({"start": segment["start"], "phrase": " ".join(phrase)})
                    break
    audit = t.read_json(home / "audit-summary.json", {})
    processing = data["provenance"]["processing_seconds"]
    report = {"video_id": video_id, "audio_duration_seconds": duration,
        "processing_seconds": processing, "processing_per_audio_second": processing / duration,
        "projected_67_53_hour_playlist_hours": processing / duration * 67.53,
        "invalid_timestamps": invalid, "unordered_timestamps": unordered,
        "last_transcript_end": segments[-1]["end"],
        "non_roman_segments": [i for i, s in enumerate(segments) if t.DEVANAGARI.search(s["text"])],
        "speech_timestamp_overlap": coverage(regions, aligned),
        "english_keyword_checks": keyword_checks, "repetition_flags": repeated,
        "restart_checks": audit.get("checkpoint_checks"),
        "user_observation": t.read_json(home / "user-observation.json"),
        "limitations": [
            "Timestamp coverage does not establish word accuracy or rule out missing speech.",
            "Independent Gemini audio samples are model-assisted references, not human-certified ground truth.",
            "Keyword checks measure literal technical spelling in sampled excerpts, not overall transcript accuracy.",
            "App memory footprints include compressed memory and cannot be summed as physical RAM use.",
            "Open applications were measured; arbitrary active IDE tasks or YouTube playback were not controlled.",
        ]}
    t.atomic_json(home / "quality-report.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", required=True, type=Path)
    parser.add_argument("--video", required=True)
    args = parser.parse_args()
    result = check(args.home.resolve(), args.video)
    print(json.dumps({k: v for k, v in result.items() if k != "english_keyword_checks"}, indent=2))
