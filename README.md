# RecallQ

Find where you learned it. Search a curated YouTube DSA playlist, jump to timestamped explanations, and request summaries grounded in the retrieved passages.

React + TypeScript + Vite, Python + FastAPI, Qdrant Cloud, and MiniLM embeddings. Gemini is used only for optional summaries with the visitor’s own key. Visitors need no Gemini key for search. Two matches are shown first, with Show more revealing up to five. Summaries use all retrieved top matches (up to five), including collapsed cards.

[Live demo](https://recallq-app.vercel.app) · [GitHub repository](https://github.com/the-ajay-panigrahi/recallq)

## Why I built it

During DSA revision, remembering an explanation is easier than remembering which video and timestamp it came from. RecallQ searches inside one indexed playlist so I can return to the relevant passage without replaying entire lectures.

## What it does

- Accepts English or Hinglish questions and retrieves relevant transcript passages.
- Shows two matches first, with up to five available through Show more.
- Opens the selected lecture at its passage timestamp, with an embedded player and YouTube link.
- Generates short, cited explanations only when requested, using the visitor's Gemini key.
- Supports temporary or remembered keys, responsive light/dark themes, and keyboard controls.

## Search flow

Question → FastAPI on Vercel → MiniLM embedding → Qdrant similarity search → relevance filtering → timestamped passages. Gemini is called only for optional summaries, not for embeddings or result cards. The model is bundled with the backend and runs on Vercel's CPU, not the visitor's laptop. This is retrieval over indexed transcripts, not training a new model.

## Run locally

Use Node 22+, uv, and Python 3.12.

```sh
npm ci
uv sync --python 3.12
cp .env.example .env
uv run python -m scripts.prepare_embeddings
uv run uvicorn backend.main:app --reload --port 8000
```

The backend can also be started with `npm run dev:api`. Keep it running. In another terminal, run `npm run dev` and open http://127.0.0.1:5173. Set the scoped QDRANT_URL, QDRANT_API_KEY, and QDRANT_COLLECTION in the ignored .env. A separate QDRANT_WRITE_KEY is needed only for offline indexing. Never commit keys or use a VITE_ prefix for secrets.

## Embeddings and search

Both documents and questions use the same pinned `sentence-transformers/all-MiniLM-L6-v2` model, recorded in backend/embedding-model.json. The ONNX encoder uses mean pooling and normalized 384-dimensional vectors. ONNX Runtime, tokenizers, and NumPy avoid a PyTorch dependency in the deployed application.

The model and tokenizer are downloaded and SHA-256 checked during the build, then bundled into the Python function. Search does not download a model, call an embedding API, or depend on the owner’s laptop. A warm Vercel instance reuses the loaded encoder. Hosting resource limits still apply.

The existing scoped Qdrant collection has 768 coordinates. The 384 MiniLM coordinates are followed by 384 zeroes for both documents and questions. This preserves cosine similarity exactly. Native dimension, model revision, chunk version, and corpus revision are recorded in payloads. Queries filter the active corpus revision and reject incompatible model metadata. Old Gemini embeddings are not used in MiniLM searches.

Candidates receive a small keyword-overlap boost when semantic scores are close. This improves explicit problem queries such as removing duplicates without replacing semantic retrieval. Weak-match status still uses the original semantic score.

Passages contain at most 220 text tokens plus at most 32 title tokens and two special tokens, avoiding MiniLM’s 256-token truncation. Long ASR segments are split at word boundaries and retain their original timestamps. Overlap uses complete pieces totaling up to 40 tokens. These are approximate passage timestamps, not verified word-level alignment.

## Transcript corpus and indexing

The November 14 audit found 126 playlist entries, one duplicate, and 125 unique videos. The index includes 121 video transcripts. See data/transcript-audit.json for missing links. Automatic recognition errors remain possible; the corpus has not been fully checked against the audio.

With source files in data/transcripts/:

```sh
uv run python -m scripts.prepare_embeddings
uv run python -m scripts.index_minilm
```

The script performs local inference without Gemini or OpenAI calls. Vectors are cached locally, completed videos are checkpointed, and deterministic IDs make upserts repeatable. A staged manifest and remote point-count verification precede promotion to data/manifest.json and deployment. Rerun after an interrupted operation to reuse completed work. Inspect work/minilm/result.json for measured timing. Raw transcripts, downloaded audio, models, and vector caches are excluded from Git.

The production app reports indexed coverage from data/manifest.json. Missing lessons remain pending. No additional transcription is running.

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

## Interface and credits

The interface uses self-hosted DM Sans, charcoal/cream themes, a responsive playlist cover, and a reduced-motion-aware crowd illustration. Text passages expand through an accessible button; new searches show two matches initially. Clearing the question returns to the library view.

The Lovable design guide from OpenDesignMD is preserved in DESIGN.md with its MIT notice. DM Sans is distributed under the SIL Open Font License in frontend/public/fonts/OFL.txt. MiniLM's Apache-2.0 license is in backend/MINILM-LICENSE.txt.

Free Skiper UI references: [SmoothInput](https://skiper-ui.com/v1/skiper106), [vertical hover expansion](https://skiper-ui.com/v1/skiper53), [crowd concept](https://skiper-ui.com/v1/skiper39), and [theme transitions](https://skiper-ui.com/v1/skiper26). Framer Motion animates the caret and result cards; the current theme reveal is a 450ms native circular View Transition. No Pro components are used, and the required Skiper attribution appears in the footer. Crowd artwork is [Open Peeps by Pablo Stanley](https://www.openpeeps.com/), distributed under CC0.

PROJECT_CONTEXT.md records product requirements and implementation decisions.

## Public-demo protection

Production has a Vercel Firewall rule named `RecallQ search protection`: POST requests to `/api/search` (including its trailing slash) allow 20 requests per IP per 60-second fixed window, then return HTTP 429. This runs before Python, so blocked requests do not run MiniLM or query Qdrant. People sharing a public IP share the allowance. The rule uses the Hobby plan's included rate-limiting feature; no external database or paid plan was added. This is burst protection, not a guarantee against a distributed attack.

Search results are cached for five minutes, with at most 128 entries per warm Python worker. Only exactly matching trimmed questions reuse an entry; changes to case, wording, result limit, corpus revision, model revision, or match threshold miss the cache. Concurrent identical requests within a worker share one search. Errors, Gemini summaries, and API keys are not cached. The cache disappears on cold starts and is not shared between Vercel workers. Its purpose is to reduce repeated work without adding infrastructure.

A limited request shows a short retry message and preserves the last usable results and video. New successful searches replace those results normally. The firewall is managed separately from deployments: inspect with `npx vercel firewall status` and review draft changes with `npx vercel firewall diff`. The source cache implementation is `backend/search_cache.py`.

Search editing keeps the last results labelled with their original question. Clearing the field (including whitespace-only text) cancels pending searches/summaries and returns to the initial library view. Aborted responses cannot restore cleared results.

Summary repair: use all retrieved top matches (up to five) as evidence even when only two cards are expanded. Gemini returns a structured status and individually cited sentences; the backend validates source numbers and renders the existing plain-text summary response. Partial but relevant evidence can produce a limited summary. Empty, malformed, blocked, or invalidly cited model output is a retryable provider error, not a false insufficient-evidence answer. Genuine lack of support still produces an honest no-evidence response.

Temporary Gemini HTTP 500/502/503/504 failures are retried once after a short delay; key, quota, and validation errors are not automatically retried.

Search normalizes common Hinglish study phrasing and compact terms such as HashMap before embedding. For example, “HashMap kab use karna hai?” becomes “when should I use hash map”. This small deterministic rewrite preserves negation, needs no Gemini call, and leaves the displayed question and summary language unchanged. It is not a general Hindi translator.
