"""
TOON (Token-Oriented Object Notation) — compact graph serialization for LLMs.

Format::

    meta:
      key: value

    nodes[N]{col1,col2,...}:
      val1,val2,...

    edges[M]{col1,col2,...}:
      val1,val2,...

Rules:
- Strings with commas, newlines, or equal to "null"/"true"/"false" are double-quoted.
  Internal ``"`` characters are escaped as ``\\"``.
- ``None`` → ``null``, ``True`` → ``true``, ``False`` → ``false``, numbers unquoted.
- Rows are parsed with the ``csv`` module (handles quoted commas).
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

import networkx as nx

_NODE_COLS = [
    "id", "type", "kind", "name", "file", "line",
    "namespace", "community_id", "server_url", "labels", "expression",
]
_EDGE_COLS = ["from", "to", "type", "confidence", "provenance"]

# Values that must be quoted even without commas/newlines (to avoid ambiguity)
_MUST_QUOTE_LITERALS = {"null", "true", "false"}


def _cell(v: Any) -> str:
    """Encode one cell value to its TOON string representation."""
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    # Must quote if contains comma, newline, or matches reserved literals
    if "," in s or "\n" in s or "\r" in s or s in _MUST_QUOTE_LITERALS or s.startswith('"'):
        # Escape internal double-quotes, then wrap
        escaped = s.replace('"', '\\"')
        return f'"{escaped}"'
    return s


def _parse_cell(s: str, hint: str | None = None) -> Any:
    """Decode one TOON cell string to a Python value."""
    if s == "null":
        return None
    if s == "true":
        return True
    if s == "false":
        return False
    # Quoted string: strip outer quotes and unescape
    if s.startswith('"') and s.endswith('"') and len(s) >= 2:
        inner = s[1:-1].replace('\\"', '"')
        return inner
    # Try int/float
    try:
        if "." in s:
            return float(s)
        return int(s)
    except ValueError:
        pass
    return s


def _encode_labels(labels: Any) -> str:
    """Encode labels dict as a JSON string cell, or null."""
    if not labels:
        return "null"
    return json.dumps(labels, separators=(",", ":"))


def _decode_labels(s: str) -> dict:
    """Decode a labels cell back to a dict."""
    if s is None or s == "null":
        return {}
    try:
        return json.loads(s)
    except Exception:
        return {}


def dumps_graph(graph: nx.DiGraph, meta: dict | None = None) -> str:
    """Serialize a NetworkX DiGraph to a TOON string."""
    lines: list[str] = []

    # ── meta section ─────────────────────────────────────────────────────────
    if meta:
        lines.append("meta:")
        for k, v in meta.items():
            lines.append(f"  {k}: {v}")
        lines.append("")

    # ── nodes section ─────────────────────────────────────────────────────────
    nodes = list(graph.nodes(data=True))
    lines.append(f"nodes[{len(nodes)}]{{{','.join(_NODE_COLS)}}}:")
    for nid, attrs in nodes:
        row_vals: list[str] = []
        for col in _NODE_COLS:
            if col == "id":
                row_vals.append(_cell(nid))
            elif col == "labels":
                raw = attrs.get("labels") or {}
                row_vals.append(_cell(_encode_labels(raw)))
            else:
                row_vals.append(_cell(attrs.get(col)))
        # Use csv writer for proper quoting of the row
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="")
        writer.writerow(row_vals)
        lines.append(f"  {buf.getvalue()}")

    lines.append("")

    # ── edges section ─────────────────────────────────────────────────────────
    edges = list(graph.edges(data=True))
    lines.append(f"edges[{len(edges)}]{{{','.join(_EDGE_COLS)}}}:")
    for frm, to, attrs in edges:
        row_vals = []
        for col in _EDGE_COLS:
            if col == "from":
                row_vals.append(_cell(frm))
            elif col == "to":
                row_vals.append(_cell(to))
            else:
                row_vals.append(_cell(attrs.get(col)))
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="")
        writer.writerow(row_vals)
        lines.append(f"  {buf.getvalue()}")

    lines.append("")
    return "\n".join(lines)


def loads_graph(text: str) -> tuple[nx.DiGraph, dict]:
    """Parse a TOON string and return (graph, meta)."""
    graph = nx.DiGraph()
    meta: dict = {}

    section = None
    node_cols: list[str] = []
    edge_cols: list[str] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()

        # Skip blank lines outside sections
        if not line:
            section = None
            continue

        # ── meta section ─────────────────────────────────────────────────────
        if line == "meta:":
            section = "meta"
            continue

        if section == "meta" and raw_line.startswith("  "):
            kv = line.strip()
            if ":" in kv:
                k, _, v = kv.partition(":")
                meta[k.strip()] = v.strip()
            continue

        # ── nodes header ─────────────────────────────────────────────────────
        if line.startswith("nodes["):
            section = "nodes"
            # Extract column names from {col1,col2,...}
            brace_start = line.index("{")
            brace_end = line.index("}")
            node_cols = line[brace_start + 1 : brace_end].split(",")
            continue

        # ── edges header ─────────────────────────────────────────────────────
        if line.startswith("edges["):
            section = "edges"
            brace_start = line.index("{")
            brace_end = line.index("}")
            edge_cols = line[brace_start + 1 : brace_end].split(",")
            continue

        # ── data rows ─────────────────────────────────────────────────────────
        if section == "nodes" and raw_line.startswith("  "):
            row_text = raw_line.strip()
            reader = csv.reader(io.StringIO(row_text))
            for row in reader:
                if not row:
                    continue
                attrs: dict[str, Any] = {}
                nid: str | None = None
                for i, col in enumerate(node_cols):
                    raw_val = row[i] if i < len(row) else "null"
                    if col == "id":
                        nid = str(_parse_cell(raw_val))
                    elif col == "labels":
                        inner = _parse_cell(raw_val)
                        attrs["labels"] = _decode_labels(inner if isinstance(inner, str) else raw_val)
                    else:
                        attrs[col] = _parse_cell(raw_val)
                if nid is not None:
                    graph.add_node(nid, **attrs)

        elif section == "edges" and raw_line.startswith("  "):
            row_text = raw_line.strip()
            reader = csv.reader(io.StringIO(row_text))
            for row in reader:
                if not row:
                    continue
                frm: str | None = None
                to: str | None = None
                eattrs: dict[str, Any] = {}
                for i, col in enumerate(edge_cols):
                    raw_val = row[i] if i < len(row) else "null"
                    parsed = _parse_cell(raw_val)
                    if col == "from":
                        frm = str(parsed) if parsed is not None else None
                    elif col == "to":
                        to = str(parsed) if parsed is not None else None
                    else:
                        eattrs[col] = parsed
                if frm and to:
                    graph.add_edge(frm, to, **eattrs)

    return graph, meta


def dump_graph(graph: nx.DiGraph, path: Path | str, meta: dict | None = None) -> None:
    """Write a graph to a .toon file."""
    text = dumps_graph(graph, meta)
    Path(path).write_text(text, encoding="utf-8")


def load_graph(path: Path | str) -> tuple[nx.DiGraph, dict]:
    """Read a .toon file and return (graph, meta)."""
    text = Path(path).read_text(encoding="utf-8")
    return loads_graph(text)
