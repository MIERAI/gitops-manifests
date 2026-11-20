"""Alertmanager webhook → speakd (the laptop reads alerts out loud).

POST /         Alertmanager webhook payload (version 4). One sentence per group and change: what started firing
               ("紧急告警" severity page / "告警" ticket), what resolved ("已恢复"), naming the SLOs. Alertmanager resends
               every firing alert of a group at each update (group_interval): those already spoken are not repeated,
               except Alertmanager's own reminder (REPEAT_HOURS, its repeat_interval).
GET  /healthz  ok

Alert names are mapped to Chinese phrases (the Chinese voice cannot read CamelCase). During quiet hours
(QUIET_HOURS, local time, default 23-8) nothing is spoken; the alert is only logged. Standard library only.
"""
import datetime
import json
import os
import sys
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SPEAKD_URL = os.environ.get("SPEAKD_URL", "http://10.42.0.1:8770/")
QUIET = os.environ.get("QUIET_HOURS", "23-8")
REPEAT = float(os.environ.get("REPEAT_HOURS", "12")) * 3600
TZ = datetime.timezone(datetime.timedelta(hours=int(os.environ.get("TZ_OFFSET_HOURS", "9"))))

PHRASES = {
    "ErrorBudgetBurn": "错误预算消耗过快",
    "SLOMetricAbsent": "服务等级指标没有数据",
    "ApiFactoryLoadTestFailed": "部署后的压力测试失败,不要发布到生产",
    "PostgresWalArchivingFailing": "数据库日志归档失败",
    "PostgresWalArchivingStale": "数据库日志十五分钟没有归档",
    "PostgresReplicationLag": "数据库备库延迟",
    "DailyRestoreCheckFailed": "每日恢复验证失败",
    "OffsiteBackupStale": "手机上的异地备份三个小时没有成功",
}
APPS = {"api-factory": "API 工厂", "sample-app": "样例应用", "storefront": "商店"}
ENVS = {"dev": "开发环境", "staging": "预发布环境", "prod": "生产环境"}
# SLO names (app.yaml slos, label slo = <app>-<name>) read word by word; unknown words are read as they are
WORDS = {"web": "网页", "api": "接口", "rpc": "接口", "hello": "hello", "order": "订单", "availability": "可用性",
         "latency": "延迟", "severe": "严重", "probe": "外部探测", "freshness": "新鲜度", "completeness": "完整性",
         "intake": "下单", "recovery": "恢复", "point": "点", "base": "基础", "backup": "备份", "restore": "恢复",
         "check": "校验", "health": "健康"}


def where(namespace):
    app, _, env = (namespace or "").rpartition("-")
    if app in APPS and env in ENVS:
        return APPS[app] + ENVS[env]
    return namespace or ""


def slo_name(labels):
    slo = labels.get("slo", "")
    app = labels.get("namespace", "").rpartition("-")[0]
    if app and slo.startswith(app + "-"):
        slo = slo[len(app) + 1:]
    return "".join(WORDS.get(w, w) for w in slo.split("-") if w)


def sentence(alerts, resolved):
    """One sentence for alerts of one group (same alertname and namespace) that changed the same way."""
    labels = alerts[0].get("labels", {})
    what = PHRASES.get(labels.get("alertname", ""), "收到告警")
    if resolved:
        head = "已恢复"
    else:
        head = "紧急告警" if any(a.get("labels", {}).get("severity") == "page" for a in alerts) else "告警"
    slos = []
    for a in alerts:
        n = slo_name(a.get("labels", {}))
        if n and n not in slos:
            slos.append(n)
    return f"{head}。{where(labels.get('namespace'))}。{'、'.join(slos) + '，' if slos else ''}{what}。"


spoken = {}  # fingerprint → (status, when spoken)


def changes(alerts, now):
    """The alerts whose status changed since we last spoke them (or whose reminder is due); remembers them."""
    out = {"firing": [], "resolved": []}
    for a in alerts:
        fp, st = a.get("fingerprint") or json.dumps(a.get("labels", {}), sort_keys=True), a.get("status")
        last = spoken.get(fp)
        if st == "resolved":
            if last and last[0] == "firing":   # never announce the end of what was never announced
                out["resolved"].append(a)
            spoken.pop(fp, None)
        elif not last or last[0] != st or now - last[1] >= REPEAT:
            out["firing"].append(a)
            spoken[fp] = (st, now)
    return out


def quiet(now):
    start, end = (int(x) for x in QUIET.split("-"))
    h = now.hour
    return (start <= h or h < end) if start > end else (start <= h < end)


def speak(text):
    body = json.dumps({"text": text, "lang": "zh"}).encode()
    req = urllib.request.Request(SPEAKD_URL, data=body, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10).read()


def log(**kw):
    print(json.dumps(kw, ensure_ascii=False), flush=True)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path == "/healthz" else 404)
        self.end_headers()
        self.wfile.write(b"ok")

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            self.send_response(400); self.end_headers(); return
        now = datetime.datetime.now(TZ)
        c = changes(payload.get("alerts", []), now.timestamp())
        for text in [sentence(c[k], k == "resolved") for k in ("firing", "resolved") if c[k]]:
            if quiet(now):
                log(action="quiet", text=text)
                continue
            try:
                speak(text)
                log(action="spoken", text=text)
            except Exception as e:  # speakd down: keep the webhook healthy, the alert is still in Alertmanager
                log(action="speak_failed", text=text, error=str(e))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):  # no access log
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
