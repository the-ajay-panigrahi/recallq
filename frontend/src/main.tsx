import { StrictMode, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { flushSync } from "react-dom";
import * as Dialog from "@radix-ui/react-dialog";
import {
  ArrowRight,
  ArrowUpRight,
  BookOpen,
  Check,
  Clock3,
  Eye,
  EyeOff,
  KeyRound,
  LoaderCircle,
  Play,
  Search,
  Sparkles,
  Sun,
  Moon,
  X,
} from "lucide-react";
import "./style.css";
import { SmoothInput } from "./components/SmoothInput";
import { motion, useReducedMotion } from "framer-motion";
import { Crowd } from "./components/Crowd";

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
const THEME_STORAGE = "recallq.theme";
const RESULT_LIMIT = 5;
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
      ? {
          "Content-Type": "application/json",
          ...(key ? { "X-Gemini-API-Key": key } : {}),
        }
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
  const [theme, setTheme] = useState<"light" | "dark">(() =>
    document.documentElement.dataset.theme === "light" ? "light" : "dark",
  );
  const themeTransition = useRef<ViewTransition | null>(null);
  const reducedMotion = useReducedMotion();
  function toggleTheme(event: React.MouseEvent<HTMLButtonElement>) {
    const next = theme === "dark" ? "light" : "dark";
    const root = document.documentElement;
    const change = () => {
      flushSync(() => setTheme(next));
      root.dataset.theme = next;
      document
        .querySelector('meta[name="theme-color"]')
        ?.setAttribute("content", next === "dark" ? "#1c1c1c" : "#f7f4ed");
      try {
        localStorage.setItem(THEME_STORAGE, next);
      } catch {
        // Theme switching still works when browser storage is blocked.
      }
    };
    if (
      !document.startViewTransition ||
      matchMedia("(prefers-reduced-motion: reduce)").matches
    ) {
      change();
      return;
    }
    const bounds = event.currentTarget.getBoundingClientRect();
    const x = bounds.left + bounds.width / 2;
    const y = bounds.top + bounds.height / 2;
    const radius = Math.hypot(
      Math.max(x, innerWidth - x),
      Math.max(y, innerHeight - y),
    );
    root.style.setProperty("--theme-x", `${x}px`);
    root.style.setProperty("--theme-y", `${y}px`);
    root.style.setProperty("--theme-radius", `${radius}px`);
    themeTransition.current?.skipTransition();
    root.classList.add("theme-transition");
    try {
      const transition = document.startViewTransition(change);
      themeTransition.current = transition;
      void transition.ready.catch(() => {});
      void transition.finished
        .catch(() => {})
        .finally(() => {
          if (themeTransition.current === transition) {
            root.classList.remove("theme-transition");
            themeTransition.current = null;
          }
        });
    } catch {
      root.classList.remove("theme-transition");
      change();
    }
  }
  const [key, setKey] = useState(readKey),
    [draft, setDraft] = useState(""),
    [remember, setRemember] = useState(Boolean(readKey())),
    [showKey, setShowKey] = useState(false),
    [open, setOpen] = useState(false),
    [pending, setPending] = useState(false);
  const [showAllResults, setShowAllResults] = useState(false);
  const [query, setQuery] = useState(""),
    [results, setResults] = useState<Results | null>(null),
    [selected, setSelected] = useState<Source | null>(null),
    [corpus, setCorpus] = useState<Corpus | null>(null);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [summary, setSummary] = useState<Summary | null>(null),
    [summaryBusy, setSummaryBusy] = useState(false),
    [summaryError, setSummaryError] = useState(""),
    [storageWarning, setStorageWarning] = useState("");
  const [playerStarted, setPlayerStarted] = useState(false);
  const [expandedPassage, setExpandedPassage] = useState<string | null>(null);
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
  async function search(text = query) {
    if (!text.trim()) return;
    setQuery(text);
    searchController.current?.abort();
    summaryController.current?.abort();
    const controller = new AbortController();
    searchController.current = controller;
    setShowAllResults(false);
    setBusy(true);
    setError("");
    setSummary(null);
    setSummaryError("");
    setSummaryBusy(false);
    setResults(null);
    setSelected(null);
    setPlayerStarted(false);
    try {
      const found = await api<Results>(
        "search",
        "",
        { query: text.trim(), limit: RESULT_LIMIT },
        controller.signal,
      );
      if (!controller.signal.aborted) {
        const topResults = {
          ...found,
          results: found.results.slice(0, RESULT_LIMIT),
        };
        setResults(topResults);
        setSelected(topResults.results[0] || null);
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
      void summarize(clean);
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
  async function summarize(credential = key) {
    if (!results) return;
    if (!credential) {
      setPending(true);
      setDraft("");
      setOpen(true);
      return;
    }
    summaryController.current?.abort();
    const controller = new AbortController();
    summaryController.current = controller;
    setSummaryBusy(true);
    setSummaryError("");
    try {
      const answer = await api<Summary>(
        "summarize",
        credential,
        {
          query: results.query,
          corpus_revision: results.corpus_revision,
          source_ids: results.results
            .slice(0, showAllResults ? RESULT_LIMIT : 2)
            .map((r) => r.source_id),
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
        <div className="header-actions">
          <button
            className="theme-toggle"
            onClick={toggleTheme}
            aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
            title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
          >
            <span className="theme-icon" key={theme} aria-hidden="true">
              {theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}
            </span>
          </button>
          <button
            ref={keyTrigger}
            className="key-button"
            aria-label={key ? "Key connected" : "Connect Gemini"}
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
        </div>
      </header>
      <main>
        <div className={`hero-layout ${results ? "has-results" : ""}`}>
          <section className={`intro ${results ? "compact" : ""}`}>
            <div className="eyebrow">Keep learning at your own pace.</div>
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
          <Crowd />
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
              <SmoothInput
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
                Free search. Your key for summaries.
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
        </div>
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
                <span>
                  {results.results.length} top{" "}
                  {results.results.length === 1 ? "match" : "matches"}
                </span>
              </div>
              <p className="result-query">For “{results.query}”</p>
              {results.match_state !== "matches" && (
                <p className="notice">
                  {results.match_state === "weak"
                    ? "These matches may be less relevant. Check the passages before relying on them."
                    : "We could not find enough support in the indexed lectures. Try another topic or phrase."}
                </p>
              )}
              {results.results
                .slice(0, showAllResults ? RESULT_LIMIT : 2)
                .map((r, i) => (
                  <motion.article
                    className={`result ${selected?.source_id === r.source_id ? "selected" : ""} ${expandedPassage === r.source_id ? "expanded" : ""}`}
                    key={r.source_id}
                    layout="position"
                    initial={reducedMotion ? false : { opacity: 0, y: 12 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{
                      opacity: { duration: 0.2, delay: i * 0.035 },
                      y: { duration: 0.25, delay: i * 0.035 },
                      layout: { type: "spring", stiffness: 380, damping: 34 },
                    }}
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
                    <p id={`passage-${r.source_id}`}>{r.excerpt}</p>
                    <button
                      className="passage-expand"
                      aria-expanded={expandedPassage === r.source_id}
                      aria-controls={`passage-${r.source_id}`}
                      onClick={() =>
                        setExpandedPassage(
                          expandedPassage === r.source_id ? null : r.source_id,
                        )
                      }
                    >
                      {expandedPassage === r.source_id
                        ? "Collapse passage"
                        : "Read full passage"}
                    </button>
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
                  </motion.article>
                ))}
              {results.results.length > 2 && !showAllResults && (
                <button
                  className="show-more-results"
                  onClick={() => {
                    setShowAllResults(true);
                    summaryController.current?.abort();
                    setSummary(null);
                    setSummaryBusy(false);
                    setSummaryError("");
                  }}
                >
                  Show more matches ({results.results.length - 2}){" "}
                  <ArrowRight size={16} />
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
                          <img
                            className="video-thumbnail"
                            src={`https://i.ytimg.com/vi/${selected.video_id}/hqdefault.jpg`}
                            alt="Selected lecture thumbnail"
                          />
                          <span
                            className="thumbnail-shade"
                            aria-hidden="true"
                          />
                          <span className="play-circle">
                            <Play size={24} fill="currentColor" />
                          </span>
                          <span className="thumbnail-caption">
                            Play from {time(selected.playback_sec)}
                          </span>
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
              <a
                className="playlist-cover"
                href="https://www.youtube.com/playlist?list=PLbJhGqY-mq47k_WLUtzVjmarUm1EuXPj2"
                target="_blank"
                rel="noreferrer"
                aria-label="Open DSA playlist on YouTube"
              >
                <img
                  src="https://i.ytimg.com/vi/qH2VQY48mg4/hqdefault.jpg"
                  alt="Cover video of the DSA Patterns playlist"
                  loading="lazy"
                  width="480"
                  height="360"
                  onError={(event) => {
                    event.currentTarget.style.display = "none";
                  }}
                />
                <span className="playlist-cover-label">
                  <Play size={15} fill="currentColor" /> DSA Patterns · Playlist
                </span>
              </a>
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
        <footer className="site-footer">
          <div className="footer-brand">
            <a href="/">RecallQ</a>
            <p>Find where you learned it.</p>
            <small>Every revisit is a step forward.</small>
          </div>
          <nav aria-label="Footer links">
            <a
              href="https://www.youtube.com/playlist?list=PLbJhGqY-mq47k_WLUtzVjmarUm1EuXPj2"
              target="_blank"
              rel="noreferrer"
            >
              Explore the playlist <ArrowUpRight size={14} />
            </a>
            <a
              href="https://github.com/the-ajay-panigrahi/recallq"
              target="_blank"
              rel="noreferrer"
            >
              View source <ArrowUpRight size={14} />
            </a>
            <button
              onClick={() =>
                window.scrollTo({
                  top: 0,
                  behavior: matchMedia("(prefers-reduced-motion: reduce)")
                    .matches
                    ? "instant"
                    : "smooth",
                })
              }
            >
              Back to top ↑
            </button>
          </nav>
          <div className="footer-bottom">
            <span>
              Built for curious minds. Answers belong to the lectures.
            </span>
            <a href="https://skiper-ui.com" target="_blank" rel="noreferrer">
              Motion by Skiper UI
            </a>
          </div>
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
              Search is free and needs no key. Summaries use your Gemini quota.
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
