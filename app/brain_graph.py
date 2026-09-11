#!/usr/bin/env python3
"""The Brain: the vault's wikilink graph, as nodes and edges.

Walks every markdown file in the vault, resolves `[[wikilinks]]` against the
note stems, and returns a graph the Workbench draws in the same ink as the rest
of the product (DESIGN.md, "The Brain").

Resolution rules, in order:

1. A path-qualified link (`[[people/Jane Roe]]`) matches that exact relative
   path first, then falls back to its basename.
2. A bare link matches on lowercased stem. When several notes share a stem, the
   one in the linking note's own folder wins, otherwise the shortest path does,
   which is stable regardless of walk order.
3. A link that resolves to nothing is dropped rather than inventing a node. A
   graph should show what the vault contains, not what it mentions.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from pathlib import Path

from config_loader import vault

_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")

# Folders that are machinery rather than thought.
SKIP_DIRS = {".obsidian", ".trash", ".git", ".smart-env", "node_modules"}
SKIP_RELATIVE = ("van-gogh/logs", "van-gogh/workbench")

RECENT_DAYS = 7
_CACHE_TTL = 300.0
_cache: dict = {"at": 0.0, "graph": None}


def _walk(root: Path) -> list:
    out = []
    for path in root.rglob("*.md"):
        parts = set(path.relative_to(root).parts)
        if parts & SKIP_DIRS:
            continue
        rel = path.relative_to(root).as_posix()
        if any(rel.startswith(prefix) for prefix in SKIP_RELATIVE):
            continue
        out.append(path)
    return sorted(out)


def build(root: Path | None = None) -> dict:
    """Build the graph. Returns `{nodes, edges, generated_at, note_count}`."""
    root = vault() if root is None else Path(root)
    if not root.is_dir():
        return {"nodes": [], "edges": [], "generated_at": datetime.now().isoformat(timespec="seconds"),
                "note_count": 0, "error": "vault not found"}

    files = _walk(root)
    recent_cutoff = (datetime.now() - timedelta(days=RECENT_DAYS)).timestamp()

    by_rel: dict = {}
    by_stem: dict = {}
    nodes: dict = {}
    for path in files:
        rel = path.relative_to(root).as_posix()
        stem = path.stem
        by_rel[rel.lower()] = rel
        by_rel[rel.lower().removesuffix(".md")] = rel
        by_stem.setdefault(stem.lower(), []).append(rel)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        nodes[rel] = {
            "id": rel,
            "label": stem,
            "folder": rel.split("/")[0] if "/" in rel else "",
            "recent": mtime >= recent_cutoff,
            "degree": 0,
        }

    def resolve(target: str, source_rel: str) -> str | None:
        key = target.strip().lower()
        if not key:
            return None
        hit = by_rel.get(key) or by_rel.get(key + ".md")
        if hit:
            return hit
        base = key.rsplit("/", 1)[-1]
        candidates = by_stem.get(base)
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        folder = source_rel.rsplit("/", 1)[0] if "/" in source_rel else ""
        same = [c for c in candidates if (c.rsplit("/", 1)[0] if "/" in c else "") == folder]
        pool = same or candidates
        return min(pool, key=lambda c: (c.count("/"), len(c), c))

    edges: set = set()
    for path in files:
        rel = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in _WIKILINK_RE.finditer(text):
            target = resolve(match.group(1), rel)
            if target and target != rel:
                edges.add((rel, target))

    for source, target in edges:
        nodes[source]["degree"] += 1
        nodes[target]["degree"] += 1

    return {
        "nodes": list(nodes.values()),
        "edges": [{"s": s, "t": t} for s, t in sorted(edges)],
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "note_count": len(files),
    }


def cached(rebuild: bool = False) -> dict:
    """Build at most once every five minutes. A vault walk is cheap but not free."""
    now = time.time()
    if rebuild or _cache["graph"] is None or now - _cache["at"] > _CACHE_TTL:
        _cache["graph"] = build()
        _cache["at"] = now
    return _cache["graph"]


def search(query: str, graph: dict | None = None, limit: int = 60) -> list:
    """Node ids whose label or path contains every whitespace-separated term."""
    terms = [t for t in (query or "").lower().split() if t]
    if not terms:
        return []
    g = cached() if graph is None else graph
    hits = [
        node["id"] for node in g["nodes"]
        if all(t in node["id"].lower() or t in node["label"].lower() for t in terms)
    ]
    hits.sort(key=lambda i: (len(i), i))
    return hits[:limit]


def neighbors(node_id: str, graph: dict | None = None) -> dict:
    """Inbound and outbound links for one note, for the Brain side panel."""
    g = cached() if graph is None else graph
    out = sorted(e["t"] for e in g["edges"] if e["s"] == node_id)
    inbound = sorted(e["s"] for e in g["edges"] if e["t"] == node_id)
    return {"id": node_id, "outbound": out, "inbound": inbound}


if __name__ == "__main__":
    import json
    print(json.dumps(build(), indent=2))
