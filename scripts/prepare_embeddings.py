"""Download pinned public MiniLM assets at build time, never during a search."""

import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main():
    spec = json.loads((ROOT / "backend/embedding-model.json").read_text())
    target = ROOT / "backend/assets/minilm"
    target.mkdir(parents=True, exist_ok=True)
    for source, expected in spec["files"].items():
        path = target / Path(source).name
        if path.exists() and expected and hashlib.sha256(path.read_bytes()).hexdigest() == expected:
            continue
        url = f"https://huggingface.co/{spec['model']}/resolve/{spec['revision']}/{source}"
        temporary = path.with_suffix(".download")
        try:
            with urllib.request.urlopen(url, timeout=120) as response, temporary.open("wb") as out:
                while block := response.read(1024 * 1024):
                    out.write(block)
            digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
            if expected and digest != expected:
                raise RuntimeError(f"Checksum mismatch for {source}")
            temporary.replace(path)
            print(f"Prepared {source}: {path.stat().st_size:,} bytes; sha256={digest}", flush=True)
        finally:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
