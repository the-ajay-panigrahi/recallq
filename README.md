# RecallQ

Find where you learned it. A React/TypeScript and FastAPI application for searching a curated YouTube DSA playlist, jumping to transcript passages, and requesting source-based summaries.

## Run locally

Requirements: Node 22+ and uv with Python 3.11+.

```sh
npm ci
uv sync --python 3.12
cp .env.example .env
```

Set QDRANT_URL and the read-only QDRANT_API_KEY in the ignored `.env`. For local ingestion also set GEMINI_INGEST_KEY and a separate collection-scoped QDRANT_WRITE_KEY. Never prefix a secret with VITE_ or commit `.env`.

```sh
uv run uvicorn backend.main:app --reload --port 8000
npm run dev
```

Open http://127.0.0.1:5173. Enter your own Gemini key through the UI. Temporary keys disappear on refresh; Remember on this device uses browser local storage. The backend receives and forwards each key to Google without storing it. The ingestion key is not an automatic public demo key.

## Index transcripts

The Qdrant collection must use 768-dimensional cosine vectors and have a keyword index on corpus_revision. The initial collection was created through the Qdrant UI. Serving uses a read-only collection key; ingestion uses a read/write collection key.

```sh
uv run python -m scripts.preflight
uv run python -m scripts.ingest --source /absolute/path/to/transcripts --limit 3
```

The script preserves raw source files locally, validates segment timings, caches embeddings, upserts deterministic IDs and removes superseded IDs from its own per-video checkpoint. Re-running completed work does not re-embed unchanged passages. Do not delete the cache/checkpoints during a partial reindex. Changing embedding models requires a compatible new collection, not mixing vectors.

Only three seed lessons are intended for the first live check. Audio verification is still pending. These are tutorial-supplied faster-whisper large-v3 transcripts; they contain recognition errors and are not certified accurate. The UI shows actual indexed coverage and this limitation. Raw transcripts and embedding caches are ignored. Review and curate further lessons before expanding the manifest. See PROJECT_CONTEXT.md for the dated missing-video audit.

## Checks

```sh
uv run pytest -q
npm run build
npx playwright install chromium
npm run test:e2e
```

Playwright tests run on desktop and mobile with explicitly mocked API/YouTube responses. They check temporary and remembered credentials, search-to-timestamp selection, summaries generated only on click, and preserving results on quota errors. They do not prove real video playback, speech accuracy, model quality or production capacity. Backend tests exercise errors, source resolution, timestamps and simultaneous key isolation. Run the live preflight separately.

## Deployment

Vercel detects backend.main:app through pyproject.toml. Its build command builds the Vite assets into dist; FastAPI mounts that directory after API routes. Keep ingestion off the server. Configure only runtime QDRANT_URL, read-only QDRANT_API_KEY, collection and model settings in Vercel. Do not deploy GEMINI_INGEST_KEY or the ingestion write key. All public search/summary calls require the visitor's key.

Initial deployment: https://recallq-three.vercel.app on Vercel Hobby, using Qdrant Free. No billing was enabled during setup. The index contains 118 passages across three seed videos, with 125 unique videos in the saved playlist listing. Generation uses gemini-3.5-flash-lite after the older default was unavailable for the new project. Actual access and quotas still vary by visitor.

The CLI deployment works. Automatic GitHub deployments are not connected yet; the Vercel GitHub integration needs account-side authorization. To deploy manually from this linked checkout, run `npx vercel --prod`. The local `.vercel` directory is ignored.

## Design and attribution

The user selected the Lovable guide from OpenDesignMD. Its original MIT notice and reference are preserved in DESIGN.md. Camera Plain Variable was not supplied; the implementation currently uses the guide's system font fallback. No custom font license is implied.

The transcript data comes from the Padho with Pratyush DSA tutorial reference. RecallQ's application and pipeline are independently implemented. Source excerpts remain distinct from generated summaries. PROJECT_CONTEXT.md contains the requirements and the transcript audit; DESIGN.md contains the visual authority.


## Initial implementation decisions

- Keys use an explicit request header and a per-request HTTP client, avoiding shared mutable visitor credentials.
- Gemini embeddings keep the Python service lightweight enough for serverless deployment; Qdrant holds all source passages needed at runtime.
- Search and generation are separate operations. Source IDs are loaded from the server index before summarization; grouped references such as [1, 2] are normalized and checked before display.
- The production build uses an explicit FastAPI framework configuration and a cross-platform npm lockfile. The first deployment caught missing Linux optional dependencies; regenerating the lockfile from an empty install context fixed it.
- Transcript audio accuracy, a labeled retrieval benchmark, full-playlist ingestion, and the requested custom font remain unfinished. No accuracy or capacity claims are made.
