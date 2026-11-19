"""Delivery board: where every change to api-factory is now, from design PR to prod.

A change goes through 3 repositories and the cluster; nothing else shows the whole chain:
  1 design PR (api-factory design/*)   AI review → answers → merged
  2 generated PR (factory/gen-<sha>)   generate → verify → merged      (merge commit M = the release)
  3 release (workflow on main, M)       build images → dev tag in gitops-manifests
  4 dev  (this cluster)                 Argo CD sync → running a version that contains M
  5 promote PR (gitops-manifests)       dev → prod, merged by a person
  6 prod (this cluster)                 running a version that contains M
Commits pushed straight to main (factory code) start at 3. "Contains M" = the running tag is M or a later commit
(GitHub compare API, cached).

GET /          the board (HTML, refreshes itself)
GET /metrics   delivery_change_stage{change, stage} (0 waiting, 1 running, 2 done, -1 failed, -2 needs a person)
GET /healthz
Env: GITHUB_TOKEN (fine-grained, read-only: Actions, Contents, Pull requests on api-factory + gitops-manifests), APP_REPO, MANIFESTS_REPO,
K8S_API (default in-cluster), INTERVAL (s). Standard library only.
"""
import datetime, html, json, os, re, ssl, sys, threading, time, urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP = os.environ.get("APP_REPO", "MIERAI/api-factory")
MANIFESTS = os.environ.get("MANIFESTS_REPO", "MIERAI/gitops-manifests")
INTERVAL = int(os.environ.get("INTERVAL", "30"))
TOKEN = os.environ.get("GITHUB_TOKEN", "")
TZ = datetime.timezone(datetime.timedelta(hours=9))
SA = "/var/run/secrets/kubernetes.io/serviceaccount"

state = {"rows": [], "envs": {}, "updated": None, "error": ""}
_contains = {}  # (commit, tag) → bool: commits never change


def gh(path):
    req = urllib.request.Request("https://api.github.com/" + path, headers={
        "Authorization": "Bearer " + TOKEN, "Accept": "application/vnd.github+json", "User-Agent": "delivery-board"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def k8s(path):
    base = os.environ.get("K8S_API")
    if base:  # local development: kubectl proxy
        req, ctx = urllib.request.Request(base + path), None
    else:
        tok = open(SA + "/token").read()
        req = urllib.request.Request("https://kubernetes.default.svc" + path, headers={"Authorization": "Bearer " + tok})
        ctx = ssl.create_default_context(cafile=SA + "/ca.crt")
    with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
        return json.loads(r.read())


def contains(commit, tag):
    """True if tag (a commit of APP) is commit or comes after it."""
    if not commit or not tag:
        return False
    if commit.startswith(tag) or tag.startswith(commit[:7]):
        return True
    key = (commit, tag)
    if key not in _contains:
        try:
            st = gh(f"repos/{APP}/compare/{commit}...{tag}")["status"]
            _contains[key] = st in ("ahead", "identical")
        except urllib.error.HTTPError:
            return False  # unknown tag (yet): not cached
    return _contains[key]


def env_state(ns):
    """Version the API runs in a namespace + Argo CD status of its Application."""
    out = {"tag": "", "sync": "?", "health": "?", "op": ""}
    try:
        d = k8s(f"/apis/apps/v1/namespaces/{ns}/deployments/api-factory")
        img = next(c["image"] for c in d["spec"]["template"]["spec"]["containers"] if c["name"] == "api")
        out["tag"] = img.rsplit(":", 1)[-1]
        out["ready"] = d.get("status", {}).get("updatedReplicas") == d["spec"]["replicas"] == d.get("status", {}).get("readyReplicas")
    except Exception as e:
        out["error"] = str(e)[:80]
    try:
        a = k8s(f"/apis/argoproj.io/v1alpha1/namespaces/argocd/applications/{ns}")["status"]
        out["sync"], out["health"] = a.get("sync", {}).get("status", "?"), a.get("health", {}).get("status", "?")
        out["op"] = a.get("operationState", {}).get("phase", "")
    except Exception as e:
        out["error"] = str(e)[:80]
    return out


def checks(sha):
    """Workflow runs of a commit (PR checks): (running, failed). Actions API: a fine-grained token has no Checks
    permission (GitHub Apps only), Actions read is enough."""
    runs = gh(f"repos/{APP}/actions/runs?head_sha={sha}&per_page=20").get("workflow_runs", [])
    latest = {}
    for r in runs:  # newest first: the latest run of each workflow counts (re-runs)
        latest.setdefault(r["name"], r)
    running = [n for n, r in latest.items() if r["status"] != "completed"]
    failed = [n for n, r in latest.items() if r["conclusion"] in ("failure", "timed_out", "cancelled")]
    return running, failed


def collect():
    pulls = gh(f"repos/{APP}/pulls?state=all&per_page=40&sort=created&direction=desc")
    runs = gh(f"repos/{APP}/actions/runs?branch=main&event=push&per_page=40")["workflow_runs"]
    releases = {}  # head sha → latest release run
    for r in runs:
        if r["name"] == "release" and r["head_sha"] not in releases:
            releases[r["head_sha"]] = r
    gens = {}  # design merge sha[:7] → generated PR
    for p in pulls:
        m = re.match(r"factory/gen-([0-9a-f]{7})", p["head"]["ref"])
        if m and m.group(1) not in gens:
            gens[m.group(1)] = p
    promotes = [p for p in gh(f"repos/{MANIFESTS}/pulls?state=all&per_page=20") if p["title"].startswith("promote api-factory")]
    envs = {ns: env_state(ns) for ns in ("api-factory-dev", "api-factory-prod")}

    rows = []
    designs = [p for p in pulls if not p["head"]["ref"].startswith(("factory/gen-", "dependabot/"))][:8]
    used = set()
    for p in designs:
        row = {"title": f'#{p["number"]} {p["title"]}', "url": p["html_url"], "when": p["created_at"], "stages": {}}
        s = row["stages"]
        if p["state"] == "open":
            running, failed = checks(p["head"]["sha"])
            s["design"] = ("running", "AI 审查中") if running else (("person", "有要回答的问题") if failed else ("person", "等合并"))
        elif not p["merged_at"]:
            s["design"] = ("closed", "已关闭(未合并)")
            rows.append(row)
            continue
        else:
            s["design"] = ("done", "已合并")
            g = gens.get(p["merge_commit_sha"][:7])
            if not g and any(contains(p["merge_commit_sha"], e["tag"]) for e in envs.values()):
                # regenerated later (manual run, another base): shipped with a later release
                s["gen"] = ("done", "随后续生成")
                row["commit"] = p["merge_commit_sha"]
            elif not g:
                gen_run = next((r for r in gh(f"repos/{APP}/actions/runs?event=push&head_sha={p['merge_commit_sha']}")["workflow_runs"] if r["name"] == "generate"), None)
                s["gen"] = ("running", "生成中") if gen_run and gen_run["status"] != "completed" else (("failed", "生成失败") if gen_run and gen_run["conclusion"] == "failure" else ("waiting", "等生成"))
            elif g["state"] == "open":
                running, failed = checks(g["head"]["sha"])
                s["gen"] = ("running", "verify 中") if running else (("failed", "verify 失败") if failed else ("person", "等合并"))
                row["gen_url"] = g["html_url"]
            elif g["merged_at"]:
                s["gen"] = ("done", "已合并")
                row["gen_url"] = g["html_url"]
                row["commit"] = g["merge_commit_sha"]
            else:
                s["gen"] = ("closed", "已关闭")
        rows.append(row)
        if row.get("commit"):
            used.add(row["commit"])
    # commits pushed straight to main (factory code, docs) that have a release
    for sha, r in list(releases.items())[:8]:
        if sha in used:
            continue
        rows.append({"title": r["display_title"][:90], "url": r["html_url"], "when": r["created_at"], "commit": sha,
                     "stages": {"design": ("skip", "—"), "gen": ("skip", "直接推 main")}})
    for row in rows:
        c, s = row.get("commit"), row["stages"]
        if not c:
            continue
        r = releases.get(c)
        shipped = any(contains(c, e["tag"]) for e in envs.values())
        s["release"] = (("done", "随后续 release") if shipped else ("waiting", "等 release")) if not r else (("running", "构建中") if r["status"] != "completed" else (("done", "已发布") if r["conclusion"] == "success" else ("failed", f"release {r['conclusion']}")))
        row["release_url"] = r["html_url"] if r else ""
        for env, key in (("api-factory-dev", "dev"), ("api-factory-prod", "prod")):
            e = envs[env]
            if contains(c, e["tag"]):
                ok = e.get("ready") and e["health"] == "Healthy"
                s[key] = ("done", f"已上线 {e['tag']}") if ok else ("running", f"部署中 {e['tag']}")
            elif key == "dev":
                s[key] = ("running", "同步中") if s["release"][0] == "done" else ("waiting", "")
        if s.get("dev", ("",))[0] == "done" and "prod" not in s:
            pr = next((p for p in promotes if p["state"] == "open"), None)
            s["promote"] = ("person", f"promote PR #{pr['number']} 等合并") if pr else ("person", "等 promote")
            row["promote_url"] = pr["html_url"] if pr else f"https://github.com/{MANIFESTS}/actions/workflows/promote.yml"
            s["prod"] = ("waiting", "")
        elif "prod" in s:
            s["promote"] = ("done", "已 promote")
    rows.sort(key=lambda r: r["when"], reverse=True)
    return rows, envs


def loop():
    while True:
        try:
            rows, envs = collect()
            state.update(rows=rows, envs=envs, updated=datetime.datetime.now(TZ), error="")
        except Exception as e:  # keep the last good board, show the error
            state["error"] = f"{type(e).__name__}: {e}"[:200]
            print(json.dumps({"error": state["error"]}), flush=True)
        time.sleep(INTERVAL)


STAGES = [("design", "设计书 PR"), ("gen", "生成 PR"), ("release", "release"), ("dev", "dev"), ("promote", "promote"), ("prod", "prod")]
COLORS = {"done": "#2e7d32", "running": "#1565c0", "person": "#ef6c00", "failed": "#c62828", "waiting": "#9e9e9e",
          "skip": "#cfd8dc", "closed": "#757575"}
LEGEND = {"done": "完成", "running": "进行中", "person": "等人", "failed": "失败", "waiting": "未开始"}
VALUES = {"done": 2, "running": 1, "waiting": 0, "failed": -1, "person": -2, "skip": 2, "closed": 2}


def page():
    rows = state["rows"]
    out = ['<!doctype html><html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
           f'<meta http-equiv="refresh" content="{INTERVAL}"><title>Delivery board</title><style>',
           "body{font:14px -apple-system,system-ui,sans-serif;margin:16px;background:#fafafa;color:#212121}",
           "table{border-collapse:separate;border-spacing:4px;width:100%}td,th{padding:6px 8px;text-align:left}",
           "th{font-weight:600;color:#555}.c{border-radius:6px;color:#fff;font-size:12px;white-space:nowrap}",
           "a{color:inherit}.t{max-width:420px}.env{display:inline-block;margin-right:16px}.err{color:#c62828}",
           "@media (prefers-color-scheme: dark){body{background:#121212;color:#eee}th{color:#aaa}}</style></head><body>",
           "<h2>api-factory delivery board</h2><div>"]
    for ns, e in state["envs"].items():
        out.append(f'<span class="env"><b>{ns}</b>: {html.escape(e.get("tag", ""))} · {e.get("sync")}/{e.get("health")}</span>')
    upd = state["updated"].strftime("%H:%M:%S") if state["updated"] else "—"
    out.append(f'</div><p>更新 {upd} · 每 {INTERVAL}s · ' + " ".join(
        f'<span class="c" style="background:{COLORS[k]};padding:2px 6px">{v}</span>' for k, v in LEGEND.items()) + "</p>")
    if state["error"]:
        out.append(f'<p class="err">{html.escape(state["error"])}</p>')
    out.append("<table><tr><th>改动</th>" + "".join(f"<th>{n}</th>" for _, n in STAGES) + "</tr>")
    links = {"gen": "gen_url", "release": "release_url", "promote": "promote_url"}
    for r in rows:
        out.append(f'<tr><td class="t"><a href="{html.escape(r["url"])}">{html.escape(r["title"])}</a></td>')
        for k, _ in STAGES:
            kind, text = r["stages"].get(k, ("waiting", ""))
            text = html.escape(text)
            if r.get(links.get(k, "")):
                text = f'<a href="{html.escape(r[links[k]])}">{text}</a>'
            out.append(f'<td class="c" style="background:{COLORS[kind]}">{text}</td>')
        out.append("</tr>")
    out.append("</table></body></html>")
    return "".join(out)


def metrics():
    lines = ["# HELP delivery_change_stage 2 done, 1 running, 0 waiting, -1 failed, -2 waiting for a person",
             "# TYPE delivery_change_stage gauge"]
    for r in state["rows"]:
        name = re.sub(r'["\\\n]', "", r["title"])[:80]
        for k, _ in STAGES:
            kind = r["stages"].get(k, ("waiting", ""))[0]
            lines.append(f'delivery_change_stage{{change="{name}",stage="{k}"}} {VALUES[kind]}')
    age = (datetime.datetime.now(TZ) - state["updated"]).total_seconds() if state["updated"] else -1
    lines.append(f"delivery_board_last_update_age_seconds {age:.0f}")
    return "\n".join(lines) + "\n"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body, ctype = {"/": (page, "text/html; charset=utf-8"), "/metrics": (metrics, "text/plain; version=0.0.4"),
                       "/healthz": (lambda: "ok", "text/plain")}.get(self.path, (None, None))
        if not body:
            self.send_response(404); self.end_headers(); return
        b = body().encode()
        self.send_response(200); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    threading.Thread(target=loop, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", int(sys.argv[1]) if len(sys.argv) > 1 else 8080), Handler).serve_forever()
