import json
from pathlib import Path

import pytest

from scripts import transcribe as pipeline


def test_atomic_checkpoint_failure_preserves_last_good_file(tmp_path):
    path = tmp_path / "part.json"
    pipeline.atomic_json(path, {"completed": 1})
    with pytest.raises(ValueError):
        pipeline.atomic_json(path, {"completed": float("nan")})
    assert pipeline.read_json(path) == {"completed": 1}
    assert list(tmp_path.iterdir()) == [path]


def test_only_one_runner_can_modify_checkpoints(tmp_path):
    with pipeline.exclusive_lock(tmp_path):
        with pytest.raises(RuntimeError, match="already running"):
            with pipeline.exclusive_lock(tmp_path):
                pytest.fail("Second process acquired the lock")


def test_overlap_owns_boundary_words_exactly_once():
    raw = {"segments": [{"words": [
        {"start": 299, "end": 300, "word": " before"},
        {"start": 299.9, "end": 300.1, "word": " boundary"},
        {"start": 301, "end": 302, "word": " after"},
    ]}]}
    a = pipeline.owned_segments(raw, 0, 0, 300)
    b = pipeline.owned_segments(raw, 0, 300, 600)
    assert [x["text"] for x in a + b] == ["before", "boundary after"]


def test_zero_duration_alignment_does_not_delete_spoken_words():
    raw = {"segments": [{"words": [
        {"start": 1, "end": 1, "word": " Do"},
        {"start": 1, "end": 2, "word": " not"},
    ]}]}
    assert pipeline.owned_segments(raw, 0, 0, 10)[0]["text"] == "Do not"


def test_repeated_hallucinations_and_truncated_words_are_rejected():
    assert "repeated_phrase_loop" in pipeline.quality_flags({"text": "ले " * 20})
    assert "repeated_phrase_loop" in pipeline.quality_flags({"text": "आपको मेरा प्रचार पर " * 8})
    assert "incomplete_unicode_word" in pipeline.quality_flags({"text": "मिन\ufffd"})
    assert pipeline.quality_flags({"text": "array sorted है और duplicates remove करने हैं"}) == []


def test_bad_asr_is_retried_in_shorter_windows(tmp_path):
    def decode(samples):
        if len(samples) > 100:
            return {"text": "ले " * 20, "segments": []}
        return {"language": "hi", "text": "speech", "segments": [
            {"start": 0, "end": 1, "text": "speech"}]}
    result = pipeline.decode_windows(list(range(200)), decode, sample_rate=10, checkpoint_dir=tmp_path)
    assert result["text"] and "ले" not in result["text"]
    assert list(tmp_path.glob("*.rejected.json"))
    with pytest.raises(RuntimeError, match="stopped safely"):
        pipeline.decode_windows(list(range(100)), lambda _: {"text": "ले " * 20}, sample_rate=10)


def test_short_decode_windows_keep_context_without_duplicating_words():
    calls = []

    def decode(samples):
        offset = samples[0] / 10
        calls.append(len(samples) / 10)
        words = [{"start": n - offset, "end": n + .1 - offset, "word": f" word-{n}"}
                 for n in range(50) if offset <= n < offset + len(samples) / 10]
        return {"language": "hi", "segments": [{"words": words}]}

    result = pipeline.decode_windows(list(range(500)), decode, sample_rate=10)
    assert max(calls) <= 24
    assert result["text"] == " ".join(f"word-{n}" for n in range(50))
    assert len(result["windows"]) == 3  # Unmodified ASR outputs are retained too.


def test_decode_restart_reuses_completed_short_windows(tmp_path):
    calls = []

    def decode(samples):
        calls.append(samples[0])
        if len(calls) == 2:
            raise RuntimeError("Interrupted")
        return {"language": "hi", "segments": [{"start": 0, "end": 1, "text": "speech"}]}

    with pytest.raises(RuntimeError):
        pipeline.decode_windows(list(range(500)), decode, sample_rate=10, checkpoint_dir=tmp_path)
    pipeline.decode_windows(list(range(500)), decode, sample_rate=10, checkpoint_dir=tmp_path)
    assert calls.count(0) == 1


def test_restart_skips_completed_sections_and_preserves_original_text(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "free_space", lambda minimum: 100)

    def extract(audio, wav, offset, length):
        wav.write_text(json.dumps({"offset": offset, "length": length}))

    monkeypatch.setattr(pipeline, "extract_audio_section", extract)
    calls = []

    def infer(wav):
        section = json.loads(wav.read_text())
        offset = section["offset"]
        calls.append(offset)
        if len(calls) == 2:
            raise RuntimeError("Simulated process crash")
        words = [{"start": x - offset, "end": x + 1 - offset, "word": f" original-{x}"}
                 for x in [5, 295, 300, 305, 595, 600, 605]
                 if offset <= x < offset + section["length"]]
        return {"language": "hi", "segments": [{"words": words}]}

    config = {"section_seconds": 300, "context_seconds": 5, "language": "hi"}
    audio_file = tmp_path / "source.webm"
    audio_file.write_bytes(b"source audio")
    audio = {"path": str(audio_file), "duration": 620, "title": "Lesson"}
    video = {"video_id": "N8vJ8RyQEes", "title": "Lesson"}
    with pytest.raises(RuntimeError, match="Simulated"):
        pipeline.transcribe_sections(audio, video, Path("model"), config, tmp_path, infer=infer)
    assert len(list(tmp_path.glob("jobs/*/*/part-*.json"))) == 1
    result = pipeline.transcribe_sections(audio, video, Path("model"), config, tmp_path, infer=infer)
    assert calls.count(0) == 1  # First section was not recomputed after the crash.
    text = " ".join(s["text"] for s in result["segments"])
    assert text == "original-5 original-295 original-300 original-305 original-595 original-600 original-605"
    # Altering model/language settings must create different checkpoints.
    changed = {**config, "language": "en"}
    assert pipeline.fingerprint(changed) != result["settings_id"]
    output = pipeline.finish_transcript(video, audio, result, config, lambda text: text.upper())
    assert output["segments"][0]["raw_text"].startswith("original")
    assert output["segments"][0]["text"].startswith("ORIGINAL")
    assert output["provenance"]["llm_rewritten"] is False
    assert output["review_required"] is True
    # A changed audio file must not reuse checkpoints from the previous source.
    audio_file.write_bytes(b"edited source audio")
    second = pipeline.transcribe_sections(audio, video, Path("model"), config, tmp_path, infer=infer)
    assert second["checkpoint_id"] != result["checkpoint_id"]
    assert second["audio_sha256"] != result["audio_sha256"]


def test_transliteration_preserves_english_and_negation():
    pytest.importorskip("uroman")
    convert = pipeline.romanizer()
    assert convert("Kadane C++ O(n) 123") == "Kadane C++ O(n) 123"
    text = convert("आप नहीं लीजिए, previous को same रखिए।")
    assert not pipeline.DEVANAGARI.search(text)
    assert "nahi" in text and "previous" in text and "same" in text


def test_download_retry_waits_and_then_resumes(monkeypatch):
    calls = []
    waits = []
    monkeypatch.setattr(pipeline.time, "sleep", waits.append)

    def operation():
        calls.append(1)
        if len(calls) < 3:
            raise OSError("Disconnected")
        return "downloaded"

    assert pipeline.retry_network(operation, "test") == "downloaded"
    assert waits == [5, 10]
    with pytest.raises(pipeline.UnavailableVideo):
        pipeline.retry_network(lambda: (_ for _ in ()).throw(pipeline.UnavailableVideo()), "private")


def test_chunks_overlap_without_losing_or_reordering_passages():
    from scripts.ingest import chunks
    source = {"segments": [{"start": n, "end": n + 20, "text": str(n)}
                           for n in range(0, 200, 20)]}
    passages = list(chunks(source))
    assert passages[0]["start_sec"] == 0 and passages[-1]["end_sec"] == 200
    assert all(a["start_sec"] < b["start_sec"] < a["end_sec"]
               for a, b in zip(passages, passages[1:]))


async def test_staged_index_preserves_live_revision_and_active_vectors(tmp_path, monkeypatch):
    import sys
    from scripts import ingest
    from backend import core
    source = tmp_path / "new"
    source.mkdir()
    (tmp_path / "data").mkdir()
    active = {"revision": "initial", "videos": [{"video_id": "N8vJ8RyQEes",
              "title": "Old lesson", "status": "indexed", "chunks": 1}]}
    live_manifest = tmp_path / "data/manifest.json"
    pipeline.atomic_json(live_manifest, active)
    transcript = {"video_id": "N8vJ8RyQEes", "title": "New lesson",
                  "segments": [{"start": 0, "end": 10, "text": "Original lecture words"}],
                  "provenance": {"source": "local audio; MLX Whisper large-v3"}}
    pipeline.atomic_json(source / "N8vJ8RyQEes.json", transcript)
    cache = tmp_path / "data/embedding-cache"
    cache.mkdir()
    pipeline.atomic_json(cache / "N8vJ8RyQEes-checkpoint.json", ["active-point"])
    writes = []

    async def embed(*args, **kwargs):
        return [0.1, 0.2]

    async def qdrant(path, body, **kwargs):
        writes.append((path, body))
        return {}

    monkeypatch.setattr(core, "ROOT", tmp_path)
    monkeypatch.setattr(core, "manifest", lambda: json.loads(live_manifest.read_text()))
    monkeypatch.setattr(core, "embed", embed)
    monkeypatch.setattr(core, "qdrant", qdrant)
    staged = tmp_path / "staged.json"
    monkeypatch.setattr(sys, "argv", ["ingest", "--source", str(source), "--limit", "0",
                                     "--manifest", str(staged), "--revision", "local-v1"])
    await ingest.main()
    assert pipeline.read_json(live_manifest) == active
    assert pipeline.read_json(staged)["videos"][0]["status"] == "indexed"
    assert pipeline.read_json(staged)["revision"] == "local-v1"
    assert all("active-point" not in json.dumps(body) for _, body in writes)
    points = [p for _, body in writes for p in body.get("points", []) if isinstance(p, dict)]
    assert points[0]["payload"]["corpus_revision"] == "local-v1"
    assert points[0]["payload"]["transcript_source"].startswith("local audio")


def test_memory_guard_waits_for_normal_pressure(tmp_path, monkeypatch):
    from types import SimpleNamespace
    levels = iter(["2", "4", "1"])
    waits = []
    monkeypatch.setattr(pipeline.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=next(levels)))
    monkeypatch.setattr(pipeline.time, "sleep", waits.append)
    pipeline.wait_for_memory(tmp_path)
    assert waits == [10, 10]
