"""Measure one local lecture, including a real restart and offline continuation.

No model/API calls are made by this supervisor. All observations are local.
"""

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import statistics
import subprocess
import sys
import time

from scripts import transcribe as t


class Usage(ctypes.Structure):
    # macOS SDK sys/resource.h: rusage_info_v2, through ri_phys_footprint.
    _fields_ = [("uuid", ctypes.c_uint8 * 16)] + [
        (name, ctypes.c_uint64) for name in
        ["user", "system", "package_wakeups", "interrupt_wakeups", "pageins",
         "wired", "resident", "footprint", "start", "exit", "child_user",
         "child_system", "child_package", "child_interrupt", "child_pageins",
         "child_elapsed", "disk_read", "disk_write"]]


LIBPROC = ctypes.CDLL("/usr/lib/libproc.dylib")
LIBPROC.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]


def footprint(pid):
    value = Usage()
    if LIBPROC.proc_pid_rusage(pid, 2, ctypes.byref(value)) != 0:
        return None
    return value.footprint / t.GIB


def output(command):
    return subprocess.run(command, capture_output=True, text=True, check=True).stdout


def snapshot(worker_pid=None):
    vm = output(["vm_stat"])
    page = int(re.search(r"page size of (\d+)", vm)[1])
    pages = {key: int(value) for key, value in re.findall(r"^([^:\n]+):\s+(\d+)\.", vm, re.M)}
    top = output(["top", "-l", "1", "-n", "0"])
    phys = next(line for line in top.splitlines() if line.startswith("PhysMem:"))
    swap = output(["sysctl", "-n", "vm.swapusage"])
    swap_used = float(re.search(r"used =\s+([\d.]+)M", swap)[1]) / 1024
    rows = []
    for line in output(["ps", "-axo", "pid=,ppid=,rss=,%cpu=,comm="]).splitlines():
        fields = line.split(None, 4)
        if len(fields) == 5:
            rows.append((int(fields[0]), int(fields[1]), int(fields[2]), float(fields[3]), fields[4]))
    worker_ids = {worker_pid} if worker_pid else set()
    for _ in range(5):
        worker_ids.update(pid for pid, parent, *_ in rows if parent in worker_ids)
    families = {name: {"footprint_gib": 0, "rss_gib": 0, "cpu_percent": 0, "processes": 0}
                for name in ["transcription", "codex", "antigravity", "browser"]}
    for pid, parent, rss, cpu, command in rows:
        family = ("transcription" if pid in worker_ids else
                  "antigravity" if "Antigravity IDE.app/" in command else
                  "codex" if "/ChatGPT.app/" in command else
                  "browser" if any(x in command for x in ["/Google Chrome.app/", "/Safari.app/"]) else None)
        if family:
            family_data = families[family]
            family_data["rss_gib"] += rss / 1024**2
            family_data["footprint_gib"] += footprint(pid) or 0
            family_data["cpu_percent"] += cpu
            family_data["processes"] += 1
    gib = lambda key: pages.get(key, 0) * page / t.GIB
    return {
        "time": time.time(), "physical_total_gib": 8,
        "top_physical_memory": phys,
        "free_plus_speculative_gib": gib("Pages free") + gib("Pages speculative"),
        "file_backed_gib": gib("File-backed pages"),
        "wired_gib": gib("Pages wired down"),
        "compressed_physical_gib": gib("Pages occupied by compressor"),
        "swap_used_gib": swap_used,
        "swapin_bytes": pages.get("Swapins", 0) * page,
        "swapout_bytes": pages.get("Swapouts", 0) * page,
        "memory_pressure": int(output(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"]).strip()),
        "families": families,
    }


def cached_windows(home):
    return sorted(p for p in (home / "jobs").glob("*/*/decode-*/window-*.json")
                  if not p.name.endswith(".rejected.json"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def start_worker(home, vid, offline, baseline=False, audio=None):
    command = ["/usr/bin/caffeinate", "-i", "/usr/bin/nice", "-n", "10",
               sys.executable, "-u", "-m", "scripts.transcribe", "--home", str(home),
               "--video", vid, "--limit", "1", "--section-seconds", "120"]
    if baseline:
        command = ["/usr/bin/caffeinate", "-i", "/usr/bin/nice", "-n", "10",
                   sys.executable, "-u", "-m", "scripts.transcribe_baseline", "--home", str(home),
                   "--video", vid, "--audio", str(audio), "--duration", "1810"]
    if offline:
        command = ["/usr/bin/sandbox-exec", "-p",
                   "(version 1)(allow default)(deny network-outbound)"] + command
    env = os.environ.copy()
    env.update({"OMP_NUM_THREADS": "2", "VECLIB_MAXIMUM_THREADS": "2",
                "TOKENIZERS_PARALLELISM": "false", "HF_HUB_OFFLINE": "1"})
    with (home / "run.log").open("a") as log:
        return subprocess.Popen(command, cwd=t.ROOT, env=env, stdin=subprocess.DEVNULL,
                                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)


def stop_worker(process):
    if process and process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", required=True)
    parser.add_argument("--home", required=True, type=Path)
    parser.add_argument("--max-minutes", type=int, default=360)
    parser.add_argument("--baseline", action="store_true", help="Audit simple faster-whisper and final-video caching")
    parser.add_argument("--audio", type=Path, help="Cached audio for the baseline audit")
    args = parser.parse_args()
    home = args.home.resolve()
    home.mkdir(parents=True, exist_ok=True)
    process = None
    phase = "baseline"
    restarted = False
    saved_before = {}
    samples = []
    began = time.monotonic()
    pause_requested = None
    resume_at = None
    last_notice = 0
    try:
        with (home / "memory.jsonl").open("a") as observations:
            while time.monotonic() - began < args.max_minutes * 60:
                item = snapshot(process.pid if process else None)
                item["phase"] = phase
                item["elapsed_seconds"] = time.monotonic() - began
                observations.write(json.dumps(item) + "\n")
                observations.flush()
                samples.append(item)
                if phase == "baseline" and item["elapsed_seconds"] >= 30:
                    process = start_worker(home, args.video, offline=args.baseline,
                                           baseline=args.baseline, audio=args.audio)
                    t.atomic_json(home / "audit-worker.json", {"pid": process.pid, "video": args.video})
                    phase = "offline_baseline" if args.baseline else "online"
                    t.log(f"One-video worker started: {args.video}; no indexing.")
                elif process and process.poll() is not None:
                    if process.returncode:
                        raise RuntimeError(f"Worker stopped with exit code {process.returncode}; inspect run.log")
                    if args.baseline and not restarted:
                        target = home / "transcripts" / f"{args.video}.json"
                        saved_before = {str(target): {"hash": digest(target), "mtime_ns": target.stat().st_mtime_ns}}
                        phase = "stopped"
                        resume_at = time.monotonic() + 3
                    else:
                        phase = "after"
                        resume_at = time.monotonic() + 30
                    process = None
                elif phase == "online" and not restarted and len(cached_windows(home)) >= 2:
                    (home / "PAUSE").touch()
                    phase = "pausing"
                    pause_requested = time.monotonic()
                elif phase == "pausing":
                    text = (home / "run.log").read_text()
                    if "Paused." in text:
                        saved_before = {str(p): {"hash": digest(p), "mtime_ns": p.stat().st_mtime_ns}
                                        for p in cached_windows(home)}
                        stop_worker(process)
                        process = None
                        phase = "stopped"
                        resume_at = time.monotonic() + 15
                        t.log(f"Stopped real worker after {len(saved_before)} saved windows.")
                    elif time.monotonic() - pause_requested > 1200:
                        raise RuntimeError("Worker did not reach a safe pause within 20 minutes")
                elif phase == "stopped" and time.monotonic() >= resume_at:
                    (home / "PAUSE").unlink(missing_ok=True)
                    process = start_worker(home, args.video, offline=True,
                                           baseline=args.baseline, audio=args.audio)
                    restarted = True
                    phase = "cache_reuse" if args.baseline else "offline_after_restart"
                    t.atomic_json(home / "restart-test.json", {"cached_before": saved_before,
                        "test_type": "completed_video_cache" if args.baseline else "mid_video_restart",
                        "network_policy": "macOS sandbox denies outbound networking for the resumed worker"})
                    t.log("Restarted same single video with outbound networking denied.")
                elif phase == "after" and time.monotonic() >= resume_at:
                    break
                if time.monotonic() - last_notice >= 60:
                    status = t.read_json(home / "status.json", {})
                    t.log(f"Audit phase={phase}; pressure={item['memory_pressure']}; "
                          f"swap={item['swap_used_gib']:.2f} GiB; status={json.dumps(status)}")
                    last_notice = time.monotonic()
                time.sleep(10)
            else:
                raise RuntimeError("Audit time limit reached; stopping the single-video job safely")
        target = home / "transcripts" / f"{args.video}.json"
        if not target.exists():
            raise RuntimeError("No final transcript exists; audit is incomplete")
        checks = {path: {"unchanged_hash": digest(Path(path)) == value["hash"],
                         "unchanged_mtime": Path(path).stat().st_mtime_ns == value["mtime_ns"]}
                  for path, value in saved_before.items()}
        summary = {"video_id": args.video, "wall_seconds": time.monotonic() - began,
                   "restart_exercised": restarted, "checkpoint_checks": checks,
                   "samples": len(samples), "phases": {}}
        for name in {s["phase"] for s in samples}:
            values = [s for s in samples if s["phase"] == name]
            summary["phases"][name] = {"samples": len(values),
                "peak_worker_footprint_gib": max(s["families"]["transcription"]["footprint_gib"] for s in values),
                "min_free_plus_speculative_gib": min(s["free_plus_speculative_gib"] for s in values),
                "median_free_plus_speculative_gib": statistics.median(s["free_plus_speculative_gib"] for s in values),
                "max_swap_gib": max(s["swap_used_gib"] for s in values),
                "pressure_levels": sorted({s["memory_pressure"] for s in values})}
        t.atomic_json(home / "audit-summary.json", summary)
        t.log("Single-video audit finished; the full playlist was not started.")
    finally:
        stop_worker(process)


if __name__ == "__main__":
    main()
