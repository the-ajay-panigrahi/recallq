from scripts.check_transcript import coverage, union


def test_interval_union_and_speech_coverage_do_not_double_count_overlap():
    merged = union([(0, 10), (8, 15), (20, 25)])
    assert merged == [[0, 15], [20, 25]]
    result = coverage([[5, 18], [22, 26]], merged)
    assert result["speech_seconds"] == 17
    assert result["covered_seconds"] == 13
    assert 0 <= result["fraction"] <= 1


def test_silent_audio_does_not_get_a_fabricated_accuracy_score():
    result = coverage([], [[0, 10]])
    assert result["fraction"] is None
