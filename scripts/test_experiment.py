"""Unit tests of scripts/experiment.py's judgement and checks (no cluster): python3 -I scripts/test_experiment.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import experiment as e  # noqa: E402


def alert(slo, ns="api-factory-prod", severity="page", long="1h", name="ErrorBudgetBurn"):
    return {"alertname": name, "namespace": ns, "slo": slo, "severity": severity, "long": long}


def want(slo, ns="api-factory-prod"):
    return {"alert": "ErrorBudgetBurn", "labels": {"namespace": ns, "slo": slo, "severity": "page", "long": "1h"}}


class Classify(unittest.TestCase):
    fire = [want("health-probe")]
    quiet = [{"alert": "ErrorBudgetBurn", "labels": {"slo": "base-backup"}}]
    may = [want("rpc-availability"), want("rpc-latency")]

    def c(self, a, marked=()):
        return e.classify(a, self.fire, self.quiet, self.may, "api-factory-prod", set(marked))

    def test_expected_is_not_judged_here(self):
        self.assertIsNone(self.c(alert("health-probe")))

    def test_may_fire_is_allowed_not_unexpected(self):
        self.assertEqual(self.c(alert("rpc-availability")), "allowed")
        self.assertEqual(self.c(alert("rpc-latency")), "allowed")

    def test_may_fire_matches_its_labels_only(self):
        self.assertEqual(self.c(alert("rpc-latency", long="6h")), "unexpected")   # a 6h page was not allowed
        self.assertEqual(self.c(alert("rpc-latency-severe")), "unexpected")

    def test_quiet_wins_over_may_fire(self):
        self.assertEqual(e.classify(alert("x"), [], [want("x")], [want("x")], "api-factory-prod", set()), "quiet")

    def test_other_namespaces(self):
        self.assertEqual(self.c(alert("web", ns="storefront-dev")), "unexpected")
        self.assertIsNone(self.c(alert("web", ns="storefront-dev"), marked={"storefront-dev"}))   # the neighbour's
        self.assertIsNone(self.c(alert("web", severity="ticket")))


class Deadlines(unittest.TestCase):
    def test_refused(self):
        x = {"timeout": "15m", "fault": [
            {"apiVersion": "chaos-mesh.org/v1alpha1", "kind": "PodChaos", "metadata": {"name": "a"}, "spec": {"duration": "10m"}},
            {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "b"}}],
            "load": [{"kind": "Job", "metadata": {"name": "j"}, "spec": {}}]}
        self.assertEqual(len(e.check_deadlines(x)), 3)

    def test_accepted(self):
        x = {"timeout": "15m", "fault": [
            {"apiVersion": "chaos-mesh.org/v1alpha1", "kind": "PodChaos", "metadata": {"name": "a"}, "spec": {"duration": "30m"}},
            {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "storefront-faults"}}]}
        self.assertEqual(e.check_deadlines(x), [])

    def test_dur(self):
        self.assertEqual(e.dur("1h30m"), 5400)
        self.assertRaises(ValueError, e.dur, "15")


if __name__ == "__main__":
    unittest.main()
