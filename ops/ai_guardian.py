import argparse
import datetime as dt
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path


def now_iso():
    return dt.datetime.now().isoformat(timespec="seconds")


def read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_text(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def append_line(path, line):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


class ManagedProcess:
    def __init__(self, cfg, project_root):
        self.enabled = bool(cfg.get("enabled", False))
        self.start_command = str(cfg.get("start_command", "")).strip()
        self.working_dir = str(cfg.get("working_dir", ".")).strip()
        self.project_root = project_root
        self.proc = None

    @property
    def workdir_path(self):
        return (self.project_root / self.working_dir).resolve()

    def start(self):
        if not self.enabled or not self.start_command:
            return "manage_process disabled"
        if self.proc and self.proc.poll() is None:
            return f"already running pid={self.proc.pid}"

        self.proc = subprocess.Popen(
            self.start_command,
            cwd=str(self.workdir_path),
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return f"started pid={self.proc.pid}"

    def stop(self):
        if not self.proc or self.proc.poll() is not None:
            return "not running"
        self.proc.terminate()
        try:
            self.proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=4)
        return "stopped"

    def restart(self):
        self.stop()
        return self.start()

    def read_output_tail(self, max_lines=120):
        if not self.proc or self.proc.stdout is None:
            return ""
        lines = []
        while True:
            line = self.proc.stdout.readline()
            if not line:
                break
            lines.append(line.rstrip("\n"))
        return "\n".join(lines[-max_lines:])


class AIGuardian:
    def __init__(self, cfg):
        self.cfg = cfg
        self.project_root = Path(cfg.get("project_root", ".")).resolve()
        self.interval = int(cfg.get("check_interval_seconds", 20))
        self.failure_threshold = int(cfg.get("failure_threshold", 2))
        self.timeout_seconds = int(cfg.get("request_timeout_seconds", 7))
        self.endpoints = cfg.get("endpoints", [])
        self.artifacts_dir = (self.project_root / cfg.get("artifacts_dir", "ops/autofix_artifacts")).resolve()
        self.log_file = self.artifacts_dir / "guardian.log"
        self.failures = 0
        self.process = ManagedProcess(cfg.get("manage_process", {}), self.project_root)
        self.ai_cfg = cfg.get("ai", {})
        self.self_heal_cfg = cfg.get("self_heal", {})

    def log(self, msg):
        line = f"[{now_iso()}] {msg}"
        print(line, flush=True)
        append_line(self.log_file, line)

    def check_endpoint(self, endpoint_cfg):
        url = endpoint_cfg["url"]
        expected = endpoint_cfg.get("expected_status", [200])
        expected = set(int(x) for x in expected)
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                code = int(resp.getcode())
                ok = code in expected
                return {
                    "url": url,
                    "ok": ok,
                    "status": code,
                    "detail": f"status={code}"
                }
        except urllib.error.HTTPError as exc:
            code = int(getattr(exc, "code", 0) or 0)
            ok = code in expected
            return {
                "url": url,
                "ok": ok,
                "status": code,
                "detail": f"http_error={code}"
            }
        except Exception as exc:
            return {
                "url": url,
                "ok": False,
                "status": None,
                "detail": f"exception={type(exc).__name__}: {exc}"
            }

    def collect_health(self):
        return [self.check_endpoint(ep) for ep in self.endpoints]

    def call_ai(self, incident):
        if not self.ai_cfg.get("enabled", False):
            return "AI is disabled"

        api_key_env = self.ai_cfg.get("api_key_env", "OPENAI_API_KEY")
        api_key = os.environ.get(api_key_env, "").strip()
        if not api_key:
            return f"AI skipped: env {api_key_env} is missing"

        base_url = self.ai_cfg.get("base_url", "https://api.openai.com/v1/chat/completions")
        model = self.ai_cfg.get("model", "gpt-5.3-codex")
        max_tokens = int(self.ai_cfg.get("max_tokens", 700))
        temperature = float(self.ai_cfg.get("temperature", 0.2))

        system_prompt = (
            "You are an on-call web reliability engineer. "
            "Analyze incident data and propose SAFE, minimal, reversible fixes. "
            "Return plain text with sections: Root cause, Immediate actions, "
            "Safe commands, Patch plan, Validation checklist."
        )
        user_prompt = (
            "Incident data:\n"
            f"{json.dumps(incident, ensure_ascii=False, indent=2)}\n\n"
            "Do not propose destructive commands."
        )

        body = {
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }

        payload = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            base_url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw)
            return data["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            return f"AI request failed: {type(exc).__name__}: {exc}"

    def maybe_self_heal(self):
        if self.self_heal_cfg.get("restart_process_on_failure", True):
            message = self.process.restart()
            self.log(f"self_heal restart: {message}")

    def write_incident_artifacts(self, incident, ai_text):
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        incident_path = self.artifacts_dir / f"incident_{stamp}.json"
        advice_path = self.artifacts_dir / f"incident_{stamp}_ai_advice.md"
        write_text(incident_path, json.dumps(incident, ensure_ascii=False, indent=2))
        write_text(advice_path, ai_text.strip() + "\n")
        self.log(f"artifact saved: {incident_path}")
        self.log(f"artifact saved: {advice_path}")

    def handle_incident(self, checks):
        process_tail = self.process.read_output_tail(max_lines=160)
        incident = {
            "timestamp": now_iso(),
            "failures_in_a_row": self.failures,
            "checks": checks,
            "process_output_tail": process_tail,
        }
        self.log("incident detected: threshold reached")
        self.maybe_self_heal()
        ai_text = self.call_ai(incident)
        self.write_incident_artifacts(incident, ai_text)

    def run_forever(self):
        self.log("guardian booting")
        start_msg = self.process.start()
        self.log(f"managed process: {start_msg}")

        while True:
            checks = self.collect_health()
            failed = [c for c in checks if not c["ok"]]

            if failed:
                self.failures += 1
                joined = "; ".join(f"{c['url']} ({c['detail']})" for c in failed)
                self.log(f"health check failed [{self.failures}/{self.failure_threshold}]: {joined}")
                if self.failures >= self.failure_threshold:
                    self.handle_incident(checks)
                    self.failures = 0
            else:
                if self.failures > 0:
                    self.log("health recovered")
                self.failures = 0

            time.sleep(self.interval)


def parse_args():
    parser = argparse.ArgumentParser(description="Always-on AI guardian for website monitoring and self-healing.")
    parser.add_argument(
        "--config",
        default="ops/guardian.config.json",
        help="Path to guardian config JSON",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    cfg_path = Path(args.config).resolve()
    cfg = read_json(cfg_path)
    guardian = AIGuardian(cfg)

    try:
        guardian.run_forever()
    except KeyboardInterrupt:
        guardian.log("guardian stopped by user")
        guardian.process.stop()


if __name__ == "__main__":
    main()
