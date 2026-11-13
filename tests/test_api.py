import asyncio
from uuid import uuid4
import httpx
import pytest
from backend.main import app
from backend import core


@pytest.fixture
def client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')


async def test_key_required_and_validation_redacts_input(client):
    async with client:
        assert (await client.post('/api/search', json={'query': 'DP'})).status_code == 401
        response = await client.post('/api/search', json={'query': ' ', 'secret': 'do-not-echo'}, headers={'X-Gemini-API-Key': 'private-key'})
        assert response.status_code == 422
        assert 'private-key' not in response.text and 'do-not-echo' not in response.text


async def test_simultaneous_keys_never_cross_requests(client, monkeypatch):
    async def fake(query, limit, key):
        await asyncio.sleep(.01)
        assert key == f'key-{query}'
        return {'results': [], 'query': query}
    monkeypatch.setattr(core, 'search', fake)
    async with client:
        responses = await asyncio.gather(*[client.post('/api/search', json={'query': str(i)}, headers={'X-Gemini-API-Key': f'key-{i}'}) for i in range(10)])
    assert all(r.status_code == 200 for r in responses)


async def test_unknown_and_stale_summary_sources(client, monkeypatch):
    monkeypatch.setattr(core, 'manifest', lambda: {'revision': 'v1'})
    async def empty(*args, **kwargs): return {'result': []}
    monkeypatch.setattr(core, 'qdrant', empty)
    async with client:
        body = {'query': 'DP', 'corpus_revision': 'old', 'source_ids': [str(uuid4())]}
        assert (await client.post('/api/summarize', json=body, headers={'X-Gemini-API-Key': 'key'})).status_code == 409
        body['corpus_revision'] = 'v1'
        assert (await client.post('/api/summarize', json=body, headers={'X-Gemini-API-Key': 'key'})).status_code == 404


def test_playback_link_uses_indexed_time_and_validated_id():
    result = core.present({'start_sec': 340, 'end_sec': 410, 'video_id': 'N8vJ8RyQEes', 'video_title': 'Kadane', 'source_id': 'id', 'text': 'excerpt'})
    assert result['playback_sec'] == 335
    assert result['watch_url'].endswith('&t=335s')
    with pytest.raises(core.ServiceError):
        core.present({'start_sec': 0, 'end_sec': 1, 'video_id': '<script>'})
