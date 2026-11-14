import json

import pytest

from backend import core


@pytest.fixture
def sources(monkeypatch):
    monkeypatch.setattr(core, "manifest", lambda: {"revision": "v1"})
    payload = {"corpus_revision":"v1", "source_id":"one", "start_sec":10,"end_sec":60,
               "video_id":"N8vJ8RyQEes","video_title":"Kadane","text":"Keep the best ending sum."}
    async def points(*args, **kwargs):
        return {"result":[{"id":"one","payload":payload}]}
    monkeypatch.setattr(core,"qdrant",points)


def model_reply(monkeypatch, text, finish="STOP"):
    async def model(*args, **kwargs):
        assert kwargs["body"]["generationConfig"]["responseMimeType"] == "application/json"
        return {"candidates":[{"finishReason":finish,"content":{"parts":[{"text":text}]}}]}
    monkeypatch.setattr(core,"request_json",model)


async def test_structured_answer_resolves_and_deduplicates_citations(sources,monkeypatch):
    model_reply(monkeypatch,json.dumps({"status":"answered","sentences":[
        {"text":"Keep the best ending sum.","sources":[1,1]},
        {"text":"These passages cover this step only.","sources":[1]}]}))
    result=await core.summarize("Kadane","v1",["one"],"test-key")
    assert result["status"]=="answered"
    assert result["summary"].count("[1]")==2
    assert len(result["citations"])==1


@pytest.mark.parametrize("reply", ["", "not json", "[]", json.dumps({"status":"answered","sentences":[]}),
    json.dumps({"status":"answered","sentences":[{"text":"Answer","sources":[]}]}),
    json.dumps({"status":"answered","sentences":[{"text":"Answer","sources":[2]}]}),
    json.dumps({"status":"answered","sentences":[{"text":"Answer","sources":[True]}]}),
    json.dumps({"status":"answered","sentences":[{"text":"Answer [99]","sources":[1]}]})])
async def test_model_failures_are_errors_not_false_insufficient_evidence(sources,monkeypatch,reply):
    model_reply(monkeypatch,reply)
    with pytest.raises(core.ServiceError) as exc:
        await core.summarize("Kadane","v1",["one"],"test-key")
    assert exc.value.status==503
    assert exc.value.code in {"SUMMARY_EMPTY","SUMMARY_FORMAT"}


async def test_only_explicit_no_evidence_is_reported_as_no_evidence(sources,monkeypatch):
    model_reply(monkeypatch,json.dumps({"status":"insufficient_evidence","sentences":[]}))
    result=await core.summarize("Unrelated question","v1",["one"],"test-key")
    assert result["status"]=="insufficient_evidence"
    assert not result["citations"]


async def test_truncated_model_output_is_retryable(sources,monkeypatch):
    model_reply(monkeypatch,'{"status":',finish="MAX_TOKENS")
    with pytest.raises(core.ServiceError) as exc:
        await core.summarize("Kadane","v1",["one"],"test-key")
    assert exc.value.code=="SUMMARY_INCOMPLETE"
