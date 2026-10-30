#!/usr/bin/env python3
"""Prepare a reproducible, text-only causal-LM corpus from real paper Markdown.

Example (all paths are local; no model or dataset downloads are performed)::

    python scripts/prepare_cpt_corpus.py --corpus-dir /path/to/cpt_multimodal_20261023
        --tokenizer-dir /path/to/Qwen3-VL-32B-Instruct
        --output-dir /path/to/cpt_text_2048 --block-size 2048

Output: {train,val,test}/{input_ids,attention_mask}.npy, split metadata,
docs_manifest.jsonl and dataset_summary.json. Labels are the input IDs with
attention_mask == 0 replaced by -100; the trainer performs the causal shift.
Packing appends exactly one EOS per retained document and preserves each
split's last partial block. No chat template, instruction or answer is added.

Only the actual input document.md is reverified against output_sha256. Historical
source/source_sha256 fields are recorded as provenance, not claimed reverified.
Deduplication is exact after the documented conservative cleaning; near duplicate
papers are NOT identified. Generated arrays/manifests are business data: do not
commit them to Git.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter, deque
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
import hashlib
import html
import json
import math
import multiprocessing
import os
from pathlib import Path
import re
import sys
import unicodedata


PIPELINE_VERSION = "paper-text-cpt-v1"
SPLITS = ("train", "val", "test")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
HTML_IMAGE_RE = re.compile(r"<img\b(?:[^>\"']|\"[^\"]*\"|'[^']*')*>", re.I)
HTML_ALT_RE = re.compile(r"\balt\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s/>]+))", re.I)
DATA_URI_RE = re.compile(
    r"data:[a-z0-9.+/-]+(?:;[a-z0-9=.+_-]+)*;base64,[A-Za-z0-9+/]+={0,2}", re.I
)
BARE_BASE64_RE = re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{256,}={0,2}(?![A-Za-z0-9+/=])")
REFERENCE_RE = re.compile(r"^ {0,3}\[([^\]\n]+)\]:[^\n]*(?:\n|$)", re.M)
UNSAFE_FORMAT = frozenset("\ufeff\u200b\u200e\u200f\u2060\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")
_WORKER_TOKENIZER = None
_WORKER_SPECIAL_IDS = None
_WORKER_SPECIAL_TEXT_RE = None


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def balanced_end(text, start, opening, closing):
    """Return one past a balanced Markdown delimiter, honoring backslash escapes."""
    depth, index = 0, start
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return None


def remove_markdown_images(text, stats):
    # Preserve descriptive alt text and ordinary prose/captions. Remove the
    # image target, including balanced parentheses in filenames or URLs.
    definitions = {" ".join(m.group(1).split()).casefold() for m in REFERENCE_RE.finditer(text)}
    image_references = set()
    pieces, cursor, search = [], 0, 0
    while True:
        start = text.find("![", search)
        if start < 0:
            break
        if start > 0 and text[start - 1] == "\\":
            search = start + 2
            continue
        alt_end = balanced_end(text, start + 1, "[", "]")
        if alt_end is None:
            search = start + 2
            continue
        alt = text[start + 2:alt_end - 1]
        target_start = alt_end
        while target_start < len(text) and text[target_start] in " \t":
            target_start += 1
        end = None
        if target_start < len(text) and text[target_start] == "(":
            end = balanced_end(text, target_start, "(", ")")
            if end is not None:
                stats["markdown_inline_images_removed"] += 1
        elif target_start < len(text) and text[target_start] == "[":
            end = balanced_end(text, target_start, "[", "]")
            if end is not None:
                label = text[target_start + 1:end - 1] or alt
                image_references.add(" ".join(label.split()).casefold())
                stats["markdown_reference_images_removed"] += 1
        elif " ".join(alt.split()).casefold() in definitions:
            end = alt_end
            image_references.add(" ".join(alt.split()).casefold())
            stats["markdown_reference_images_removed"] += 1
        if end is None:
            search = alt_end
            continue
        pieces.extend((text[cursor:start], alt))
        if alt:
            stats["image_alt_texts_preserved"] += 1
        cursor = search = end
    pieces.append(text[cursor:])
    text = "".join(pieces)

    def remove_reference(match):
        if " ".join(match.group(1).split()).casefold() in image_references:
            stats["image_reference_definitions_removed"] += 1
            return "\n"
        return match.group(0)

    return REFERENCE_RE.sub(remove_reference, text)


def is_image_base64(value):
    # Decode only a short prefix. A long alphanumeric scientific sequence is
    # not sufficient reason to delete text: require an actual image signature.
    prefix = value[:120]
    try:
        decoded = base64.b64decode(prefix + "=" * (-len(prefix) % 4), validate=True)
    except (ValueError, base64.binascii.Error):
        return False
    return (
        decoded.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a", b"BM", b"II*\x00", b"MM\x00*"))
        or (decoded.startswith(b"RIFF") and decoded[8:12] == b"WEBP")
        or decoded.lstrip().startswith(b"<svg")
    )


def clean_markdown(raw_text):
    """Conservative cleanup, without Unicode folding or rewriting equations."""
    stats = Counter()
    text = raw_text.replace("\r\n", "\n").replace("\r", "\n")

    def remove_html_image(match):
        stats["html_images_removed"] += 1
        alt_match = HTML_ALT_RE.search(match.group(0))
        alt = html.unescape(next((value for value in alt_match.groups() if value is not None), "")) if alt_match else ""
        if alt:
            stats["html_image_alt_texts_preserved"] += 1
        return alt

    text = HTML_IMAGE_RE.sub(remove_html_image, text)
    text = remove_markdown_images(text, stats)
    text, stats["data_uri_payloads_removed"] = DATA_URI_RE.subn("", text)

    def remove_payload(match):
        if is_image_base64(match.group(0)):
            stats["bare_image_base64_payloads_removed"] += 1
            return ""
        return match.group(0)

    text = BARE_BASE64_RE.sub(remove_payload, text)
    filtered = []
    for char in text:
        if (unicodedata.category(char) == "Cc" and char not in "\n\t") or char in UNSAFE_FORMAT:
            stats["control_characters_removed"] += 1
        else:
            filtered.append(char)
    # This is also the complete deduplication normalization: normalize line
    # endings, remove trailing horizontal whitespace and surrounding blank lines.
    # Preserve headings, formulas, units, punctuation and internal whitespace.
    text = "\n".join(line.rstrip(" \t") for line in "".join(filtered).split("\n")).strip("\n")
    stats["characters_removed_net"] = len(raw_text) - len(text)
    if re.search(r"data:[^\s,]*;base64,", text, re.I):
        raise ValueError("Unremoved or malformed base64 data URI remains; refusing to train on it")
    return text, dict(stats)


def split_for_hash(clean_sha256, seed, val_ratio, test_ratio):
    value = int(hashlib.sha256((str(seed) + ":" + clean_sha256).encode("ascii")).hexdigest(), 16) / (1 << 256)
    if value < test_ratio:
        return "test"
    if value < test_ratio + val_ratio:
        return "val"
    return "train"


def read_manifest(corpus_dir, manifest_path):
    raw = manifest_path.read_bytes()
    records, seen = [], set()
    documents_dir = (corpus_dir / "documents").resolve(strict=True)
    for line_number, line in enumerate(raw.decode("utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        identifier = row.get("id")
        expected_sha = row.get("output_sha256")
        if not isinstance(identifier, str) or not SHA256_RE.fullmatch(identifier):
            raise ValueError("Invalid document id at manifest line %d" % line_number)
        if identifier in seen:
            raise ValueError("Repeated manifest id: " + identifier)
        if not isinstance(expected_sha, str) or not SHA256_RE.fullmatch(expected_sha):
            raise ValueError("Missing or invalid output_sha256 for " + identifier)
        if row.get("status") != "ready" or row.get("failures"):
            raise ValueError("Document is not ready or has extraction failures: " + identifier)
        document_path = (documents_dir / identifier / "document.md").resolve(strict=True)
        document_path.relative_to(documents_dir)
        row["_document_path"] = document_path
        records.append(row)
        seen.add(identifier)
    if not records:
        raise ValueError("The manifest has no documents")
    return sorted(records, key=lambda row: row["id"]), hashlib.sha256(raw).hexdigest()


def tokenizer_fingerprint(tokenizer_dir):
    # Weight files are irrelevant to tokenization and may be many GB. Include
    # the actual vocabulary, merge rules and tokenizer/model configuration.
    files = []
    for path in sorted(tokenizer_dir.iterdir()):
        name = path.name
        if path.is_file() and (
            name.startswith(("tokenizer", "vocab", "merges", "added_tokens", "special_tokens_map"))
            or name in ("config.json", "chat_template.jinja")
        ):
            files.append({"name": name, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    if not files:
        raise ValueError("No tokenizer files found in " + str(tokenizer_dir))
    return files


def initialize_tokenizer_worker(tokenizer_dir):
    """One local tokenizer per process; avoid nested Rust/BLAS thread pools."""
    global _WORKER_TOKENIZER, _WORKER_SPECIAL_IDS, _WORKER_SPECIAL_TEXT_RE
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    os.environ["OMP_NUM_THREADS"] = "1"
    os.environ["OPENBLAS_NUM_THREADS"] = "1"
    os.environ["MKL_NUM_THREADS"] = "1"
    from transformers import AutoTokenizer
    _WORKER_TOKENIZER = AutoTokenizer.from_pretrained(tokenizer_dir, local_files_only=True, trust_remote_code=False)
    _WORKER_SPECIAL_IDS = set(_WORKER_TOKENIZER.all_special_ids)
    special_strings = [str(value) for value in _WORKER_TOKENIZER.all_special_tokens if str(value)]
    _WORKER_SPECIAL_TEXT_RE = re.compile("|".join(re.escape(value) for value in special_strings)) if special_strings else None


def prepare_document(task):
    """Read/verify/clean/tokenize inside a worker; never send full text to parent."""
    import numpy as np
    identifier, document_path, expected_sha = task
    path = Path(document_path)
    raw_bytes = path.read_bytes()
    actual_sha = hashlib.sha256(raw_bytes).hexdigest()
    if actual_sha != expected_sha:
        raise ValueError("Input hash mismatch for " + str(path))
    raw_text = raw_bytes.decode("utf-8")
    clean_text, cleaning = clean_markdown(raw_text)
    if not clean_text.strip():
        raise ValueError("Document has no text after cleanup: " + identifier)
    special_match = _WORKER_SPECIAL_TEXT_RE.search(clean_text) if _WORKER_SPECIAL_TEXT_RE is not None else None
    if special_match is not None:
        raise ValueError("Tokenizer control-token literal in document %s: %r" % (identifier, special_match.group(0)))
    tokenizer = _WORKER_TOKENIZER
    raw_token_count = len(tokenizer.encode(raw_text, add_special_tokens=False, truncation=False))
    token_ids = tokenizer.encode(clean_text, add_special_tokens=False, truncation=False)
    if not token_ids:
        raise ValueError("Tokenizer returned no text tokens for " + identifier)
    if min(token_ids) < 0 or max(token_ids) >= len(tokenizer):
        raise ValueError("Tokenizer returned an out-of-vocabulary token for " + identifier)
    unexpected_special_ids = sorted(set(token_ids).intersection(_WORKER_SPECIAL_IDS))
    if unexpected_special_ids:
        raise ValueError("Tokenizer control-token IDs before appended EOS in document %s: %s" %
                         (identifier, unexpected_special_ids))
    clean_token_count = len(token_ids)
    token_ids.append(tokenizer.eos_token_id)
    return {
        "id": identifier, "output_sha256": actual_sha,
        "clean_sha256": hashlib.sha256(clean_text.encode("utf-8")).hexdigest(),
        "raw_bytes": len(raw_bytes), "raw_characters": len(raw_text), "clean_characters": len(clean_text),
        "raw_tokens": raw_token_count, "clean_tokens": clean_token_count, "cleaning": cleaning,
        "token_bytes": np.asarray(token_ids, dtype="<i4").tobytes(),
    }


def ordered_bounded_map(executor, function, iterable, max_pending):
    """Bound task/result memory while consuming futures in input order."""
    source, pending = iter(iterable), deque()
    try:
        for _ in range(max_pending):
            try:
                item = next(source)
            except StopIteration:
                break
            pending.append(executor.submit(function, item))
        while pending:
            yield pending.popleft().result()
            try:
                item = next(source)
            except StopIteration:
                continue
            pending.append(executor.submit(function, item))
    finally:
        for future in pending:
            future.cancel()


def prepared_documents(records, tokenizer_dir, workers):
    tasks = ((row["id"], str(row["_document_path"]), row["output_sha256"]) for row in records)
    if workers == 1:
        initialize_tokenizer_worker(str(tokenizer_dir))
        for task in tasks:
            yield prepare_document(task)
        return
    # Spawn avoids inheriting a used fast-tokenizer thread pool on Linux and
    # gives the same behavior on Windows. Only <= 2*workers docs are in flight.
    with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn"),
                             initializer=initialize_tokenizer_worker, initargs=(str(tokenizer_dir),)) as executor:
        yield from ordered_bounded_map(executor, prepare_document, tasks, max_pending=2 * workers)


def pack_split(output_dir, split, token_count, block_size, eos_id, expected_hash, np):
    target = output_dir / split
    target.mkdir()
    block_count = math.ceil(token_count / block_size)
    ids_path, masks_path = target / "input_ids.npy", target / "attention_mask.npy"
    if not block_count:
        np.save(str(ids_path), np.empty((0, block_size), dtype="<i4"), allow_pickle=False)
        np.save(str(masks_path), np.empty((0, block_size), dtype="u1"), allow_pickle=False)
    else:
        ids = np.lib.format.open_memmap(str(ids_path), mode="w+", dtype="<i4", shape=(block_count, block_size))
        masks = np.lib.format.open_memmap(str(masks_path), mode="w+", dtype="u1", shape=(block_count, block_size))
        stream = np.memmap(str(output_dir / (".tokens." + split + ".int32")), mode="r", dtype="<i4", shape=(token_count,))
        ids_flat, masks_flat = ids.reshape(-1), masks.reshape(-1)
        # Bound temporary memory; even a very large corpus is copied in chunks.
        for start in range(0, token_count, 1024 * 1024):
            end = min(start + 1024 * 1024, token_count)
            ids_flat[start:end] = stream[start:end]
            masks_flat[start:end] = 1
        ids_flat[token_count:] = eos_id
        masks_flat[token_count:] = 0
        # A singleton tail has no next-token target after the model's causal
        # shift. Rebalance the final two rows without duplicating/dropping a
        # token: [B, 1] becomes [B-1, 2], both right-padded to block_size.
        singleton_tail_rebalanced = block_count > 1 and token_count % block_size == 1
        if singleton_tail_rebalanced:
            ids[-1, 0] = stream[-2]
            ids[-1, 1] = stream[-1]
            ids[-2, -1] = eos_id
            masks[-2, -1] = 0
            masks[-1, 1] = 1
        ids.flush()
        masks.flush()
        del ids_flat, masks_flat, ids, masks, stream
    # Read back the saved arrays and prove lossless packing, correct masks and
    # exact equality of the stream to all tokenized documents plus their EOS.
    ids = np.load(str(ids_path), mmap_mode="r", allow_pickle=False)
    masks = np.load(str(masks_path), mmap_mode="r", allow_pickle=False)
    actual_hash = hashlib.sha256()
    ids_flat, masks_flat = ids.reshape(-1), masks.reshape(-1)
    real_tokens_verified = 0
    zero_loss_block_indices = []
    for start in range(0, block_count, 256):
        end = min(start + 256, block_count)
        values, mask_values = ids[start:end], masks[start:end]
        if not np.all((mask_values == 0) | (mask_values == 1)):
            raise ValueError("Invalid attention mask value: " + split)
        if np.any(np.diff(mask_values.astype("i2"), axis=1) > 0):
            raise ValueError("Attention mask is not right-padded: " + split)
        valid = mask_values == 1
        actual_hash.update(values[valid].tobytes())
        row_lengths = mask_values.sum(axis=1)
        real_tokens_verified += int(row_lengths.sum())
        zero_loss_block_indices.extend(start + int(i) for i in np.flatnonzero(row_lengths < 2))
        if not np.all(values[~valid] == eos_id):
            raise ValueError("Invalid padding token: " + split)
    if actual_hash.hexdigest() != expected_hash:
        raise ValueError("Token stream differs after packing: " + split)
    if real_tokens_verified != token_count:
        raise ValueError("Real token count differs after packing: " + split)
    if zero_loss_block_indices:
        raise ValueError("A packed block has fewer than two real tokens: " + split)
    metadata = {
        "split": split, "shape": [block_count, block_size], "input_ids_dtype": "int32",
        "attention_mask_dtype": "uint8", "real_tokens_including_eos": token_count,
        "supervised_shifted_tokens": max(token_count - block_count, 0),
        "zero_loss_block_indices": zero_loss_block_indices,
        "singleton_tail_rebalanced": block_count > 1 and token_count % block_size == 1,
        "padding_tokens": block_count * block_size - token_count, "eos_token_id": eos_id,
        "real_token_stream_sha256": actual_hash.hexdigest(),
        "labels": "Copy input_ids; replace attention_mask == 0 with -100; model performs causal shift.",
        "packing_verified": True,
    }
    del ids_flat, masks_flat, ids, masks
    metadata["input_ids_sha256"] = sha256_file(ids_path)
    metadata["attention_mask_sha256"] = sha256_file(masks_path)
    write_json(target / "metadata.json", metadata)
    return metadata


def prepare(args):
    import numpy as np
    import transformers
    from transformers import AutoTokenizer

    corpus_dir = args.corpus_dir.resolve(strict=True)
    tokenizer_dir = args.tokenizer_dir.resolve(strict=True)
    manifest_path = (args.manifest or corpus_dir / "manifest.jsonl").resolve(strict=True)
    output_dir = args.output_dir.resolve()
    if output_dir.exists() and (not output_dir.is_dir() or any(output_dir.iterdir())):
        raise ValueError("Output must be absent or empty: " + str(output_dir))
    if output_dir == corpus_dir or output_dir == tokenizer_dir:
        raise ValueError("Output must differ from the input corpus and tokenizer directories")
    records, manifest_sha256 = read_manifest(corpus_dir, manifest_path)
    tokenizer_files = tokenizer_fingerprint(tokenizer_dir)
    tokenizer = AutoTokenizer.from_pretrained(str(tokenizer_dir), local_files_only=True, trust_remote_code=False)
    eos_id = tokenizer.eos_token_id
    if not isinstance(eos_id, int) or not 0 <= eos_id < 2 ** 31:
        raise ValueError("Tokenizer must define one int32-compatible eos_token_id")
    if len(tokenizer) >= 2 ** 31:
        raise ValueError("Tokenizer vocabulary exceeds int32")
    special_ids = set(tokenizer.all_special_ids)
    special_strings = [str(value) for value in tokenizer.all_special_tokens if str(value)]
    output_dir.mkdir(parents=True, exist_ok=True)
    progress_path = output_dir / "PREPARATION_INCOMPLETE.json"
    write_json(progress_path, {"status": "incomplete", "manifest_sha256": manifest_sha256})
    handles = {split: (output_dir / (".tokens." + split + ".int32")).open("wb") for split in SPLITS}
    token_hashes = {split: hashlib.sha256() for split in SPLITS}
    counts = {split: Counter() for split in SPLITS}
    seen, documents, cleaning_totals = {}, [], Counter()
    totals = Counter()
    print("Preparing %d documents with %d worker(s); at most %d tasks in flight" %
          (len(records), args.workers, 2 * args.workers if args.workers > 1 else 1), flush=True)
    prepared = prepared_documents(records, tokenizer_dir, args.workers)
    try:
        for index, (row, result) in enumerate(zip(records, prepared), 1):
            path = row["_document_path"]
            if result["id"] != row["id"] or result["output_sha256"] != row["output_sha256"]:
                raise ValueError("Worker result differs from ordered input manifest")
            clean_sha = result["clean_sha256"]
            cleaning = result["cleaning"]
            clean_token_count = result["clean_tokens"]
            raw_token_count = result["raw_tokens"]
            split = split_for_hash(clean_sha, args.seed, args.val_ratio, args.test_ratio)
            canonical = seen.get(clean_sha)
            if canonical is None:
                if len(result["token_bytes"]) != (clean_token_count + 1) * 4:
                    raise ValueError("Worker token bytes have an invalid length")
                handles[split].write(result["token_bytes"])
                token_hashes[split].update(result["token_bytes"])
                start = counts[split]["real_tokens_including_eos"]
                canonical = {"id": row["id"], "split": split, "clean_tokens": clean_token_count, "stream_start_token": start}
                seen[clean_sha] = canonical
                counts[split]["unique_documents"] += 1
                counts[split]["text_tokens"] += clean_token_count
                counts[split]["real_tokens_including_eos"] += clean_token_count + 1
                totals["retained_clean_characters"] += result["clean_characters"]
            else:
                if clean_token_count != canonical["clean_tokens"]:
                    raise ValueError("Identical cleaned documents have different token counts")
                if split != canonical["split"]:
                    raise ValueError("Duplicate documents assigned to different splits")
                totals["exact_duplicate_documents_removed"] += 1
                counts[split]["exact_duplicate_documents_removed"] += 1
            counts[split]["source_documents"] += 1
            totals.update({"source_documents": 1, "raw_bytes": result["raw_bytes"], "raw_characters": result["raw_characters"],
                           "clean_characters_before_dedup": result["clean_characters"], "raw_tokens_before_dedup": raw_token_count,
                           "clean_tokens_before_dedup": clean_token_count})
            cleaning_totals.update(cleaning)
            documents.append({
                "id": row["id"], "input_path": str(path), "output_sha256": result["output_sha256"],
                "historical_source": row.get("source"), "historical_source_sha256": row.get("source_sha256"),
                "historical_source_reverified": False, "clean_sha256": clean_sha, "split": split,
                "retained": canonical["id"] == row["id"], "canonical_document_id": canonical["id"],
                "raw_bytes": result["raw_bytes"], "raw_characters": result["raw_characters"], "clean_characters": result["clean_characters"],
                "raw_tokens": raw_token_count, "clean_tokens": clean_token_count,
                "eos_tokens_added": int(canonical["id"] == row["id"]),
                "stream_start_token": canonical["stream_start_token"],
                "stream_end_token_exclusive": canonical["stream_start_token"] + clean_token_count + 1,
                "cleaning": cleaning,
            })
            if index == 1 or index % 25 == 0 or index == len(records):
                print("Tokenized %d/%d documents; unique=%d, text tokens=%d" %
                      (index, len(records), len(seen), sum(c["text_tokens"] for c in counts.values())), flush=True)
    finally:
        prepared.close()
        for handle in handles.values():
            handle.close()
    if totals["source_documents"] != len(records):
        raise ValueError("Not all manifest documents were returned by workers")
    for split, ratio in (("train", 1 - args.val_ratio - args.test_ratio), ("val", args.val_ratio), ("test", args.test_ratio)):
        if ratio > 0 and counts[split]["unique_documents"] == 0:
            raise ValueError("Hash split is empty: %s; choose a different seed or ratio explicitly" % split)
    split_metadata = {}
    for split in SPLITS:
        split_metadata[split] = pack_split(output_dir, split, counts[split]["real_tokens_including_eos"],
                                          args.block_size, eos_id, token_hashes[split].hexdigest(), np)
        split_metadata[split].update(dict(counts[split]))
        write_json(output_dir / split / "metadata.json", split_metadata[split])
    print("Rechecking all input hashes and tokenizer files before marking the dataset complete", flush=True)
    if sha256_file(manifest_path) != manifest_sha256:
        raise ValueError("Manifest changed during preparation")
    for row in records:
        if sha256_file(row["_document_path"]) != row["output_sha256"]:
            raise ValueError("Input changed during preparation: " + str(row["_document_path"]))
    if tokenizer_fingerprint(tokenizer_dir) != tokenizer_files:
        raise ValueError("Tokenizer files changed during preparation")
    duplicate_groups = {}
    for document in documents:
        duplicate_groups.setdefault(document["canonical_document_id"], []).append(document["id"])
    duplicate_groups = [ids for ids in duplicate_groups.values() if len(ids) > 1]
    with (output_dir / "docs_manifest.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for document in documents:
            handle.write(json.dumps(document, ensure_ascii=False, sort_keys=True) + "\n")
    settings = {"pipeline_version": PIPELINE_VERSION, "script_sha256": sha256_file(Path(__file__).resolve()),
                "manifest_sha256": manifest_sha256, "tokenizer_files": tokenizer_files, "block_size": args.block_size,
                "seed": args.seed, "val_ratio": args.val_ratio, "test_ratio": args.test_ratio,
                "transformers_version": transformers.__version__, "numpy_version": np.__version__,
                "tokenizer_class": type(tokenizer).__name__, "eos_token_id": eos_id,
                "add_special_tokens": False, "append_eos_per_document": True, "chat_template_used": False,
                "source_control_token_literals_and_ids_rejected": True,
                "tokenizer_special_tokens": special_strings, "tokenizer_special_ids": sorted(special_ids)}
    summary = {
        "status": "complete", "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "corpus_dir": str(corpus_dir), "manifest_path": str(manifest_path), "tokenizer_dir": str(tokenizer_dir),
        "settings": settings, "version_signature": hashlib.sha256(json_bytes(settings)).hexdigest(),
        "execution": {"workers": args.workers, "max_inflight_documents": 2 * args.workers if args.workers > 1 else 1,
                      "worker_start_method": "spawn" if args.workers > 1 else "in_process",
                      "results_consumed_in_manifest_order": True, "full_document_text_sent_to_parent": False},
        "totals": dict(totals), "cleaning_totals": dict(cleaning_totals), "splits": split_metadata,
        "unique_documents": len(seen), "duplicate_groups": duplicate_groups,
        "deduplication": "Exact SHA256 of cleaned UTF-8 text; no Unicode folding or internal whitespace folding.",
        "near_duplicate_detection_performed": False,
        "split_method": "SHA256(seed + ':' + clean_sha256) / 2**256; test, then val, then train; no cross-split packing.",
        "historical_source_reverified": False,
        "historical_source_note": "Only actual documents/{id}/document.md was reverified against output_sha256 twice. Historical source paths/hashes are provenance only.",
        "labels": "All real text and EOS positions have labels=input_ids; padding labels must be -100. The model performs the causal shift.",
        "verification": {"all_actual_input_hashes_verified_before_and_after": True,
                         "all_packed_token_stream_hashes_match": True, "all_attention_masks_verified": True,
                         "all_tokenizer_files_unchanged": True, "no_truncated_document_tails": True},
    }
    write_json(output_dir / "dataset_summary.json", summary)
    # Remove only this run's exact temporary files; never recursively remove an output directory.
    for split in SPLITS:
        (output_dir / (".tokens." + split + ".int32")).unlink()
    progress_path.unlink()
    print(json.dumps({"status": "complete", "output_dir": str(output_dir), "unique_documents": len(seen),
                      "splits": {s: {"shape": split_metadata[s]["shape"], "tokens": split_metadata[s]["real_tokens_including_eos"]} for s in SPLITS}}, ensure_ascii=False), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corpus-dir", type=Path, required=True)
    parser.add_argument("--tokenizer-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True, help="Must not exist or must be empty")
    parser.add_argument("--manifest", type=Path, help="Default: <corpus-dir>/manifest.jsonl")
    parser.add_argument("--block-size", type=int, choices=(2048, 4096), default=2048)
    parser.add_argument("--seed", type=int, default=20261030)
    parser.add_argument("--val-ratio", type=float, default=0.01)
    parser.add_argument("--test-ratio", type=float, default=0.01)
    parser.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1),
                        help="Tokenizer worker processes (default: min(8, CPU count)); at most 2*workers docs in flight")
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error("--workers must be at least 1")
    if not (0 <= args.val_ratio < 1 and 0 <= args.test_ratio < 1 and args.val_ratio + args.test_ratio < 1):
        parser.error("Split ratios must be finite and nonnegative, with val + test < 1")
    try:
        prepare(args)
    except Exception as error:
        print("ERROR: %s: %s" % (type(error).__name__, error), file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
