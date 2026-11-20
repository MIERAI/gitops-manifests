#!/usr/bin/env python3
"""Alert value report: which alerts fired, how often, how long, would they notify anyone, and was each firing useful.
gitops-platform-blueprint docs/slo.md §4 ("an unactionable page is a bug") and §8 (monthly review).

  python3 scripts/alert-report.py --cluster laptop [--since 30d | --from ISO --to ISO] [-o reports/alerts/X.md]

Read-only: Prometheus queries through the API server's service proxy, and (laptop) the alert-speaker's log.

Episodes: ALERTS{alertstate="firing"} (query_range, --step) per identity (alertname, namespace, slo, severity, long);
firing runs of the same identity less than --gap apart are one episode (a flap), the merged runs are counted.

Would it notify: the cluster's Alertmanager config, read from this repository (clusters/<cluster>/kustomization.yaml)
at the commit in force when the episode started (git log; merge time ≈ Argo CD sync), routes and inhibit rules
applied to the episode's labels and to the other alerts firing at the same time. No config → the chart's default
(every alert to the "null" receiver: nobody is notified).

Classification, first match wins (the reasons are printed, so a human can disagree):
  label       a human said so: reports/alerts/labels.yaml (the monthly review resolves "unknown" there)
  experiment  a fault experiment marked that namespace: from its first log line to its last + the 30 min cooldown
              (experiments/results/*.md, local time; and slo_experiment_last_run_timestamp_seconds if exported);
              expected = matches the experiment's expect.fire, unexpected otherwise. Negative tests (no fault) explain
              nothing.
  incident    something independent of the alert was broken in that namespace around the onset (15 min before to
              15 min after): a container restarted, a running pod not ready, a scrape target down, a job failed, a
              ReplicaSet created (a rollout: change-related). Never the metric the alert's own rule reads.
  noise       nothing of the above, and: resolved in < --short (nobody could act), flapping (>= 3 runs merged), the
              SLO's long window held so few events that a handful of bad ones paged (low traffic), or an absent-metric
              alert right after Prometheus started (young data).
  unknown     none of these: the data cannot tell. Never guessed.
"""
import argparse
import ast
import concurrent.futures as cf
import datetime as dt
import glob
import json
import math
import os
import re
import subprocess
import sys
import urllib.parse

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("PyYAML is required (apt install python3-yaml, or pip install pyyaml)")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROM = "/api/v1/namespaces/monitoring/services/kps-prometheus:9090/proxy/api/v1/"
KUBECONFIGS = {"laptop": "~/.kube/laptop-lan.yaml", "demo": "~/.kube/kind-gitops.yaml"}
IDENTITY = ("alertname", "namespace", "slo", "severity", "long")
ALWAYS_ON = {"Watchdog", "InfoInhibitor"}   # fire by design (dead man's switch, inhibition helper)
COOLDOWN_S = 1800                           # scripts/experiment.py: late alerts still belong to the experiment
SYNC_S = 180                                # a merged overlay reaches Alertmanager after Argo CD's poll (3 min)
LOOKBEHIND_S = 900                          # incident evidence: from 15 min before the episode
CLASSES = ("experiment", "incident", "noise", "unknown")
# kube-prometheus-stack's Alertmanager config when values set none: every alert to "null"
DEFAULT_AM = {"route": {"receiver": "null", "routes": [{"receiver": "null", "matchers": ['alertname="Watchdog"']}]},
              "receivers": [{"name": "null"}], "inhibit_rules": []}


# ---------------------------------------------------------------- small helpers

def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def dur(s):
    """'30s' / '5m' / '1h' / '1d' / '2w' (or a number of seconds) → seconds."""
    if isinstance(s, (int, float)):
        return float(s)
    total, rest = 0.0, s.strip()
    for n, u in re.findall(r"(\d+(?:\.\d+)?)(ms|s|m|h|d|w|y)", rest):
        total += float(n) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800, "y": 31536000}[u]
    if not total and rest:
        raise ValueError(f"not a duration: {s}")
    return total


def fmt_dur(s):
    s = int(round(s))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s" if s % 60 else f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h{(s % 3600) // 60:02d}m"
    return f"{s // 86400}d{(s % 86400) // 3600:02d}h"


def union(iv):
    out = []
    for s, e in sorted(iv):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [tuple(x) for x in out]


def intersect(a, b):
    out, i, j = [], 0, 0
    a, b = union(a), union(b)
    while i < len(a) and j < len(b):
        s, e = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if s < e:
            out.append((s, e))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return out


def total(iv):
    return sum(e - s for s, e in union(iv))


# ---------------------------------------------------------------- Alertmanager routing

MATCHER = re.compile(r'^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*(=~|!~|!=|=)\s*"?(.*?)"?\s*$')


def parse_matcher(s):
    m = MATCHER.match(s)
    if not m:
        raise ValueError(f"not a matcher: {s}")
    name, op, value = m.groups()
    return name, op, value.replace('\\"', '"')


def matches(labels, matchers):
    """Alertmanager semantics: a missing label is the empty string; regexes are anchored."""
    for m in matchers:
        name, op, value = parse_matcher(m) if isinstance(m, str) else m
        v = labels.get(name, "")
        if op == "=":
            ok = v == value
        elif op == "!=":
            ok = v != value
        else:
            ok = (re.fullmatch(value, v) is not None) == (op == "=~")
        if not ok:
            return False
    return True


def _route_matchers(r):
    ms = list(r.get("matchers") or [])
    ms += [f'{k}="{v}"' for k, v in (r.get("match") or {}).items()]          # deprecated forms
    ms += [f'{k}=~"{v}"' for k, v in (r.get("match_re") or {}).items()]
    return ms


def receivers(route, labels):
    """The receivers an alert reaches (Alertmanager's tree: first matching child, unless continue: true)."""
    out = []
    for child in route.get("routes") or []:
        if matches(labels, _route_matchers(child)):
            sub = receivers({**child, "receiver": child.get("receiver", route.get("receiver"))}, labels)
            out += sub
            if not child.get("continue"):
                return out
    return out or [route.get("receiver")]


def notifies(config, labels):
    """True if a receiver with any notifier configured gets the alert."""
    real = {r["name"] for r in config.get("receivers") or []
            if any(k.endswith("_configs") and r[k] for k in r)}
    return any(r in real for r in receivers(config["route"], labels))


def am_config_from_kustomization(text):
    """The alertmanager.config value a cluster overlay patches into platform-monitoring, or None."""
    for p in (yaml.safe_load(text) or {}).get("patches") or []:
        for op in yaml.safe_load(p.get("patch") or "[]") or []:
            if op.get("path", "").endswith("/alertmanager/config"):
                return op["value"]
    return None


def load_routing(root, cluster):
    """[(effective_from_epoch, config)] oldest first: every commit that changed the overlay (git), else the file."""
    path = f"clusters/{cluster}/kustomization.yaml"
    versions = []
    try:
        log = subprocess.run(["git", "-C", root, "log", "--format=%H %ct", "--", path],
                             capture_output=True, text=True, check=True).stdout.split("\n")
        for line in reversed([x for x in log if x.strip()]):
            sha, ts = line.split()
            text = subprocess.run(["git", "-C", root, "show", f"{sha}:{path}"], capture_output=True, text=True,
                                  check=True).stdout
            versions.append((float(ts) + SYNC_S, am_config_from_kustomization(text) or DEFAULT_AM, sha[:7]))
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    if not versions:
        f = os.path.join(root, path)
        cfg = am_config_from_kustomization(read(f)) if os.path.exists(f) else None
        versions = [(0.0, cfg or DEFAULT_AM, "worktree")]
    return versions


def config_at(versions, t):
    cur = versions[0]
    for v in versions:
        if v[0] <= t:
            cur = v
    return cur


# ---------------------------------------------------------------- episodes

def series_runs(matrix, step):
    """query_range result → [(labels, [(start, end)])]; a run ends one step after its last sample."""
    out = []
    for r in matrix:
        labels = {k: v for k, v in r["metric"].items() if k not in ("__name__", "alertstate")}
        ts = sorted(float(t) for t, _ in r["values"])
        runs = []
        for t in ts:
            if runs and t - runs[-1][1] <= step * 0.5 + 1e-6:
                runs[-1][1] = t + step
            else:
                runs.append([t, t + step])
        out.append((labels, [tuple(x) for x in runs]))
    return out


def merge_series(parts):
    """The same series from several query_range chunks → one run list each."""
    by = {}
    for labels, runs in parts:
        k = json.dumps(labels, sort_keys=True)
        by.setdefault(k, (labels, []))[1].extend(runs)
    return [(labels, union(runs)) for labels, runs in by.values()]


def identity(labels):
    return tuple(labels.get(k, "") for k in IDENTITY)


def build_episodes(series, gap, range_start, range_end, step):
    """Group series by identity, merge runs closer than `gap` into episodes."""
    by = {}
    for labels, runs in series:
        if labels.get("alertname") in ALWAYS_ON:
            continue
        ent = by.setdefault(identity(labels), {"labels": dict(labels), "runs": []})
        ent["labels"] = {**labels, **ent["labels"]}
        ent["runs"].extend(runs)
    episodes = []
    for ident, ent in sorted(by.items()):
        runs = union(ent["runs"])
        groups = []
        for s, e in runs:
            if groups and s - groups[-1]["end"] <= gap:
                groups[-1]["end"] = max(groups[-1]["end"], e)
                groups[-1]["runs"].append((s, e))
            else:
                groups.append({"start": s, "end": e, "runs": [(s, e)]})
        for g in groups:
            episodes.append({
                "id": ident, "labels": ent["labels"], "start": g["start"], "end": min(g["end"], range_end),
                "runs": g["runs"], "firing_s": total(g["runs"]),
                "before_range": g["start"] <= range_start + step * 0.5,
                "ongoing": g["end"] >= range_end - step * 0.5,
            })
    return sorted(episodes, key=lambda e: (e["start"], e["id"]))


def apply_routing(episodes, series, versions):
    """notified: some firing time not inhibited, on a route with a real receiver (config in force at the start)."""
    for ep in episodes:
        t0, cfg, sha = config_at(versions, ep["start"])
        ep["routing"] = sha
        ep["routed"] = notifies(cfg, ep["labels"])
        inhibited = []
        for rule in cfg.get("inhibit_rules") or []:
            src = list(rule.get("source_matchers") or []) + [f'{k}="{v}"' for k, v in (rule.get("source_match") or {}).items()]
            tgt = list(rule.get("target_matchers") or []) + [f'{k}="{v}"' for k, v in (rule.get("target_match") or {}).items()]
            if not matches(ep["labels"], tgt):
                continue
            eq = rule.get("equal") or []
            for labels, runs in series:
                if identity(labels) == ep["id"]:
                    continue   # Alertmanager: an alert matching both sides does not inhibit itself
                if matches(labels, src) and all(labels.get(k, "") == ep["labels"].get(k, "") for k in eq):
                    inhibited += intersect(ep["runs"], runs)
        ep["inhibited_s"] = total(inhibited)
        ep["notified"] = ep["routed"] and ep["firing_s"] - ep["inhibited_s"] > 0
    return episodes


# ---------------------------------------------------------------- experiments

LOGLINE = re.compile(r"^(\d\d):(\d\d):(\d\d) \[([^\]]+)\] (.*)$")


def parse_results(path, tz, experiments_dir):
    """A runner report (experiments/results/<date>-<name>[-suffix].md) → an experiment window, or None."""
    m = re.match(r"(\d{4}-\d{2}-\d{2})-", os.path.basename(path))
    if not m:
        return None
    day = dt.date.fromisoformat(m.group(1))
    times, name, lines = [], None, []
    prev = None
    for line in read(path).splitlines():
        lm = LOGLINE.match(line.rstrip("\n"))
        if not lm:
            continue
        h, mi, s, nm, msg = lm.groups()
        t = dt.datetime.combine(day, dt.time(int(h), int(mi), int(s)), tz)
        if prev and t < prev:          # past midnight
            day += dt.timedelta(days=1)
            t += dt.timedelta(days=1)
        prev = t
        times.append(t.timestamp())
        name = name or nm
        lines.append(msg)
    if not times:
        return None
    spec_path = os.path.join(experiments_dir, f"{name}.yaml")
    spec = yaml.safe_load(read(spec_path)) if os.path.exists(spec_path) else {}
    has_fault = bool(spec.get("fault")) or any(x.startswith("fault created") for x in lines)
    ns = spec.get("namespace")
    if not ns:
        fm = next((re.search(r"fault created in (\S+):", x) for x in lines if "fault created in" in x), None)
        ns = fm.group(1) if fm else None
    return {"name": name, "file": os.path.relpath(path, ROOT), "namespace": ns, "start": min(times),
            "end": max(times) + COOLDOWN_S, "fault": has_fault, "expect": spec.get("expect") or {},
            "verdict": "PASS" if lines and lines[-1].strip() == "PASS" else "FAIL"}


def expectation(ep, x):
    want = lambda w: ep["labels"].get("alertname") == w.get("alert") and all(
        ep["labels"].get(k) == str(v) for k, v in (w.get("labels") or {}).items())
    if any(want(w) for w in x["expect"].get("fire") or []):
        return "expected"
    if any(want(w) for w in x["expect"].get("quiet") or []):
        return "must-stay-quiet"
    return "unexpected"


# ---------------------------------------------------------------- classification

def classify(ep, ctx):
    """→ (class, detail, [reasons]). ctx: experiments, evidence, traffic, tsdb_start, labels, short, rules."""
    L = ep["labels"]
    for lab in ctx.get("labels") or []:
        m = lab.get("match") or {}
        if all(L.get(k) == str(v) for k, v in m.items()) and \
                lab.get("from", 0) <= ep["start"] <= lab.get("to", float("inf")):
            return lab["class"], "labelled", [f"labelled: {lab.get('note', '')}".strip()]
    for x in ctx.get("experiments") or []:
        if x["fault"] is not False and x["namespace"] == L.get("namespace") and x["start"] <= ep["start"] <= x["end"]:
            detail = expectation(ep, x) if x["fault"] else "unverified"   # None: a mark without a name
            return "experiment", detail, [f"experiment {x['name']} ({x['verdict']}, {x['file']})"]
    circular = (ctx.get("circular") or {}).get(L.get("alertname"), set())
    ev = [x for x in ctx.get("evidence", {}).get(evidence_key(ep)) or [] if x.split(":")[0] not in circular]
    if ev:
        return "incident", "change" if all(e.startswith("rollout") for e in ev) else "fault", ev
    reasons = []
    if ep["firing_s"] < ctx.get("short", 180) and not ep["ongoing"]:
        reasons.append(f"short: resolved after {fmt_dur(ep['firing_s'])}, nobody could act")
    if len(ep["runs"]) >= 3:
        reasons.append(f"flapping: {len(ep['runs'])} runs merged")
    tr = ctx.get("traffic", {}).get(traffic_key(ep))
    if tr and tr["bad_needed"] < ctx.get("min_bad", 5):
        reasons.append(f"low traffic: {tr['events']:.0f} events in {L.get('long')}, "
                       f"{tr['bad_needed']:.1f} bad ones were enough")
    tsdb = ctx.get("tsdb_start")
    rule_for = (ctx.get("rules") or {}).get(rule_key(L), {}).get("for", 0)
    if tsdb and "Absent" in L.get("alertname", "") and ep["start"] - tsdb < rule_for + 600:
        reasons.append(f"young data: fired {fmt_dur(ep['start'] - tsdb)} after Prometheus' first sample")
    if reasons:
        return "noise", reasons[0].split(":")[0], reasons
    why = []
    if tsdb and L.get("long") and ep["start"] - tsdb < dur(L["long"]):
        why.append(f"the {L['long']} window was not full (Prometheus data from {fmt_dur(ep['start'] - tsdb)} before)")
    return "unknown", "", why or ["no experiment, no independent evidence, no noise signature"]


# the metric behind each kind of evidence: an alert built on it cannot be confirmed by it
EVIDENCE_METRIC = {"restart": "kube_pod_container_status_restarts_total", "not-ready": "kube_pod_status_ready",
                   "target-down": "up", "job-failed": "kube_job_status_failed", "rollout": "kube_replicaset_created"}


def circular_evidence(alerts):
    """{alertname: {evidence kinds its own rule reads}}."""
    out = {}
    for k, r in alerts.items():
        for kind, metric in EVIDENCE_METRIC.items():
            if re.search(rf"(?<![\w:]){re.escape(metric)}(?![\w:])", r.get("query", "")):
                out.setdefault(k[0], set()).add(kind)
    return out


def evidence_key(ep):
    """Evidence must be there around the onset: from 15 min before the start to 15 min after (or the end)."""
    return (ep["labels"].get("namespace", ""), round(ep["start"] - LOOKBEHIND_S), round(min(ep["end"], ep["start"] + LOOKBEHIND_S)))


def traffic_key(ep):
    L = ep["labels"]
    return (L.get("namespace", ""), L.get("slo", ""), L.get("long", ""), L.get("severity", ""), round(ep["start"]))


def rule_key(L):
    return (L.get("alertname", ""), L.get("namespace", ""), L.get("slo", ""), L.get("long", ""), L.get("severity", ""))


# ---------------------------------------------------------------- Prometheus

class Prom:
    def __init__(self, kubeconfig):
        self.kc = os.path.expanduser(kubeconfig)

    def _get(self, path, params):
        url = PROM + path + "?" + urllib.parse.urlencode(params)
        p = subprocess.run(["kubectl", "--request-timeout=60s", "--kubeconfig", self.kc, "get", "--raw", url],
                           capture_output=True, text=True)
        if p.returncode:
            raise RuntimeError(p.stderr.strip()[:300])
        d = json.loads(p.stdout)
        if d.get("status") != "success":
            raise RuntimeError(str(d)[:300])
        return d["data"]

    def query(self, q, t=None):
        return self._get("query", {"query": q, **({"time": f"{t:.3f}"} if t else {})})["result"]

    def query_range(self, q, start, end, step):
        return self._get("query_range", {"query": q, "start": f"{start:.3f}", "end": f"{end:.3f}", "step": f"{step:g}"})["result"]

    def rules(self):
        return self._get("rules", {})["groups"]


def fetch_alerts(prom, start, end, step, pool):
    """ALERTS{firing} over the range, in chunks under Prometheus' 11000 points per series."""
    chunk = step * 10000
    edges, t = [], start
    while t < end:
        edges.append((t, min(end, t + chunk)))
        t += chunk + step
    parts = []
    for res in pool.map(lambda e: prom.query_range('ALERTS{alertstate="firing"}', e[0], e[1], step), edges):
        parts += series_runs(res, step)
    return merge_series(parts)


def rule_info(groups):
    """Alert rules → {rule_key: {for, factor, objective}}; burn-rate recording rules → {(ns, slo, window): expr}."""
    alerts, records = {}, {}
    for g in groups:
        for r in g["rules"]:
            L = r.get("labels") or {}
            if r["type"] == "alerting":
                m = re.search(r">\s*\(\s*([\d.]+)\s*\*\s*\(\s*1\s*-\s*([\d.]+)\s*\)\s*\)", r["query"])
                ns = L.get("namespace") or (re.search(r'namespace="([^"]+)"', r["query"]) or [None, ""])[1]
                key = (r["name"], ns, L.get("slo", ""), L.get("long", ""), L.get("severity", ""))
                alerts[key] = {"for": float(r.get("duration") or 0), "query": r["query"],
                               **({"factor": float(m.group(1)), "objective": float(m.group(2))} if m else {})}
            else:
                m = re.match(r".+:burnrate(\w+)$", r["name"])
                if m and L.get("slo"):
                    records[(L.get("namespace", ""), L["slo"], m.group(1))] = r["query"]
    return alerts, records


def denominator(expr):
    """The right side of the top-level '/' of a Pyrra burn-rate expression (the SLI's events), or None."""
    depth = 0
    for i, c in enumerate(expr):
        depth += c == "("
        depth -= c == ")"
        if c == "/" and depth == 0:
            return expr[i + 1:].strip()
    return None


def gather_evidence(prom, ep):
    """Signals independent of the alert itself, in the episode's namespace, around its onset (evidence_key)."""
    ns, s, e = evidence_key(ep)
    if not ns:
        return []
    d = f"{max(60, int(e - s))}s"
    sel = f'namespace="{ns}"'
    out = []
    qs = {
        "restart": f"sum by (pod, container) (increase(kube_pod_container_status_restarts_total{{{sel}}}[{d}])) >= 1",
        "not-ready": f'max by (pod) (max_over_time((kube_pod_status_ready{{{sel},condition="false"}} * on(namespace, pod) '
                     f'group_left() (kube_pod_status_phase{{{sel},phase="Running"}} == 1))[{d}:1m])) > 0',
        "target-down": f"min by (job) (min_over_time(up{{{sel}}}[{d}])) == 0",
        "job-failed": f"max by (job_name) (max_over_time((kube_job_status_failed{{{sel}}} > 0 and on(namespace, job_name) "
                      f"kube_job_created{{{sel}}} > {s:.0f})[{d}:1m]))",
        "rollout": f"max by (replicaset) (max_over_time((kube_replicaset_created{{{sel}}} > {s:.0f})[{d}:1m]))",
    }
    for kind, q in qs.items():
        for r in prom.query(q, e):
            what = r["metric"].get("pod") or r["metric"].get("job") or r["metric"].get("job_name") \
                or r["metric"].get("replicaset") or "?"
            extra = f" ×{float(r['value'][1]):.0f}" if kind == "restart" else ""
            out.append(f"{kind}: {what}{extra}")
    return sorted(set(out))


def gather_traffic(prom, ep, alerts, records):
    L = ep["labels"]
    if L.get("alertname") != "ErrorBudgetBurn":
        return None
    rule = alerts.get(rule_key(L)) or {}
    expr = records.get((L.get("namespace", ""), L.get("slo", ""), L.get("long", "")))
    den = denominator(expr) if expr else None
    if not den or "factor" not in rule:
        return None
    res = prom.query(den, ep["start"])
    if not res:
        return None
    v = float(res[0]["value"][1])
    events = v * dur(L["long"]) if "rate(" in den else v
    return {"events": events, "bad_needed": events * rule["factor"] * (1 - rule["objective"])}


# ---------------------------------------------------------------- alert-speaker (laptop): what a human heard

def speaker_tables(path):
    """PHRASES / APPS / ENVS from platform/alert-speaker/speaker.py (literal dicts), to read its sentences back."""
    out = {}
    for node in ast.parse(read(path)).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and getattr(node.targets[0], "id", "") in ("PHRASES", "APPS", "ENVS"):
            out[node.targets[0].id] = ast.literal_eval(node.value)
    return out


def parse_speaker(lines, tables):
    """kubectl logs --timestamps lines → [{t, action, state, severity, alertname, namespace, text}]."""
    rev_phrase = {v: k for k, v in tables.get("PHRASES", {}).items()}
    rev_where = {}
    for a, an in tables.get("APPS", {}).items():
        for e, en in tables.get("ENVS", {}).items():
            rev_where[an + en] = f"{a}-{e}"
    out = []
    for line in lines:
        ts, _, rest = line.partition(" ")
        try:
            d = json.loads(rest)
            t = dt.datetime.fromisoformat(re.sub(r"(\.\d{6})\d+", r"\1", ts.replace("Z", "+00:00"))).timestamp()
        except ValueError:
            continue
        parts = [p for p in d.get("text", "").split("。") if p]
        if len(parts) < 3:
            continue
        head, where, what = parts[0], parts[1], parts[2]
        out.append({"t": t, "action": d.get("action"), "text": d.get("text"),
                    "state": "resolved" if head == "已恢复" else "firing",
                    "severity": "page" if head == "紧急告警" else ("" if head == "已恢复" else "ticket"),
                    "alertname": rev_phrase.get(what, ""), "namespace": rev_where.get(where, where)})
    return out


def heard(ep, events, slack=180):
    """Speaker lines announcing this episode's firing (same alert name, namespace, page or not)."""
    L = ep["labels"]
    sev = "page" if L.get("severity") == "page" else "ticket"
    return [x for x in events if x["state"] == "firing" and x["alertname"] == L.get("alertname")
            and x["namespace"] == L.get("namespace") and x["severity"] == sev
            and ep["start"] - slack <= x["t"] <= ep["end"] + slack]


# ---------------------------------------------------------------- report

def name_of(ident):
    a, ns, slo, sev, long_ = ident
    extra = ", ".join(x for x in (slo, sev, long_ and f"long={long_}") if x)
    return f"{a} {ns or '-'}" + (f" ({extra})" if extra else "")


def app_of(ns):
    app, _, env = (ns or "").rpartition("-")
    return app if env in ("dev", "staging", "prod") else (ns or "-")


def periodic(starts, tz):
    """Minute of the hour shared (±2 min) by >= 3 starts: something scheduled behind it."""
    if len(starts) < 3:
        return None
    mins = [dt.datetime.fromtimestamp(s, tz).minute for s in starts]
    dist = lambda a, b: min((a - b) % 60, (b - a) % 60)
    best = max(range(60), key=lambda m: (sum(dist(x, m) <= 2 for x in mins), -sum(dist(x, m) for x in mins)))
    near = sum(dist(x, best) <= 2 for x in mins)
    return best if near >= 3 and near >= 0.6 * len(mins) else None


def recommend(ident, eps, tz):
    """One line per alert identity, by §4 / §8. eps: its episodes (classified)."""
    sev = ident[3]
    notified = [e for e in eps if e["notified"]]
    real = [e for e in eps if e["class"] != "experiment"]
    c = {k: sum(e["class"] == k for e in real) for k in CLASSES}
    recs = []
    if real and c["noise"] >= max(1, 0.5 * len(real)):
        reasons = sorted({e["detail"] for e in real if e["class"] == "noise"})
        verb = "fix the SLI or demote to ticket" if sev == "page" else "fix the rule or delete it"
        recs.append(f"noise in {c['noise']}/{len(real)} ({', '.join(reasons)}): {verb} (§4: an unactionable page is a bug)")
    if c["unknown"] and not c["incident"]:
        recs.append(f"{c['unknown']} unexplained: label them in reports/alerts/labels.yaml; if nobody acted, "
                    f"{'demote to ticket' if sev == 'page' else 'delete'} (§8)")
    changes = sum(e["class"] == "incident" and e["detail"] == "change" for e in real)
    if c["incident"] - changes:
        recs.append(f"{c['incident'] - changes} with an independent fault signal: keep; check the runbook's first step found it")
    if changes:
        recs.append(f"{changes} during a rollout only: was the change the cause? (promotion gate, canary analysis)")
    m = periodic([e["start"] for e in real], tz)
    if m is not None:
        recs.append(f"periodic: starts cluster at :{m:02d} of the hour — look for a schedule (CronJob) behind it")
    if not real and eps:
        recs.append("fired only in experiments: tested, nothing to do")
    if not recs:
        recs.append("—")
    if not notified and sev in ("page", "ticket"):
        recs.append("(notified nobody here)")
    return recs


def render(cluster, rng, episodes, info, tz):
    start, end = rng
    f = lambda t: dt.datetime.fromtimestamp(t, tz).strftime("%m-%d %H:%M")
    hours = (end - start) / 3600
    lines = [f"# Alert value report: {cluster}, {dt.datetime.fromtimestamp(start, tz):%Y-%m-%d %H:%M} – "
             f"{dt.datetime.fromtimestamp(end, tz):%Y-%m-%d %H:%M} ({tz})", ""]
    lines += [f"Generated by `scripts/alert-report.py` ({info['generated']}). Range: {fmt_dur(end - start)}"
              + (f" — requested from {f(info['requested_start'])}, Prometheus data only from {f(info['tsdb_start'])}"
                 if info.get("tsdb_start") and info["requested_start"] < info["tsdb_start"] else "") + ".", ""]
    lines += [f"- Routing: {info['routing']}", f"- Experiments known: {info['experiments']}",
              f"- Episode = `ALERTS{{alertstate=\"firing\"}}` runs (step {info['step']:g}s) merged when < "
              f"{fmt_dur(info['gap'])} apart; Watchdog / InfoInhibitor left out.", ""]
    for w in info.get("warnings") or []:
        lines.append(f"> ⚠ {w}")
    if info.get("warnings"):
        lines.append("")
    pages = [e for e in episodes if e["notified"] and e["labels"].get("severity") == "page"]
    tickets = [e for e in episodes if e["notified"] and e["labels"].get("severity") == "ticket"]
    nn = [e for e in episodes if e["notified"] and e["class"] != "experiment"]
    act = sum(e["class"] == "incident" for e in nn)
    unk = sum(e["class"] == "unknown" for e in nn)
    lines += ["## Headline", "",
              f"- {len(episodes)} episodes of {len({e['id'] for e in episodes})} alerts; "
              f"{sum(e['notified'] for e in episodes)} notified someone ({len(pages)} pages, {len(tickets)} tickets).",
              "- By class (all episodes): " + ", ".join(f"{k} {sum(e['class'] == k for e in episodes)}" for k in CLASSES) + ".",
              (f"- Actionable share of notifications outside experiments: {act}/{len(nn)}"
               + (f" (between {act / len(nn):.0%} and {(act + unk) / len(nn):.0%}: {unk} unknown)" if nn and unk else
                  (f" ({act / len(nn):.0%})" if nn else "")) + ".") if nn else
              "- No notification outside experiments in this range.", ""]
    weeks = max(hours / 168, 1e-9)
    per_app = {}
    for e in pages:
        per_app.setdefault(app_of(e["labels"].get("namespace")), []).append(e)
    if per_app:
        lines += ["Pages per application (§4 target: < ~2 per week, all actionable)"
                  + (" — less than a day of data: no weekly rate" if hours < 24 else
                     " — extrapolated from less than a week" if hours < 168 else "") + ":", ""]
        lines += ["| Application | Pages | Per week | Actionable (incident) | Experiment | Noise | Unknown |", "|---|---|---|---|---|---|---|"]
        for app, es in sorted(per_app.items()):
            k = {c: sum(e["class"] == c for e in es) for c in CLASSES}
            lines.append(f"| {app} | {len(es)} | {f'{len(es) / weeks:.1f}' if hours >= 24 else '—'} | {k['incident']} | {k['experiment']} | {k['noise']} | {k['unknown']} |")
        lines.append("")
    by = {}
    for e in episodes:
        by.setdefault(e["id"], []).append(e)
    lines += ["## Per alert", "",
              "| Alert | Episodes | Firing | Notified | Experiment | Incident | Noise | Unknown | Actionable share |",
              "|---|---|---|---|---|---|---|---|---|"]
    order = sorted(by, key=lambda i: (-sum(e["notified"] for e in by[i]), -len(by[i]), -sum(e["firing_s"] for e in by[i])))
    for i in order:
        es = by[i]
        k = {c: sum(e["class"] == c for e in es) for c in CLASSES}
        n = [e for e in es if e["notified"] and e["class"] != "experiment"]
        share = "—" if not n else (f"{sum(e['class'] == 'incident' for e in n)}/{len(n)}")
        lines.append(f"| {name_of(i)} | {len(es)} | {fmt_dur(sum(e['firing_s'] for e in es))} | "
                     f"{sum(e['notified'] for e in es)} | {k['experiment']} | {k['incident']} | {k['noise']} | {k['unknown']} | {share} |")
    lines.append("")
    top = [i for i in order if any(e["notified"] for e in by[i])][:5]
    lines += ["## Top offenders and recommendations", ""]
    if not top:
        lines += ["Nothing notified anyone in this range. Recommendations for the alerts that fired:", ""]
        top = order[:8]
    for i in top:
        lines.append(f"- **{name_of(i)}** — {len(by[i])} episodes, {sum(e['notified'] for e in by[i])} notified: "
                     + "; ".join(recommend(i, by[i], tz)))
    rest = [i for i in order if i not in top]
    for i in rest:
        r = recommend(i, by[i], tz)
        if r and r[0] != "—":
            lines.append(f"- {name_of(i)}: " + "; ".join(r))
    never = info.get("never_fired") or []
    if never:
        lines += ["", f"Page rules that never fired in this range and have no experiment: {len(never)} "
                  f"(§5: an alert that never fired is a hope). " + ", ".join(never[:12]) + (" …" if len(never) > 12 else "")]
    lines += ["", "## Episodes", "", "| Start | End | Alert | Firing | Notified | Class | Why |", "|---|---|---|---|---|---|---|"]
    for e in episodes:
        notified = ("yes" if e["notified"] else ("inhibited" if e["routed"] else "no"))
        if e.get("heard") is not None:
            notified += f", heard {e['heard']}×"
        t0 = ("≤" if e["before_range"] else "") + f(e["start"])
        t1 = "ongoing" if e["ongoing"] else f(e["end"])
        firing = fmt_dur(e["firing_s"]) + (" (%d runs)" % len(e["runs"]) if len(e["runs"]) > 1 else "")
        cls = e["class"] + (" / " + e["detail"] if e["detail"] else "")
        why = "; ".join(e["reasons"])[:300].replace("|", "/")
        lines.append(f"| {t0} | {t1} | {name_of(e['id'])} | {firing} | {notified} | {cls} | {why} |")
    sp = info.get("speaker")
    if sp is not None:
        lines += ["", "## What the laptop said (alert-speaker log: the ground truth of \"a human was notified\")", ""]
        if isinstance(sp, str):
            lines.append(sp)
        else:
            lines.append(f"Log coverage: {f(sp['from'])} – {f(end)} (the pod's lifetime; earlier lines are lost with it). "
                         f"{sp['batches']} webhook deliveries, {sp['firing']} firing sentences, {sp['resolved']} resolved, "
                         f"{sp['quiet']} kept silent (quiet hours), {sp['failed']} failed.")
            lines += ["", "| Sentence | Spoken | Quiet hours | First | Last | Pattern |", "|---|---|---|---|---|---|"]
            for text, c in sorted(sp["by_text"].items(), key=lambda x: -x[1]["n"]):
                m = periodic(c["batches"], tz)
                pat = f"{len(c['batches'])} deliveries" + (f", mostly at :{m:02d}" if m is not None else "")
                lines.append(f"| {text} | {c['spoken']} | {c['quiet']} | {f(c['first'])} | {f(c['last'])} | {pat} |")
            if sp.get("mismatch"):
                lines += ["", "Model vs. speaker log (the routing model, Argo CD sync delay or delivery is off): "
                          + "; ".join(sp["mismatch"])]
    lines += ["", "## How to read this", "",
              "- experiment: a fault experiment marked the namespace (start → end + 30 min cooldown); expected / "
              "unexpected per its `expect`. Results files carry no cluster name: they are applied to the cluster "
              "given by `--results-cluster`.",
              "- incident: an independent signal in the namespace (restart, running pod not ready, target down, failed "
              "job, new ReplicaSet = rollout) from 15 min before the start to 15 min after. A coincidence (e.g. a routine rollout) "
              "counts too: read the reason.",
              "- noise: no experiment, no signal, and a noise signature (short, flapping, low traffic, absent right after "
              "Prometheus started). unknown: the data cannot tell — label it in `reports/alerts/labels.yaml`.",
              "- Notified: routing and inhibition of the overlay at the commit in force; Alertmanager's own state "
              "(silences, delivery failures, repeat_interval) is not modelled — the speaker log is the check.", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------- main

def load_labels(path):
    if not path or not os.path.exists(path):
        return []
    out = []
    for x in yaml.safe_load(read(path)) or []:
        x = dict(x)
        for k in ("from", "to"):
            if k in x:
                v = x[k]
                x[k] = (v if isinstance(v, dt.datetime) else dt.datetime.fromisoformat(str(v))).timestamp()
        out.append(x)
    return out


def experiment_windows(results_glob, tz, metric_runs, experiments_dir, marks=()):
    """Windows from the results files, the exported run metrics, and the namespaces' current marks (in that order of
    trust; a later source adds only runs the earlier ones do not cover)."""
    xs = [x for x in (parse_results(p, tz, experiments_dir) for p in sorted(glob.glob(results_glob))) if x]
    for name, ns, t, took in metric_runs:   # slo_experiment_last_run_timestamp_seconds: when a run ended
        spec_path = os.path.join(experiments_dir, f"{name}.yaml")
        spec = yaml.safe_load(read(spec_path)) if os.path.exists(spec_path) else {}
        if any(x["name"] == name and x["start"] <= t <= x["end"] for x in xs):
            continue
        took = took or dur(spec.get("timeout", "15m")) + dur(spec.get("recover", "0s"))
        xs.append({"name": name, "file": "metric slo_experiment_last_run_timestamp_seconds", "namespace": ns or spec.get("namespace"),
                   "start": t - took, "end": t + COOLDOWN_S, "fault": bool(spec.get("fault")),
                   "expect": spec.get("expect") or {}, "verdict": "?"})
    for name, ns, s, e in marks:   # gitops-platform/experiment(-cooldown) on a namespace now: a run not recorded above
        if any(x["namespace"] == ns and x["start"] <= e and x["end"] >= s for x in xs):
            continue
        spec_path = os.path.join(experiments_dir, f"{name}.yaml") if name else ""
        spec = yaml.safe_load(read(spec_path)) if spec_path and os.path.exists(spec_path) else {}
        xs.append({"name": name or "?", "file": "namespace annotation" + ("" if name else ", start approximate"),
                   "namespace": ns, "start": s, "end": e, "fault": bool(spec.get("fault")) if name else None,
                   "expect": spec.get("expect") or {}, "verdict": "?"})
    return xs


def namespace_marks(kubeconfig, now):
    """(name, namespace, start, end) from the runner's annotations (scripts/experiment.py): a running experiment (its
    mark has the start), or a cooldown (only its end is known: the fault ended 30 min before; start assumed 1h earlier)."""
    p = subprocess.run(["kubectl", "--request-timeout=30s", "--kubeconfig", os.path.expanduser(kubeconfig), "get",
                        "namespaces", "-o", "json"], capture_output=True, text=True)
    out = []
    if p.returncode:
        return out
    for n in json.loads(p.stdout)["items"]:
        a = n["metadata"].get("annotations") or {}
        ns = n["metadata"]["name"]
        try:
            if "gitops-platform/experiment" in a:
                name, _, started = a["gitops-platform/experiment"].split()
                out.append((name, ns, float(started), now + COOLDOWN_S))
            elif "gitops-platform/experiment-cooldown" in a:
                e = float(a["gitops-platform/experiment-cooldown"])
                out.append((None, ns, e - COOLDOWN_S - 3600, e))
        except ValueError:
            continue
    return out


def metric_runs_of(prom, start, end, step):
    """Runs recorded as metrics (platform/experiments' exporter): (experiment, namespace, ended, took or None)."""
    try:
        res = prom.query_range("max by (experiment, namespace) (slo_experiment_last_run_timestamp_seconds)",
                               start - 6 * 3600, end, max(step, 60))
    except RuntimeError:
        return []
    first = {}
    for r in res:
        for t, v in r["values"]:
            k = (r["metric"].get("experiment", ""), r["metric"].get("namespace"), float(v))
            first.setdefault(k, float(t))
    out = []
    for (name, ns, v), t in sorted(first.items()):
        try:
            d = prom.query(f'max(slo_experiment_last_duration_seconds{{experiment="{name}"}})', t)
        except RuntimeError:
            d = []
        out.append((name, ns, v, float(d[0]["value"][1]) if d else None))
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cluster", required=True, help="clusters/<name> of this repository: laptop, demo (kind)")
    p.add_argument("--kubeconfig", help="default: " + ", ".join(f"{k} → {v}" for k, v in KUBECONFIGS.items()))
    p.add_argument("--since", default="30d")
    p.add_argument("--from", dest="from_", help="ISO time (default: now - since)")
    p.add_argument("--to", help="ISO time (default: now)")
    p.add_argument("--step", type=float, default=30)
    p.add_argument("--gap", default="5m", help="firing runs closer than this are one episode")
    p.add_argument("--short", default="3m", help="a notified episode shorter than this is noise")
    p.add_argument("--min-bad", type=float, default=5, help="fewer bad events than this paging = low traffic")
    p.add_argument("--results", default=os.path.join(ROOT, "experiments/results/*.md"))
    p.add_argument("--results-cluster", default="demo", help="the cluster the results files were run on")
    p.add_argument("--labels", default=os.path.join(ROOT, "reports/alerts/labels.yaml"))
    p.add_argument("--tz", default="Asia/Tokyo")
    p.add_argument("-o", "--output", help="Markdown file (default: stdout)")
    p.add_argument("--json", help="also write the episodes as JSON")
    p.add_argument("--workers", type=int, default=12)
    a = p.parse_args(argv)
    tz = load_tz(a.tz)
    prom = Prom(a.kubeconfig or KUBECONFIGS.get(a.cluster, "~/.kube/config-missing"))
    now = dt.datetime.now(dt.timezone.utc).timestamp()
    end = parse_time(a.to, tz) if a.to else now
    start = parse_time(a.from_, tz) if a.from_ else end - dur(a.since)
    warnings = []
    try:
        lowest = prom.query("min(prometheus_tsdb_lowest_timestamp_seconds)")
    except RuntimeError as e:
        sys.exit(f"{a.cluster}: Prometheus unreachable ({e})")
    tsdb_start = float(lowest[0]["value"][1]) if lowest else None
    requested = start
    if tsdb_start and start < tsdb_start:
        start = tsdb_start
        warnings.append(f"Prometheus keeps data only from {dt.datetime.fromtimestamp(tsdb_start, tz):%Y-%m-%d %H:%M}: "
                        f"the report covers {fmt_dur(end - start)}, not the {fmt_dur(end - requested)} asked for.")
    start = math.floor(start / a.step) * a.step + a.step
    with cf.ThreadPoolExecutor(a.workers) as pool:
        fut_rules = pool.submit(prom.rules)
        fut_runs = pool.submit(metric_runs_of, prom, start, end, a.step)
        fut_marks = pool.submit(namespace_marks, prom.kc, now)
        series = fetch_alerts(prom, start, end, a.step, pool)
        alerts, records = rule_info(fut_rules.result())
        metric_runs, marks = fut_runs.result(), fut_marks.result()
        versions = load_routing(ROOT, a.cluster)
        episodes = build_episodes(series, dur(a.gap), start, end, a.step)
        apply_routing(episodes, series, versions)
        experiments = experiment_windows(a.results if a.cluster == a.results_cluster else "", tz, metric_runs,
                                         os.path.join(ROOT, "experiments"), marks)
        ev_keys = {evidence_key(e): e for e in episodes}
        tr_keys = {traffic_key(e): e for e in episodes}
        ev = dict(zip(ev_keys, pool.map(lambda e: gather_evidence(prom, e), ev_keys.values())))
        tr = dict(zip(tr_keys, pool.map(lambda e: gather_traffic(prom, e, alerts, records), tr_keys.values())))
    ctx = {"circular": circular_evidence(alerts), "experiments": experiments, "evidence": ev, "traffic": tr, "tsdb_start": tsdb_start, "rules": alerts,
           "labels": load_labels(a.labels), "short": dur(a.short), "min_bad": a.min_bad}
    for e in episodes:
        e["class"], e["detail"], e["reasons"] = classify(e, ctx)
    speaker = speaker_summary(a, episodes, requested, end, tz) if a.cluster == "laptop" else None
    never = never_fired(alerts, episodes, os.path.join(ROOT, "experiments"))
    v = versions[-1]
    info = {"generated": dt.datetime.fromtimestamp(now, tz).strftime("%Y-%m-%d %H:%M %Z"), "requested_start": requested,
            "tsdb_start": tsdb_start, "step": a.step, "gap": dur(a.gap), "warnings": warnings, "speaker": speaker,
            "never_fired": never,
            "routing": (f"clusters/{a.cluster}/kustomization.yaml, {len(versions)} version(s) from git, latest {v[2]}"
                        + (" (no Alertmanager config: chart default, nobody is notified)" if v[1] is DEFAULT_AM else "")),
            "experiments": (f"{sum(x['start'] < end and x['end'] > start for x in experiments)} in range "
                            f"(of {len(experiments)} known: {os.path.relpath(a.results, ROOT) if a.cluster == a.results_cluster else 'results files are for ' + a.results_cluster}"
                            f"; {len(metric_runs)} runs from slo_experiment_* metrics; {len(marks)} namespace marks now)")}
    md = render(a.cluster, (start, end), episodes, info, tz)
    if a.output:
        os.makedirs(os.path.dirname(os.path.abspath(a.output)), exist_ok=True)
        with open(a.output, "w", encoding="utf-8") as fh:
            fh.write(md + "\n")
        print(f"wrote {a.output}: {len(episodes)} episodes", file=sys.stderr)
    else:
        print(md)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump([{k: v for k, v in e.items() if k != "id"} | {"identity": dict(zip(IDENTITY, e["id"]))}
                       for e in episodes], fh, indent=1, ensure_ascii=False)


def speaker_summary(a, episodes, start, end, tz):
    """The speaker's log from `start` (it may reach further back than Prometheus) and episodes matched to it."""
    kc = os.path.expanduser(a.kubeconfig or KUBECONFIGS["laptop"])
    base = ["kubectl", "--request-timeout=30s", "--kubeconfig", kc, "-n", "alert-speaker"]
    p = subprocess.run(base + ["logs", "deploy/alert-speaker", "--timestamps",
                               "--since-time=" + dt.datetime.fromtimestamp(start, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")],
                       capture_output=True, text=True)
    q = subprocess.run(base + ["get", "pods", "-l", "app=alert-speaker", "-o", "json"], capture_output=True, text=True)
    if p.returncode:
        return f"alert-speaker log not readable: {p.stderr.strip()[:200]}"
    try:
        started = min(dt.datetime.fromisoformat(x["status"]["startTime"].replace("Z", "+00:00")).timestamp()
                      for x in json.loads(q.stdout)["items"])
    except (ValueError, KeyError, json.JSONDecodeError):
        started = start
    events = parse_speaker(p.stdout.splitlines(), speaker_tables(os.path.join(ROOT, "platform/alert-speaker/speaker.py")))
    cover = max(start, started)
    by_text, batches, last = {}, 0, None
    for x in events:
        c = by_text.setdefault(x["text"], {"n": 0, "spoken": 0, "quiet": 0, "first": x["t"], "last": x["t"], "batches": []})
        c["n"] += 1
        if not c["batches"] or x["t"] - c["batches"][-1] > 2:
            c["batches"].append(x["t"])
        c["spoken" if x["action"] == "spoken" else "quiet"] += x["action"] in ("spoken", "quiet")
        c["last"] = x["t"]
        if last is None or x["t"] - last > 2:
            batches += 1
        last = x["t"]
    mismatch = []
    for e in episodes:
        h = heard(e, events)
        e["heard"] = len(h) if e["start"] >= cover else None
        when = dt.datetime.fromtimestamp(e["start"], tz).strftime("%m-%d %H:%M")
        if e["start"] >= cover + 60 and e["start"] < end - 600:   # enough time to have been delivered
            if e["notified"] and not h:
                mismatch.append(f"predicted to notify, not heard: {name_of(e['id'])} at {when}")
            elif h and not e["notified"]:
                mismatch.append(f"heard although predicted silent: {name_of(e['id'])} at {when}")
    return {"from": cover, "batches": batches, "by_text": by_text, "mismatch": mismatch,
            "firing": sum(x["state"] == "firing" for x in events), "resolved": sum(x["state"] == "resolved" for x in events),
            "quiet": sum(x["action"] == "quiet" for x in events), "failed": sum(x["action"] == "speak_failed" for x in events)}


def never_fired(alerts, episodes, experiments_dir):
    """Prod page rules (per SLO) with no episode in the range and no experiment that expects them to fire."""
    fired = {(e["labels"].get("alertname"), e["labels"].get("slo", "")) for e in episodes}
    tested = set()
    for x in glob.glob(os.path.join(experiments_dir, "*.yaml")):
        for w in ((yaml.safe_load(read(x)) or {}).get("expect") or {}).get("fire") or []:
            tested.add((w.get("alert"), (w.get("labels") or {}).get("slo", "")))
    return sorted({f"{k[0]} {k[2]} ({k[1]})" for k in alerts
                   if k[4] == "page" and re.search(r"-prod$", k[1] or "") and (k[0], k[2]) not in fired | tested})


def load_tz(name):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:  # no tz database: JST, the default
        return dt.timezone(dt.timedelta(hours=9), "JST")


def parse_time(s, tz):
    t = dt.datetime.fromisoformat(s)
    return (t if t.tzinfo else t.replace(tzinfo=tz)).timestamp()


if __name__ == "__main__":
    main()
