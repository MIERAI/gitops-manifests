"""Unit tests of scripts/experiment-regression.py's lab guard (no cluster): python3 -I scripts/test_experiment_regression.py"""
import importlib.util
import os
import unittest

spec = importlib.util.spec_from_file_location(
    "regression", os.path.join(os.path.dirname(os.path.abspath(__file__)), "experiment-regression.py"))
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


def node(name, provider="", labels=None):
    return {"metadata": {"name": name, "labels": labels or {}}, "spec": {"providerID": provider} if provider else {}}


LAB = node("xiaoxin", "k3s://xiaoxin", {"gitops-platform/lab": "true"})
LAPTOP = node("laptop", "k3s://laptop")
KIND = node("gitops-control-plane", "kind://docker/gitops/gitops-control-plane")


class Guard(unittest.TestCase):
    def test_lab_needs_the_label_on_every_node(self):
        self.assertTrue(r.is_lab("lab", [LAB]))
        self.assertFalse(r.is_lab("lab", [LAB, LAPTOP]))

    def test_the_laptop_is_never_a_lab(self):
        for cluster in ("lab", "kind"):
            self.assertFalse(r.is_lab(cluster, [LAPTOP]))

    def test_kind_by_provider_id(self):
        self.assertTrue(r.is_lab("kind", [KIND]))
        self.assertFalse(r.is_lab("kind", [LAB]))   # the label alone does not make kind
        self.assertFalse(r.is_lab("lab", [KIND]))

    def test_label_must_be_true(self):
        self.assertFalse(r.is_lab("lab", [node("x", "k3s://x", {"gitops-platform/lab": "false"})]))

    def test_no_nodes_no_lab(self):
        self.assertFalse(r.is_lab("lab", []))
        self.assertFalse(r.is_lab("demo", [LAB]))


if __name__ == "__main__":
    unittest.main()
