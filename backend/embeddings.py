"""Pinned MiniLM ONNX encoder shared by offline indexing and deployed search.

Mean pooling and normalization follow the official Sentence Transformers model
card. ONNX avoids deploying PyTorch or downloading weights on a cold start.
"""

from functools import lru_cache
import json
from pathlib import Path
import threading

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

ROOT = Path(__file__).resolve().parent
SPEC = json.loads((ROOT / "embedding-model.json").read_text())
MODEL = SPEC["model"]
REVISION = SPEC["revision"]
DIMENSION = 384
CHUNK_VERSION = "minilm-token220-overlap40-v1"
_lock = threading.Lock()


@lru_cache(maxsize=1)
def tokenizer():
    value = Tokenizer.from_file(str(ROOT / "assets/minilm/tokenizer.json"))
    value.enable_padding(pad_id=0, pad_token="[PAD]")
    value.enable_truncation(max_length=256)
    return value


@lru_cache(maxsize=1)
def session():
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    return ort.InferenceSession(str(ROOT / "assets/minilm/model.onnx"),
                               sess_options=options, providers=["CPUExecutionProvider"])


def encode(texts):
    if not texts:
        return []
    with _lock:
        encoded = tokenizer().encode_batch(texts)
        inputs = {"input_ids": np.array([e.ids for e in encoded], dtype=np.int64),
                  "attention_mask": np.array([e.attention_mask for e in encoded], dtype=np.int64),
                  "token_type_ids": np.array([e.type_ids for e in encoded], dtype=np.int64)}
        model = session()
        output = model.run(None, {i.name: inputs[i.name] for i in model.get_inputs()})[0]
        mask = inputs["attention_mask"][..., None]
        pooled = (output * mask).sum(axis=1) / np.maximum(mask.sum(axis=1), 1)
        pooled /= np.maximum(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12)
        if pooled.shape != (len(texts), DIMENSION) or not np.isfinite(pooled).all():
            raise ValueError("Invalid MiniLM embedding")
        return pooled.tolist()


@lru_cache(maxsize=1)
def full_tokenizer():
    # Separate immutable configuration for counting/splitting without truncation.
    value = Tokenizer.from_file(str(ROOT / "assets/minilm/tokenizer.json"))
    value.no_truncation()
    value.no_padding()
    return value


def token_count(text):
    return len(full_tokenizer().encode(text, add_special_tokens=False).ids)


def document_text(title, text):
    offsets = full_tokenizer().encode(title, add_special_tokens=False).offsets
    short_title = title[:offsets[31][1]] if len(offsets) > 32 else title
    return short_title + "\n\n" + text


def passages(data):
    """Keep every source character and source timing, within MiniLM's token limit.

    Long ASR segments are split by tokenizer offsets. Their original segment
    timestamps are retained rather than inventing word-level timing.
    """
    pieces = []
    previous = -1
    for source in data["segments"]:
        start, end = float(source["start"]), float(source["end"])
        if not np.isfinite([start, end]).all() or start < previous or start < 0 or end <= start:
            raise ValueError("Invalid transcript timing")
        previous = start
        text = source["text"].strip()
        while token_count(text) > 220:
            offsets = full_tokenizer().encode(text, add_special_tokens=False).offsets
            boundary = offsets[220][0]
            word_boundary = boundary
            while word_boundary > 0 and not text[word_boundary - 1].isspace():
                word_boundary -= 1
            boundary = word_boundary or boundary
            pieces.append({"start": start, "end": end, "text": text[:boundary]})
            text = text[boundary:]
        if text.strip():
            pieces.append({"start": start, "end": end, "text": text})
    index = 0
    while index < len(pieces):
        end = index + 1
        while end < len(pieces):
            candidate = " ".join(s["text"].strip() for s in pieces[index:end + 1])
            if token_count(candidate) > 220 or pieces[end]["end"] - pieces[index]["start"] > 75:
                break
            end += 1
        group = pieces[index:end]
        text = " ".join(s["text"].strip() for s in group)
        if token_count(document_text(data["title"], text)) + 2 > 256:
            raise ValueError("Passage would be silently truncated by MiniLM")
        yield {"start_sec": group[0]["start"], "end_sec": group[-1]["end"],
               "text": text, "segments": group}
        if end == len(pieces):
            break
        next_index = end
        while next_index > index + 1:
            overlap = " ".join(s["text"].strip() for s in pieces[next_index - 1:end])
            if token_count(overlap) > 40:
                break
            next_index -= 1
        index = next_index
