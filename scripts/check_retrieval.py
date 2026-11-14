"""Small real-index topic smoke check; not a word/timestamp accuracy benchmark."""

import asyncio
import json
import re
import time

from backend import core
from scripts.files import atomic_json

CASES = [
    ("How does Kadane's algorithm find the maximum sum subarray?", "kadane"),
    ("Remove duplicates from a sorted array using two pointers", "pointer"),
    ("Longest substring without repeating characters using sliding window", "sliding"),
    ("Detect a linked list cycle using slow and fast pointers", "linked|slow|fast"),
    ("How does binary search work on a sorted array?", "binary"),
    ("What is a base case in recursion?", "recursion"),
    ("Explain memoization and tabulation in dynamic programming", "dynamic|dp|memoization|tabulation"),
    ("How do min heaps and priority queues work?", "heap|priority"),
    ("How do I merge overlapping intervals?", "interval"),
    ("Graph representation using adjacency matrix and adjacency list", "graph|adjacency"),
    ("sliding window mein window kab shrink karna chahiye?", "sliding"),
    ("linked list mein cycle detect karne ke liye slow aur fast pointer kaise use kare?", "linked|slow|fast"),
]


async def main():
    semaphore = asyncio.Semaphore(3)

    async def check(query, expected):
        async with semaphore:
            started = time.monotonic()
            result = await core.search(query, 5)
            sources = [{k: p[k] for k in ("video_id", "video_title", "start_sec", "watch_url")}
                       for p in result["results"]]
            return {"query": query, "expected_topic_pattern": expected,
                    "topic_present_in_top_5": any(re.search(expected, p["video_title"], re.I) for p in sources),
                    "elapsed_seconds": round(time.monotonic() - started, 2),
                    "match_state": result["match_state"], "sources": sources}

    results = await asyncio.gather(*(check(*case) for case in CASES))
    report = {"corpus_revision": core.manifest()["revision"], "cases": len(results),
              "topic_checks_passed": sum(r["topic_present_in_top_5"] for r in results),
              "method": "Expected topic appears in a top-five video title; not manually labeled passage accuracy or a general model comparison.",
              "results": results}
    atomic_json(core.ROOT / "data/retrieval-check.json", report)
    print(json.dumps({k: v for k, v in report.items() if k != "results"}))
    for result in results:
        if not result["topic_present_in_top_5"]:
            print("Review:", json.dumps(result))


if __name__ == "__main__":
    asyncio.run(main())
