from scripts.transcribe_baseline import completed
from scripts.transcribe import atomic_json


def test_only_a_complete_matching_transcript_is_reused(tmp_path):
    settings = {"model": "large-v3", "compute_type": "int8", "language": "hi"}
    target = tmp_path / "transcript.json"
    (tmp_path / "partial.jsonl").write_text('{"text":"unfinished"}\n')
    assert completed(target, settings) is None
    data = {"segments": [{"start": 0, "end": 1, "text": "speech"}],
            "provenance": {"settings": settings}}
    atomic_json(target, data)
    assert completed(target, settings) == data
    assert completed(target, {**settings, "language": "en"}) is None
