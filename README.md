# RecallQ

Find where you learned it. Search a curated YouTube DSA playlist, jump to timestamped explanations, and request summaries grounded in the retrieved passages.

React + TypeScript + Vite, Python + FastAPI, Qdrant Cloud, and MiniLM embeddings. Gemini is used only for optional summaries with the visitor’s own key. Search needs no API key.

Live: https://recallq-app.vercel.app

## Run locally

Use Node 22+, uv, and Python 3.12.

```sh
npm ci
uv sync --python 3.12
cp .env.example .env
uv run python -m scripts.prepare_embeddings
uv run uvicorn backend.main:app --reload --port 8000
```

In another terminal, run `npm run dev` and open http://127.0.0.1:5173. Set the scoped QDRANT_URL, QDRANT_API_KEY, and QDRANT_COLLECTION in the ignored .env. A separate QDRANT_WRITE_KEY is needed only for offline indexing. Never commit keys or use a VITE_ prefix for secrets.

## Embeddings and search

Both documents and questions use the same pinned `sentence-transformers/all-MiniLM-L6-v2` model, recorded in backend/embedding-model.json. The ONNX encoder uses mean pooling and normalized 384-dimensional vectors. ONNX Runtime, tokenizers, and NumPy avoid a PyTorch dependency in the deployed application.

The model and tokenizer are downloaded and SHA-256 checked during the build, then bundled into the Python function. Search does not download a model, call an embedding API, or depend on the owner’s laptop. A warm Vercel instance reuses the loaded encoder. Hosting resource limits still apply.

The existing scoped Qdrant collection has 768 coordinates. The 384 MiniLM coordinates are followed by 384 zeroes for both documents and questions. This preserves cosine similarity exactly. Native dimension, model revision, chunk version, and corpus revision are recorded in payloads. Queries filter the active corpus revision and reject incompatible model metadata. Old Gemini embeddings are not used in MiniLM searches.

Candidates receive a small keyword-overlap boost when semantic scores are close. This improves explicit problem queries such as removing duplicates without replacing semantic retrieval. Weak-match status still uses the original semantic score.

Passages contain at most 220 text tokens plus at most 32 title tokens and two special tokens, avoiding MiniLM’s 256-token truncation. Long ASR segments are split at word boundaries and retain their original timestamps. Overlap uses complete pieces totaling up to 40 tokens. These are approximate passage timestamps, not verified word-level alignment.

## Transcript corpus and indexing

The November 14 audit found 126 playlist entries, one duplicate, and 125 unique videos. The tutorial repository contains 121 matching transcripts, copied byte-for-byte into ignored data/transcripts/. Five older prefix-sum lessons outside the current playlist were not imported. See data/transcript-audit.json for missing links. Transcript recognition errors remain possible; no full-corpus speech-accuracy claim is made.

With source files in data/transcripts/:

```sh
uv run python -m scripts.prepare_embeddings
uv run python -m scripts.index_minilm
```

The script performs local inference without Gemini or OpenAI calls. Vectors are cached locally, completed videos are checkpointed, and deterministic IDs make upserts repeatable. A staged manifest and remote point-count verification precede promotion to data/manifest.json and deployment. Rerun after an interrupted operation to reuse completed work. Inspect work/minilm/result.json for measured timing. Raw transcripts, downloaded audio, models, and vector caches are excluded from Git.

The production app reports actual indexed coverage from data/manifest.json. Missing videos remain pending, and the tutorial’s RAG demonstration is deliberately excluded. No additional transcription is running.

One separate local Lecture 4 transcription took 83m13s for 30m10s of audio using faster-whisper large-v3. Six sampled comparisons did not establish a consistent improvement over the tutorial transcript, so a full-playlist transcription run was rejected. Older MLX experiments and the Gemini ingestion script remain development references, not the active workflow.

## Optional summaries and key handling

Only Summarize requires a Gemini key. Temporary keys stay in page memory; Remember on this device opts into browser local storage. Forget key removes both. The backend forwards each summary key to Google without persisting it. Keys never enter shared model state, Qdrant payloads, or URLs. Local storage is readable by scripts on the same origin and privileged extensions.

Summaries use only selected source passages and validated citations. They are not guaranteed factually correct. Gemini generation quotas are separate from free local embedding inference. There is no authentication or private per-user corpus in this MVP.

## Checks

```sh
uv run pytest -q
npm run build
npx playwright install chromium
npm run test:e2e
uv run python -m scripts.preflight
```

Unit checks cover token budgets, preservation of long segments, padding-independent embeddings, cosine preservation, model isolation, credentials, and source resolution. Playwright covers desktop/mobile search without a key, summaries prompted only on click, temporary/remembered keys, thumbnails and playback URLs, and quota errors preserving search results. Mocked UI tests do not prove real YouTube playback or transcription accuracy. Real retrieval and live deployment checks are recorded separately.

## Deployment

Vercel serves the Vite build and FastAPI API together. The build prepares pinned MiniLM assets; the Python function runs query embeddings on Vercel’s CPU. Qdrant remains external. Configure only the collection-scoped read key and runtime settings in Vercel; never deploy the write key or an owner Gemini key.

```sh
npx vercel --prod
```

The project uses Vercel Hobby and Qdrant Free. No billing was enabled. CLI deployments work; automatic GitHub deployments require the account’s GitHub integration. The root .vercel directory is ignored.

## Attribution

The Lovable design guide from OpenDesignMD is preserved in DESIGN.md with its MIT notice. The supplied font was unavailable, so the guide’s system fallback is used.

Transcript source: https://github.com/perryvegehan/padho_with_pratyush_Ai_Enginner. Application code and processing tools are independently implemented. MiniLM model attribution and Apache-2.0 license are in backend/MINILM-LICENSE.txt. PROJECT_CONTEXT.md records product requirements and implementation decisions.
