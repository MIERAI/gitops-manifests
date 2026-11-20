"""Fault experiments: inject a fault, check that exactly the expected alerts fire (and nothing else pages), remove it.
An alert that has never fired in an experiment is a hope, not a safety net (gitops-platform-blueprint docs/slo.md §5).

  python3 scripts/experiment.py [--kubeconfig F] [--no-recover] experiments/<name>.yaml [...]

Several experiments run in parallel (give them different namespaces: they share the 1h burn-rate windows).
Experiment format (experiments/README.md):

  hypothesis: what is broken           namespace: where (needs chaos-mesh.org/inject=enabled)
  fault: [Chaos Mesh objects]          created in the namespace; deleted at the end, whatever happens
  load: [Kubernetes objects]           optional: what the fault needs to matter (e.g. a Job that writes), created
                                       before the fault, deleted with it; not checked for injection
  timeout: 15m                         how long the fault stays at most
  expect:
    fire:  [{alert, labels: {...}, within: 10m}]   each must fire within its time
    quiet: [{alert, labels: {...}}]                these must not fire (besides: no other page may fire)
  recover: 10m                         after the fault: the fired alerts resolve within this (skip: --no-recover)
  ignore: '.+-(dev|staging)'           namespaces (regex) whose pages notify nobody on this cluster: not judged
  effect: {query: <promql>, within: 3m}  the fault must visibly bite (the query returns data) — Chaos Mesh's
                                       "AllInjected" only says the rules are in place, not that traffic hits them
                                       (a NetworkChaos on the client side, filtered on pod IPs, never matched Service traffic)

Namespaces are marked while an experiment runs (annotation gitops-platform/experiment, which is also the lock: one
experiment per namespace, across processes and people) and for 30 min after (a burn-rate alert can still fire after
recovery). Pages in a marked namespace belong to its experiment; every other page fails every running experiment.

Exit 1 if any experiment failed. A report per run: experiments/results/<date>-<name>.md.
"""
import argparse
import datetime
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("PyYAML is required (apt install python3-yaml, or pip install pyyaml)")

PROM = "/api/v1/namespaces/monitoring/services/kps-prometheus:9090/proxy/api/v1/"
POLL = 15
KUBE = ["kubectl"]
lock = threading.Lock()
MARK = "gitops-platform/experiment"            # <name> <host> <started epoch>
COOLDOWN = "gitops-platform/experiment-cooldown"  # epoch until which late alerts still belong to the last experiment
COOLDOWN_S = 1800
STALE_S = 4 * 3600                                # a mark older than this is left over from a killed run


def marked_namespaces():
    """Namespaces with an experiment running or cooling down (any process): their pages are theirs."""
    out, now = set(), time.time()
    for n in json.loads(kubectl("get", "namespaces", "-o", "json"))["items"]:
        a = n["metadata"].get("annotations") or {}
        if MARK in a and now - float(a[MARK].split()[-1]) < STALE_S:
            out.add(n["metadata"]["name"])
        elif COOLDOWN in a and now < float(a[COOLDOWN]):
            out.add(n["metadata"]["name"])
    return out


def dur(s):
    m = re.fullmatch(r"(\d+)([smh])", str(s))
    return int(m.group(1)) * {"s": 1, "m": 60, "h": 3600}[m.group(2)]


def kubectl(*args, stdin=None):
    r = subprocess.run(KUBE + list(args), input=stdin, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"kubectl {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def firing():
    """Every firing alert: [labels]."""
    q = urllib.parse.quote('ALERTS{alertstate="firing"}')
    out = json.loads(kubectl("get", "--raw", f"{PROM}query?query={q}"))
    return [r["metric"] for r in out["data"]["result"]]


def matches(alert, want):
    return alert.get("alertname") == want["alert"] and all(alert.get(k) == str(v) for k, v in want.get("labels", {}).items())


def describe(a):
    keep = ("namespace", "slo", "severity", "long", "short")
    return a["alertname"] + "{" + ", ".join(f"{k}={a[k]}" for k in keep if k in a) + "}"


class Run:
    def __init__(self, path, recover):
        self.path, self.recover = path, recover
        self.x = yaml.safe_load(open(path))
        self.name = os.path.splitext(os.path.basename(path))[0]
        self.ns = self.x["namespace"]
        self.log, self.ok = [], True

    def say(self, msg):
        line = f"{datetime.datetime.now():%H:%M:%S} [{self.name}] {msg}"
        with lock:
            print(line, flush=True)
        self.log.append(line)

    def fail(self, msg):
        self.ok = False
        self.say("✗ " + msg)

    def run(self):
        try:
            self.lock()
        except Exception as e:
            self.fail(f"not started: {e}")
            self.say("FAIL")
            return
        try:
            self._run()
        except Exception as e:  # a crash is a failed experiment, never a silent pass
            self.fail(f"runner error: {e}")
            self.say("FAIL")
        finally:
            self.unlock()

    def lock(self):
        """Mark the namespace; kubectl annotate without --overwrite refuses an existing mark: the lock."""
        a = (json.loads(kubectl("get", "namespace", self.ns, "-o", "json"))["metadata"].get("annotations") or {})
        if MARK in a and time.time() - float(a[MARK].split()[-1]) >= STALE_S:
            kubectl("annotate", "namespace", self.ns, f"{MARK}-")   # left over from a killed run
        try:
            kubectl("annotate", "namespace", self.ns, f"{MARK}={self.name} {socket.gethostname()} {time.time():.0f}")
        except RuntimeError:
            raise RuntimeError(f"{self.ns} is busy: {a.get(MARK)} (one experiment per namespace)")

    def unlock(self):
        try:
            kubectl("annotate", "--overwrite", "namespace", self.ns, f"{COOLDOWN}={time.time() + COOLDOWN_S:.0f}", f"{MARK}-")
        except RuntimeError as e:
            self.say(f"could not unmark {self.ns}: {e}")

    def _run(self):
        x = self.x
        fire = x.get("expect", {}).get("fire", [])
        quiet = x.get("expect", {}).get("quiet", [])
        before = firing()
        # alerts firing before the fault (another problem, not ours) are not counted for or against it
        baseline = {json.dumps(a, sort_keys=True) for a in before}
        stale = [w for w in fire if any(matches(a, w) for a in before)]
        if stale:
            self.fail(f"expected alert already firing before the fault: {stale} — cannot test it now")
            return
        objs = x.get("fault") or []
        manifest = self.manifest(objs)
        loads = x.get("load") or []
        load_manifest = self.manifest(loads)
        self.say(f"hypothesis: {x['hypothesis']}")
        ignore = re.compile(x["ignore"]) if x.get("ignore") else None

        def ignored(a):
            return bool(ignore and ignore.fullmatch(a.get("namespace", "")))

        if not objs:  # the negative test: a page already firing is noise too
            for a in before:
                if a.get("severity") == "page" and not ignored(a):
                    self.fail(f"page firing under normal conditions: {describe(a)}")   # once (baseline: not again)
        if loads:
            kubectl("apply", "-f", "-", stdin=load_manifest)
            self.say(f"load created in {self.ns}: " + ", ".join(f"{o['kind']}/{o['metadata']['name']}" for o in loads))
        t0 = time.time()
        if objs:
            kubectl("apply", "-f", "-", stdin=manifest)
            self.say(f"fault created in {self.ns}: " + ", ".join(f"{o['kind']}/{o['metadata']['name']}" for o in objs))
        seen, unexpected = {}, {}
        try:
            if objs:
                self.wait_injected(objs)
                if x.get("effect"):
                    self.wait_effect(x["effect"])
            deadline = t0 + max([dur(x.get("timeout", "15m"))] + [dur(w.get("within", "10m")) for w in fire])
            while time.time() < deadline:
                now, marked = firing(), marked_namespaces()
                for i, w in enumerate(fire):
                    if i not in seen and any(matches(a, w) for a in now):
                        seen[i] = time.time() - t0
                        self.say(f"✓ fired after {seen[i] / 60:.1f} min: {w['alert']} {w.get('labels', {})}")
                for a in now:
                    k = json.dumps(a, sort_keys=True)
                    if k in baseline or k in unexpected or ignored(a):
                        continue
                    if any(matches(a, q) for q in quiet):
                        unexpected[k] = f"must stay quiet but fired: {describe(a)}"
                    elif a.get("severity") == "page" and not any(matches(a, w) for w in fire) \
                            and (a.get("namespace") == self.ns or a.get("namespace") not in marked):
                        # judged here: pages in our namespace, and in namespaces no experiment (of anyone) marks
                        # (a page next door is the neighbour's result, not noise caused by us)
                        unexpected[k] = f"unexpected page: {describe(a)}"
                    else:
                        continue
                    self.fail(unexpected[k])
                if len(seen) == len(fire) and time.time() - t0 >= dur(x.get("hold", "0s")):
                    break
                time.sleep(POLL)
        finally:
            if objs:
                self.remove(objs, manifest, t0)
            if loads:
                kubectl("delete", "--ignore-not-found", "--wait=false", "-f", "-", stdin=load_manifest)
                self.say("load removed")
        for i, w in enumerate(fire):
            if i not in seen:
                self.fail(f"did not fire within {w.get('within', '10m')}: {w['alert']} {w.get('labels', {})}")
            elif seen[i] > dur(w.get("within", "10m")):
                self.fail(f"fired too late ({seen[i] / 60:.1f} min > {w['within']}): {w['alert']} {w.get('labels', {})}")
        if self.recover and seen and x.get("recover"):
            t1, left = time.time(), set(seen)
            while left and time.time() - t1 < dur(x["recover"]):
                time.sleep(POLL)
                now = firing()
                for i in list(left):
                    if not any(matches(a, fire[i]) for a in now):
                        left.discard(i)
                        self.say(f"✓ resolved {(time.time() - t1) / 60:.1f} min after the fault: {fire[i]['alert']} {fire[i].get('labels', {})}")
            for i in left:
                self.fail(f"still firing {x['recover']} after the fault: {fire[i]['alert']} {fire[i].get('labels', {})}")
        self.say("PASS" if self.ok else "FAIL")

    def manifest(self, objs):
        return "\n---\n".join(yaml.safe_dump({**o, "metadata": {**o.get("metadata", {}), "namespace": self.ns}}) for o in objs)

    def remove(self, objs, manifest, t0, limit=60):
        """Delete the fault without blocking on Chaos Mesh's finalizer (it hangs when the fault never got in, or the
        daemon is unreachable), then say plainly if it is still there."""
        kubectl("delete", "--ignore-not-found", "--wait=false", "-f", "-", stdin=manifest)
        t = time.time()
        while time.time() - t < limit:
            left = [o for o in objs if subprocess.run(KUBE + ["get", "-n", self.ns, o["kind"].lower(), o["metadata"]["name"]],
                                                      capture_output=True).returncode == 0]
            if not left:
                self.say(f"fault removed after {(time.time() - t0) / 60:.1f} min")
                return
            time.sleep(5)
        self.fail("fault still present after delete (finalizer): " + ", ".join(f"{o['kind']}/{o['metadata']['name']}" for o in left)
                  + " — check `kubectl -n " + self.ns + " get podnetworkchaos,podhttpchaos` and the chaos-daemon")

    def wait_injected(self, objs, limit=90):
        """Chaos Mesh accepts an object and may still inject nothing (a denied pause image, a daemon with an old
        certificate): then "no alert fired" would test nothing. Fail unless every object is AllInjected."""
        t = time.time()
        while True:
            pending = []
            for o in objs:
                st = json.loads(kubectl("get", "-n", self.ns, o["kind"].lower(), o["metadata"]["name"], "-o", "json")).get("status", {})
                if not any(c["type"] == "AllInjected" and c["status"] == "True" for c in st.get("conditions", [])):
                    events = [e["message"] for r in st.get("experiment", {}).get("containerRecords", []) for e in r.get("events", [])
                              if e.get("type") == "Failed"]
                    pending.append(f"{o['kind']}/{o['metadata']['name']}" + (f" ({events[-1]})" if events else ""))
            if not pending:
                self.say(f"✓ fault injected ({time.time() - t:.0f}s)")
                return
            if time.time() - t > limit:
                raise RuntimeError(f"fault not injected after {limit}s: {'; '.join(pending)}")
            time.sleep(5)

    def wait_effect(self, e):
        t = time.time()
        q = urllib.parse.quote(e["query"])
        while time.time() - t < dur(e.get("within", "3m")):
            if json.loads(kubectl("get", "--raw", f"{PROM}query?query={q}"))["data"]["result"]:
                self.say(f"✓ fault takes effect ({time.time() - t:.0f}s): {e['query']}")
                return
            time.sleep(POLL)
        raise RuntimeError(f"fault injected but no effect within {e.get('within', '3m')}: {e['query']}")

    def report(self, root):
        os.makedirs(os.path.join(root, "experiments", "results"), exist_ok=True)
        f = os.path.join(root, "experiments", "results", f"{datetime.date.today()}-{self.name}.md")
        with open(f, "w") as out:
            out.write(f"# {self.name}: {'PASS' if self.ok else 'FAIL'}\n\n{self.x['hypothesis']}\n\n```\n" + "\n".join(self.log) + "\n```\n")
        return f


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--kubeconfig")
    p.add_argument("--no-recover", action="store_true")
    p.add_argument("files", nargs="+")
    a = p.parse_args()
    if a.kubeconfig:
        KUBE.extend(["--kubeconfig", a.kubeconfig])
    runs = [Run(f, not a.no_recover) for f in a.files]
    if len({r.ns for r in runs}) < len(runs):
        sys.exit("two experiments in one namespace: they would share the burn-rate windows (run them one after the other)")
    threads = [threading.Thread(target=r.run) for r in runs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    print()
    for r in runs:
        print(f"{'PASS' if r.ok else 'FAIL'}  {r.name}  ({os.path.relpath(r.report(root))})")
    sys.exit(0 if all(r.ok for r in runs) else 1)


if __name__ == "__main__":
    main()
