# RecallQ

Find where you learned it. Search a curated YouTube DSA playlist, jump to timestamped explanations, and request summaries grounded in the retrieved passages.

React + TypeScript + Vite, Python + FastAPI, Qdrant Cloud, and MiniLM embeddings. Gemini is used only for optional summaries with the visitor’s own key. Search needs no API key. Two matches are shown first, with Show more revealing up to five. Summaries use the currently visible passages.

[Live demo](https://recallq-app.vercel.app) · [GitHub repository](https://github.com/the-ajay-panigrahi/recallq)

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

## Attribution

The Lovable design guide from OpenDesignMD is preserved in DESIGN.md with its MIT notice. The supplied Camera Plain font was unavailable; the user requested an improved replacement, so DM Sans is self-hosted with its SIL Open Font License in frontend/public/fonts/OFL.txt. Warm charcoal is the default theme, and the original cream palette is available through the saved theme toggle. Native View Transitions and CSS animate the switch and icons, with reduced-motion and browser fallbacks. Motion reference: https://skiper-ui.com/v1/skiper26. No paid UI or animation dependency is used.

MiniLM model attribution and Apache-2.0 license are in backend/MINILM-LICENSE.txt. PROJECT_CONTEXT.md records product requirements and implementation decisions.


### Skiper UI refinements

Search uses the free Skiper UI SmoothInput (skiper106), adapted to the existing field without its demo control panel. Attribution: https://skiper-ui.com/v1/skiper106. Framer Motion drives the spring caret. Native input editing and selection are retained; touch, IME composition, and reduced-motion preferences use a native caret.

Theme changes use a 450ms circular View Transition originating at the toggle. There is no GIF loading, masking, or decoding. Reduced motion and unsupported browsers switch immediately. This replaces the GIF variant following the user's decision to prioritize smoothness.

The Open Peeps crowd sits between the hero copy and search. It retains pause/resume, offscreen suspension, reduced-motion support, dense sprites, and feathered canvas edges. Illustrations: https://www.openpeeps.com/ (CC0). Crowd concept: https://skiper-ui.com/v1/skiper39.

The side flower/scroll illustration is removed. Results retain five accessible cards. Their hover expansion is a reading-focused adaptation of https://skiper-ui.com/v1/skiper53, with spring layout movement, a gentle surface/shadow response, and an explicit Read full passage button for touch and keyboard. Staggered entry and movement respect reduced motion. No Pro components are used.

“Keep learning at your own pace.” sits above the headline. The shared main container is capped at 1440px with responsive gutters; the query stays within 840px and reading prose within 70 characters. The footer follows the main container width, with playlist, source, and back-to-top links.

Responsive refinement: the key dialog portal uses explicit overlay/content layers (100/101), above the page and filtered canvas. Short laptop screens place hero copy and the larger crowd side by side above search; smaller screens stack them. The middle header tagline is removed. Crowd uses balanced monochrome tones, and body/supporting text is larger. The dialog scrolls internally on small screens.

Search displays two results initially, with Show more revealing at most five. Low semantic scores and candidates too far below the leading match are removed; lower-scoring candidates also need meaningful query terms in their passage or title. This is a relevance heuristic, not a guarantee of correctness. Summary generation uses the currently visible passages. The bookshelf uses a linked cover image from the playlist's opening video.
