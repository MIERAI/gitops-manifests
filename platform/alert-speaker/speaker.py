"""Alertmanager webhook → speakd (the laptop reads alerts out loud).

POST /         Alertmanager webhook payload (version 4). Each alert becomes one short sentence:
               firing → "紧急告警" (severity page) / "告警" (ticket), resolved → "已恢复".
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
TZ = datetime.timezone(datetime.timedelta(hours=int(os.environ.get("TZ_OFFSET_HOURS", "9"))))

PHRASES = {
    "ApiFactoryServerErrors": "服务端错误超过百分之一",
    "ApiFactoryHighLatency": "响应变慢",
    "ApiFactoryLoadTestFailed": "部署后的压力测试失败,不要发布到生产",
    "SampleAppErrorBudgetFastBurn": "错误预算消耗过快",
    "SampleAppHighLatency": "响应变慢",
    "PostgresWalArchivingFailing": "数据库日志归档失败",
    "PostgresWalArchivingStale": "数据库日志十五分钟没有归档",
    "PostgresReplicationLag": "数据库备库延迟",
    "DailyRestoreCheckFailed": "每日恢复验证失败",
}
APPS = {"api-factory": "API 工厂", "sample-app": "样例应用"}
ENVS = {"dev": "开发环境", "prod": "生产环境"}


def where(namespace):
    app, _, env = (namespace or "").rpartition("-")
    if app in APPS and env in ENVS:
        return APPS[app] + ENVS[env]
    return namespace or ""


def sentence(alert):
    labels = alert.get("labels", {})
    name = labels.get("alertname", "")
    what = PHRASES.get(name, "收到告警")
    if alert.get("status") == "resolved":
        head = "已恢复"
    else:
        head = "紧急告警" if labels.get("severity") == "page" else "告警"
    return f"{head}。{where(labels.get('namespace'))}。{what}。"


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
        for alert in payload.get("alerts", []):
            text = sentence(alert)
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
