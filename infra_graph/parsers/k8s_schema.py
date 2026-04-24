"""
Kubernetes, ArgoCD, cert-manager, ExternalSecrets, Istio, Flux CD,
Prometheus Operator, Argo Rollouts, KEDA, and Gateway API resource parser.

Any YAML document with apiVersion + kind + metadata produces a node.
Known kinds receive typed edge extraction; unknown CRDs produce a node only.
"""

from __future__ import annotations

import warnings
from collections import defaultdict
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

_yaml = YAML()
_yaml.preserve_quotes = True

# ── Core Kubernetes kinds ──────────────────────────────────────────────────────
K8S_KINDS = {
    "Deployment", "Service", "ConfigMap", "Secret", "Ingress",
    "Namespace", "StatefulSet", "DaemonSet", "HorizontalPodAutoscaler",
    "PersistentVolumeClaim", "ServiceAccount",
}

# ── ArgoCD kinds (argoproj.io) ────────────────────────────────────────────────
ARGOCD_KINDS = {"AppProject", "Application", "ApplicationSet"}

# ── cert-manager kinds (cert-manager.io) ──────────────────────────────────────
CERTMANAGER_KINDS = {"ClusterIssuer", "Issuer", "Certificate", "CertificateRequest"}

# ── External Secrets Operator kinds (external-secrets.io) ────────────────────
ESO_KINDS = {
    "ExternalSecret", "ClusterExternalSecret",
    "SecretStore", "ClusterSecretStore",
    "PushSecret",
}

# ── Istio (networking.istio.io / security.istio.io) ───────────────────────────
ISTIO_KINDS = {
    "VirtualService", "DestinationRule", "ServiceEntry",
    "PeerAuthentication", "AuthorizationPolicy", "EnvoyFilter",
    "Sidecar", "RequestAuthentication", "WorkloadEntry",
    "Gateway",  # also used by Gateway API; disambiguated by apiVersion in dispatcher
}

# ── Flux CD (*.toolkit.fluxcd.io) ─────────────────────────────────────────────
FLUX_KINDS = {
    "HelmRelease", "HelmRepository", "HelmChart", "GitRepository",
    "OCIRepository", "Bucket", "Kustomization",
    "ImageRepository", "ImagePolicy", "ImageUpdateAutomation",
    "Alert", "Provider", "Receiver",
}

# ── Prometheus Operator (monitoring.coreos.com) ───────────────────────────────
PROM_KINDS = {
    "Prometheus", "PrometheusRule", "ServiceMonitor", "PodMonitor",
    "Alertmanager", "AlertmanagerConfig", "ThanosRuler",
}

# ── Argo Rollouts (argoproj.io — shares prefix with ArgoCD) ──────────────────
ROLLOUTS_KINDS = {"Rollout", "AnalysisTemplate", "AnalysisRun", "Experiment"}

# ── KEDA (keda.sh) ────────────────────────────────────────────────────────────
KEDA_KINDS = {
    "ScaledObject", "ScaledJob",
    "TriggerAuthentication", "ClusterTriggerAuthentication",
}

# ── Kubernetes Gateway API (gateway.networking.k8s.io) ───────────────────────
GATEWAY_API_KINDS = {
    "GatewayClass", "HTTPRoute", "GRPCRoute", "TCPRoute", "TLSRoute", "ReferenceGrant",
}

# All known kinds — node creation + edge extraction for these; unknown CRDs
# still get a node (no longer a gate), just no edge extraction.
ALL_KNOWN_KINDS = (
    K8S_KINDS | ARGOCD_KINDS | CERTMANAGER_KINDS | ESO_KINDS
    | ISTIO_KINDS | FLUX_KINDS | PROM_KINDS | ROLLOUTS_KINDS
    | KEDA_KINDS | GATEWAY_API_KINDS
)

# ── API group prefix constants for disambiguation ─────────────────────────────
_ARGOCD_API_PREFIXES = ("argoproj.io",)
_CERTMANAGER_API_PREFIXES = ("cert-manager.io",)
_ESO_API_PREFIXES = ("external-secrets.io",)
_ISTIO_API_PREFIXES = ("networking.istio.io", "security.istio.io", "istio.io")
_FLUX_API_PREFIXES = (
    "helm.toolkit.fluxcd.io", "kustomize.toolkit.fluxcd.io",
    "notification.toolkit.fluxcd.io", "source.toolkit.fluxcd.io",
    "image.toolkit.fluxcd.io",
)
_PROM_API_PREFIXES = ("monitoring.coreos.com",)
_ROLLOUTS_API_PREFIXES = ("argoproj.io",)
_KEDA_API_PREFIXES = ("keda.sh",)
_GATEWAY_API_PREFIXES = ("gateway.networking.k8s.io",)

# Placeholder value injected by the Helm template stripper
_HELM_PLACEHOLDER = "__helm__"

# Kind → display type (shorten verbose names)
_KIND_ALIAS: dict[str, str] = {
    "HorizontalPodAutoscaler": "HPA",
    "PersistentVolumeClaim": "PVC",
}


def _norm_kind(kind: str) -> str:
    return _KIND_ALIAS.get(kind, kind)


def _node_id(kind: str, namespace: str, name: str) -> str:
    return f"{_norm_kind(kind)}/{namespace}/{name}"


def _get_labels(spec: dict) -> dict[str, str]:
    raw = spec.get("labels") or {}
    return {k: v for k, v in raw.items() if isinstance(v, str)}


def _safe_str(v: Any) -> str | None:
    """Return v as string if it's a meaningful non-placeholder value, else None."""
    if v is None:
        return None
    s = str(v)
    if _HELM_PLACEHOLDER in s or s.strip() == "":
        return None
    return s


class KubernetesParser:
    """
    Parse Kubernetes, ArgoCD, cert-manager, ESO, Istio, Flux CD,
    Prometheus Operator, Argo Rollouts, KEDA, and Gateway API manifests.

    Any document with apiVersion + kind + metadata produces a node.
    Known kinds receive typed edge extraction; unknown CRDs get a node only.

    Usage:
        parser = KubernetesParser()
        result = parser.parse_file(path)
        extra_edges = parser.resolve_selectors()          # call after all files
        extra_edges += parser.resolve_cluster_selectors() # call after all files
    """

    def __init__(self) -> None:
        self._label_index: dict[str, list[str]] = defaultdict(list)
        self._all_nodes: dict[str, dict] = {}
        # Pending cluster selectors: (node_id, selector_dict, namespace)
        self._pending_cluster_selectors: list[tuple[str, dict, str]] = []

    def parse_file(
        self,
        path: Path,
        preprocessed_text: str | None = None,
    ) -> dict[str, Any]:
        """
        Parse a single manifest file (may contain multiple YAML documents).

        ``preprocessed_text`` is used when the caller has already stripped
        Helm template directives — the file on disk is not re-read in that case.
        """
        nodes: list[dict] = []
        edges: list[dict] = []

        try:
            text = preprocessed_text if preprocessed_text is not None else path.read_text(encoding="utf-8")
            docs = list(_yaml.load_all(text))
        except Exception as exc:
            warnings.warn(f"[k8s_schema] Failed to parse {path}: {exc}")
            return {"nodes": nodes, "edges": edges}

        for doc in docs:
            if not isinstance(doc, dict):
                continue
            api_version = doc.get("apiVersion", "") or ""
            kind = doc.get("kind", "") or ""
            if not (api_version and kind):
                continue

            # ── Node creation for ALL K8s-style resources ────────────────────
            # Removed ALL_KNOWN_KINDS gate: unknown CRDs still get a node.
            metadata = doc.get("metadata") or {}
            name = _safe_str(metadata.get("name")) or "unknown"
            namespace = _safe_str(metadata.get("namespace")) or "default"
            node_labels = _get_labels(metadata)
            node_id = _node_id(kind, namespace, name)

            line = None
            try:
                line = doc.lc.line + 1
            except AttributeError:
                pass

            node = {
                "id": node_id,
                "type": _norm_kind(kind),
                "kind": kind,
                "name": name,
                "file": str(path),
                "line": line,
                "labels": node_labels,
                "community_id": None,
                "namespace": namespace,
                "api_version": api_version,
            }
            nodes.append(node)
            self._all_nodes[node_id] = node

            for k, v in node_labels.items():
                self._label_index[f"{k}={v}"].append(node_id)

            spec = doc.get("spec") or {}
            new_edges = self._extract_edges(kind, api_version, node_id, spec, namespace, metadata, doc)
            edges.extend(new_edges)

        return {"nodes": nodes, "edges": edges}

    # ── Edge extraction dispatcher ─────────────────────────────────────────────

    def _extract_edges(
        self,
        kind: str,
        api_version: str,
        node_id: str,
        spec: dict,
        namespace: str,
        metadata: dict,
        doc: dict,
    ) -> list[dict]:
        # ── Core Kubernetes ──────────────────────────────────────────────────
        if kind in ("Deployment", "StatefulSet", "DaemonSet"):
            pod_spec = spec.get("template", {}).get("spec", {}) or {}
            return self._extract_pod_mounts(node_id, pod_spec, namespace)

        if kind == "Service":
            selector = (spec.get("selector") or {})
            if selector and isinstance(selector, dict):
                self.store_selector(node_id, {k: v for k, v in selector.items() if isinstance(v, str)})
            return []

        if kind == "Ingress":
            return self._extract_ingress_edges(node_id, spec, namespace)

        if kind == "HorizontalPodAutoscaler":
            return self._extract_hpa_edges(node_id, spec, namespace)

        # ── ArgoCD ──────────────────────────────────────────────────────────
        if kind == "AppProject":
            return self._extract_appproject_edges(node_id, spec, namespace)

        if kind == "Application":
            return self._extract_application_edges(node_id, spec, namespace)

        if kind == "ApplicationSet":
            return self._extract_applicationset_edges(node_id, spec, namespace)

        # ── External Secrets Operator ────────────────────────────────────────
        if kind in ("ExternalSecret", "ClusterExternalSecret"):
            return self._extract_externalsecret_edges(node_id, spec, namespace)

        # ── cert-manager ────────────────────────────────────────────────────
        if kind == "Certificate":
            return self._extract_certificate_edges(node_id, spec, namespace)

        # ── Istio ────────────────────────────────────────────────────────────
        if kind == "VirtualService" and api_version.startswith(_ISTIO_API_PREFIXES):
            return self._extract_istio_virtualservice_edges(node_id, spec, namespace)

        if kind == "DestinationRule" and api_version.startswith(_ISTIO_API_PREFIXES):
            return self._extract_istio_destinationrule_edges(node_id, spec, namespace)

        # ── Flux CD ──────────────────────────────────────────────────────────
        if kind == "HelmRelease":
            return self._extract_flux_helmrelease_edges(node_id, spec, namespace)

        if kind == "Kustomization" and api_version.startswith(_FLUX_API_PREFIXES):
            return self._extract_flux_kustomization_edges(node_id, spec, namespace)

        if kind == "Alert" and api_version.startswith(_FLUX_API_PREFIXES):
            return self._extract_flux_alert_edges(node_id, spec, namespace)

        # ── Argo Rollouts ────────────────────────────────────────────────────
        # Disambiguate from ArgoCD using kind name (Rollout is only Argo Rollouts)
        if kind == "Rollout":
            return self._extract_rollout_edges(node_id, spec, namespace)

        # ── KEDA ─────────────────────────────────────────────────────────────
        if kind == "ScaledObject":
            return self._extract_keda_scaledobject_edges(node_id, spec, namespace)

        # ── Gateway API ──────────────────────────────────────────────────────
        if kind == "HTTPRoute" and api_version.startswith(_GATEWAY_API_PREFIXES):
            return self._extract_httproute_edges(node_id, spec, namespace)

        return []

    # ── Core Kubernetes ────────────────────────────────────────────────────────

    def _extract_pod_mounts(self, owner_id: str, pod_spec: dict, namespace: str) -> list[dict]:
        edges = []
        for vol in pod_spec.get("volumes") or []:
            if not isinstance(vol, dict):
                continue
            cm = vol.get("configMap") or {}
            if cm:
                cm_name = _safe_str(cm.get("name"))
                if cm_name:
                    edges.append(self._edge(owner_id, _node_id("ConfigMap", namespace, cm_name),
                                            "mounts_config", 1.0, "EXTRACTED"))
            sec = vol.get("secret") or {}
            if sec:
                sec_name = _safe_str(sec.get("secretName"))
                if sec_name:
                    edges.append(self._edge(owner_id, _node_id("Secret", namespace, sec_name),
                                            "mounts_secret", 1.0, "EXTRACTED"))

        for container in (pod_spec.get("containers") or []) + (pod_spec.get("initContainers") or []):
            if not isinstance(container, dict):
                continue
            for env in container.get("env") or []:
                if not isinstance(env, dict):
                    continue
                vfrom = env.get("valueFrom") or {}
                cm_ref = vfrom.get("configMapKeyRef") or {}
                if cm_ref:
                    cm_name = _safe_str(cm_ref.get("name"))
                    if cm_name:
                        edges.append(self._edge(owner_id, _node_id("ConfigMap", namespace, cm_name),
                                                "mounts_config", 1.0, "EXTRACTED"))
                sec_ref = vfrom.get("secretKeyRef") or {}
                if sec_ref:
                    sec_name = _safe_str(sec_ref.get("name"))
                    if sec_name:
                        edges.append(self._edge(owner_id, _node_id("Secret", namespace, sec_name),
                                                "mounts_secret", 1.0, "EXTRACTED"))
            for envfrom in container.get("envFrom") or []:
                if not isinstance(envfrom, dict):
                    continue
                cm_ref = envfrom.get("configMapRef") or {}
                if cm_ref:
                    cm_name = _safe_str(cm_ref.get("name"))
                    if cm_name:
                        edges.append(self._edge(owner_id, _node_id("ConfigMap", namespace, cm_name),
                                                "mounts_config", 1.0, "EXTRACTED"))
                sec_ref = envfrom.get("secretRef") or {}
                if sec_ref:
                    sec_name = _safe_str(sec_ref.get("name"))
                    if sec_name:
                        edges.append(self._edge(owner_id, _node_id("Secret", namespace, sec_name),
                                                "mounts_secret", 1.0, "EXTRACTED"))
        return edges

    def _extract_ingress_edges(self, ingress_id: str, spec: dict, namespace: str) -> list[dict]:
        edges = []
        for rule in spec.get("rules") or []:
            if not isinstance(rule, dict):
                continue
            for path_item in (rule.get("http") or {}).get("paths") or []:
                if not isinstance(path_item, dict):
                    continue
                backend = path_item.get("backend") or {}
                svc = backend.get("service") or backend
                svc_name = _safe_str(svc.get("name") or backend.get("serviceName"))
                if svc_name:
                    edges.append(self._edge(ingress_id, _node_id("Service", namespace, svc_name),
                                            "exposes", 1.0, "EXTRACTED"))
        return edges

    def _extract_hpa_edges(self, node_id: str, spec: dict, namespace: str) -> list[dict]:
        target = spec.get("scaleTargetRef") or {}
        target_kind = _norm_kind(_safe_str(target.get("kind")) or "")
        target_name = _safe_str(target.get("name"))
        if target_kind and target_name:
            return [self._edge(node_id, _node_id(target_kind, namespace, target_name),
                               "scales", 1.0, "EXTRACTED")]
        return []

    # ── ArgoCD ────────────────────────────────────────────────────────────────

    def _extract_appproject_edges(self, node_id: str, spec: dict, namespace: str) -> list[dict]:
        edges = []
        for dest in spec.get("destinations") or []:
            if not isinstance(dest, dict):
                continue
            cluster_name = _safe_str(dest.get("name"))
            if cluster_name and cluster_name != "*":
                target_id = _node_id("Secret", "argocd", f"argocd-cluster-{cluster_name}")
                edges.append(self._edge(node_id, target_id, "targets_cluster", 0.7, "INFERRED"))
        return edges

    def _extract_application_edges(self, node_id: str, spec: dict, namespace: str) -> list[dict]:
        edges = []
        project = _safe_str(spec.get("project"))
        if project:
            target_id = _node_id("AppProject", namespace, project)
            edges.append(self._edge(node_id, target_id, "member_of", 1.0, "EXTRACTED"))

        dest = spec.get("destination") or {}
        cluster_name = _safe_str(dest.get("name"))
        server = _safe_str(dest.get("server"))
        if cluster_name and cluster_name != "*":
            target_id = _node_id("Secret", "argocd", f"argocd-cluster-{cluster_name}")
            edges.append(self._edge(node_id, target_id, "deploys_to", 0.9, "INFERRED"))
        elif server:
            if node_id in self._all_nodes:
                self._all_nodes[node_id]["dest_server"] = server

        sources = spec.get("sources")
        if isinstance(sources, list):
            for source in sources:
                if not isinstance(source, dict):
                    continue
                chart = _safe_str(source.get("chart"))
                if chart:
                    edges.append(self._edge(node_id, f"helm_chart/{chart}",
                                            "uses_chart", 1.0, "EXTRACTED"))
        return edges

    def _extract_applicationset_edges(self, node_id: str, spec: dict, namespace: str) -> list[dict]:
        edges = []
        template = spec.get("template") or {}
        tmpl_spec = template.get("spec") or {}
        project = _safe_str(tmpl_spec.get("project"))
        if project:
            target_id = _node_id("AppProject", namespace, project)
            edges.append(self._edge(node_id, target_id, "member_of", 1.0, "EXTRACTED"))

        tmpl_sources = tmpl_spec.get("sources")
        if isinstance(tmpl_sources, list):
            for source in tmpl_sources:
                if not isinstance(source, dict):
                    continue
                chart = _safe_str(source.get("chart"))
                if chart:
                    edges.append(self._edge(node_id, f"helm_chart/{chart}",
                                            "uses_chart", 1.0, "EXTRACTED"))

        for gen in self._flatten_generators(spec.get("generators") or []):
            clusters_block = gen.get("clusters") or {}
            selector = (clusters_block.get("selector") or {}).get("matchLabels") or {}
            if selector:
                self._pending_cluster_selectors.append((node_id, selector, namespace))

        return edges

    def _flatten_generators(self, generators: list) -> list[dict]:
        flat: list[dict] = []
        for gen in generators:
            if not isinstance(gen, dict):
                continue
            flat.append(gen)
            for nested_key in ("matrix", "merge"):
                nested = gen.get(nested_key) or {}
                if isinstance(nested, dict):
                    flat.extend(self._flatten_generators(nested.get("generators") or []))
        return flat

    # ── External Secrets Operator ─────────────────────────────────────────────

    def _extract_externalsecret_edges(self, node_id: str, spec: dict, namespace: str) -> list[dict]:
        edges = []
        store_ref = spec.get("secretStoreRef") or {}
        store_name = _safe_str(store_ref.get("name"))
        store_kind = _safe_str(store_ref.get("kind")) or "SecretStore"
        if store_name:
            store_ns = "default" if store_kind == "ClusterSecretStore" else namespace
            target_id = _node_id(store_kind, store_ns, store_name)
            edges.append(self._edge(node_id, target_id, "uses_store", 1.0, "EXTRACTED"))
        return edges

    # ── cert-manager ──────────────────────────────────────────────────────────

    def _extract_certificate_edges(self, node_id: str, spec: dict, namespace: str) -> list[dict]:
        edges = []
        issuer_ref = spec.get("issuerRef") or {}
        issuer_name = _safe_str(issuer_ref.get("name"))
        issuer_kind = _safe_str(issuer_ref.get("kind")) or "Issuer"
        if issuer_name:
            issuer_ns = "default" if issuer_kind == "ClusterIssuer" else namespace
            target_id = _node_id(issuer_kind, issuer_ns, issuer_name)
            edges.append(self._edge(node_id, target_id, "uses_issuer", 1.0, "EXTRACTED"))

        secret_name = _safe_str(spec.get("secretName"))
        if secret_name:
            target_id = _node_id("Secret", namespace, secret_name)
            edges.append(self._edge(node_id, target_id, "creates_secret", 1.0, "EXTRACTED"))

        return edges

    # ── Istio ─────────────────────────────────────────────────────────────────

    def _extract_istio_virtualservice_edges(
        self, node_id: str, spec: dict, namespace: str
    ) -> list[dict]:
        edges = []
        for proto in ("http", "tcp", "tls"):
            for route_block in spec.get(proto) or []:
                if not isinstance(route_block, dict):
                    continue
                for route in route_block.get("route") or []:
                    if not isinstance(route, dict):
                        continue
                    dest = route.get("destination") or {}
                    host = _safe_str(dest.get("host"))
                    if host:
                        # Use short name (first DNS label) as Service name
                        svc_name = host.split(".")[0]
                        edges.append(self._edge(
                            node_id,
                            _node_id("Service", namespace, svc_name),
                            "routes_to", 1.0, "EXTRACTED",
                        ))
        return edges

    def _extract_istio_destinationrule_edges(
        self, node_id: str, spec: dict, namespace: str
    ) -> list[dict]:
        host = _safe_str(spec.get("host"))
        if host:
            svc_name = host.split(".")[0]
            return [self._edge(node_id, _node_id("Service", namespace, svc_name),
                               "configures", 1.0, "EXTRACTED")]
        return []

    # ── Flux CD ───────────────────────────────────────────────────────────────

    def _extract_flux_helmrelease_edges(
        self, node_id: str, spec: dict, namespace: str
    ) -> list[dict]:
        edges = []
        chart_spec = (spec.get("chart") or {}).get("spec") or {}
        source_ref = chart_spec.get("sourceRef") or {}
        repo_kind = _safe_str(source_ref.get("kind")) or "HelmRepository"
        repo_name = _safe_str(source_ref.get("name"))
        repo_ns = _safe_str(source_ref.get("namespace")) or namespace
        if repo_name:
            edges.append(self._edge(
                node_id,
                _node_id(repo_kind, repo_ns, repo_name),
                "from_repo", 1.0, "EXTRACTED",
            ))
        chart_name = _safe_str(chart_spec.get("chart"))
        if chart_name:
            edges.append(self._edge(node_id, f"helm_chart/{chart_name}",
                                    "uses_chart", 1.0, "EXTRACTED"))
        return edges

    def _extract_flux_kustomization_edges(
        self, node_id: str, spec: dict, namespace: str
    ) -> list[dict]:
        source_ref = spec.get("sourceRef") or {}
        repo_kind = _safe_str(source_ref.get("kind")) or "GitRepository"
        repo_name = _safe_str(source_ref.get("name"))
        repo_ns = _safe_str(source_ref.get("namespace")) or namespace
        if repo_name:
            return [self._edge(
                node_id,
                _node_id(repo_kind, repo_ns, repo_name),
                "from_repo", 1.0, "EXTRACTED",
            )]
        return []

    def _extract_flux_alert_edges(
        self, node_id: str, spec: dict, namespace: str
    ) -> list[dict]:
        provider_ref = spec.get("providerRef") or {}
        provider_name = _safe_str(provider_ref.get("name"))
        if provider_name:
            return [self._edge(
                node_id,
                _node_id("Provider", namespace, provider_name),
                "uses_provider", 1.0, "EXTRACTED",
            )]
        return []

    # ── Argo Rollouts ─────────────────────────────────────────────────────────

    def _extract_rollout_edges(
        self, node_id: str, spec: dict, namespace: str
    ) -> list[dict]:
        edges = []
        canary = (spec.get("strategy") or {}).get("canary") or {}
        for svc_key in ("stableService", "canaryService"):
            svc_name = _safe_str(canary.get(svc_key))
            if svc_name:
                edges.append(self._edge(
                    node_id, _node_id("Service", namespace, svc_name),
                    "routes_to", 1.0, "EXTRACTED",
                ))
        for step in canary.get("steps") or []:
            if not isinstance(step, dict):
                continue
            for tmpl in (step.get("analysis") or {}).get("templates") or []:
                if not isinstance(tmpl, dict):
                    continue
                tmpl_name = _safe_str(tmpl.get("templateName"))
                if tmpl_name:
                    edges.append(self._edge(
                        node_id, _node_id("AnalysisTemplate", namespace, tmpl_name),
                        "uses_analysis", 1.0, "EXTRACTED",
                    ))
        return edges

    # ── KEDA ──────────────────────────────────────────────────────────────────

    def _extract_keda_scaledobject_edges(
        self, node_id: str, spec: dict, namespace: str
    ) -> list[dict]:
        target = spec.get("scaleTargetRef") or {}
        target_kind = _norm_kind(_safe_str(target.get("kind")) or "Deployment")
        target_name = _safe_str(target.get("name"))
        if target_kind and target_name:
            return [self._edge(node_id, _node_id(target_kind, namespace, target_name),
                               "scales", 1.0, "EXTRACTED")]
        return []

    # ── Gateway API ───────────────────────────────────────────────────────────

    def _extract_httproute_edges(
        self, node_id: str, spec: dict, namespace: str
    ) -> list[dict]:
        edges = []
        for parent in spec.get("parentRefs") or []:
            if not isinstance(parent, dict):
                continue
            gw_name = _safe_str(parent.get("name"))
            gw_ns = _safe_str(parent.get("namespace")) or namespace
            if gw_name:
                edges.append(self._edge(
                    node_id, _node_id("Gateway", gw_ns, gw_name),
                    "attached_to", 1.0, "EXTRACTED",
                ))
        for rule in spec.get("rules") or []:
            if not isinstance(rule, dict):
                continue
            for backend in rule.get("backendRefs") or []:
                if not isinstance(backend, dict):
                    continue
                svc_name = _safe_str(backend.get("name"))
                svc_ns = _safe_str(backend.get("namespace")) or namespace
                if svc_name:
                    edges.append(self._edge(
                        node_id, _node_id("Service", svc_ns, svc_name),
                        "routes_to", 1.0, "EXTRACTED",
                    ))
        return edges

    # ── Selector resolution (cross-file, called after all files parsed) ────────

    def resolve_selectors(self) -> list[dict]:
        extra_edges: list[dict] = []
        for node_id, node in self._all_nodes.items():
            kind = node.get("type", "")
            namespace = node.get("namespace", "default")
            selector = node.get("_selector") or {}
            if not selector:
                continue
            matched = self._match_labels(selector, namespace, exclude_id=node_id)
            edge_type = "routes_to" if kind == "Service" else "selects"
            for target_id in matched:
                extra_edges.append(self._edge(node_id, target_id, edge_type, 0.9, "INFERRED"))
        return extra_edges

    def resolve_cluster_selectors(self) -> list[dict]:
        extra_edges: list[dict] = []
        for node_id, selector, namespace in self._pending_cluster_selectors:
            for label_k, label_v in selector.items():
                label_v_s = _safe_str(label_v)
                if label_v_s:
                    key = f"{label_k}={label_v_s}"
                    for target_id in self._label_index.get(key, []):
                        extra_edges.append(self._edge(node_id, target_id,
                                                      "selects_clusters", 0.9, "INFERRED"))
        return extra_edges

    def store_selector(self, node_id: str, selector: dict) -> None:
        if node_id in self._all_nodes:
            self._all_nodes[node_id]["_selector"] = selector

    def _match_labels(self, selector: dict[str, str], namespace: str, exclude_id: str) -> list[str]:
        if not selector:
            return []
        sets: list[set[str]] = []
        for k, v in selector.items():
            candidates = set(self._label_index.get(f"{k}={v}", []))
            candidates = {
                nid for nid in candidates
                if self._all_nodes.get(nid, {}).get("namespace") == namespace
            }
            sets.append(candidates)
        if not sets:
            return []
        result = sets[0]
        for s in sets[1:]:
            result = result & s
        return [nid for nid in result if nid != exclude_id]

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _edge(
        from_id: str,
        to_id: str,
        edge_type: str,
        confidence: float,
        provenance: str,
    ) -> dict:
        return {
            "from": from_id,
            "to": to_id,
            "type": edge_type,
            "confidence": confidence,
            "provenance": provenance,
        }


def is_kubernetes_file(doc: dict) -> bool:
    """Return True if a YAML document looks like a Kubernetes manifest."""
    return bool(doc.get("apiVersion") and doc.get("kind") and doc.get("metadata"))
