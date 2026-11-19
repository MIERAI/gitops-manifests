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
        out.append({"id": f"pr-{p['number']}", "name": name, "apis": apis, "title": p["title"], "trigger": f"#{p['number']}",
                    "trigger_url": p["html_url"], "created": ts(p["created_at"]), "steps": steps})
    for (n, sha), r in by_sha.items():
        if n != "release" or sha in used or r["event"] != "push":
            continue
        steps = delivery(sha, release_steps(sha, releases))
        out.append({"id": f"c-{sha[:7]}", "name": "工厂: " + r["display_title"][:70], "apis": [], "title": r["display_title"],
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


# ── try it: the delivered APIs' cases (gen/api-catalog.json at the version dev runs) + a live call on dev ──────────
DEV_URL = os.environ.get("PUBLIC_DEV_URL", "http://100.90.245.89:30080")
PROD_URL = os.environ.get("PUBLIC_PROD_URL", "http://100.90.245.89:30081")
DEV_INTERNAL = os.environ.get("DEV_INTERNAL_URL", "http://api-factory.api-factory-dev.svc:8080")
DEV_TOKEN = os.environ.get("DEV_TOKEN", "")
_live = {}  # (path, body) → (time, status, response)


def catalog():
    tag = state["envs"].get("dev", {}).get("tag")
    if not tag:
        return {}
    try:
        c = gh_cached(f"repos/{APP}/contents/gen/api-catalog.json?ref={tag}")
        return {a["method"]: a for a in json.loads(base64.b64decode(c["content"]))}
    except Exception:
        return {}


def live(path, body):
    key = (path, json.dumps(body, sort_keys=True))
    hit = _live.get(key)
    if hit and time.time() - hit[0] < 60:
        return hit[1], hit[2]
    req = urllib.request.Request(DEV_INTERNAL + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + DEV_TOKEN})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            res = (200, json.loads(r.read() or b"{}"))
    except urllib.error.HTTPError as e:
        res = (e.code, json.loads(e.read() or b"{}"))
    except Exception as e:
        res = (0, {"error": str(e)[:120]})
    res = (res[0], res[1], int((time.time() - t0) * 1000))
    _live[key] = (time.time(), res[0], res[1:])
    return res[0], res[1:]


# ── pages ──────────────────────────────────────────────────────────────────────────────────────────────────────────
COLORS = {"done": "#188038", "running": "#1a73e8", "person": "#e37400", "failed": "#d93025", "waiting": "#9aa0a6", "skip": "#bdc1c6"}
WORDS = {"done": "成功", "running": "进行中", "person": "等人处理", "failed": "失败", "waiting": "未开始", "skip": "跳过"}
STAGES = {"design": "设计书", "gen": "生成", "release": "Release", "dev": "Dev", "promote": "Promote", "prod": "Prod"}
SVG = {  # 16px icons (stroke = currentColor)
    "done": '<path d="M3.5 8.5l3 3 6-7" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
    "failed": '<path d="M4.5 4.5l7 7m0-7l-7 7" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
    "running": '<path d="M8 2a6 6 0 1 1-6 6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
    "person": '<path d="M6 4v8M10 4v8" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
    "waiting": '<circle cx="8" cy="8" r="2" fill="currentColor"/>',
    "skip": '<path d="M4 8h8" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
}


def icon(st, size=22):
    spin = ' class="spin"' if st == "running" else ""
    return (f'<span class="ico ico-{st}" style="width:{size}px;height:{size}px"><svg viewBox="0 0 16 16" width="{size * 0.64:.0f}" '
            f'height="{size * 0.64:.0f}"{spin}>{SVG[st]}</svg></span>')


def pill(st):
    return f'<span class="pill pill-{st}">{icon(st, 16)}{WORDS[st]}</span>'


CSS = """
:root{--bg:#f4f6f9;--card:#fff;--fg:#1f2328;--mut:#5f6b7a;--faint:#8a96a3;--line:#e3e8ee;--soft:#f7f9fb;--shadow:0 1px 2px rgba(16,24,40,.06),0 1px 3px rgba(16,24,40,.08);
--done:#188038;--running:#1a73e8;--person:#e37400;--failed:#d93025;--waiting:#9aa0a6;--skip:#bdc1c6;
--done-bg:#e6f4ea;--running-bg:#e8f0fe;--person-bg:#fef3e2;--failed-bg:#fce8e6;--waiting-bg:#f1f3f4;--skip-bg:#f1f3f4}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a21;--fg:#e6e9ef;--mut:#a0a8b4;--faint:#77808c;--line:#262b34;--soft:#1c2029;--shadow:none;
--done-bg:rgba(24,128,56,.18);--running-bg:rgba(26,115,232,.18);--person-bg:rgba(227,116,0,.18);--failed-bg:rgba(217,48,37,.18);--waiting-bg:#232730;--skip-bg:#232730}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Noto Sans SC",sans-serif}
a{color:inherit;text-decoration:none}code,pre{font:12.5px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace}
.top{background:linear-gradient(120deg,#0b3d91,#1a73e8 55%,#3b9dff);color:#fff;padding:18px 28px 54px}
.top .bar{display:flex;align-items:center;gap:16px;flex-wrap:wrap;max-width:1240px;margin:0 auto}
.brand{font-size:18px;font-weight:650;letter-spacing:.2px;display:flex;gap:10px;align-items:center}
.brand .logo{width:28px;height:28px;border-radius:8px;background:rgba(255,255,255,.18);display:grid;place-items:center}
.envs{display:flex;gap:10px;margin-left:auto;flex-wrap:wrap}
.env{background:rgba(255,255,255,.14);border:1px solid rgba(255,255,255,.22);border-radius:999px;padding:5px 12px;font-size:12.5px;display:flex;gap:8px;align-items:center}
.env b{font-family:ui-monospace,Menlo,monospace;font-weight:600}.dot{width:8px;height:8px;border-radius:50%;background:#7dffa8;box-shadow:0 0 0 3px rgba(125,255,168,.25)}
.dot.bad{background:#ffb4a8;box-shadow:0 0 0 3px rgba(255,180,168,.25)}.upd{font-size:12px;opacity:.8}
.wrap{max-width:1240px;margin:-36px auto 40px;padding:0 20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow)}
.card h2{font-size:15px;margin:0;padding:16px 20px;border-bottom:1px solid var(--line);font-weight:600;display:flex;align-items:center;gap:10px}
.list .row{display:grid;grid-template-columns:34px minmax(0,2.4fr) 90px 120px 90px minmax(0,1.6fr) 150px;gap:14px;align-items:center;padding:13px 20px;border-bottom:1px solid var(--line);transition:background .15s}
.list .row:last-child{border-bottom:0}.list .row:hover{background:var(--soft)}.list .head{font-size:11.5px;color:var(--faint);text-transform:uppercase;letter-spacing:.6px;padding:10px 20px}
.list .head:hover{background:none}.name{font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.sub{color:var(--mut);font-size:12.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mut{color:var(--mut)}.mono{font-family:ui-monospace,Menlo,monospace;font-size:12.5px}
.ico{display:inline-grid;place-items:center;border-radius:50%;color:#fff;flex:none}
.ico-done{background:var(--done)}.ico-running{background:var(--running)}.ico-person{background:var(--person)}.ico-failed{background:var(--failed)}.ico-waiting{background:var(--waiting-bg);color:var(--waiting)}.ico-skip{background:var(--skip-bg);color:var(--skip)}
.spin{animation:spin 1s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}
.pill{display:inline-flex;gap:6px;align-items:center;border-radius:999px;padding:3px 10px 3px 4px;font-size:12px;font-weight:600}
.pill-done{background:var(--done-bg);color:var(--done)}.pill-running{background:var(--running-bg);color:var(--running)}.pill-person{background:var(--person-bg);color:var(--person)}
.pill-failed{background:var(--failed-bg);color:var(--failed)}.pill-waiting,.pill-skip{background:var(--waiting-bg);color:var(--mut)}
.seg{display:flex;gap:3px}.seg i{flex:1;height:7px;border-radius:4px;background:var(--waiting-bg)}
.seg i.done,.seg i.skip{background:var(--done)}.seg i.running{background:var(--running);animation:pulse 1s infinite}.seg i.person{background:var(--person);animation:pulse 1.4s infinite}.seg i.failed{background:var(--failed)}
@keyframes pulse{50%{opacity:.35}}
.hero{padding:20px 22px;display:flex;gap:16px;align-items:center;flex-wrap:wrap}.hero .t{font-size:19px;font-weight:650}
.meta{display:flex;gap:22px;flex-wrap:wrap;margin-left:auto}.meta div{font-size:12px;color:var(--faint)}.meta b{display:block;color:var(--fg);font-size:14px;font-weight:600}
.pipe{display:flex;align-items:stretch;gap:0;overflow-x:auto;padding:22px 20px 24px}
.stage{min-width:190px;flex:1;background:var(--soft);border:1px solid var(--line);border-radius:12px;padding:12px;display:flex;flex-direction:column;gap:10px}
.stage .sh{display:flex;align-items:center;gap:8px;font-size:12px;font-weight:650;color:var(--mut);text-transform:uppercase;letter-spacing:.6px}
.job{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--c);border-radius:9px;padding:9px 11px;display:flex;gap:9px;align-items:flex-start;transition:transform .15s,box-shadow .15s}
a.job:hover{transform:translateY(-1px);box-shadow:var(--shadow)}.job .jn{font-weight:600;font-size:13px}.job .js{font-size:11.5px;color:var(--mut)}
.job.running{animation:ring 1.6s infinite}@keyframes ring{0%{box-shadow:0 0 0 0 rgba(26,115,232,.45)}70%{box-shadow:0 0 0 9px rgba(26,115,232,0)}}
.job.person{animation:ringp 1.8s infinite}@keyframes ringp{0%{box-shadow:0 0 0 0 rgba(227,116,0,.45)}70%{box-shadow:0 0 0 9px rgba(227,116,0,0)}}
.link{flex:none;width:46px;align-self:center;height:4px;border-radius:2px;background:var(--line);position:relative;margin:0 4px}
.link.done{background:var(--done)}.link.flow{background:repeating-linear-gradient(90deg,var(--running) 0 9px,transparent 9px 16px);background-size:32px 4px;animation:flow .5s linear infinite}
.link.wait{background:repeating-linear-gradient(90deg,var(--person) 0 7px,transparent 7px 13px);background-size:26px 4px;animation:flow 1.1s linear infinite}
.link:after{content:"";position:absolute;right:-5px;top:-4px;border:6px solid transparent;border-left:7px solid var(--line)}
.link.done:after{border-left-color:var(--done)}.link.flow:after{border-left-color:var(--running)}.link.wait:after{border-left-color:var(--person)}
@keyframes flow{to{background-position:32px 0}}
.apis{display:grid;gap:16px;padding:18px 20px}.api{border:1px solid var(--line);border-radius:12px;overflow:hidden}
.api .ah{padding:12px 16px;background:var(--soft);border-bottom:1px solid var(--line);display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.api .ah .m{font-weight:650;font-size:15px}.ep{display:flex;gap:8px;flex-wrap:wrap;margin-left:auto}
.tag{font-size:11px;font-weight:700;border-radius:6px;padding:2px 7px;letter-spacing:.4px}.tag.dev{background:var(--running-bg);color:var(--running)}.tag.prod{background:var(--done-bg);color:var(--done)}
.case{padding:14px 16px;border-top:1px solid var(--line)}.case:first-of-type{border-top:0}.case .cn{font-weight:600;margin-bottom:10px}
.cols{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.cols h4{margin:0 0 6px;font-size:11.5px;color:var(--faint);text-transform:uppercase;letter-spacing:.5px;display:flex;gap:8px;align-items:center}
pre{margin:0;background:var(--soft);border:1px solid var(--line);border-radius:8px;padding:10px 12px;overflow:auto;max-height:260px}
.curl{position:relative;margin-top:12px}.curl button{position:absolute;right:8px;top:8px;border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:6px;font-size:12px;padding:3px 9px;cursor:pointer}
.status{font-size:11px;border-radius:5px;padding:1px 6px;font-weight:700}.status.ok{background:var(--done-bg);color:var(--done)}.status.ng{background:var(--failed-bg);color:var(--failed)}
.empty{padding:22px;color:var(--mut)}.err{background:var(--failed-bg);color:var(--failed);padding:10px 16px;border-radius:10px;margin-bottom:12px}
@media (max-width:900px){.list .row{grid-template-columns:30px minmax(0,1fr) 110px}.list .row>:nth-child(n+4){display:none}.cols{grid-template-columns:1fr}.top{padding:16px 16px 50px}.wrap{padding:0 12px}}
"""


def fmt_dur(a, b):
    if not a:
        return "—"
    s = int(((b or datetime.datetime.now(datetime.timezone.utc)) - a).total_seconds())
    return f"{s // 3600}h {s % 3600 // 60}m" if s >= 3600 else (f"{s // 60}m {s % 60}s" if s >= 60 else f"{s}s")


def ago(t):
    if not t:
        return "—"
    s = int((datetime.datetime.now(datetime.timezone.utc) - t).total_seconds())
    return "刚刚" if s < 60 else (f"{s // 60} 分钟前" if s < 3600 else (f"{s // 3600} 小时前" if s < 86400 else t.astimezone(TZ).strftime("%m-%d %H:%M")))


def page(title, refresh, body):
    envs = []
    for k, e in state["envs"].items():
        ok = e.get("health") == "Healthy" and e.get("sync") == "Synced"
        envs.append(f'<span class="env"><span class="dot{"" if ok else " bad"}"></span>{k} <b>{html.escape(e.get("tag", ""))}</b></span>')
    upd = state["updated"].strftime("%H:%M:%S") if state["updated"] else "—"
    err = f'<div class="err">{html.escape(state["error"])}</div>' if state["error"] else ""
    return (f'<!doctype html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta http-equiv="refresh" content="{refresh}"><title>{html.escape(title)}</title><style>{CSS}</style></head><body>'
            f'<div class="top"><div class="bar"><a class="brand" href="/"><span class="logo">{icon("done", 18).replace("ico-done", "")}</span>api-factory 交付</a>'
            f'<div class="envs">{"".join(envs)}<span class="env upd">更新 {upd}</span></div></div></div><div class="wrap">{err}{body}</div>'
            '<script>function cp(b){navigator.clipboard.writeText(b.parentElement.querySelector("code").innerText);b.textContent="已复制";setTimeout(()=>b.textContent="复制",1200)}</script>'
            "</body></html>")


def list_page():
    rows = ['<div class="card list"><h2>交付历史<span class="mut" style="font-weight:400;font-size:12.5px">设计书 PR 或推到 main 的提交 → prod</span></h2>',
            '<div class="row head"><span></span><span>交付</span><span>触发</span><span>开始</span><span>耗时</span><span>当前</span><span>进度</span></div>']
    for r in state["runs"]:
        st, cur = overall(r["steps"]), current(r["steps"])
        start = next((s.start or s.end for s in r["steps"] if s.start or s.end), r["created"])
        end = None if st in ("running", "person", "waiting") else max((s.end for s in r["steps"] if s.end), default=None)
        seg = "".join(f'<i class="{s.status}" title="{html.escape(STAGES[s.stage])} · {html.escape(s.name)}: {WORDS[s.status]}"></i>' for s in r["steps"])
        now = "已上线 Prod" if st == "done" else f'{STAGES[cur.stage]} · {cur.name}' + (f" · {cur.note}" if cur.note else "")
        rows.append(f'<a class="row" href="/run/{r["id"]}">{icon(st)}<span><div class="name">{html.escape(r["name"])}</div>'
                    f'<div class="sub">{html.escape(r["title"])}</div></span><span class="mono">{html.escape(r["trigger"])}</span>'
                    f'<span class="mut">{ago(start)}</span><span class="mut">{fmt_dur(start, end)}</span>'
                    f'<span>{pill(st) if st != "done" else ""} <span class="mut">{html.escape(now)}</span></span><span class="seg">{seg}</span></a>')
    if not state["runs"]:
        rows.append('<div class="empty">还没有数据(启动后第一次收集需要几十秒)</div>')
    rows.append("</div>")
    return page("api-factory 交付", INTERVAL, "".join(rows))


def try_section(r):
    if not r["apis"]:
        return ('<div class="card" style="margin-top:18px"><h2>试一下</h2><div class="empty">这次是工厂本身的改动,'
                '没有新增或修改 API 定义。API 的地址: dev ' + html.escape(DEV_URL) + ' · prod ' + html.escape(PROD_URL) + '</div></div>')
    cat = catalog()
    out = ['<div class="card" style="margin-top:18px"><h2>试一下<span class="mut" style="font-weight:400;font-size:12.5px">'
           '用例 = 设计书里人写的例子(预期结果基于用例自己的准备数据);dev 实际返回基于 dev 的演示数据</span></h2><div class="apis">']
    for m in r["apis"]:
        a = cat.get(m)
        if not a:
            out.append(f'<div class="api"><div class="ah"><span class="m">{html.escape(m)}</span><span class="mut">dev 还没有这个 API(未上线或已撤回)</span></div></div>')
            continue
        out.append(f'<div class="api"><div class="ah"><span class="m">{html.escape(m)}</span><span class="mut">{html.escape(a["summary"])}</span>'
                   f'<span class="ep"><span><span class="tag dev">DEV</span> <code>{html.escape(DEV_URL + a["path"])}</code></span>'
                   f'<span><span class="tag prod">PROD</span> <code>{html.escape(PROD_URL + a["path"])}</code></span></span></div>')
        for c in a["cases"]:
            code, (resp, ms) = live(a["path"], c["request"])
            ok = code == 200
            req = json.dumps(c["request"], ensure_ascii=False, indent=2)
            exp = json.dumps(c.get("response") or {}, ensure_ascii=False, indent=2) if c.get("response") is not None else "(没有写预期)"
            got = json.dumps(resp, ensure_ascii=False, indent=2)
            curl = (f"curl -s -X POST {DEV_URL}{a['path']} \\\n  -H 'Content-Type: application/json' \\\n"
                    f"  -H \"Authorization: Bearer $(cat ~/workspace/gitops/infrastructure-live/.secrets/laptop/api-factory-dev.token)\" \\\n"
                    f"  -d '{json.dumps(c['request'], ensure_ascii=False)}'")
            out.append(f'<div class="case"><div class="cn">{html.escape(c["name"])}</div><div class="cols">'
                       f'<div><h4>请求</h4><pre><code>{html.escape(req)}</code></pre></div>'
                       f'<div><h4>用例预期</h4><pre><code>{html.escape(exp)}</code></pre></div>'
                       f'<div><h4>dev 实际返回 <span class="status {"ok" if ok else "ng"}">{code or "ERR"}</span><span>{ms} ms</span></h4>'
                       f'<pre><code>{html.escape(got[:4000])}</code></pre></div></div>'
                       f'<div class="curl"><pre><code>{html.escape(curl)}</code></pre><button onclick="cp(this)">复制</button></div></div>')
        out.append("</div>")
    out.append("</div></div>")
    return "".join(out)


def run_page(rid):
    r = next((x for x in state["runs"] if x["id"] == rid), None)
    if not r:
        return None
    st = overall(r["steps"])
    start = next((s.start or s.end for s in r["steps"] if s.start or s.end), r["created"])
    end = None if st in ("running", "person", "waiting") else max((s.end for s in r["steps"] if s.end), default=None)
    body = [f'<div class="card"><div class="hero">{icon(st, 34)}<div><div class="t">{html.escape(r["name"])}</div>'
            f'<div class="mut">{html.escape(r["title"])} · <a class="mono" href="{html.escape(r["trigger_url"])}">{html.escape(r["trigger"])}</a></div></div>'
            f'<div class="meta"><div>状态<b>{pill(st)}</b></div><div>开始<b>{start.astimezone(TZ).strftime("%m-%d %H:%M") if start else "—"}</b></div>'
            f'<div>耗时<b>{fmt_dur(start, end)}</b></div></div></div><div class="pipe">']
    groups = []
    for s in r["steps"]:
        if not groups or groups[-1][0] != s.stage:
            groups.append((s.stage, []))
        groups[-1][1].append(s)
    prev_st = None
    for stage, steps in groups:
        gst = overall(steps)
        if prev_st is not None:
            cls = "done" if prev_st in ("done", "skip") and gst in ("done", "skip") else (
                "flow" if gst == "running" or (prev_st in ("done", "skip") and gst == "waiting" and False) else ("wait" if gst == "person" else ""))
            body.append(f'<div class="link {cls}"></div>')
        jobs = []
        for s in steps:
            when = (s.end or s.start).astimezone(TZ).strftime("%m-%d %H:%M") if (s.end or s.start) else ""
            sub = " · ".join(x for x in (s.note, fmt_dur(s.start, s.end) if s.start else "", when) if x) or WORDS[s.status]
            inner = (f'{icon(s.status, 20)}<span><div class="jn">{html.escape(s.name)}</div><div class="js">{html.escape(sub)}</div></span>')
            tagname = "a" if s.url else "div"
            href = f' href="{html.escape(s.url)}" target="_blank"' if s.url else ""
            jobs.append(f'<{tagname} class="job {s.status}" style="--c:{COLORS[s.status]}"{href}>{inner}</{tagname}>')
        body.append(f'<div class="stage"><div class="sh">{icon(gst, 18)}{STAGES[stage]}</div>{"".join(jobs)}</div>')
        prev_st = gst
    body.append("</div></div>")
    body.append(try_section(r))
    return page(r["name"], 15, "".join(body))


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
        try:
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
        except Exception as e:  # a rendering bug must not take the board down
            body, ctype = f"render error: {type(e).__name__}: {e}", "text/plain"
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
