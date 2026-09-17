#!/usr/bin/env python3
"""Collect stop-reason evidence for last grok-register-panel batch. No secrets."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path("/home/ubuntu/grok-register-panel")
LOG = ROOT / "log"
RUN = LOG / "runs" / "run_batch_20260901_072504.log"
STATS = Path(str(RUN) + ".stats.json")
ORCH = LOG / "batch-orch-20260901-072504-n2000.log"
STDOUT = LOG / "orch100-stdout.log"
CONTROL = LOG / "monitor_control.json"
TRAFFIC = LOG / "batch_traffic.json"
HISTORY = LOG / "batch_traffic_history.json"

SECRET_RE = re.compile(
    r"(sso=)\S+|([A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})"
)


def redact(text: str) -> str:
    text = SECRET_RE.sub(r"\1<redacted>", text)
    text = re.sub(r"(socks5h?://)([^/@\s]+@)", r"\1<redacted>@", text, flags=re.I)
    text = re.sub(r"(https?://)([^/@\s]+@)", r"\1<redacted>@", text, flags=re.I)
    return text


def sh(cmd: str) -> str:
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    out = (p.stdout or "") + (("\n" + p.stderr) if p.stderr else "")
    return redact(out.strip())


def tail_file(path: Path, n: int = 80) -> None:
    print(f"\n===== TAIL {path} =====")
    if not path.is_file():
        print("MISSING")
        return
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    print(f"lines={len(lines)} size={path.stat().st_size} mtime={path.stat().st_mtime}")
    for line in lines[-n:]:
        print(redact(line))


def grep_file(path: Path, keys: tuple[str, ...], last: int = 40) -> None:
    print(f"\n===== GREP {path.name} {keys} =====")
    if not path.is_file():
        print("MISSING")
        return
    hits = []
    for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines()):
        if any(k.lower() in line.lower() for k in keys):
            hits.append(f"{i+1}|{redact(line)}")
    print("count", len(hits))
    for h in hits[-last:]:
        print(h)


def main() -> None:
    print("=== STATS ===")
    print(STATS.read_text(encoding="utf-8"))
    print("\n=== CONTROL ===")
    if CONTROL.is_file():
        print(CONTROL.read_text(encoding="utf-8"))
    print("\n=== TRAFFIC ===")
    if TRAFFIC.is_file():
        print(TRAFFIC.read_text(encoding="utf-8"))

    grep_file(
        RUN,
        (
            "supervisor",
            "user_stop",
            "KeyboardInterrupt",
            "停止",
            "SIGINT",
            "SIGTERM",
            "restart limit",
            "finished rc",
            "RUN SUMMARY",
            "用户停止",
            "stop_requested",
        ),
        last=80,
    )
    grep_file(
        ORCH,
        (
            "supervisor",
            "user_stop",
            "KeyboardInterrupt",
            "停止",
            "finished rc",
            "RUN SUMMARY",
            "restart limit",
            "SIGTERM",
            "SIGINT",
        ),
        last=80,
    )
    tail_file(RUN, 60)
    tail_file(ORCH, 60)
    tail_file(STDOUT, 80)

    print("\n=== HISTORY last 3 ===")
    if HISTORY.is_file():
        try:
            data = json.loads(HISTORY.read_text(encoding="utf-8"))
            items = data if isinstance(data, list) else data.get("batches") or data.get("items") or data
            if isinstance(items, list):
                print(json.dumps(items[-3:], ensure_ascii=False, indent=2)[:4000])
            else:
                print(type(data), list(data)[:20] if isinstance(data, dict) else str(data)[:500])
        except Exception as exc:
            print("history parse fail", exc)

    print("\n=== journal grok-register-panel around stop ===")
    print(sh("timedatectl | head -8"))
    print(sh("date; date -u"))
    print(
        sh(
            "journalctl -u grok-register-panel.service --since '2026-09-01 23:00:00' "
            "--until '2026-09-02 02:00:00' --no-pager -o short-iso | tail -80"
        )
    )
    print("\n=== journal all /api/stop-ish around stop (unit + syslog) ===")
    print(
        sh(
            "journalctl --since '2026-09-02 00:25:00' --until '2026-09-02 00:40:00' "
            "--no-pager -o short-iso | grep -Ei 'stop|kill|SIGINT|SIGTERM|grok-register|batch|orch|8787' | tail -80"
        )
    )
    print("\n=== systemd status ===")
    print(sh("systemctl is-active grok-register-panel.service; systemctl show grok-register-panel.service -p ActiveEnterTimestamp -p MainPID -p NRestarts -p Result --no-pager"))
    print("\n=== pid files ===")
    print(sh("ls -l /home/ubuntu/grok-register-panel/log/*.pid /home/ubuntu/grok-register-panel/log/*.stop 2>/dev/null; echo '---'; cat /home/ubuntu/grok-register-panel/log/*.pid 2>/dev/null"))
    print("\n=== ps grok/batch ===")
    print(sh("ps -eo pid,ppid,lstart,etime,cmd | grep -E 'run_batch|run_until|grok_register|monitor.py|xvfb' | grep -v grep"))
    print("\n=== last / who / auth around stop UTC ===")
    print(sh("last -n 20"))
    print(sh("grep -E '2026-09-02T00:3|Sep  2 00:3' /var/log/auth.log 2>/dev/null | tail -40"))
    print("\n=== OOM? ===")
    print(sh("dmesg -T 2>/dev/null | grep -Ei 'killed process|out of memory|oom' | tail -20"))
    print(sh("journalctl -k --since '2026-09-01 00:00:00' --until '2026-09-02 02:00:00' --no-pager | grep -Ei 'killed process|out of memory|oom' | tail -20"))
    print("\n=== nginx/caddy access around stop ===")
    print(sh("ls -l /var/log/nginx /var/log/caddy /home/ubuntu/grok-register-panel/log/*access* 2>/dev/null | head"))
    print(
        sh(
            "grep -h '/api/stop' /var/log/nginx/access.log /var/log/nginx/access.log.1 "
            "/home/ubuntu/grok-register-panel/log/* 2>/dev/null | tail -20"
        )
    )


if __name__ == "__main__":
    main()
