import { StrictMode, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import * as Dialog from "@radix-ui/react-dialog";
import {
  ArrowRight,
  ArrowUpRight,
  BookOpen,
  Check,
  ChevronDown,
  Clock3,
  Eye,
  EyeOff,
  KeyRound,
  LoaderCircle,
  Play,
  Search,
  Sparkles,
  X,
} from "lucide-react";
import "./style.css";

type Source = {
  source_id: string;
  video_id: string;
  video_title: string;
  start_sec: number;
  end_sec: number;
  playback_sec: number;
  excerpt: string;
  watch_url: string;
};
type Results = {
  query: string;
  corpus_revision: string;
  match_state: "matches" | "weak" | "none";
  results: Source[];
};
type Summary = {
  status: string;
  summary: string;
  citations: (Source & { number: number })[];
};
type Corpus = {
  title: string;
  indexed_videos: number;
  total_videos: number;
  quality_status: string;
  index_connected: boolean;
};
const STORAGE = "recallq.gemini-key";
const examples = [
  "Sliding window intuition",
  "Memoization vs tabulation",
  "Kadane’s algorithm",
  "HashMap kab use karna hai?",
];
function readKey() {
  try {
    return localStorage.getItem(STORAGE) || "";
  } catch {
    return "";
  }
}
function time(n: number) {
  const sec = Math.floor(n);
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
}
async function api<T>(
  path: string,
  key: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(`/api/${path}`, {
    method: body ? "POST" : "GET",
    headers: body
      ? { "Content-Type": "application/json", "X-Gemini-API-Key": key }
      : {},
    body: body ? JSON.stringify(body) : undefined,
    signal,
  });
  const data = await response.json();
  if (!response.ok)
    throw new Error(
      data.error?.message || "Something went wrong. Please try again.",
    );
  return data;
}
function App() {
  const [key, setKey] = useState(readKey),
    [draft, setDraft] = useState(""),
    [remember, setRemember] = useState(Boolean(readKey())),
    [showKey, setShowKey] = useState(false),
    [open, setOpen] = useState(false),
    [pending, setPending] = useState(false);
  const [query, setQuery] = useState(""),
    [results, setResults] = useState<Results | null>(null),
    [selected, setSelected] = useState<Source | null>(null),
    [count, setCount] = useState(5),
    [corpus, setCorpus] = useState<Corpus | null>(null);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [summary, setSummary] = useState<Summary | null>(null),
    [summaryBusy, setSummaryBusy] = useState(false),
    [summaryError, setSummaryError] = useState(""),
    [storageWarning, setStorageWarning] = useState("");
  const [playerStarted, setPlayerStarted] = useState(false);
  const searchController = useRef<AbortController | null>(null),
    summaryController = useRef<AbortController | null>(null),
    keyTrigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    api<Corpus>("corpus", "")
      .then(setCorpus)
      .catch(() =>
        setError(
          "Could not load the lecture library. Please try again shortly.",
        ),
      );
    return () => {
      searchController.current?.abort();
      summaryController.current?.abort();
    };
  }, []);
  async function search(text = query, credential = key) {
    if (!text.trim()) return;
    setQuery(text);
    if (!credential) {
      setPending(true);
      setDraft("");
      setOpen(true);
      return;
    }
    searchController.current?.abort();
    summaryController.current?.abort();
    const controller = new AbortController();
    searchController.current = controller;
    setBusy(true);
    setError("");
    setSummary(null);
    setSummaryError("");
    setSummaryBusy(false);
    setResults(null);
    setSelected(null);
    setCount(5);
    setPlayerStarted(false);
    try {
      const found = await api<Results>(
        "search",
        credential,
        { query: text.trim(), limit: 20 },
        controller.signal,
      );
      if (!controller.signal.aborted) {
        setResults(found);
        setSelected(found.results[0] || null);
      }
    } catch (e) {
      if (!controller.signal.aborted) setError((e as Error).message);
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }
  function saveKey() {
    const clean = draft.trim();
    if (!clean) return;
    setStorageWarning("");
    try {
      if (remember) localStorage.setItem(STORAGE, clean);
      else localStorage.removeItem(STORAGE);
    } catch {
      setStorageWarning(
        "Browser storage is unavailable. Your key is temporary for this visit.",
      );
      setRemember(false);
    }
    setKey(clean);
    setDraft("");
    setOpen(false);
    if (pending) {
      setPending(false);
      void search(query, clean);
    }
  }
  function forget() {
    searchController.current?.abort();
    summaryController.current?.abort();
    setKey("");
    setDraft("");
    setRemember(false);
    setPending(false);
    setBusy(false);
    setSummaryBusy(false);
    try {
      localStorage.removeItem(STORAGE);
    } catch {
      /* Temporary mode remains usable. */
    }
    setOpen(false);
  }
  function select(source: Source) {
    setSelected(source);
    setPlayerStarted(true);
  }
  async function summarize() {
    if (!results || !key) return;
    summaryController.current?.abort();
    const controller = new AbortController();
    summaryController.current = controller;
    setSummaryBusy(true);
    setSummaryError("");
    try {
      const answer = await api<Summary>(
        "summarize",
        key,
        {
          query: results.query,
          corpus_revision: results.corpus_revision,
          source_ids: results.results.slice(0, 6).map((r) => r.source_id),
        },
        controller.signal,
      );
      if (!controller.signal.aborted) setSummary(answer);
    } catch (e) {
      if (!controller.signal.aborted) setSummaryError((e as Error).message);
    } finally {
      if (!controller.signal.aborted) setSummaryBusy(false);
    }
  }
  function summaryText() {
    return summary?.summary.split(/(\[\d+\])/g).map((part, i) => {
      const citation = summary.citations.find((c) => `[${c.number}]` === part);
      return citation ? (
        <button
          className="citation"
          key={i}
          onClick={() => select(citation)}
          aria-label={`Play source ${citation.number}`}
        >
          {part}
        </button>
      ) : (
        part
      );
    });
  }
  return (
    <>
      <header className="header">
        <a className="brand" href="/" aria-label="RecallQ home">
          <span className="brand-icon">
            <BookOpen size={19} />
          </span>
          Recall<span>Q</span>
        </a>
        <span className="header-note">
          A little less searching. A little more learning.
        </span>
        <button
          ref={keyTrigger}
          className="key-button"
          onClick={() => {
            setDraft(key);
            setPending(false);
            setOpen(true);
          }}
        >
          <KeyRound size={15} />
          <span>{key ? "Key connected" : "Connect Gemini"}</span>
          {key && <span className="status-dot" />}
        </button>
      </header>
      <main>
        <section className={`intro ${results ? "compact" : ""}`}>
          <div className="eyebrow">
            <span className="tiny-line" /> YOUR LECTURES, WITH A WAY BACK
          </div>
          <h1>
            Find where you
            <br className="desktop-break" />{" "}
            <span className="soft">learned it.</span>
          </h1>
          <p>
            That concept you remember. That explanation you need.
            <br /> Find the right moment in your DSA lectures.
          </p>
        </section>
        <section className="search-section" aria-label="Search lectures">
          <form
            className="search-box"
            onSubmit={(e) => {
              e.preventDefault();
              void search();
            }}
          >
            <Search className="search-icon" size={21} />
            <label className="sr-only" htmlFor="query">
              Search lectures
            </label>
            <input
              id="query"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="What would you like to revisit?"
              maxLength={1000}
            />
            <button
              className="primary search-submit"
              aria-label="Find a moment"
              disabled={busy || !query.trim()}
              type="submit"
            >
              {busy ? (
                <LoaderCircle className="spin" size={18} />
              ) : (
                <>
                  <span>Find a moment</span>
                  <ArrowRight size={17} />
                </>
              )}
            </button>
          </form>
          <div className="search-help">
            <span>Ask in English or Hinglish</span>
            <span>
              <KeyRound size={12} />
              Your key. Your quota.
            </span>
          </div>
          {!results && !busy && (
            <div className="suggestions">
              {examples.map((text) => (
                <button key={text} onClick={() => void search(text)}>
                  {text}
                  <ArrowUpRight size={12} />
                </button>
              ))}
            </div>
          )}
        </section>
        {storageWarning && (
          <p className="notice" role="status">
            {storageWarning}
          </p>
        )}
        {error && (
          <div className="notice error" role="alert">
            {error}
            <button
              onClick={() => {
                setDraft(key);
                setOpen(true);
              }}
            >
              Key settings <ArrowUpRight size={14} />
            </button>
          </div>
        )}
        {busy && (
          <div className="searching" role="status">
            <LoaderCircle className="spin" size={22} />
            <h2>Looking through the lectures</h2>
            <p>Finding passages that match your question.</p>
            <div className="skeleton" />
            <div className="skeleton shorter" />
          </div>
        )}
        {results && (
          <section className="workspace" aria-label="Search workspace">
            <div className="results-column">
              <div className="section-heading">
                <h2>
                  {results.results.length
                    ? "Moments to revisit"
                    : "No matching moments"}
                </h2>
                <span>{results.results.length} results</span>
              </div>
              <p className="result-query">For “{results.query}”</p>
              {results.match_state !== "matches" && (
                <p className="notice">
                  {results.match_state === "weak"
                    ? "These matches may be less relevant. Check the passages before relying on them."
                    : "We could not find enough support in the indexed lectures. Try another topic or phrase."}
                </p>
              )}
              {results.results.slice(0, count).map((r, i) => (
                <article
                  className={`result ${selected?.source_id === r.source_id ? "selected" : ""}`}
                  key={r.source_id}
                >
                  <div className="result-top">
                    <span className="result-number">
                      {String(i + 1).padStart(2, "0")}
                    </span>
                    <button
                      className="timestamp"
                      onClick={() => select(r)}
                      aria-label={`Play ${r.video_title} at ${time(r.playback_sec)}`}
                    >
                      <Play size={11} fill="currentColor" />
                      {time(r.playback_sec)}
                    </button>
                  </div>
                  <h3>
                    <button onClick={() => select(r)}>{r.video_title}</button>
                  </h3>
                  <p>{r.excerpt}</p>
                  <div className="result-bottom">
                    <span>
                      <Clock3 size={12} />
                      Passage {time(r.start_sec)}–{time(r.end_sec)}
                    </span>
                    {selected?.source_id === r.source_id && (
                      <span>
                        <Check size={12} />
                        Selected
                      </span>
                    )}
                  </div>
                </article>
              ))}
              {count < results.results.length && (
                <button
                  className="show-more"
                  onClick={() => setCount((c) => c + 5)}
                >
                  Show more moments <ChevronDown size={15} />
                </button>
              )}
            </div>
            <aside className="player-column">
              <div className="player-sticky">
                <div className="section-heading">
                  <h2>The original explanation</h2>
                  <span>FROM THE LECTURE</span>
                </div>
                {selected && (
                  <>
                    <div className="video-frame">
                      {playerStarted ? (
                        <iframe
                          key={`${selected.video_id}:${selected.playback_sec}`}
                          title="Selected lecture video"
                          src={`https://www.youtube.com/embed/${selected.video_id}?start=${selected.playback_sec}&autoplay=1&rel=0`}
                          allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
                          referrerPolicy="strict-origin-when-cross-origin"
                          allowFullScreen
                        />
                      ) : (
                        <button
                          className="video-placeholder"
                          onClick={() => setPlayerStarted(true)}
                          aria-label="Play selected lecture"
                        >
                          <span className="play-circle">
                            <Play size={24} fill="currentColor" />
                          </span>
                          <span>Revisit the explanation</span>
                          <small>Starts at {time(selected.playback_sec)}</small>
                        </button>
                      )}
                    </div>
                    <div className="video-caption">
                      <h3>{selected.video_title}</h3>
                      <a
                        href={selected.watch_url}
                        target="_blank"
                        rel="noreferrer"
                      >
                        Open on YouTube <ArrowUpRight size={13} />
                      </a>
                    </div>
                  </>
                )}
                {results.results.length > 0 && (
                  <div className="summary-card">
                    <div className="summary-heading">
                      <span>
                        <Sparkles size={16} />
                        From the lectures
                      </span>
                      <span className="small-label">OPTIONAL</span>
                    </div>
                    {!summary && (
                      <p>
                        A short explanation, grounded in these passages.
                        <br />
                        Only generated when you ask.
                      </p>
                    )}
                    {summary && (
                      <div className="summary-text">{summaryText()}</div>
                    )}
                    {summaryError && (
                      <p className="error" role="alert">
                        {summaryError}
                      </p>
                    )}
                    <button
                      className="secondary"
                      disabled={summaryBusy}
                      onClick={() => void summarize()}
                    >
                      {summaryBusy ? (
                        <LoaderCircle size={15} className="spin" />
                      ) : (
                        <Sparkles size={15} />
                      )}{" "}
                      {summaryBusy
                        ? "Reading the passages…"
                        : summary
                          ? "Summarize again"
                          : "Summarize these moments"}
                    </button>
                  </div>
                )}
                <p className="player-tip">
                  <BookOpen size={14} />
                  Timestamps point to transcript passages. A few seconds of
                  context help the explanation make sense.
                </p>
              </div>
            </aside>
          </section>
        )}
        {!results && !busy && (
          <section className="library">
            <div className="library-heading">
              <span>
                <BookOpen size={17} /> ON YOUR BOOKSHELF
              </span>
              <span>01 PLAYLIST</span>
            </div>
            <div className="library-body">
              <div className="course-art" aria-hidden="true">
                <span>DSA</span>
                <small>PATTERNS & PRACTICE</small>
                <div className="course-lines">
                  <i />
                  <i />
                  <i />
                </div>
              </div>
              <div className="course-info">
                <span className="small-label">PADHO WITH PRATYUSH</span>
                <h2>One playlist. A clearer way back.</h2>
                <p>
                  Arrays, sliding windows, graphs, dynamic programming.
                  <br />
                  Search inside the explanations, beyond the video titles.
                </p>
                <div className="course-meta">
                  <span>
                    <Clock3 size={14} />
                    {corpus
                      ? `${corpus.indexed_videos} of ${corpus.total_videos} videos indexed`
                      : "Loading lecture library…"}
                  </span>
                  <a
                    href="https://www.youtube.com/playlist?list=PLbJhGqY-mq47k_WLUtzVjmarUm1EuXPj2"
                    target="_blank"
                    rel="noreferrer"
                  >
                    View playlist <ArrowUpRight size={13} />
                  </a>
                </div>
              </div>
            </div>
            {corpus && <p className="quality-note">{corpus.quality_status}</p>}
          </section>
        )}
        <footer>
          <span>
            RecallQ <span className="footer-divider">/</span> Made for the
            moments worth revisiting.
          </span>
          <span>Answers belong to the lectures.</span>
        </footer>
      </main>
      <Dialog.Root
        open={open}
        onOpenChange={(value) => {
          setOpen(value);
          if (!value) {
            setDraft("");
            setPending(false);
          }
        }}
      >
        <Dialog.Portal>
          <Dialog.Overlay className="dialog-overlay" />
          <Dialog.Content
            className="dialog-content"
            onCloseAutoFocus={(e) => {
              e.preventDefault();
              keyTrigger.current?.focus();
            }}
          >
            <Dialog.Close
              className="dialog-close"
              aria-label="Close key settings"
            >
              <X size={18} />
            </Dialog.Close>
            <div className="key-emblem">
              <KeyRound size={22} />
            </div>
            <Dialog.Title>Bring your own Gemini key</Dialog.Title>
            <Dialog.Description>
              One small setup, then back to learning. Requests use your Gemini
              quota.
            </Dialog.Description>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                saveKey();
              }}
            >
              <label htmlFor="gemini-key">Gemini API key</label>
              <div className="key-input">
                <input
                  id="gemini-key"
                  type={showKey ? "text" : "password"}
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  autoComplete="off"
                  placeholder="Paste your API key"
                  maxLength={512}
                />
                <button
                  type="button"
                  aria-label={showKey ? "Hide key" : "Show key"}
                  onClick={() => setShowKey((s) => !s)}
                >
                  {showKey ? <EyeOff size={17} /> : <Eye size={17} />}
                </button>
              </div>
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={remember}
                  onChange={(e) => {
                    setRemember(e.target.checked);
                    if (!e.target.checked) {
                      try {
                        localStorage.removeItem(STORAGE);
                      } catch {
                        /* Storage may be unavailable. */
                      }
                    }
                  }}
                />
                Remember on this device
              </label>
              <p className="key-privacy">
                {remember
                  ? "Saved in this browser’s local storage."
                  : "Temporary by default. Refreshing clears the key."}{" "}
                Our backend forwards your key to Google without saving it.
                Browser storage can be read by scripts running on this site.
              </p>
              <div className="dialog-actions">
                {key && (
                  <button
                    type="button"
                    className="text-button"
                    onClick={forget}
                  >
                    Forget key
                  </button>
                )}
                <button
                  className="primary"
                  disabled={!draft.trim()}
                  type="submit"
                >
                  Continue <ArrowRight size={16} />
                </button>
              </div>
            </form>
            <a
              className="get-key"
              href="https://aistudio.google.com/apikey"
              target="_blank"
              rel="noreferrer"
            >
              Get a Gemini API key <ArrowUpRight size={13} />
            </a>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </>
  );
}
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
