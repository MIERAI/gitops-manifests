"""Tests of speaker.py (run by scripts/validate.sh): what is said, and that nothing is said twice."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import speaker as s  # noqa: E402


def alert(fp, status, slo, severity="page", ns="api-factory-prod", name="ErrorBudgetBurn"):
    return {"fingerprint": fp, "status": status,
            "labels": {"alertname": name, "namespace": ns, "slo": slo, "severity": severity}}


class Speaker(unittest.TestCase):
    def setUp(self):
        s.spoken.clear()
        s.held.clear()

    def test_group_is_one_sentence_naming_the_slos(self):
        c = s.changes([alert("1", "firing", "api-factory-rpc-latency"), alert("2", "firing", "api-factory-rpc-availability")], 0)
        self.assertEqual(s.sentence(c["firing"], False), "紧急告警。API 工厂生产环境。接口延迟、接口可用性，错误预算消耗过快。")

    def test_resent_group_is_not_repeated(self):
        group = [alert("1", "firing", "api-factory-rpc-latency")]
        s.changes(group, 0)
        self.assertEqual(s.changes(group, 300), {"firing": [], "resolved": []})

    def test_reminder_after_repeat_interval(self):
        group = [alert("1", "firing", "api-factory-rpc-latency")]
        s.changes(group, 0)
        self.assertEqual(len(s.changes(group, s.REPEAT + 1)["firing"]), 1)

    def test_resolved_only_if_announced(self):
        self.assertEqual(s.changes([alert("9", "resolved", "storefront-web-probe", ns="storefront-prod")], 0)["resolved"], [])
        s.changes([alert("1", "firing", "api-factory-rpc-latency")], 0)
        c = s.changes([alert("1", "resolved", "api-factory-rpc-latency")], 60)
        self.assertEqual(s.sentence(c["resolved"], True), "已恢复。API 工厂生产环境。接口延迟，错误预算消耗过快。")

    def test_quiet_hours_21_to_8(self):
        at = lambda h: s.datetime.datetime(2026, 11, 21, h, 0, tzinfo=s.TZ)
        self.assertEqual([h for h in range(24) if s.quiet(at(h))], [0, 1, 2, 3, 4, 5, 6, 7, 21, 22, 23])

    def test_night_alerts_still_firing_are_spoken_in_the_morning(self):
        s.hold(s.changes([alert("1", "firing", "api-factory-rpc-latency"), alert("2", "firing", "storefront-web-probe", ns="storefront-prod")], 0))
        s.hold(s.changes([alert("2", "resolved", "storefront-web-probe", ns="storefront-prod")], 60))   # over before morning
        self.assertEqual(s.morning(), "夜里的告警，还没有恢复。紧急告警。API 工厂生产环境。接口延迟，错误预算消耗过快。")
        self.assertIsNone(s.morning())   # once
        self.assertEqual(s.changes([alert("1", "firing", "api-factory-rpc-latency")], 120)["firing"], [])   # not again

    def test_ticket_and_names(self):
        a = alert("3", "firing", "storefront-order-freshness", "ticket", "storefront-staging", "SLOMetricAbsent")
        self.assertEqual(s.sentence([a], False), "告警。商店预发布环境。订单新鲜度，服务等级指标没有数据。")


if __name__ == "__main__":
    unittest.main(verbosity=0)
