"""Local, single-playlist audio preparation. Never imported by the web server.

Run with: uv run --group transcription python -m scripts.transcribe --help
Raw ASR, Roman text, checkpoints and audio stay in an ignored local directory.
"""

import argparse
import contextlib
import fcntl
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import re
import resource
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from scripts.files import atomic_json

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / "data/local-transcription"
MODEL = "mlx-community/whisper-large-v3-mlx"
VOCABULARY = ("DSA, two pointers, array, sorted, duplicates, unique elements, index, previous, "
              "loop, return, time complexity, space complexity.")
DEVANAGARI = re.compile(r"[\u0900-\u097f\ua8e0-\ua8ff]+")
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")
GIB = 1024**3


class UnavailableVideo(Exception):
    pass


class DownloadBlocked(RuntimeError):
    pass


def read_json(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def log(message):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), message, flush=True)


def free_space(minimum_gib):
    available = shutil.disk_usage(ROOT).free / GIB
    if available < minimum_gib:
        raise RuntimeError(
            f"Only {available:.1f} GiB free; need {minimum_gib:g} GiB. "
            "Work is saved. Free space and run the same command again."
        )
    return available


@contextlib.contextmanager
def exclusive_lock(home):
    home.mkdir(parents=True, exist_ok=True)
    with (home / "run.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another transcription process is already running.") from None
        lock.seek(0)
        lock.truncate()
        lock.write(str(os.getpid()))
        lock.flush()
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def pause_if_requested(home):
    announced = False
    while (home / "PAUSE").exists():
        if not announced:
            release_model()
            log("Paused. Remove data/local-transcription/PAUSE to continue.")
            announced = True
        time.sleep(3)


def release_model():
    module = sys.modules.get("mlx_whisper.transcribe")
    if module:
        import gc
        module.ModelHolder.model = None
        gc.collect()
        import mlx.core as mx
        mx.clear_cache()


def wait_for_memory(home):
    """Darwin reports 1=normal, 2=warning, 4=critical. Do not pile on pressure."""
    announced = False
    while True:
        pause_if_requested(home)
        reading = subprocess.run(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"],
                                 capture_output=True, text=True)
        if reading.returncode or reading.stdout.strip() not in {"2", "4"}:
            return
        if not announced:
            # Drop our own weights/cache while waiting, so they cannot keep us paused.
            release_model()
            log("Memory pressure is elevated. Model released; waiting for macOS to recover.")
            announced = True
        time.sleep(10)


def retry_network(operation, label):
    """Network failures wait without discarding completed local work."""
    attempt = 0
    while True:
        try:
            return operation()
        except (UnavailableVideo, DownloadBlocked):
            raise
        except (OSError, RuntimeError) as exc:
            release_model()
            attempt += 1
            delay = min(120, 5 * 2 ** min(attempt - 1, 5))
            # Some upstream exception strings include signed URLs. Do not log them.
            log(f"{label}: {type(exc).__name__}; retrying in {delay}s. Ctrl+C safely stops.")
            time.sleep(delay)


class QuietDownloader:
    def debug(self, message):
        pass

    info = debug
    warning = debug
    error = debug


def list_playlist(url, home):
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadError

    def fetch():
        try:
            with YoutubeDL({"extract_flat": "in_playlist", "quiet": True,
                            "logger": QuietDownloader(), "socket_timeout": 20}) as ydl:
                return ydl.extract_info(url, download=False)
        except DownloadError as exc:
            raise RuntimeError("Playlist fetch failed") from exc

    saved = read_json(home / "playlist.json")
    if saved and saved["url"] == url:
        return saved
    result = retry_network(fetch, "Playlist")
    seen, entries = set(), []
    for item in result.get("entries", []):
        vid = item.get("id", "")
        if not VIDEO_ID.fullmatch(vid) or vid in seen:
            continue
        seen.add(vid)
        entries.append({"video_id": vid, "title": item.get("title") or vid,
                        "duration": item.get("duration")})
    if not entries:
        raise RuntimeError("No usable video IDs returned for this playlist.")
    saved = {"url": url, "title": result.get("title"), "videos": entries}
    atomic_json(home / "playlist.json", saved)
    return saved


def download_audio(video, home):
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadError

    folder = home / "audio"
    folder.mkdir(exist_ok=True)
    cached = read_json(folder / f"{video['video_id']}.json")
    if cached and Path(cached["path"]).is_file():
        return cached

    def fetch():
        try:
            with YoutubeDL({
                "format": "bestaudio/best", "noplaylist": True, "quiet": True,
                "logger": QuietDownloader(), "socket_timeout": 20,
                "retries": 2, "fragment_retries": 2, "continuedl": True,
                "outtmpl": str(folder / f"{video['video_id']}.%(ext)s"),
                "js_runtimes": {"node": {"path": shutil.which("node")}} if shutil.which("node") else {"deno": {}},
            }) as ydl:
                info = ydl.extract_info(
                    f"https://www.youtube.com/watch?v={video['video_id']}", download=True)
                duration = float(info.get("duration") or 0)
                if not math.isfinite(duration) or duration <= 0:
                    raise UnavailableVideo("Video has no finite duration")
                value = {"path": ydl.prepare_filename(info), "duration": duration,
                         "title": info.get("title") or video["title"]}
                atomic_json(folder / f"{video['video_id']}.json", value)
                return value
        except DownloadError as exc:
            message = str(exc).lower()
            if "sign in to confirm" in message or "not a bot" in message:
                raise DownloadBlocked("YouTube blocked the download. Progress is saved; "
                                      "no browser cookies have been accessed.") from None
            if any(s in message for s in ["private video", "video unavailable", "has been removed",
                                         "not available in your country", "members-only"]):
                raise UnavailableVideo("Private, removed or unavailable video") from None
            raise RuntimeError("Audio download failed") from None

    return retry_network(fetch, f"Download {video['video_id']}")


def model_path():
    from huggingface_hub import snapshot_download

    patterns = ["config.json", "weights.npz", "*.safetensors"]

    def complete(folder):
        return (folder / "config.json").is_file() and any(
            (folder / name).is_file()
            for name in ["weights.npz", "model.safetensors", "weights.safetensors"])

    # A fully cached model needs no internet connection, including a Hub metadata request.
    try:
        cached = Path(snapshot_download(MODEL, allow_patterns=patterns, local_files_only=True))
        if complete(cached):
            return cached
    except OSError:
        pass

    def fetch():
        try:
            folder = Path(snapshot_download(MODEL, allow_patterns=patterns))
            if not complete(folder):
                raise RuntimeError("Incomplete model download")
            return folder
        except Exception as exc:
            raise RuntimeError("Model download failed") from exc

    return retry_network(fetch, "Model download")


def romanizer():
    import uroman
    converter = uroman.Uroman()

    def convert(text):
        # Preserve existing English spelling, punctuation and numbers verbatim.
        return DEVANAGARI.sub(lambda m: converter.romanize_string(m[0], lcode="hin"), text)

    return convert


def settings(model, language, section_seconds):
    return {"schema": 2, "model": MODEL, "model_revision": model.name,
            "engine": "mlx-whisper", "engine_version": importlib.metadata.version("mlx-whisper"),
            "task": "transcribe", "language": language,
            "fp16": True, "hallucination_silence_threshold": None,
            "decode_window_seconds": 12, "decode_context_seconds": 1, "sample_len": 400,
            "without_timestamps": True, "temperature": 0.0, "initial_prompt": VOCABULARY,
            "section_seconds": section_seconds, "context_seconds": 12,
            "word_timestamps": True, "condition_on_previous_text": False,
            "romanizer": f"uroman-{importlib.metadata.version('uroman')}"}


def fingerprint(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:16]


def audio_digest(path):
    digest = hashlib.sha256()
    with open(path, "rb") as audio:
        for block in iter(lambda: audio.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def owned_segments(result, offset, start, end):
    """Overlapping audio sections own words by midpoint, avoiding double insertion."""
    output = []
    for segment in result.get("segments", []):
        words = []
        for word in segment.get("words", []):
            a, b = float(word["start"]) + offset, float(word["end"]) + offset
            if math.isfinite(a) and math.isfinite(b) and b >= a and start <= (a + b) / 2 < end:
                words.append({**word, "start": a, "end": b})
        if words:
            segment_end = max(words[-1]["end"], words[0]["start"] + 0.01)
            output.append({"start": words[0]["start"], "end": segment_end,
                           "text": "".join(w["word"] for w in words).strip(), "words": words})
        elif not segment.get("words"):
            # Preserve a non-word-aligned segment rather than silently discarding speech.
            a, b = float(segment["start"]) + offset, float(segment["end"]) + offset
            if math.isfinite(a) and math.isfinite(b) and b > a and start <= (a + b) / 2 < end:
                output.append({"start": a, "end": b, "text": segment["text"].strip()})
    return output


def extract_audio_section(audio_path, wav, offset, length):
    import imageio_ffmpeg
    subprocess.run([
        imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-y", "-ss", str(offset),
        "-i", audio_path, "-t", str(length), "-ac", "1", "-ar", "16000",
        "-c:a", "pcm_s16le", str(wav),
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def quality_flags(raw):
    flags = []
    text = raw.get("text", "")
    if "\ufffd" in text:
        flags.append("incomplete_unicode_word")
    if any(len(s.get("tokens", [])) >= 395 for s in raw.get("segments", [])):
        flags.append("output_limit_reached")
    words = text.lower().split()
    for width in range(1, 6):
        repeats = 12 if width == 1 else 6
        for start in range(len(words) - width * repeats + 1):
            phrase = words[start:start + width]
            if all(words[start + i * width:start + (i + 1) * width] == phrase for i in range(repeats)):
                flags.append("repeated_phrase_loop")
                return flags
    return flags


def decode_windows(samples, decode, sample_rate=16000, checkpoint_dir=None, keep_start=0, keep_end=None,
                   progress=None, window_seconds=20, context_seconds=2, allow_repair=True):
    """Dense Hindi consumes more tokens than English; avoid truncating 30s windows.

    MLX defaults to 224 output tokens. Our real sample exhausted that limit at
    about 14 seconds. Shorter audio windows plus a 440-token cap retain more
    speech without changing models or asking an LLM to fill missing text.
    """
    duration = len(samples) / sample_rate
    keep_end = duration if keep_end is None else min(duration, keep_end)
    segments, windows, languages, elapsed_total = [], [], set(), 0.0
    for start in range(int(keep_start) // window_seconds * window_seconds, math.ceil(keep_end), window_seconds):
        end = min(keep_end, start + window_seconds)
        offset, stop = max(0, start - context_seconds), min(duration, end + context_seconds)
        saved = checkpoint_dir / f"window-{start:05d}.json" if checkpoint_dir else None
        cached = read_json(saved) if saved else None
        if cached is None:
            began = time.monotonic()
            piece = samples[int(offset * sample_rate):int(stop * sample_rate)]
            raw = decode(piece)
            flags = quality_flags(raw)
            if flags:
                if saved:
                    atomic_json(saved.with_suffix(".rejected.json"), {"flags": flags, "raw": raw})
                if not allow_repair:
                    raise RuntimeError("Repeated or truncated ASR persisted in a short retry; "
                                       "stopped safely for review, without accepting the text.")
                repaired = decode_windows(piece, decode, sample_rate=sample_rate,
                    checkpoint_dir=checkpoint_dir / f"repair-{start:05d}" if checkpoint_dir else None,
                    window_seconds=6, context_seconds=1, allow_repair=False)
                raw = {**repaired, "rejected_original": raw, "repair_reason": flags}
            cached = {"raw": raw, "elapsed_seconds": time.monotonic() - began}
            if saved:
                atomic_json(saved, cached)
            if progress:
                progress(end, cached["elapsed_seconds"])
        raw = cached["raw"]
        elapsed_total += cached["elapsed_seconds"]
        windows.append({"offset": offset, "duration": stop - offset, "raw": raw})
        languages.add(raw.get("language", "hi"))
        segments.extend(owned_segments(raw, offset, max(start, keep_start), end))
    return {"text": " ".join(s["text"] for s in segments), "segments": segments,
            "language": ",".join(sorted(languages)), "windows": windows,
            "processing_seconds": elapsed_total}


def transcribe_sections(audio, video, model, config, home, infer=None):
    """Network-free after the model and current audio are downloaded."""
    use_mlx = infer is None
    if infer is None:
        import mlx.core as mx
        import mlx_whisper
        import numpy as np
        mx.set_cache_limit(256 * 1024**2)

        def infer(path):
            mx.clear_cache()
            # MLX's filename loader expects a system ffmpeg binary. We bundle ffmpeg
            # for extraction, then pass PCM directly, so no Homebrew install is needed.
            with wave.open(str(path), "rb") as pcm:
                samples = np.frombuffer(pcm.readframes(pcm.getnframes()), dtype=np.int16)
            samples = samples.astype(np.float32) / 32768.0
            def decode(window):
                # Every completed short window is already cached before these waits.
                wait_for_memory(home)
                mx.clear_cache()
                return mlx_whisper.transcribe(
                    window, path_or_hf_repo=str(model), language=config["language"],
                    task="transcribe", fp16=True, word_timestamps=True, sample_len=400,
                    without_timestamps=True, temperature=0.0, initial_prompt=VOCABULARY,
                    condition_on_previous_text=False, verbose=None,
                    hallucination_silence_threshold=None,
                )

            def progress(decoded_end, elapsed):
                state_path = home / "status.json"
                state = read_json(state_path, {"videos": {}})
                state["videos"][video["video_id"]] = {
                    "stage": "processing", "title": video["title"],
                    "through_seconds": min(end, offset + decoded_end), "duration": audio["duration"],
                }
                atomic_json(state_path, state)
                log(f"Saved decode {video['video_id']} through {offset + decoded_end:.0f}s "
                    f"({elapsed:.1f}s processing)")

            return decode_windows(samples, decode, checkpoint_dir=folder / f"decode-{number:05d}",
                                  keep_start=start - offset, keep_end=end - offset, progress=progress,
                                  window_seconds=12, context_seconds=1)

    source_hash = audio_digest(audio["path"])
    source_config = {**config, "audio_sha256": source_hash, "duration": audio["duration"]}
    run_id = fingerprint(source_config)
    folder = home / "jobs" / video["video_id"] / run_id
    folder.mkdir(parents=True, exist_ok=True)
    atomic_json(folder / "settings.json", source_config)
    duration = float(audio["duration"])
    section = config["section_seconds"]
    saved_segments, languages, elapsed_total = [], [], 0.0
    for number, start in enumerate(range(0, math.ceil(duration), section)):
        pause_if_requested(home)
        if use_mlx:
            wait_for_memory(home)
        free_space(8)
        end = min(duration, start + section)
        part_path = folder / f"part-{number:05d}.json"
        part = read_json(part_path)
        if part is None:
            offset = max(0, start - config["context_seconds"])
            stop = min(duration, end + config["context_seconds"])
            # Decode only this section, not a whole lecture, into memory.
            with tempfile.TemporaryDirectory(dir=folder) as temporary:
                wav = Path(temporary) / "section.wav"
                extract_audio_section(audio["path"], wav, offset, stop - offset)
                log(f"Transcribing {video['video_id']} {start:.0f}–{end:.0f}s")
                began = time.monotonic()
                raw = infer(wav)
                elapsed = raw.get("processing_seconds", time.monotonic() - began)
            part = {"start": start, "end": end, "audio_offset": offset,
                    "elapsed_seconds": elapsed, "raw": raw,
                    "segments": owned_segments(raw, offset, start, end)}
            if use_mlx:
                part["memory"] = {"mlx_peak_gib": mx.get_peak_memory() / GIB,
                                  "process_peak_rss_gib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / GIB}
            atomic_json(part_path, part)
            status_path = home / "status.json"
            progress = read_json(status_path, {"videos": {}})
            progress["videos"][video["video_id"]] = {
                "stage": "processing", "title": video["title"],
                "through_seconds": end, "duration": duration,
            }
            atomic_json(status_path, progress)
            log(f"Saved {video['video_id']} through {end:.0f}s ({elapsed:.1f}s processing)")
        saved_segments.extend(part["segments"])
        languages.append(part["raw"].get("language"))
        elapsed_total += part["elapsed_seconds"]
    return {"segments": saved_segments, "detected_languages": sorted({x for x in languages if x}),
            "processing_seconds": elapsed_total, "checkpoint_id": run_id,
            "settings_id": fingerprint(config), "audio_sha256": source_hash}


def finish_transcript(video, audio, result, config, convert):
    segments = []
    for source in result["segments"]:
        segments.append({**source, "raw_text": source["text"], "text": convert(source["text"])})
    if not segments:
        raise RuntimeError("No speech returned; retaining audio and checkpoints for review.")
    return {"video_id": video["video_id"], "title": audio["title"],
            "duration": audio["duration"], "model": "large-v3", "language": "roman-hinglish",
            "segments": segments, "provenance": {**config, **{k: v for k, v in result.items() if k != "segments"},
                "source": "local audio; MLX Whisper large-v3; uroman transliteration",
                "audio_verified": False, "llm_rewritten": False},
            "review_required": True}


def index_staged(home, config):
    """Publish to an inactive revision. Never switch the live manifest automatically."""
    revision = "local-" + fingerprint(config)
    manifest_path = home / f"manifest-{revision}.json"
    status = read_json(home / "status.json", {"videos": {}})
    for vid, value in status["videos"].items():
        if value["stage"] == "unavailable":
            # Carry unavailable statuses into the staged manifest, not an indexed count.
            if not manifest_path.exists():
                manifest = read_json(ROOT / "data/manifest.json")
                manifest["revision"] = revision
                for item in manifest["videos"]:
                    if item["status"] != "excluded":
                        item["status"] = "pending"
                        item.pop("chunks", None)
                atomic_json(manifest_path, manifest)
            manifest = read_json(manifest_path)
            for item in manifest["videos"]:
                if item["video_id"] == vid:
                    item["status"] = "unavailable"
            atomic_json(manifest_path, manifest)

    def index():
        outcome = subprocess.run([
            sys.executable, "-m", "scripts.ingest", "--source", str(home / "transcripts"),
            "--limit", "0", "--manifest", str(manifest_path), "--revision", revision,
        ], cwd=ROOT)
        if outcome.returncode:
            raise RuntimeError("Indexing incomplete; cached progress will be reused")

    retry_network(index, "Staged indexing")
    log(f"New corpus staged in {manifest_path}. The live manifest/index selection is unchanged.")


def run(args):
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise RuntimeError("This MLX runner needs an Apple silicon Mac.")
    home = args.home.resolve()
    with exclusive_lock(home):
        log(f"Preflight: {free_space(20):.1f} GiB free; one video at a time; large-v3 FP16.")
        model = model_path()
        if args.prepare:
            log("Model cached. No audio transcription was started.")
            return
        config = settings(model, None if args.language == "auto" else args.language, args.section_seconds)
        if args.audio:
            if not args.duration or args.duration <= 0:
                raise RuntimeError("--audio needs a positive --duration in seconds.")
            videos = [{"video_id": "local-sample", "title": "Local timing sample"}]
        else:
            manifest = read_json(ROOT / "data/manifest.json")
            url = f"https://www.youtube.com/playlist?list={manifest['playlist_id']}"
            listing = list_playlist(url, home)
            excluded = {v["video_id"] for v in manifest["videos"] if v["status"] == "excluded"}
            videos = [v for v in listing["videos"] if v["video_id"] not in excluded]
            if args.video:
                videos = [v for v in videos if v["video_id"] in args.video]
        if args.limit:
            videos = videos[:args.limit]
        playlist_hours = sum(v.get("duration") or 0 for v in videos) / 3600
        convert = romanizer()
        status = read_json(home / "status.json", {"videos": {}})
        try:
            for video in videos:
                pause_if_requested(home)
                vid = video["video_id"]
                target = home / "transcripts" / f"{vid}.json"
                previous = read_json(target)
                if previous and previous.get("provenance", {}).get("settings_id") == fingerprint(config):
                    log(f"Already complete: {vid}")
                    continue
                free_space(8)
                status["videos"][vid] = {"stage": "processing", "title": video["title"]}
                atomic_json(home / "status.json", status)
                try:
                    audio = ({"path": str(args.audio.resolve()), "duration": args.duration,
                              "title": video["title"]} if args.audio else download_audio(video, home))
                    result = transcribe_sections(audio, video, model, config, home)
                    output = finish_transcript(video, audio, result, config, convert)
                    atomic_json(target, output)
                    status["videos"][vid] = {"stage": "transcribed", "title": audio["title"],
                        "duration": audio["duration"], "processing_seconds": result["processing_seconds"],
                        "checkpoint_id": result["checkpoint_id"]}
                    atomic_json(home / "status.json", status)
                    if not args.audio:
                        Path(audio["path"]).unlink(missing_ok=True)
                        (home / "audio" / f"{vid}.json").unlink(missing_ok=True)
                    factor = result["processing_seconds"] / audio["duration"]
                    log(f"Complete {vid}. Measured processing/audio ratio {factor:.3f}.")
                    if playlist_hours:
                        log(f"Selected {playlist_hours:.1f} audio hours ≈ {playlist_hours * factor:.1f} "
                            "processing hours at this video's speed, excluding downloads/indexing.")
                except UnavailableVideo:
                    status["videos"][vid] = {"stage": "unavailable", "title": video["title"]}
                    atomic_json(home / "status.json", status)
                    log(f"Unavailable: {vid}; continuing.")
            log("Selected videos processed. Raw ASR and Roman transcripts are saved locally; index unchanged.")
            if args.index and not args.audio:
                # Release the large speech model before the small network-only indexing process.
                release_model()
                index_staged(home, config)
        finally:
            # Section-level progress may be newer than the enclosing loop's snapshot.
            atomic_json(home / "status.json", read_json(home / "status.json", status))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=HOME, help="Ignored directory for jobs and transcripts")
    parser.add_argument("--prepare", action="store_true", help="Download model only; no transcription")
    parser.add_argument("--index", action="store_true", help="After transcription, index an inactive corpus revision")
    parser.add_argument("--language", choices=["auto", "hi", "en"], default="hi",
                        help="Hindi/Hinglish speech by default; auto or en for English recordings")
    parser.add_argument("--section-seconds", type=int, default=300,
                        help="Checkpoint interval; unrelated to the 75-second retrieval passages")
    parser.add_argument("--video", action="append", help="Only this playlist video ID; repeatable")
    parser.add_argument("--limit", type=int, help="Process this many selected videos")
    parser.add_argument("--audio", type=Path, help="Benchmark an existing audio clip without YouTube")
    parser.add_argument("--duration", type=float, help="Existing clip duration in seconds")
    controls = parser.add_mutually_exclusive_group()
    controls.add_argument("--pause", action="store_true", help="Pause after the current section is saved")
    controls.add_argument("--resume", action="store_true", help="Release a running paused job")
    controls.add_argument("--status", action="store_true", help="Show saved video status without loading MLX")
    args = parser.parse_args()
    if args.pause or args.resume or args.status:
        args.home.mkdir(parents=True, exist_ok=True)
        if args.pause:
            (args.home / "PAUSE").touch()
            log("Pause requested; the current section will finish and be saved first.")
        elif args.resume:
            (args.home / "PAUSE").unlink(missing_ok=True)
            log("Pause released. A stopped process needs its original command run again.")
        else:
            print(json.dumps(read_json(args.home / "status.json", {"videos": {}}), indent=2))
        return
    if args.section_seconds < 24 or args.section_seconds % 12 or (args.limit is not None and args.limit < 1):
        parser.error("Sections must be multiples of 12 seconds and >=24; limit must be positive.")
    try:
        run(args)
    except KeyboardInterrupt:
        log("Stopped. Saved sections remain. Run the same command to resume.")
        raise SystemExit(130)
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        # Do not dump upstream errors, signed download URLs, or environment variables.
        log(str(exc) if isinstance(exc, RuntimeError) else f"Stopped safely: {type(exc).__name__}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
