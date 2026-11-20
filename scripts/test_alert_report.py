"""Tests of scripts/alert-report.py on synthetic fixtures (no cluster): episodes, routing / inhibition, experiment
windows, classification, the speaker log, and one end-to-end run with a fake Prometheus.

  python3 -I scripts/test_alert_report.py
"""
import datetime as dt
import importlib.util
import os
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("alert_report", os.path.join(HERE, "alert-report.py"))
ar = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ar)

JST = dt.timezone(dt.timedelta(hours=9), "JST")
T0 = dt.datetime(2026, 11, 20, 12, 0, tzinfo=JST).timestamp()
STEP = 30


def put(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def burn(ns, slo, sev="page", long="1h", **kw):
    return {"alertname": "ErrorBudgetBurn", "namespace": ns, "slo": slo, "severity": sev, "long": long, **kw}


def matrix(*series):
    """[(labels, [(from_min, to_min)])] → a query_range result, a sample every STEP while firing (to exclusive)."""
    out = []
    for labels, spans in series:
        vals = []
        for a, b in spans:
            t = T0 + a * 60
            while t < T0 + b * 60:
                vals.append([t, "1"])
                t += STEP
        out.append({"metric": {"__name__": "ALERTS", "alertstate": "firing", **labels}, "values": vals})
    return out


def episodes(*series, gap=300, end_min=600):
    s = ar.series_runs(matrix(*series), STEP)
    return ar.build_episodes(s, gap, T0, T0 + end_min * 60, STEP), s


LAPTOP_AM = {
    "route": {"receiver": "null", "routes": [
        {"receiver": "null", "matchers": ['namespace=~".+-(dev|staging)"']},
        {"receiver": "speaker", "matchers": ['severity=~"page|ticket"']}]},
    "inhibit_rules": [
        {"source_matchers": ['alertname="ErrorBudgetBurn"', 'severity="page"', 'long="1h"'],
         "target_matchers": ['alertname="ErrorBudgetBurn"', 'severity="page"', 'long="6h"'], "equal": ["namespace", "slo"]},
        {"source_matchers": ['alertname="ErrorBudgetBurn"', 'severity="page"'],
         "target_matchers": ['alertname="ErrorBudgetBurn"', 'severity="ticket"'], "equal": ["namespace", "slo"]}],
    "receivers": [{"name": "null"}, {"name": "speaker", "webhook_configs": [{"url": "http://x/"}]}],
}
VERSIONS = [(0.0, LAPTOP_AM, "test")]


class Episodes(unittest.TestCase):
    def test_flaps_within_gap_merge(self):
        eps, _ = episodes((burn("a-prod", "x"), [(10, 20), (22, 30), (33, 40)]))
        self.assertEqual(len(eps), 1)
        e = eps[0]
        self.assertEqual(len(e["runs"]), 3)
        self.assertEqual((e["start"], e["end"]), (T0 + 600, T0 + 2400))
        self.assertEqual(e["firing_s"], (10 + 8 + 7) * 60)   # gaps are not firing time

    def test_gap_beyond_tolerance_splits(self):
        eps, _ = episodes((burn("a-prod", "x"), [(10, 20), (26, 30)]))
        self.assertEqual(len(eps), 2)

    def test_identities_separate_and_label_variants_union(self):
        absent = {"alertname": "SLOMetricAbsent", "namespace": "a-prod", "slo": "x", "severity": "ticket"}
        eps, _ = episodes((burn("a-prod", "x"), [(0, 5)]), (burn("a-prod", "x", long="6h"), [(0, 5)]),
                          ({**absent, "le": "0.1"}, [(10, 20)]), (absent, [(15, 25)]))
        self.assertEqual(len(eps), 3)
        a = [e for e in eps if e["labels"]["alertname"] == "SLOMetricAbsent"][0]
        self.assertEqual((a["start"], a["end"]), (T0 + 600, T0 + 1500))

    def test_edges_and_always_on(self):
        eps, _ = episodes((burn("a-prod", "x"), [(0, 5), (590, 600)]), ({"alertname": "Watchdog"}, [(0, 600)]))
        self.assertEqual(len(eps), 2)
        self.assertTrue(eps[0]["before_range"])
        self.assertTrue(eps[1]["ongoing"])

    def test_chunks_merge(self):
        m = matrix((burn("a-prod", "x"), [(0, 10)]))
        first = {"metric": m[0]["metric"], "values": m[0]["values"][:10]}
        second = {"metric": m[0]["metric"], "values": m[0]["values"][10:]}
        s = ar.merge_series(ar.series_runs([first], STEP) + ar.series_runs([second], STEP))
        self.assertEqual(s[0][1], [(T0, T0 + 600)])


class Routing(unittest.TestCase):
    def test_matchers(self):
        self.assertTrue(ar.matches({"namespace": "x-dev"}, ['namespace=~".+-(dev|staging)"']))
        self.assertFalse(ar.matches({"namespace": "x-prod"}, ['namespace=~"prod"']))   # anchored
        self.assertTrue(ar.matches({}, ['team=""']))                                     # missing = empty
        self.assertTrue(ar.matches({"a": "b"}, ['a!="c"', 'a!~"c.*"']))
        self.assertFalse(ar.matches({"a": "b"}, ['a!~"b"']))

    def test_routes(self):
        self.assertTrue(ar.notifies(LAPTOP_AM, burn("a-prod", "x")))
        self.assertFalse(ar.notifies(LAPTOP_AM, burn("a-dev", "x")))
        self.assertFalse(ar.notifies(LAPTOP_AM, {"alertname": "TargetDown", "namespace": "a-prod", "severity": "warning"}))
        self.assertFalse(ar.notifies(ar.DEFAULT_AM, burn("a-prod", "x")))

    def test_continue(self):
        cfg = {"route": {"receiver": "null", "routes": [
            {"receiver": "null", "matchers": ['a="1"'], "continue": True},
            {"receiver": "pager", "matchers": ['b="1"']}]},
            "receivers": [{"name": "null"}, {"name": "pager", "webhook_configs": [{"url": "u"}]}]}
        self.assertTrue(ar.notifies(cfg, {"a": "1", "b": "1"}))
        self.assertFalse(ar.notifies(cfg, {"a": "1"}))

    def test_fast_burn_inhibits_medium_burn_and_tickets_of_the_same_slo(self):
        eps, s = episodes((burn("a-prod", "x"), [(10, 40)]),
                          (burn("a-prod", "x", long="6h"), [(20, 35)]),             # inside the 1h page: silent
                          (burn("a-prod", "x", sev="ticket", long="1d"), [(30, 60)]),  # outlives it: notifies
                          (burn("a-prod", "y", sev="ticket", long="1d"), [(20, 30)]))  # another SLO: notifies
        ar.apply_routing(eps, s, VERSIONS)
        got = {(e["labels"]["slo"], e["labels"]["long"]): (e["notified"], e["inhibited_s"]) for e in eps}
        self.assertEqual(got[("x", "1h")], (True, 0))
        self.assertEqual(got[("x", "6h")], (False, 900))
        self.assertEqual(got[("x", "1d")], (True, 600))
        self.assertEqual(got[("y", "1d")], (True, 0))

    def test_dev_never_notifies(self):
        eps, s = episodes((burn("a-dev", "x"), [(10, 40)]))
        ar.apply_routing(eps, s, VERSIONS)
        self.assertFalse(eps[0]["notified"])
        self.assertFalse(eps[0]["routed"])

    def test_config_in_force_at_the_start(self):
        old = {**LAPTOP_AM, "route": {"receiver": "null", "routes": [{"receiver": "speaker", "matchers": ['severity="page"']}]}}
        versions = [(0.0, old, "old"), (T0 + 1200, LAPTOP_AM, "new")]
        eps, s = episodes((burn("a-dev", "x"), [(10, 15)]), (burn("a-dev", "y"), [(30, 35)]))
        ar.apply_routing(eps, s, versions)
        self.assertEqual([(e["routing"], e["notified"]) for e in eps], [("old", True), ("new", False)])

    def test_overlay_config_parsed_from_this_repository(self):
        text = ar.read(os.path.join(ar.ROOT, "clusters/laptop/kustomization.yaml"))
        cfg = ar.am_config_from_kustomization(text)
        self.assertTrue(ar.notifies(cfg, burn("api-factory-prod", "x")))
        self.assertFalse(ar.notifies(cfg, burn("api-factory-dev", "x")))
        self.assertIsNone(ar.am_config_from_kustomization(ar.read(os.path.join(ar.ROOT, "clusters/demo/kustomization.yaml"))))


class Experiments(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.d, "results"))
        put(os.path.join(self.d, "boom.yaml"), textwrap.dedent("""\
            hypothesis: h
            namespace: a-staging
            fault: [{apiVersion: chaos-mesh.org/v1alpha1, kind: PodChaos, metadata: {name: p}}]
            expect:
              fire: [{alert: ErrorBudgetBurn, labels: {namespace: a-staging, slo: x, severity: page, long: 1h}}]
              quiet: [{alert: ErrorBudgetBurn, labels: {namespace: a-staging, slo: z}}]
            """))
        put(os.path.join(self.d, "calm.yaml"), "hypothesis: h\nnamespace: a-prod\nfault: []\nexpect: {fire: []}\n")
        put(os.path.join(self.d, "results", "2026-11-20-boom-night.md"), textwrap.dedent("""\
            # boom: PASS

            ```
            23:50:00 [boom] hypothesis: h
            23:50:01 [boom] fault created in a-staging: PodChaos/p
            00:05:00 [boom] ✓ fired after 4.0 min: ErrorBudgetBurn {}
            00:10:00 [boom] PASS
            ```
            """))
        put(os.path.join(self.d, "results", "2026-11-20-calm.md"), 
            "```\n11:00:00 [calm] hypothesis: h\n12:00:00 [calm] PASS\n```\n")

    def windows(self):
        return ar.experiment_windows(os.path.join(self.d, "results", "*.md"), JST, [], self.d)

    def test_window_crosses_midnight_and_adds_cooldown(self):
        x = [x for x in self.windows() if x["name"] == "boom"][0]
        self.assertEqual(x["start"], dt.datetime(2026, 11, 20, 23, 50, tzinfo=JST).timestamp())
        self.assertEqual(x["end"], dt.datetime(2026, 11, 21, 0, 40, tzinfo=JST).timestamp())
        self.assertEqual((x["namespace"], x["fault"], x["verdict"]), ("a-staging", True, "PASS"))

    def test_expectations(self):
        xs = self.windows()
        t = dt.datetime(2026, 11, 21, 0, 0, tzinfo=JST).timestamp()
        ctx = {"experiments": xs}
        for labels, want in ((burn("a-staging", "x"), "expected"), (burn("a-staging", "z"), "must-stay-quiet"),
                             (burn("a-staging", "x", long="6h"), "unexpected")):
            ep = {"labels": labels, "start": t, "end": t + 60, "runs": [(t, t + 60)], "firing_s": 60, "ongoing": False}
            self.assertEqual(ar.classify(ep, ctx)[:2], ("experiment", want))

    def test_negative_test_and_other_namespace_explain_nothing(self):
        xs = self.windows()
        t = dt.datetime(2026, 11, 20, 11, 30, tzinfo=JST).timestamp()
        for labels in (burn("a-prod", "x"), burn("b-staging", "x")):
            ep = {"labels": labels, "start": t, "end": t + 600, "runs": [(t, t + 600)], "firing_s": 600, "ongoing": False}
            self.assertEqual(ar.classify(ep, {"experiments": xs})[0], "unknown")

    def test_metric_runs_and_marks_fill_gaps_only(self):
        t = dt.datetime(2026, 11, 21, 0, 10, tzinfo=JST).timestamp()
        xs = ar.experiment_windows(os.path.join(self.d, "results", "*.md"), JST,
                                   [("boom", "a-staging", t, 1200.0), ("boom", "a-staging", t + 86400, 1200.0)], self.d,
                                   [(None, "c-prod", t - 100, t + 100), (None, "a-staging", t - 100, t)])
        self.assertEqual(len([x for x in xs if x["name"] == "boom"]), 2)   # the recorded run is not doubled
        late = [x for x in xs if x["file"].startswith("metric")][0]
        self.assertEqual((late["start"], late["end"]), (t + 86400 - 1200, t + 86400 + 1800))
        mark = [x for x in xs if x["file"].startswith("namespace")]
        self.assertEqual([m["namespace"] for m in mark], ["c-prod"])
        ep = {"labels": burn("c-prod", "x"), "start": t, "end": t + 60, "runs": [(t, t + 60)], "firing_s": 60, "ongoing": False}
        self.assertEqual(ar.classify(ep, {"experiments": xs})[:2], ("experiment", "unverified"))


def ep_at(labels, a=10, b=40, runs=None):
    s, e = T0 + a * 60, T0 + b * 60
    runs = runs or [(s, e)]
    return {"labels": labels, "id": ar.identity(labels), "start": s, "end": e, "runs": runs,
            "firing_s": ar.total(runs), "ongoing": False, "before_range": False}


class Classify(unittest.TestCase):
    def test_label_overrides_everything(self):
        ep = ep_at(burn("a-prod", "x"))
        ctx = {"labels": [{"match": {"slo": "x"}, "from": T0, "to": T0 + 3600, "class": "noise", "note": "load test"}],
               "evidence": {ar.evidence_key(ep): ["restart: p ×1"]}}
        self.assertEqual(ar.classify(ep, ctx)[:2], ("noise", "labelled"))

    def test_evidence_makes_an_incident_and_rollouts_alone_a_change(self):
        ep = ep_at(burn("a-prod", "x"))
        self.assertEqual(ar.classify(ep, {"evidence": {ar.evidence_key(ep): ["restart: p ×2", "rollout: rs"]}})[:2],
                         ("incident", "fault"))
        self.assertEqual(ar.classify(ep, {"evidence": {ar.evidence_key(ep): ["rollout: rs"]}})[:2], ("incident", "change"))

    def test_an_alert_is_not_its_own_evidence(self):
        ep = ep_at({"alertname": "TargetDown", "namespace": "a-prod", "severity": "warning"})
        alerts = {("TargetDown", "", "", "", "warning"): {"query": "100 * (count by (job) (up == 0)) > 10"}}
        ctx = {"circular": ar.circular_evidence(alerts), "evidence": {ar.evidence_key(ep): ["target-down: a-prod/web"]}}
        self.assertEqual(ar.classify(ep, ctx)[0], "unknown")

    def test_noise_signatures(self):
        short = ep_at(burn("a-prod", "x"), 10, 12)
        self.assertEqual(ar.classify(short, {})[:2], ("noise", "short"))
        still = dict(short, ongoing=True)   # still firing at the end of the range: not "short"
        self.assertEqual(ar.classify(still, {})[0], "unknown")
        flap = ep_at(burn("a-prod", "x"), 10, 40, runs=[(T0 + 600, T0 + 900), (T0 + 1000, T0 + 1500), (T0 + 1600, T0 + 2400)])
        self.assertEqual(ar.classify(flap, {})[:2], ("noise", "flapping"))
        low = ep_at(burn("a-prod", "x"))
        self.assertEqual(ar.classify(low, {"traffic": {ar.traffic_key(low): {"events": 40, "bad_needed": 2.8}}})[:2],
                         ("noise", "low traffic"))
        busy = {"traffic": {ar.traffic_key(low): {"events": 4000, "bad_needed": 280}}}
        self.assertEqual(ar.classify(low, busy)[0], "unknown")

    def test_young_data_only_for_absent_alerts(self):
        absent = ep_at({"alertname": "SLOMetricAbsent", "namespace": "a-prod", "slo": "x", "severity": "ticket"}, 5, 40)
        ctx = {"tsdb_start": T0, "rules": {ar.rule_key(absent["labels"]): {"for": 180}}}
        self.assertEqual(ar.classify(absent, ctx)[:2], ("noise", "young data"))
        later = ep_at(absent["labels"], 60, 90)
        self.assertEqual(ar.classify(later, ctx)[0], "unknown")
        b = ep_at(burn("a-prod", "x", long="6h"), 30, 60)
        cls, _, why = ar.classify(b, ctx)
        self.assertEqual(cls, "unknown")       # a burn in a half-empty window is not called noise
        self.assertIn("not full", why[0])


class Prometheus(unittest.TestCase):
    def test_denominator(self):
        self.assertEqual(ar.denominator('sum(rate(a{c=~"5.."}[5m])) / sum(rate(a[5m]))'), "sum(rate(a[5m]))")
        self.assertEqual(ar.denominator("(sum(rate(c[1h])) - sum(rate(b{le=\"0.1\"}[1h]))) / sum(rate(c[1h]))"),
                         "sum(rate(c[1h]))")
        self.assertIsNone(ar.denominator("sum(x)"))

    def test_rule_info(self):
        groups = [{"rules": [
            {"type": "alerting", "name": "ErrorBudgetBurn", "duration": 120,
             "labels": burn("a-prod", "x", short="5m"),
             "query": 'r:burnrate5m{slo="x"} > (14 * (1 - 0.995)) and r:burnrate1h{slo="x"} > (14 * (1 - 0.995))'},
            {"type": "recording", "name": "r:burnrate1h", "labels": {"namespace": "a-prod", "slo": "x"},
             "query": "sum(rate(e[1h])) / sum(rate(t[1h]))"}]}]
        alerts, records = ar.rule_info(groups)
        self.assertEqual(alerts[("ErrorBudgetBurn", "a-prod", "x", "1h", "page")]["factor"], 14)
        self.assertAlmostEqual(alerts[("ErrorBudgetBurn", "a-prod", "x", "1h", "page")]["objective"], 0.995)
        self.assertEqual(records[("a-prod", "x", "1h")], "sum(rate(e[1h])) / sum(rate(t[1h]))")


class Speaker(unittest.TestCase):
    TABLES = {"PHRASES": {"ErrorBudgetBurn": "错误预算消耗过快", "SLOMetricAbsent": "服务等级指标没有数据"},
              "APPS": {"api-factory": "API 工厂"}, "ENVS": {"prod": "生产环境"}}

    def test_parse_and_match(self):
        lines = [
            '2026-11-20T12:12:00.123456789+09:00 {"action": "spoken", "text": "紧急告警。API 工厂生产环境。错误预算消耗过快。"}',
            '2026-11-20T12:13:00.1+09:00 {"action": "quiet", "text": "告警。storefront-prod。服务等级指标没有数据。"}',
            '2026-11-20T12:50:00Z {"action": "spoken", "text": "已恢复。API 工厂生产环境。错误预算消耗过快。"}',
            "not json"]
        ev = ar.parse_speaker(lines, self.TABLES)
        self.assertEqual([(e["state"], e["severity"], e["alertname"], e["namespace"]) for e in ev], [
            ("firing", "page", "ErrorBudgetBurn", "api-factory-prod"),
            ("firing", "ticket", "SLOMetricAbsent", "storefront-prod"),
            ("resolved", "", "ErrorBudgetBurn", "api-factory-prod")])
        self.assertEqual(len(ar.heard(ep_at(burn("api-factory-prod", "x")), ev)), 1)
        self.assertEqual(len(ar.heard(ep_at(burn("api-factory-prod", "x", sev="ticket")), ev)), 0)

    def test_tables_from_the_speaker(self):
        t = ar.speaker_tables(os.path.join(ar.ROOT, "platform/alert-speaker/speaker.py"))
        self.assertEqual(t["PHRASES"]["ErrorBudgetBurn"], "错误预算消耗过快")

    def test_periodic(self):
        hourly = [T0 + h * 3600 + 30 * 60 + d for h, d in enumerate((0, 40, -60, 20))]
        self.assertEqual(ar.periodic(hourly, JST), 30)
        self.assertIsNone(ar.periodic([T0, T0 + 1300, T0 + 2900], JST))


class FakeProm:
    """ALERTS from fixtures; a restart in a-prod; low traffic for slo y; everything else empty."""
    def __init__(self, series):
        self.series, self.kc = series, "/dev/null"

    def query(self, q, t=None):
        if "prometheus_tsdb_lowest" in q:
            return [{"metric": {}, "value": [0, str(T0 - 86400)]}]
        if "restarts_total" in q and 'namespace="a-prod"' in q:
            return [{"metric": {"pod": "web-1", "container": "web"}, "value": [t, "2"]}]
        if q == "sum(rate(t[1h]))":
            return [{"metric": {}, "value": [t, "0.01"]}]   # 36 events an hour
        return []

    def query_range(self, q, start, end, step):
        if q.startswith("ALERTS"):
            m = matrix(*self.series)
            for r in m:
                r["values"] = [v for v in r["values"] if start <= v[0] <= end]
            return [r for r in m if r["values"]]
        return []

    def rules(self):
        return [{"rules": [
            {"type": "alerting", "name": "ErrorBudgetBurn", "duration": 120, "labels": burn("b-prod", "y"),
             "query": "r:burnrate5m > (14 * (1 - 0.995)) and r:burnrate1h > (14 * (1 - 0.995))"},
            {"type": "recording", "name": "r:burnrate1h", "labels": {"namespace": "b-prod", "slo": "y"},
             "query": "sum(rate(e[1h])) / sum(rate(t[1h]))"}]}]


class EndToEnd(unittest.TestCase):
    def test_report(self):
        fake = FakeProm([(burn("a-prod", "x"), [(10, 40)]), (burn("b-prod", "y"), [(100, 130)]),
                         (burn("c-prod", "z"), [(200, 201)]), (burn("c-prod", "z", long="6h"), [(300, 330)])])
        old = ar.Prom, ar.namespace_marks
        ar.Prom, ar.namespace_marks = (lambda kc: fake), (lambda kc, now: [])
        out = os.path.join(tempfile.mkdtemp(), "r.md")
        to = dt.datetime.fromtimestamp(T0 + 600 * 60, JST).isoformat()
        try:
            ar.main(["--cluster", "laptop-test", "--from", dt.datetime.fromtimestamp(T0, JST).isoformat(), "--to", to,
                     "--results", "", "--results-cluster", "none", "--labels", "", "--workers", "4", "-o", out])
        finally:
            ar.Prom, ar.namespace_marks = old
        md = ar.read(out)
        self.assertIn("4 episodes of 4 alerts", md)
        self.assertIn("incident 1, noise 2, unknown 1", md)
        self.assertIn("restart: web-1 ×2", md)
        self.assertIn("low traffic: 36 events in 1h", md)
        self.assertIn("short: resolved after 1m", md)


if __name__ == "__main__":
    unittest.main(verbosity=1)
