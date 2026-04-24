"""
YAML dispatcher: detects file type and routes to the correct sub-parser.

Handles: Kubernetes manifests, GitHub Actions workflows, Docker Compose,
Helm charts, Kustomize overlays, Helm templates (Go {{}} directives stripped),
Ansible playbooks and task files, and any other YAML file (generic fallback).
"""

from __future__ import annotations

import re
import warnings
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from .actions_schema import ActionsParser
from .ansible_schema import AnsibleParser
from .compose_schema import ComposeParser
from .helm_schema import HelmParser
from .k8s_schema import KubernetesParser, is_kubernetes_file

_yaml = YAML()
_yaml.preserve_quotes = True

# Detects whether a file contains any Helm/Go template directives
_HELM_DIRECTIVE_RE = re.compile(r"\{\{")


def _strip_helm_directives(text: str) -> str:
    """
    Strip Go/Helm template directives so the static YAML structure is parseable.

    Strategy (line-by-line, no cross-line regex):
    - A line whose ENTIRE non-whitespace content is `{{...}}` expressions is
      dropped (e.g. ``{{- if .Values.azure.enabled }}``,
      ``{{- toYaml .Values.foo | nindent 4 }}``).
    - Lines that contain a mix of real YAML and inline expressions have the
      ``{{...}}`` replaced with the safe YAML string ``__helm__``
      (e.g. ``name: {{ .Values.name | quote }}`` → ``name: __helm__``).

    Greedy per-line replacement handles Helm's nested-brace patterns like
    ``'{{ printf "{{.name}}" }}'`` correctly — the outermost {{ to }} pair
    is replaced as a unit.
    """
    lines: list[str] = []
    for line in text.splitlines():
        without = re.sub(r"\{\{.*\}\}", "", line)
        if without.strip() == "":
            continue
        cleaned = re.sub(r"\{\{.*\}\}", "__helm__", line)
        lines.append(cleaned)
    return "\n".join(lines)


class YAMLParser:
    """Dispatching parser for all YAML-based infrastructure files."""

    def __init__(self) -> None:
        self._k8s = KubernetesParser()
        self._actions = ActionsParser()
        self._compose = ComposeParser()
        self._helm = HelmParser()
        self._ansible = AnsibleParser()

    @property
    def k8s_parser(self) -> KubernetesParser:
        return self._k8s

    def parse_file(self, path: Path) -> dict[str, Any]:
        """
        Auto-detect file type and parse.
        Returns {"nodes": [...], "edges": [...]}.

        Dispatch order (first match wins):
        1. Helm / Kustomize  — by filename
        2. GitHub Actions    — by path pattern
        3. Docker Compose    — by filename
        4. Ansible           — by content sniff (playbook list or task file)
        5. Kubernetes / CRD  — any YAML with apiVersion + kind + metadata
        6. Generic fallback  — any other parseable YAML dict → config node
        """
        empty: dict[str, Any] = {"nodes": [], "edges": []}

        # ── Helm / Kustomize (by filename, no YAML sniff needed) ─────────────
        helm_result = self._helm.parse_file(path)
        if helm_result is not None:
            return helm_result

        # ── GitHub Actions (by path pattern) ─────────────────────────────────
        if self._actions.is_actions_file(path):
            return self._actions.parse_file(path)

        # ── Docker Compose (by filename) ──────────────────────────────────────
        if self._compose.is_compose_file(path):
            return self._compose.parse_file(path)

        if path.suffix not in (".yml", ".yaml"):
            return empty

        # ── Ansible (content sniff — before K8s so playbooks aren't mis-routed) ─
        if self._ansible.is_ansible_file(path):
            return self._ansible.parse_file(path)

        # ── Read and optionally strip Helm directives ─────────────────────────
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as exc:
            warnings.warn(f"[yaml_parser] Cannot read {path}: {exc}")
            return empty

        is_helm_template = bool(_HELM_DIRECTIVE_RE.search(text))
        if is_helm_template:
            text = _strip_helm_directives(text)

        try:
            docs = list(_yaml.load_all(text))
        except Exception as exc:
            if not is_helm_template:
                warnings.warn(f"[yaml_parser] Cannot parse YAML in {path}: {exc}")
            return empty

        # ── Kubernetes / CRD (any apiVersion + kind + metadata) ──────────────
        k8s_docs = [d for d in docs if isinstance(d, dict) and is_kubernetes_file(d)]
        if k8s_docs:
            return self._k8s.parse_file(
                path, preprocessed_text=text if is_helm_template else None
            )

        # ── Generic YAML fallback — any parseable YAML dict → config node ─────
        for doc in docs:
            if isinstance(doc, dict) and doc:
                config_id = f"config/{path.stem}"
                return {
                    "nodes": [{
                        "id": config_id,
                        "type": "config",
                        "kind": "generic_yaml",
                        "name": path.stem,
                        "file": str(path),
                        "line": None,
                        "labels": {},
                        "community_id": None,
                    }],
                    "edges": [],
                }

        return empty

    def finalize(self) -> list[dict]:
        """
        Call after all files are parsed.
        Runs selector resolution for Kubernetes and returns extra edges.
        """
        extra_edges = self._k8s.resolve_selectors()
        extra_edges += self._k8s.resolve_cluster_selectors()
        return extra_edges
