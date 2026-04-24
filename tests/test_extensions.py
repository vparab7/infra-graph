"""
Tests for extended parser coverage:
- Unknown CRD fallback (any apiVersion+kind+metadata → node)
- Istio VirtualService
- Flux CD HelmRelease
- KEDA ScaledObject
- Ansible playbook
- Generic YAML fallback
"""

from pathlib import Path

import pytest

from infra_graph.parsers.ansible_schema import AnsibleParser
from infra_graph.parsers.k8s_schema import KubernetesParser
from infra_graph.parsers.yaml_parser import YAMLParser

FIXTURES = Path(__file__).parent / "fixtures"


# ── Unknown CRD fallback ──────────────────────────────────────────────────────

class TestUnknownCrd:
    def test_creates_node(self):
        parser = KubernetesParser()
        result = parser.parse_file(FIXTURES / "unknown_crd.yaml")
        node_ids = {n["id"] for n in result["nodes"]}
        assert "WidgetConfig/default/my-widget" in node_ids

    def test_node_type_is_kind(self):
        parser = KubernetesParser()
        result = parser.parse_file(FIXTURES / "unknown_crd.yaml")
        node = next(n for n in result["nodes"] if n["id"] == "WidgetConfig/default/my-widget")
        assert node["type"] == "WidgetConfig"

    def test_no_edges_for_unknown_kind(self):
        parser = KubernetesParser()
        result = parser.parse_file(FIXTURES / "unknown_crd.yaml")
        assert result["edges"] == []

    def test_required_fields_present(self):
        parser = KubernetesParser()
        result = parser.parse_file(FIXTURES / "unknown_crd.yaml")
        required = {"id", "type", "kind", "name", "file", "line", "labels", "community_id"}
        for node in result["nodes"]:
            assert required <= set(node.keys()), f"Node {node['id']} missing fields"


# ── Istio VirtualService ──────────────────────────────────────────────────────

@pytest.fixture
def istio_result():
    parser = KubernetesParser()
    return parser.parse_file(FIXTURES / "istio_virtualservice.yaml")


class TestIstioVirtualService:
    def test_node_created(self, istio_result):
        node_ids = {n["id"] for n in istio_result["nodes"]}
        assert "VirtualService/default/myapp-vs" in node_ids

    def test_routes_to_service(self, istio_result):
        edges = [e for e in istio_result["edges"] if e["type"] == "routes_to"]
        assert len(edges) >= 1
        targets = {e["to"] for e in edges}
        assert "Service/default/myapp-svc" in targets

    def test_edge_provenance(self, istio_result):
        edges = [e for e in istio_result["edges"] if e["type"] == "routes_to"]
        assert all(e["provenance"] == "EXTRACTED" for e in edges)
        assert all(e["confidence"] == 1.0 for e in edges)


# ── Flux CD HelmRelease ───────────────────────────────────────────────────────

@pytest.fixture
def flux_result():
    parser = KubernetesParser()
    return parser.parse_file(FIXTURES / "flux_helmrelease.yaml")


class TestFluxHelmRelease:
    def test_node_created(self, flux_result):
        node_ids = {n["id"] for n in flux_result["nodes"]}
        assert "HelmRelease/flux-system/my-release" in node_ids

    def test_from_repo_edge(self, flux_result):
        edges = [e for e in flux_result["edges"] if e["type"] == "from_repo"]
        assert len(edges) >= 1
        pairs = {(e["from"], e["to"]) for e in edges}
        assert (
            "HelmRelease/flux-system/my-release",
            "HelmRepository/flux-system/bitnami",
        ) in pairs

    def test_uses_chart_edge(self, flux_result):
        edges = [e for e in flux_result["edges"] if e["type"] == "uses_chart"]
        assert len(edges) >= 1
        targets = {e["to"] for e in edges}
        assert "helm_chart/nginx" in targets


# ── KEDA ScaledObject ─────────────────────────────────────────────────────────

@pytest.fixture
def keda_result():
    parser = KubernetesParser()
    return parser.parse_file(FIXTURES / "keda_scaledobject.yaml")


class TestKedaScaledObject:
    def test_node_created(self, keda_result):
        node_ids = {n["id"] for n in keda_result["nodes"]}
        assert "ScaledObject/default/myapp-scaler" in node_ids

    def test_scales_edge(self, keda_result):
        edges = [e for e in keda_result["edges"] if e["type"] == "scales"]
        assert len(edges) >= 1
        pairs = {(e["from"], e["to"]) for e in edges}
        assert ("ScaledObject/default/myapp-scaler", "Deployment/default/myapp") in pairs

    def test_edge_is_extracted(self, keda_result):
        edges = [e for e in keda_result["edges"] if e["type"] == "scales"]
        assert all(e["provenance"] == "EXTRACTED" for e in edges)


# ── Ansible playbook ──────────────────────────────────────────────────────────

@pytest.fixture
def ansible_result():
    parser = AnsibleParser()
    return parser.parse_file(FIXTURES / "ansible_playbook.yaml")


class TestAnsiblePlaybook:
    def test_play_node(self, ansible_result):
        node_ids = {n["id"] for n in ansible_result["nodes"]}
        assert "play/ansible_playbook/webservers" in node_ids

    def test_role_nodes(self, ansible_result):
        node_ids = {n["id"] for n in ansible_result["nodes"]}
        assert "role/common" in node_ids
        assert "role/nginx" in node_ids

    def test_uses_role_edges(self, ansible_result):
        edges = [e for e in ansible_result["edges"] if e["type"] == "uses_role"]
        assert len(edges) >= 2
        pairs = {(e["from"], e["to"]) for e in edges}
        assert ("play/ansible_playbook/webservers", "role/common") in pairs
        assert ("play/ansible_playbook/webservers", "role/nginx") in pairs

    def test_includes_tasks_edge(self, ansible_result):
        edges = [e for e in ansible_result["edges"] if e["type"] == "includes_tasks"]
        assert len(edges) >= 1
        pairs = {(e["from"], e["to"]) for e in edges}
        assert ("play/ansible_playbook/webservers", "task_file/firewall") in pairs

    def test_node_types(self, ansible_result):
        types = {n["type"] for n in ansible_result["nodes"]}
        assert "play" in types
        assert "role" in types

    def test_is_ansible_file_detection(self):
        parser = AnsibleParser()
        assert parser.is_ansible_file(FIXTURES / "ansible_playbook.yaml")
        assert not parser.is_ansible_file(FIXTURES / "k8s_deployment.yaml")
        assert not parser.is_ansible_file(FIXTURES / "generic_config.yaml")


# ── Generic YAML fallback ─────────────────────────────────────────────────────

class TestGenericYamlFallback:
    def test_creates_config_node(self):
        parser = YAMLParser()
        result = parser.parse_file(FIXTURES / "generic_config.yaml")
        node_ids = {n["id"] for n in result["nodes"]}
        assert "config/generic_config" in node_ids

    def test_node_type_is_config(self):
        parser = YAMLParser()
        result = parser.parse_file(FIXTURES / "generic_config.yaml")
        types = {n["type"] for n in result["nodes"]}
        assert "config" in types

    def test_no_edges(self):
        parser = YAMLParser()
        result = parser.parse_file(FIXTURES / "generic_config.yaml")
        assert result["edges"] == []

    def test_k8s_file_not_treated_as_generic(self):
        """Proper K8s files must go through the K8s parser, not the fallback."""
        parser = YAMLParser()
        result = parser.parse_file(FIXTURES / "k8s_deployment.yaml")
        types = {n["type"] for n in result["nodes"]}
        assert "config" not in types
        assert "Deployment" in types or len(result["nodes"]) > 0


# ── Node schema compliance ────────────────────────────────────────────────────

class TestNodeSchema:
    REQUIRED = {"id", "type", "kind", "name", "file", "line", "labels", "community_id"}

    def test_istio_nodes(self, istio_result):
        for node in istio_result["nodes"]:
            assert self.REQUIRED <= set(node.keys())

    def test_flux_nodes(self, flux_result):
        for node in flux_result["nodes"]:
            assert self.REQUIRED <= set(node.keys())

    def test_keda_nodes(self, keda_result):
        for node in keda_result["nodes"]:
            assert self.REQUIRED <= set(node.keys())

    def test_ansible_nodes(self, ansible_result):
        ansible_required = {"id", "type", "kind", "name", "file", "line", "labels", "community_id"}
        for node in ansible_result["nodes"]:
            assert ansible_required <= set(node.keys())
