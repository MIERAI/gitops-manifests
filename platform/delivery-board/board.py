"""Delivery board: every delivery of api-factory as a run, like a CI build history, from design PR to prod.

A run is one design PR (design → generate → release → dev → promote → prod) or one commit pushed straight to main
(release → dev → promote → prod). The list page shows one row per run; /run/<id> shows its steps as a flow graph
(done green, running animated, waiting for a person orange, failed red).

Where the steps come from (all read-only):
  GitHub, APP repo:       design PR (spec-pr runs = AI review rounds, merge), generate run, generated PR (verify, merge),
                          release run and its jobs (verify, images, push to dev)
  GitHub, MANIFESTS repo: commits of overlays/dev and overlays/prod (when a version reached dev / prod)
  this cluster:           Argo CD sync / health and the running version of dev and prod
"Contains commit M" = the running tag is M or a later commit of main (order of main's commit list).

GET / (list)  GET /run/<id>  GET /metrics  GET /healthz.  Env: GITHUB_TOKEN (fine-grained read-only: Actions,
Contents, Pull requests), APP_REPO, MANIFESTS_REPO, K8S_API (local dev: kubectl proxy), INTERVAL. Standard library only.
"""
import base64, datetime, html, json, os, re, ssl, sys, threading, time, urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP = os.environ.get("APP_REPO", "MIERAI/api-factory")
MANIFESTS = os.environ.get("MANIFESTS_REPO", "MIERAI/gitops-manifests")
INTERVAL = int(os.environ.get("INTERVAL", "30"))
TOKEN = os.environ.get("GITHUB_TOKEN", "")
TZ = datetime.timezone(datetime.timedelta(hours=9))
SA = "/var/run/secrets/kubernetes.io/serviceaccount"
MAX_RUNS = 12

state = {"runs": [], "envs": {}, "updated": None, "error": ""}
cache = {}  # immutable GitHub objects (files of a merged PR, file content at a commit, jobs of a finished run)


# ── sources ────────────────────────────────────────────────────────────────────────────────────────────────────────
def gh(path):
    req = urllib.request.Request("https://api.github.com/" + path, headers={
        "Authorization": "Bearer " + TOKEN, "Accept": "application/vnd.github+json", "User-Agent": "delivery-board"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def gh_cached(path, final=True):
    if path in cache:
        return cache[path]
    v = gh(path)
    if final:
        cache[path] = v
    return v


def k8s(path):
    base = os.environ.get("K8S_API")
    if base:
        req, ctx = urllib.request.Request(base + path), None
    else:
        req = urllib.request.Request("https://kubernetes.default.svc" + path,
                                     headers={"Authorization": "Bearer " + open(SA + "/token").read()})
        ctx = ssl.create_default_context(cafile=SA + "/ca.crt")
    with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
        return json.loads(r.read())


def ts(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def env_state(ns):
    out = {"tag": "", "sync": "?", "health": "?", "synced_at": None}
    try:
        d = k8s(f"/apis/apps/v1/namespaces/{ns}/deployments/api-factory")
        img = next(c["image"] for c in d["spec"]["template"]["spec"]["containers"] if c["name"] == "api")
        out["tag"] = img.rsplit(":", 1)[-1]
        st = d.get("status", {})
        out["ready"] = st.get("updatedReplicas") == d["spec"]["replicas"] == st.get("readyReplicas")
    except Exception as e:
        out["error"] = str(e)[:80]
    try:
        a = k8s(f"/apis/argoproj.io/v1alpha1/namespaces/argocd/applications/{ns}")["status"]
        out["sync"], out["health"] = a.get("sync", {}).get("status", "?"), a.get("health", {}).get("status", "?")
        out["synced_at"] = ts(a.get("operationState", {}).get("finishedAt"))
    except Exception as e:
        out["error"] = str(e)[:80]
    return out


# ── model ──────────────────────────────────────────────────────────────────────────────────────────────────────────
class Step:
    def __init__(self, stage, name, status="waiting", start=None, end=None, url="", note=""):
        self.stage, self.name, self.status, self.start, self.end, self.url, self.note = stage, name, status, start, end, url, note


def run_status(r):
    if r.status == "completed":
        return "done" if r.conclusion == "success" else ("skip" if r.conclusion == "skipped" else "failed")
    return "running"


def wf(run, stage, name):
    if not run:
        return Step(stage, name)
    return Step(stage, name, run_status(Obj(run)), ts(run.get("run_started_at") or run["created_at"]),
                ts(run["updated_at"]) if run["status"] == "completed" else None, run["html_url"])


class Obj(dict):
    __getattr__ = dict.get


def job_steps(run, stage, names, fallback):
    """Steps from the jobs of a workflow run (names: job name → step name)."""
    if not run:
        return [Step(stage, fallback)]
    jobs = gh_cached(f"repos/{APP}/actions/runs/{run['id']}/jobs?per_page=30", run["status"] == "completed")["jobs"]
    out = []
    for j in jobs:
        if j["name"] in names:
            st = "running" if j["status"] != "completed" else ("done" if j["conclusion"] == "success" else (
                "skip" if j["conclusion"] == "skipped" else "failed"))
            out.append(Step(stage, names[j["name"]], st, ts(j["started_at"]), ts(j["completed_at"]), j["html_url"]))
    return out or [wf(run, stage, fallback)]


def release_steps(sha, releases):
    r = releases.get(sha)
    if not r:
        return [Step("release", "release")]
    final = r["status"] == "completed"
    jobs = gh_cached(f"repos/{APP}/actions/runs/{r['id']}/jobs?per_page=30", final)["jobs"]
    names = {"verify / verify": "verify", "image-api / build": "API 镜像", "image-schema / build": "迁移镜像",
             "promote / promote": "推送 dev"}
    out = []
    for j in jobs:
        if j["name"] not in names:
            continue
        st = "running" if j["status"] != "completed" else ("done" if j["conclusion"] == "success" else "failed")
        out.append(Step("release", names[j["name"]], st, ts(j["started_at"]), ts(j["completed_at"]), j["html_url"]))
    return out or [wf(r, "release", "release")]


def env_history(path):
    """Versions a kustomization went through: [(time, tag, commit url)], oldest first."""
    out = []
    for c in gh(f"repos/{MANIFESTS}/commits?path={path}&per_page=40"):
        content = gh_cached(f"repos/{MANIFESTS}/contents/{path}?ref={c['sha']}")
        text = base64.b64decode(content["content"]).decode()
        m = re.search(r'name: ghcr.io/mierai/api-factory\n\s+newTag: "?([0-9a-f]+)"?', text)
        if m:
            out.append((ts(c["commit"]["committer"]["date"]), m.group(1), c["html_url"]))
    return sorted(out)


def collect():
    main = gh(f"repos/{APP}/commits?sha=main&per_page=100")
    pos = {c["sha"][:7]: i for i, c in enumerate(main)}  # 0 = newest

    def contains(commit, tag):
        a, b = pos.get(commit[:7]), pos.get(tag[:7])
        return a is not None and b is not None and b <= a

    pulls = gh(f"repos/{APP}/pulls?state=all&per_page=40&sort=created&direction=desc")
    runs = gh(f"repos/{APP}/actions/runs?per_page=100")["workflow_runs"]
    by_sha = {}
    for r in runs:  # newest first: the first run of a workflow for a commit is its latest
        by_sha.setdefault((r["name"], r["head_sha"]), r)
    releases = {sha: r for (n, sha), r in by_sha.items() if n == "release"}
    gens = {}
    for p in pulls:
        m = re.match(r"factory/gen-([0-9a-f]{7})", p["head"]["ref"])
        if m:
            gens.setdefault(m.group(1), p)
    envs = {"dev": env_state("api-factory-dev"), "prod": env_state("api-factory-prod")}
    hist = {"dev": env_history("api-factory/overlays/dev/kustomization.yaml"),
            "prod": env_history("api-factory/overlays/prod/kustomization.yaml")}
    promote_open = [p for p in gh(f"repos/{MANIFESTS}/pulls?state=open&per_page=10") if p["title"].startswith("promote api-factory")]

    def delivery(sha, steps):
        """dev / promote / prod steps for main commit sha."""
        for env in ("dev", "prod"):
            first = next(((t, tag, url) for t, tag, url in hist[env] if contains(sha, tag)), None)
            if env == "prod":
                if first:
                    steps.append(Step("promote", "promote PR", "done", end=first[0], url=first[2]))
                elif steps[-1].status == "done":
                    pr = promote_open[0] if promote_open else None
                    steps.append(Step("promote", "promote PR", "person", note=f"#{pr['number']} 等合并" if pr else "等 promote",
                                      url=pr["html_url"] if pr else f"https://github.com/{MANIFESTS}/actions/workflows/promote.yml"))
                else:
                    steps.append(Step("promote", "promote PR"))
            e = envs[env]
            label = f"{env} 上线"
            if not first:
                steps.append(Step(env, label))
            elif contains(sha, e["tag"]) and not (e.get("ready") and e["health"] == "Healthy") and hist[env][-1][1] == e["tag"]:
                steps.append(Step(env, label, "running", start=first[0], note=f"{e['sync']}/{e['health']}"))
            else:
                steps.append(Step(env, label, "done", start=first[0], end=first[0], url=first[2], note=first[1]))
        return steps

    out, used = [], set()
    for p in [p for p in pulls if p["head"]["ref"].startswith(("design/", "review/")) or p["title"].startswith("design")][:MAX_RUNS]:
        if p["state"] == "closed" and not p["merged_at"]:
            continue
        files = gh_cached(f"repos/{APP}/pulls/{p['number']}/files?per_page=100", final=p["state"] == "closed")
        apis = sorted({re.sub(r"\.yaml$", "", f["filename"].split("/")[-1]) for f in files
                       if re.match(r"design/[^/]+/[A-Z][A-Za-z0-9]+\.yaml$", f["filename"])})
        steps = []
        reviews = [r for r in runs if r["name"] == "spec-pr" and r["head_branch"] == p["head"]["ref"]]
        if reviews:
            last = reviews[0]
            st = run_status(Obj(last))
            steps.append(Step("design", "AI 审查", "person" if st == "failed" and p["state"] == "open" else st,
                              ts(reviews[-1]["created_at"]), ts(last["updated_at"]) if last["status"] == "completed" else None,
                              last["html_url"], f"{len(reviews)} 轮" + (" · 有问题要回答" if st == "failed" and p["state"] == "open" else "")))
        if p["merged_at"]:
            steps.append(Step("design", "设计书合并", "done", end=ts(p["merged_at"]), url=p["html_url"]))
            msha = p["merge_commit_sha"]
            g = gens.get(msha[:7])
            gen_run = by_sha.get(("generate", msha))
            # the gate is the verify job inside the generate run (the pull_request verify of a PR opened by the bot
            # fails at start-up: 0 jobs)
            steps += job_steps(gen_run, "gen", {"generate": "生成", "verify / verify": "verify"}, "生成") if gen_run else [
                Step("gen", "生成", "skip", note="后续手动生成")]
            sha = None
            if g:
                if g["merged_at"]:
                    steps.append(Step("gen", "生成 PR 合并", "done", end=ts(g["merged_at"]), url=g["html_url"]))
                    sha = g["merge_commit_sha"]
                elif g["state"] == "open":
                    steps.append(Step("gen", "生成 PR 合并", "person", url=g["html_url"], note="等合并"))
            elif any(contains(msha, e["tag"]) for e in envs.values()):
                sha = msha
            if sha:
                used.add(sha)
                steps += release_steps(sha, releases) if sha in releases else [Step("release", "release", "skip", note="随后续 release")]
                delivery(sha, steps)
        else:
            steps.append(Step("design", "设计书合并", "person", url=p["html_url"], note="等合并"))
        name = ", ".join(apis[:2]) + (f" +{len(apis) - 2}" if len(apis) > 2 else "") if apis else p["title"]
        out.append({"id": f"pr-{p['number']}", "name": name, "title": p["title"], "trigger": f"#{p['number']}",
                    "trigger_url": p["html_url"], "created": ts(p["created_at"]), "steps": steps})
    for (n, sha), r in by_sha.items():
        if n != "release" or sha in used or r["event"] != "push":
            continue
        steps = delivery(sha, release_steps(sha, releases))
        out.append({"id": f"c-{sha[:7]}", "name": "工厂: " + r["display_title"][:70], "title": r["display_title"],
                    "trigger": sha[:7], "trigger_url": f"https://github.com/{APP}/commit/{sha}", "created": ts(r["created_at"]),
                    "steps": steps})
    out.sort(key=lambda x: x["created"], reverse=True)
    return out[:MAX_RUNS], envs


def overall(steps):
    sts = [s.status for s in steps]
    for k in ("failed", "running", "person"):
        if k in sts:
            return k
    return "done" if all(s in ("done", "skip") for s in sts) else "waiting"


def current(steps):
    for s in steps:
        if s.status in ("failed", "running", "person", "waiting"):
            return s
    return steps[-1] if steps else None


def loop():
    while True:
        try:
            runs, envs = collect()
            state.update(runs=runs, envs=envs, updated=datetime.datetime.now(TZ), error="")
        except Exception as e:
            state["error"] = f"{type(e).__name__}: {e}"[:200]
            print(json.dumps({"error": state["error"]}), flush=True)
        time.sleep(INTERVAL)


# ── pages ──────────────────────────────────────────────────────────────────────────────────────────────────────────
COLORS = {"done": "#1e8e3e", "running": "#1a73e8", "person": "#e37400", "failed": "#d93025", "waiting": "#9aa0a6", "skip": "#bdc1c6"}
ICONS = {"done": "✓", "running": "⟳", "person": "⏸", "failed": "✕", "waiting": "·", "skip": "–"}
WORDS = {"done": "完成", "running": "进行中", "person": "等人处理", "failed": "失败", "waiting": "未开始", "skip": "跳过"}
STAGES = {"design": "设计书", "gen": "生成", "release": "release", "dev": "dev", "promote": "promote", "prod": "prod"}

CSS = """
:root{--bg:#f8f9fa;--card:#fff;--fg:#202124;--mut:#5f6368;--line:#dadce0}
@media (prefers-color-scheme:dark){:root{--bg:#1f1f1f;--card:#2a2a2a;--fg:#e8eaed;--mut:#9aa0a6;--line:#3c4043}}
body{margin:0;background:var(--bg);color:var(--fg);font:14px -apple-system,system-ui,"PingFang SC",sans-serif}
header{padding:14px 20px;border-bottom:1px solid var(--line);display:flex;gap:20px;align-items:center;flex-wrap:wrap}
header h1{font-size:18px;margin:0;font-weight:600}.env{color:var(--mut)}.env b{color:var(--fg)}
main{padding:16px 20px;max-width:1300px}a{color:inherit;text-decoration:none}
table{width:100%;border-collapse:collapse;background:var(--card);border-radius:8px;overflow:hidden}
th{font-weight:500;color:var(--mut);text-align:left;padding:10px 12px;border-bottom:1px solid var(--line);font-size:12px}
td{padding:10px 12px;border-bottom:1px solid var(--line);vertical-align:middle}tr.r:hover{background:rgba(26,115,232,.06);cursor:pointer}
.ic{display:inline-flex;width:22px;height:22px;border-radius:50%;color:#fff;align-items:center;justify-content:center;font-size:13px;font-weight:700}
.running .ic,.ic.running{animation:spin 1.2s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}
.dots{display:flex;gap:3px}.dot{width:14px;height:6px;border-radius:3px}.dot.running{animation:pulse 1s ease-in-out infinite}
@keyframes pulse{50%{opacity:.35}}.mut{color:var(--mut);font-size:12px}.name{font-weight:500}
.flow{display:flex;flex-wrap:wrap;align-items:stretch;gap:0;margin-top:12px}
.stage{display:flex;flex-direction:column;gap:6px}.stage h3{margin:0 0 2px;font-size:12px;color:var(--mut);font-weight:500;text-transform:uppercase}
.row{display:flex;align-items:center}
.node{background:var(--card);border:2px solid var(--c);border-radius:10px;padding:10px 12px;min-width:118px;position:relative}
.node .t{font-weight:600;display:flex;gap:6px;align-items:center}.node .s{font-size:12px;color:var(--mut);margin-top:4px}
.node.running{box-shadow:0 0 0 0 var(--c);animation:glow 1.6s infinite}@keyframes glow{70%{box-shadow:0 0 0 10px rgba(26,115,232,0)}}
.node.person{animation:glowp 1.6s infinite}@keyframes glowp{0%{box-shadow:0 0 0 0 rgba(227,116,0,.6)}70%{box-shadow:0 0 0 10px rgba(227,116,0,0)}}
.edge{width:44px;height:4px;margin:0 2px;border-radius:2px;background:var(--line);align-self:center}
.edge.done{background:#1e8e3e}
.edge.flow{background:repeating-linear-gradient(90deg,#1a73e8 0 10px,transparent 10px 18px);background-size:36px 4px;animation:flow .6s linear infinite}
.edge.wait{background:repeating-linear-gradient(90deg,#e37400 0 8px,transparent 8px 14px);background-size:28px 4px;animation:flow 1.2s linear infinite}
@keyframes flow{from{background-position:0 0}to{background-position:36px 0}}
.legend span{margin-right:12px}.err{color:#d93025}
"""


def fmt_dur(a, b):
    if not a:
        return ""
    s = int(((b or datetime.datetime.now(datetime.timezone.utc)) - a).total_seconds())
    return f"{s // 3600}h{s % 3600 // 60:02d}m" if s >= 3600 else (f"{s // 60}m{s % 60:02d}s" if s >= 60 else f"{s}s")


def fmt_t(t):
    return t.astimezone(TZ).strftime("%m-%d %H:%M") if t else ""


def head(title, refresh):
    envs = " ".join(f'<span class="env">{k} <b>{html.escape(e.get("tag", ""))}</b> {e.get("sync")}/{e.get("health")}</span>'
                    for k, e in state["envs"].items())
    upd = state["updated"].strftime("%H:%M:%S") if state["updated"] else "—"
    err = f'<span class="err">{html.escape(state["error"])}</span>' if state["error"] else ""
    return (f'<!doctype html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta http-equiv="refresh" content="{refresh}"><title>{html.escape(title)}</title><style>{CSS}</style></head><body>'
            f'<header><h1><a href="/">api-factory 交付</a></h1>{envs}<span class="mut">更新 {upd}</span>{err}</header><main>')


def list_page():
    out = [head("Delivery board", INTERVAL), '<table><tr><th></th><th>交付</th><th>触发</th><th>开始</th><th>耗时</th><th>当前</th><th>进度</th></tr>']
    for r in state["runs"]:
        st, cur = overall(r["steps"]), current(r["steps"])
        start = next((s.start or s.end for s in r["steps"] if s.start or s.end), r["created"])
        end = None if st in ("running", "person", "waiting") else max((s.end for s in r["steps"] if s.end), default=None)
        dots = "".join(f'<span class="dot {s.status}" title="{html.escape(s.name)}: {WORDS[s.status]}" style="background:{COLORS[s.status]}"></span>' for s in r["steps"])
        curtxt = "已上线 prod" if st == "done" else f"{STAGES[cur.stage]} · {cur.name}" + (f" · {cur.note}" if cur.note else "")
        out.append(f'<tr class="r {st}" onclick="location.href=\'/run/{r["id"]}\'">'
                   f'<td><span class="ic {st}" style="background:{COLORS[st]}">{ICONS[st]}</span></td>'
                   f'<td><div class="name">{html.escape(r["name"])}</div><div class="mut">{html.escape(r["title"][:90])}</div></td>'
                   f'<td><a href="{html.escape(r["trigger_url"])}" onclick="event.stopPropagation()">{html.escape(r["trigger"])}</a></td>'
                   f'<td class="mut">{fmt_t(start)}</td><td class="mut">{fmt_dur(start, end)}</td>'
                   f'<td>{html.escape(curtxt)}</td><td><div class="dots">{dots}</div></td></tr>')
    out.append("</table>")
    out.append('<p class="legend mut">' + "".join(f'<span><span class="ic" style="background:{COLORS[k]};width:16px;height:16px;font-size:10px">{ICONS[k]}</span> {WORDS[k]}</span>' for k in ("done", "running", "person", "failed", "waiting")) + "</p></main></body></html>")
    return "".join(out)


def run_page(rid):
    r = next((x for x in state["runs"] if x["id"] == rid), None)
    if not r:
        return None
    st = overall(r["steps"])
    out = [head(r["name"], 10), f'<div style="display:flex;gap:12px;align-items:center"><span class="ic {st}" style="background:{COLORS[st]};width:28px;height:28px">{ICONS[st]}</span>'
           f'<div><div class="name" style="font-size:17px">{html.escape(r["name"])}</div><div class="mut">{html.escape(r["title"])} · '
           f'<a href="{html.escape(r["trigger_url"])}">{html.escape(r["trigger"])}</a> · {WORDS[st]}</div></div></div><div class="flow">']
    groups = []
    for s in r["steps"]:
        if not groups or groups[-1][0] != s.stage:
            groups.append((s.stage, []))
        groups[-1][1].append(s)
    prev = None
    for gi, (stage, steps) in enumerate(groups):
        cells = []
        for s in steps:
            if prev is not None:
                cls = "done" if prev.status in ("done", "skip") and s.status in ("done", "skip") else (
                    "flow" if s.status == "running" else ("wait" if s.status == "person" else ""))
                cells.append(f'<div class="edge {cls}"></div>')
            when = fmt_t(s.end or s.start)
            dur = fmt_dur(s.start, s.end) if s.start else ""
            sub = " · ".join(x for x in (s.note, dur, when) if x)
            node = (f'<div class="node {s.status}" style="--c:{COLORS[s.status]}"><div class="t"><span class="ic {s.status if s.status == "running" else ""}" '
                    f'style="background:{COLORS[s.status]};width:18px;height:18px;font-size:11px">{ICONS[s.status]}</span>{html.escape(s.name)}</div>'
                    f'<div class="s">{html.escape(sub) or WORDS[s.status]}</div></div>')
            cells.append(f'<a href="{html.escape(s.url)}" target="_blank">{node}</a>' if s.url else node)
            prev = s
        out.append(f'<div class="stage"><h3>{STAGES[stage]}</h3><div class="row">{"".join(cells)}</div></div>')
    out.append('</div><p class="mut" style="margin-top:20px">节点可点击(GitHub 上的运行记录 / PR / gitops 提交)。每 10 秒刷新。</p></main></body></html>')
    return "".join(out)


VALUES = {"done": 2, "skip": 2, "running": 1, "waiting": 0, "failed": -1, "person": -2}


def metrics():
    lines = ["# HELP delivery_run_status 2 done, 1 running, 0 waiting, -1 failed, -2 waiting for a person",
             "# TYPE delivery_run_status gauge"]
    for r in state["runs"]:
        name = re.sub(r'["\\\n]', "", r["name"])[:80]
        lines.append(f'delivery_run_status{{run="{r["id"]}",name="{name}"}} {VALUES[overall(r["steps"])]}')
    age = (datetime.datetime.now(TZ) - state["updated"]).total_seconds() if state["updated"] else -1
    lines.append(f"delivery_board_last_update_age_seconds {age:.0f}")
    return "\n".join(lines) + "\n"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            body, ctype = list_page(), "text/html; charset=utf-8"
        elif self.path.startswith("/run/"):
            body, ctype = run_page(self.path[5:]), "text/html; charset=utf-8"
        elif self.path == "/metrics":
            body, ctype = metrics(), "text/plain; version=0.0.4"
        elif self.path == "/healthz":
            body, ctype = "ok", "text/plain"
        else:
            body = None
        if body is None:
            self.send_response(404); self.end_headers(); return
        b = body.encode()
        self.send_response(200); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    threading.Thread(target=loop, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", int(sys.argv[1]) if len(sys.argv) > 1 else 8080), Handler).serve_forever()
