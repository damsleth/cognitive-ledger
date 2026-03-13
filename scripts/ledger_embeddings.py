#!/usr/bin/env python3
"""Native embedding helpers for Cognitive Ledger.

This module isolates embedding/index logic from the main CLI script so it can
be reused by query/eval/discovery flows and tested independently.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import urllib.error
import urllib.request
from collections import OrderedDict
from pathlib import Path
from typing import Any

import numpy as np
from ledger.io import append_timeline_entry as append_timeline_entry_safe
from ledger.parsing import extract_title, parse_frontmatter_text

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent

SEMANTIC_ROOT = ROOT_DIR / ".smart-env" / "semantic"
LEDGER_NOTES_ROOT = ROOT_DIR / "notes"
LEDGER_TIMELINE_PATH = LEDGER_NOTES_ROOT / "08_indices" / "timeline.md"
SEMANTIC_MANIFEST_PATH = LEDGER_NOTES_ROOT / "08_indices" / "semantic_manifest.json"

DEFAULT_SOURCE_ROOT = Path.home() / "notes"

SUPPORTED_BACKENDS = ("local", "openai")
SUPPORTED_TARGETS = ("ledger", "source", "both")

DEFAULT_LOCAL_MODEL = "TaylorAI/bge-micro-v2"
DEFAULT_OPENAI_MODEL = "text-embedding-3-small"

LEDGER_EMBED_NOTE_TYPES = {
    "notes/02_facts": "fact",
    "notes/03_preferences": "pref",
    "notes/04_goals": "goal",
    "notes/05_open_loops": "loop",
    "notes/06_concepts": "concept",
}

INDEX_ITEM_FIELDS = (
    "id",
    "rel_path",
    "abs_path",
    "type",
    "scope",
    "status",
    "lang",
    "updated",
    "content_hash",
    "row",
)

_LOCAL_ENCODER_CACHE: dict[str, Any] = {}
_QUERY_VECTOR_CACHE: OrderedDict[tuple[str, str, str], np.ndarray] = OrderedDict()
_QUERY_VECTOR_CACHE_MAX = 256


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sanitize_model_key(model: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "__", str(model or "").strip())
    return cleaned or "unknown_model"


def default_model_for_backend(backend: str) -> str:
    backend = str(backend or "").strip().lower()
    if backend == "local":
        return DEFAULT_LOCAL_MODEL
    if backend == "openai":
        return DEFAULT_OPENAI_MODEL
    raise ValueError(f"Unsupported embedding backend: {backend}")


def ensure_openai_api_key() -> str:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for OpenAI embedding backend")
    return api_key


def corpus_root_for_target(target: str, source_root: Path | None = None) -> Path:
    if target == "ledger":
        return LEDGER_NOTES_ROOT
    if target == "source":
        return Path(source_root or DEFAULT_SOURCE_ROOT).expanduser().resolve()
    raise ValueError(f"Unsupported corpus target: {target}")


def semantic_dir(target: str, backend: str, model: str) -> Path:
    return SEMANTIC_ROOT / target / f"{backend}__{sanitize_model_key(model)}"


def semantic_index_path(target: str, backend: str, model: str) -> Path:
    return semantic_dir(target, backend, model) / "index.json"


def semantic_vectors_path(target: str, backend: str, model: str) -> Path:
    return semantic_dir(target, backend, model) / "vectors.npy"


def infer_ledger_note_type(rel_path: str) -> str:
    for prefix, note_type in LEDGER_EMBED_NOTE_TYPES.items():
        if rel_path.startswith(prefix + "/"):
            return note_type
    return "unknown"


def normalize_for_hash(frontmatter: dict[str, Any], body: str, title: str) -> str:
    tags = frontmatter.get("tags")
    if isinstance(tags, list):
        tag_value = ",".join(sorted(str(item).strip() for item in tags if str(item).strip()))
    else:
        tag_value = str(tags or "").strip()

    payload = {
        "title": title.strip(),
        "body": body.strip(),
        "tags": tag_value,
        "scope": str(frontmatter.get("scope", "")).strip(),
        "status": str(frontmatter.get("status", "")).strip(),
        "lang": str(frontmatter.get("lang", "")).strip(),
        "updated": str(frontmatter.get("updated", "")).strip(),
        "confidence": str(frontmatter.get("confidence", "")).strip(),
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def sha1_text(text: str) -> str:
    digest = hashlib.sha1()
    digest.update(text.encode("utf-8"))
    return digest.hexdigest()


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def append_timeline_entry(action: str, rel_path: str, description: str) -> None:
    append_timeline_entry_safe(
        timeline_path=LEDGER_TIMELINE_PATH,
        action=action,
        note_path=rel_path,
        description=description,
        root_dir=ROOT_DIR,
    )


def iso_from_mtime(path: Path) -> str:
    ts = dt.datetime.fromtimestamp(path.stat().st_mtime, tz=dt.timezone.utc)
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def collect_ledger_notes() -> list[Path]:
    files: list[Path] = []
    for rel_prefix in LEDGER_EMBED_NOTE_TYPES:
        note_dir = ROOT_DIR / rel_prefix
        if not note_dir.is_dir():
            continue
        files.extend(sorted(note_dir.glob("*.md")))
    return files


def collect_source_notes(source_root: Path) -> list[Path]:
    if not source_root.exists():
        return []
    return sorted(path for path in source_root.rglob("*.md") if path.is_file())


def build_item_record(path: Path, target: str, source_root: Path | None = None) -> dict[str, Any]:
    abs_path = path.resolve()
    text = abs_path.read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter_text(text)

    if target == "ledger":
        rel_path = abs_path.relative_to(ROOT_DIR).as_posix()
        note_type = infer_ledger_note_type(rel_path)
    else:
        root = Path(source_root or DEFAULT_SOURCE_ROOT).expanduser().resolve()
        rel_path = abs_path.relative_to(root).as_posix()
        note_type = "source"

    fallback_title = abs_path.stem.replace("_", " ")
    title = extract_title(body) or fallback_title
    embedding_text = "\n".join([title.strip(), body.strip()]).strip()

    content_hash = sha1_text(normalize_for_hash(frontmatter, body, title))
    updated = str(frontmatter.get("updated", "")).strip() or iso_from_mtime(abs_path)

    return {
        "id": f"{target}:{rel_path}",
        "rel_path": rel_path,
        "abs_path": abs_path.as_posix(),
        "type": note_type,
        "scope": str(frontmatter.get("scope", "")).strip(),
        "status": str(frontmatter.get("status", "")).strip(),
        "lang": str(frontmatter.get("lang", "")).strip(),
        "updated": updated,
        "content_hash": content_hash,
        "embedding_text": embedding_text,
        "row": -1,
    }


def collect_target_items(target: str, source_root: Path | None = None) -> list[dict[str, Any]]:
    if target == "ledger":
        files = collect_ledger_notes()
    elif target == "source":
        files = collect_source_notes(Path(source_root or DEFAULT_SOURCE_ROOT))
    else:
        raise ValueError(f"Unsupported target: {target}")

    items = [build_item_record(path, target=target, source_root=source_root) for path in files]
    items.sort(key=lambda item: item["rel_path"])
    return items


def clear_runtime_caches() -> None:
    _LOCAL_ENCODER_CACHE.clear()
    _QUERY_VECTOR_CACHE.clear()


def _get_local_encoder(model: str) -> Any:
    encoder = _LOCAL_ENCODER_CACHE.get(model)
    if encoder is not None:
        return encoder

    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "Local embedding backend requires sentence-transformers. "
            "Install with: ./scripts/setup-venv.sh --embeddings"
        ) from exc

    encoder = SentenceTransformer(model)
    _LOCAL_ENCODER_CACHE[model] = encoder
    return encoder


def _local_embed_texts(texts: list[str], model: str) -> np.ndarray:
    encoder = _get_local_encoder(model)
    vectors = encoder.encode(
        texts,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    array = np.asarray(vectors, dtype=np.float32)
    if array.ndim == 1:
        array = array.reshape(1, -1)
    return array


def _openai_embed_texts(texts: list[str], model: str) -> np.ndarray:
    api_key = ensure_openai_api_key()
    url = os.getenv("OPENAI_EMBEDDINGS_URL", "https://api.openai.com/v1/embeddings").strip()

    payload = json.dumps({"model": model, "input": texts}).encode("utf-8")
    request = urllib.request.Request(
        url=url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI embeddings request failed ({exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"OpenAI embeddings request failed: {exc}") from exc

    payload_json = json.loads(raw)
    data = payload_json.get("data", [])
    data_sorted = sorted(data, key=lambda item: int(item.get("index", 0)))
    embeddings = [item.get("embedding", []) for item in data_sorted]
    if not embeddings:
        return np.zeros((0, 0), dtype=np.float32)
    vectors = np.asarray(embeddings, dtype=np.float32)
    if vectors.ndim == 1:
        vectors = vectors.reshape(1, -1)
    return vectors


def embed_texts(texts: list[str], backend: str, model: str) -> np.ndarray:
    backend = str(backend or "").strip().lower()
    if not texts:
        return np.zeros((0, 0), dtype=np.float32)
    if backend == "local":
        return _local_embed_texts(texts, model)
    if backend == "openai":
        return _openai_embed_texts(texts, model)
    raise ValueError(f"Unsupported embedding backend: {backend}")


def embed_query_text(query: str, backend: str, model: str) -> np.ndarray:
    cache_key = (str(backend or "").strip().lower(), str(model or "").strip(), str(query))
    cached = _QUERY_VECTOR_CACHE.get(cache_key)
    if cached is not None:
        _QUERY_VECTOR_CACHE.move_to_end(cache_key)
        return cached

    vectors = embed_texts([query], backend=backend, model=model)
    _QUERY_VECTOR_CACHE[cache_key] = vectors
    _QUERY_VECTOR_CACHE.move_to_end(cache_key)
    while len(_QUERY_VECTOR_CACHE) > _QUERY_VECTOR_CACHE_MAX:
        _QUERY_VECTOR_CACHE.popitem(last=False)
    return vectors


def load_semantic_index(
    target: str,
    backend: str,
    model: str,
) -> tuple[dict[str, Any] | None, np.ndarray | None]:
    index_path = semantic_index_path(target, backend, model)
    vectors_path = semantic_vectors_path(target, backend, model)
    if not index_path.is_file() or not vectors_path.is_file():
        return None, None

    try:
        index_data = json.loads(index_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None, None

    vectors = np.load(vectors_path, allow_pickle=False)
    item_count = int(index_data.get("item_count", 0))
    if vectors.ndim != 2:
        return None, None
    if vectors.shape[0] != item_count:
        return None, None
    return index_data, np.asarray(vectors, dtype=np.float32)


def _public_item(item: dict[str, Any], row: int) -> dict[str, Any]:
    return {
        "id": str(item.get("id", "")),
        "rel_path": str(item.get("rel_path", "")),
        "abs_path": str(item.get("abs_path", "")),
        "type": str(item.get("type", "")),
        "scope": str(item.get("scope", "")),
        "status": str(item.get("status", "")),
        "lang": str(item.get("lang", "")),
        "updated": str(item.get("updated", "")),
        "content_hash": str(item.get("content_hash", "")),
        "row": int(row),
    }


def write_semantic_index(
    target: str,
    backend: str,
    model: str,
    source_root: Path,
    items: list[dict[str, Any]],
    vectors: np.ndarray,
) -> dict[str, Any]:
    target_dir = semantic_dir(target, backend, model)
    target_dir.mkdir(parents=True, exist_ok=True)

    dims = int(vectors.shape[1]) if vectors.ndim == 2 and vectors.shape[0] > 0 else 0
    index_payload = {
        "version": 1,
        "backend": backend,
        "model": model,
        "dims": dims,
        "corpus": target,
        "source_root": source_root.as_posix(),
        "built_at": now_iso(),
        "item_count": len(items),
        "items": [_public_item(item, row) for row, item in enumerate(items)],
    }

    np.save(semantic_vectors_path(target, backend, model), vectors.astype(np.float32), allow_pickle=False)
    semantic_index_path(target, backend, model).write_text(
        json.dumps(index_payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return index_payload


def _previous_vector_map(
    previous_index: dict[str, Any] | None,
    previous_vectors: np.ndarray | None,
) -> dict[str, dict[str, Any]]:
    mapping: dict[str, dict[str, Any]] = {}
    if not previous_index or previous_vectors is None:
        return mapping

    for item in previous_index.get("items", []):
        item_id = str(item.get("id", ""))
        row = int(item.get("row", -1))
        if not item_id or row < 0 or row >= previous_vectors.shape[0]:
            continue
        mapping[item_id] = {
            "content_hash": str(item.get("content_hash", "")),
            "vector": np.asarray(previous_vectors[row], dtype=np.float32),
        }
    return mapping


def _rebuild_target_index(
    target: str,
    backend: str,
    model: str,
    source_root: Path,
) -> dict[str, Any]:
    items = collect_target_items(target=target, source_root=source_root)
    previous_index, previous_vectors = load_semantic_index(target, backend, model)
    prev_map = _previous_vector_map(previous_index, previous_vectors)

    vectors: np.ndarray | None = None
    embed_positions: list[int] = []
    embed_batch: list[str] = []
    reused_count = 0

    for idx, item in enumerate(items):
        prev = prev_map.get(item["id"])
        if prev and prev["content_hash"] == item["content_hash"]:
            prev_vec = np.asarray(prev["vector"], dtype=np.float32)
            if vectors is None:
                vectors = np.zeros((len(items), prev_vec.shape[0]), dtype=np.float32)
            if prev_vec.shape[0] == vectors.shape[1]:
                vectors[idx] = prev_vec
                reused_count += 1
                continue

        embed_positions.append(idx)
        embed_batch.append(item["embedding_text"])

    if embed_batch:
        embedded = embed_texts(embed_batch, backend=backend, model=model)
        if embedded.ndim != 2:
            raise RuntimeError("Embedding backend returned invalid vector shape")

        if vectors is None:
            vectors = np.zeros((len(items), embedded.shape[1]), dtype=np.float32)
        elif vectors.shape[1] != embedded.shape[1]:
            # Model dimensions changed. Reinitialize and perform shape-safe reuse.
            vectors = np.zeros((len(items), embedded.shape[1]), dtype=np.float32)
            reused_count = 0
            for idx, item in enumerate(items):
                prev = prev_map.get(item["id"])
                if not prev or prev["content_hash"] != item["content_hash"]:
                    continue
                prev_vec = np.asarray(prev["vector"], dtype=np.float32)
                if prev_vec.shape[0] == embedded.shape[1]:
                    vectors[idx] = prev_vec
                    reused_count += 1

        for batch_idx, item_idx in enumerate(embed_positions):
            vectors[item_idx] = embedded[batch_idx]

    if vectors is None:
        vectors = np.zeros((len(items), 0), dtype=np.float32)

    index_payload = write_semantic_index(
        target=target,
        backend=backend,
        model=model,
        source_root=source_root,
        items=items,
        vectors=vectors,
    )

    prev_ids = {
        str(item.get("id", ""))
        for item in (previous_index or {}).get("items", [])
        if str(item.get("id", ""))
    }
    curr_ids = {item["id"] for item in items}

    return {
        "target": target,
        "backend": backend,
        "model": model,
        "dims": int(index_payload.get("dims", 0)),
        "item_count": len(items),
        "embedded_count": len(embed_positions),
        "reused_count": reused_count,
        "removed_count": len(prev_ids - curr_ids),
        "index_path": semantic_index_path(target, backend, model).as_posix(),
        "vectors_path": semantic_vectors_path(target, backend, model).as_posix(),
        "source_root": source_root.as_posix(),
        "built_at": index_payload.get("built_at", ""),
    }


def load_semantic_manifest() -> dict[str, Any]:
    if not SEMANTIC_MANIFEST_PATH.is_file():
        return {"version": 1, "updated": "", "targets": {}}
    try:
        payload = json.loads(SEMANTIC_MANIFEST_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"version": 1, "updated": "", "targets": {}}

    if not isinstance(payload, dict):
        return {"version": 1, "updated": "", "targets": {}}
    payload.setdefault("targets", {})
    return payload


def write_semantic_manifest(manifest: dict[str, Any]) -> None:
    ensure_parent(SEMANTIC_MANIFEST_PATH)
    SEMANTIC_MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def build_indices(
    target: str,
    backend: str,
    model: str | None = None,
    source_root: Path | None = None,
    write_manifest: bool = True,
    append_timeline: bool = True,
) -> dict[str, Any]:
    backend = str(backend or "").strip().lower()
    if backend not in SUPPORTED_BACKENDS:
        raise ValueError(f"Unsupported embedding backend: {backend}")
    if backend == "openai":
        ensure_openai_api_key()

    target = str(target or "").strip().lower()
    if target not in SUPPORTED_TARGETS:
        raise ValueError(f"Unsupported target: {target}")

    resolved_model = str(model or default_model_for_backend(backend)).strip()
    resolved_source_root = Path(source_root or DEFAULT_SOURCE_ROOT).expanduser().resolve()

    targets = ["ledger", "source"] if target == "both" else [target]
    results: list[dict[str, Any]] = []

    for current_target in targets:
        current_root = corpus_root_for_target(current_target, source_root=resolved_source_root)
        result = _rebuild_target_index(
            target=current_target,
            backend=backend,
            model=resolved_model,
            source_root=current_root,
        )
        results.append(result)

    if write_manifest:
        manifest = load_semantic_manifest()
        manifest["version"] = 1
        manifest["updated"] = now_iso()
        targets_payload = manifest.setdefault("targets", {})

        for result in results:
            target_name = result["target"]
            targets_payload.setdefault(target_name, {})
            model_key = f"{result['backend']}__{sanitize_model_key(result['model'])}"
            targets_payload[target_name][model_key] = {
                "backend": result["backend"],
                "model": result["model"],
                "dims": result["dims"],
                "item_count": result["item_count"],
                "embedded_count": result["embedded_count"],
                "reused_count": result["reused_count"],
                "removed_count": result["removed_count"],
                "source_root": result["source_root"],
                "index_path": result["index_path"],
                "vectors_path": result["vectors_path"],
                "built_at": result["built_at"],
            }
            targets_payload[target_name]["latest"] = {
                "backend": result["backend"],
                "model": result["model"],
                "built_at": result["built_at"],
            }

        write_semantic_manifest(manifest)
        if append_timeline:
            append_timeline_entry(
                action="updated",
                rel_path="notes/08_indices/semantic_manifest.json",
                description=f"updated semantic embedding manifest ({backend}/{resolved_model})",
            )

    return {
        "target": target,
        "backend": backend,
        "model": resolved_model,
        "results": results,
    }


def index_status(target: str) -> dict[str, Any]:
    target = str(target or "").strip().lower()
    if target not in SUPPORTED_TARGETS:
        raise ValueError(f"Unsupported target: {target}")

    targets = ["ledger", "source"] if target == "both" else [target]
    output: dict[str, Any] = {"target": target, "targets": {}}

    for current_target in targets:
        target_root = SEMANTIC_ROOT / current_target
        entries = []
        if target_root.is_dir():
            for model_dir in sorted(path for path in target_root.iterdir() if path.is_dir()):
                index_path = model_dir / "index.json"
                vectors_path = model_dir / "vectors.npy"
                if not index_path.is_file() or not vectors_path.is_file():
                    continue
                try:
                    index_data = json.loads(index_path.read_text(encoding="utf-8"))
                    vectors = np.load(vectors_path, allow_pickle=False)
                    dims = (
                        int(vectors.shape[1])
                        if vectors.ndim == 2 and vectors.shape[0] > 0
                        else int(index_data.get("dims", 0))
                    )
                except Exception:
                    index_data = {}
                    dims = 0

                entries.append(
                    {
                        "name": model_dir.name,
                        "backend": str(index_data.get("backend", "")),
                        "model": str(index_data.get("model", "")),
                        "item_count": int(index_data.get("item_count", 0)),
                        "dims": dims,
                        "built_at": str(index_data.get("built_at", "")),
                        "index_path": index_path.as_posix(),
                        "vectors_path": vectors_path.as_posix(),
                    }
                )

        output["targets"][current_target] = entries

    return output


def clean_indices(target: str) -> dict[str, Any]:
    target = str(target or "").strip().lower()
    if target not in SUPPORTED_TARGETS:
        raise ValueError(f"Unsupported target: {target}")

    targets = ["ledger", "source"] if target == "both" else [target]
    removed: list[str] = []

    for current_target in targets:
        path = SEMANTIC_ROOT / current_target
        if path.exists():
            shutil.rmtree(path)
            removed.append(path.as_posix())

    return {"target": target, "removed": removed}


def _normalize_rows(vectors: np.ndarray) -> np.ndarray:
    if vectors.size == 0:
        return vectors
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vectors / norms


def _cosine_scores(vectors: np.ndarray, query_vector: np.ndarray) -> np.ndarray:
    if vectors.size == 0:
        return np.zeros((0,), dtype=np.float32)
    q = np.asarray(query_vector, dtype=np.float32)
    if q.ndim != 1:
        q = q.reshape(-1)
    q_norm = np.linalg.norm(q)
    if q_norm == 0:
        return np.zeros((vectors.shape[0],), dtype=np.float32)
    q = q / q_norm
    matrix = _normalize_rows(np.asarray(vectors, dtype=np.float32))
    return matrix @ q


def _validate_semantic_search_inputs(
    target: str,
    backend: str,
    allow_api_on_source: bool,
) -> tuple[str, str]:
    backend = str(backend or "").strip().lower()
    target = str(target or "").strip().lower()

    if backend not in SUPPORTED_BACKENDS:
        raise ValueError(f"Unsupported embedding backend: {backend}")
    if target not in ("ledger", "source"):
        raise ValueError(f"Unsupported semantic search target: {target}")

    if target == "source" and backend == "openai" and not allow_api_on_source:
        raise RuntimeError(
            "OpenAI backend for source discovery is blocked by default. "
            "Pass --allow-api-on-source to confirm intentional externalization."
        )

    return target, backend


def semantic_score_map(
    query: str,
    target: str,
    backend: str,
    model: str | None = None,
    source_root: Path | None = None,
    allow_api_on_source: bool = False,
) -> dict[str, Any]:
    del source_root  # Reserved for future target-specific query-time checks.

    target, backend = _validate_semantic_search_inputs(target, backend, allow_api_on_source)
    if backend == "openai":
        ensure_openai_api_key()
    resolved_model = str(model or default_model_for_backend(backend)).strip()

    index_data, vectors = load_semantic_index(target, backend, resolved_model)
    if index_data is None or vectors is None:
        return {
            "available": False,
            "reason": "missing_index",
            "target": target,
            "backend": backend,
            "model": resolved_model,
            "results": [],
            "score_by_id": {},
            "score_by_rel_path": {},
        }

    query_vector = embed_query_text(query, backend=backend, model=resolved_model)
    if query_vector.ndim != 2 or query_vector.shape[0] == 0:
        return {
            "available": False,
            "reason": "empty_query_vector",
            "target": target,
            "backend": backend,
            "model": resolved_model,
            "results": [],
            "score_by_id": {},
            "score_by_rel_path": {},
        }

    scores = _cosine_scores(vectors, query_vector[0])
    items = index_data.get("items", [])

    ranked_rows = sorted(
        range(min(len(items), scores.shape[0])),
        key=lambda idx: float(scores[idx]),
        reverse=True,
    )

    results: list[dict[str, Any]] = []
    score_by_id: dict[str, float] = {}
    score_by_rel_path: dict[str, float] = {}

    for idx in ranked_rows:
        item = dict(items[idx])
        cosine = float(scores[idx])
        item["cosine_similarity"] = cosine
        results.append(item)

        item_id = str(item.get("id", ""))
        rel_path = str(item.get("rel_path", ""))
        if item_id:
            score_by_id[item_id] = cosine
        if rel_path:
            score_by_rel_path[rel_path] = cosine

    return {
        "available": True,
        "target": target,
        "backend": backend,
        "model": resolved_model,
        "index_item_count": int(index_data.get("item_count", 0)),
        "results": results,
        "score_by_id": score_by_id,
        "score_by_rel_path": score_by_rel_path,
    }


def semantic_search(
    query: str,
    target: str,
    backend: str,
    model: str | None = None,
    limit: int = 20,
    source_root: Path | None = None,
    allow_api_on_source: bool = False,
) -> dict[str, Any]:
    payload = semantic_score_map(
        query=query,
        target=target,
        backend=backend,
        model=model,
        source_root=source_root,
        allow_api_on_source=allow_api_on_source,
    )

    if not payload.get("available"):
        return payload

    top_limit = max(1, int(limit))
    payload["results"] = payload["results"][:top_limit]
    return payload
