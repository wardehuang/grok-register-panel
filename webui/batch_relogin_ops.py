# -*- coding: utf-8 -*-
"""Panel job control for batch re-login."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime_platform import (
    RuntimePlatformError,
    popen_group_kwargs,
    runtime_python,
    wrap_browser_command,
)
from secure_files import (
    atomic_write_text,
    best_effort_fchmod,
    ensure_private_dir,
)

try:
    from webui.process_utils import (
        find_managed_processes,
        terminate_managed_processes,
        write_pid_file,
    )
    from webui.registered_accounts_store import parse_email_list
except ImportError:  # running from webui/
    from process_utils import (  # type: ignore
        find_managed_processes,
        terminate_managed_processes,
        write_pid_file,
    )
    from registered_accounts_store import parse_email_list  # type: ignore

LOG_DIR = ROOT / "log"
PID_FILE = LOG_DIR / "batch_relogin.pid"
STOP_FILE = LOG_DIR / "batch_relogin.stop"
REPORT_FILE = LOG_DIR / "batch_relogin_report.json"
INPUT_FILE = LOG_DIR / "batch_relogin_emails.txt"
SCRIPT = ROOT / "run_batch_relogin.py"
CONFIG_FILE = ROOT / "config.json"
VENV_PY = runtime_python(ROOT)
RUNS_DIR = LOG_DIR / "runs"


def _prepare_run_log() -> tuple[str, str]:
    """Create log/runs/run_relogin_*.log for panel scroll view; return (log, stats) paths."""
    ensure_private_dir(RUNS_DIR)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    run_path = RUNS_DIR / f"run_relogin_{stamp}.log"
    stats_path = Path(str(run_path) + ".stats.json")
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    header = (
        f"===== run start kind=relogin at {time.strftime('%H:%M:%S')} / {started} "
        f"source=panel =====\n"
        f"[{time.strftime('%H:%M:%S')}] [*] 面板启动批量重登，等待 worker 接入…\n"
    )
    atomic_write_text(run_path, header)
    try:
        atomic_write_text(
            stats_path,
            json.dumps(
                {
                    "kind": "relogin",
                    "started_at": started,
                    "path": str(run_path),
                    "success": 0,
                    "fail": 0,
                    "completed": 0,
                    "target": 0,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
    except Exception:
        pass
    return str(run_path.resolve()), str(stats_path.resolve())


def _read_report() -> dict:
    try:
        data = json.loads(REPORT_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    items = []
    for row in (data.get("items") or [])[-30:]:
        if not isinstance(row, dict):
            continue
        items.append(
            {
                "email": str(row.get("email") or "")[:120],
                "ok": bool(row.get("ok")),
                "has_sso": bool(row.get("has_sso")),
                "reason": str(row.get("reason") or "")[:240],
            }
        )
    return {
        "started_at": data.get("started_at") or "",
        "finished_at": data.get("finished_at") or "",
        "input_count": int(data.get("input_count") or 0),
        "success_count": int(data.get("success_count") or 0),
        "fail_count": int(data.get("fail_count") or 0),
        "skipped_count": int(data.get("skipped_count") or 0),
        "cancelled": bool(data.get("cancelled")),
        "only_cpa": bool(data.get("only_cpa")),
        "error": str(data.get("error") or "")[:300],
        "items": items,
    }


def relogin_status() -> dict:
    jobs = find_managed_processes(ROOT, ("run_batch_relogin.py",))
    return {
        "ok": True,
        "running": bool(jobs),
        "pid": jobs[0]["pid"] if jobs else None,
        "stop_requested": STOP_FILE.is_file(),
        "last_report": _read_report(),
        "input_file": str(INPUT_FILE.relative_to(ROOT)) if INPUT_FILE.is_file() else "",
    }


def start_relogin(text: str = "", only_cpa: bool = False) -> dict:
    emails = parse_email_list(text)
    if not emails:
        return {"ok": False, "error": "请输入至少一个 xAI 邮箱（一行一个）"}
    if find_managed_processes(ROOT, ("run_until_100.py", "run_batch_headless.py")):
        return {"ok": False, "error": "注册任务正在运行，请先停止"}
    if find_managed_processes(ROOT, ("sso_to_auth_json.py",)):
        return {"ok": False, "error": "账号补录正在运行，请先停止"}
    existing = find_managed_processes(ROOT, ("run_batch_relogin.py",))
    if existing:
        return {"ok": False, "error": "批量重登已在运行", "pid": existing[0]["pid"]}
    if not VENV_PY.is_file():
        return {"ok": False, "error": f"missing runtime python: {VENV_PY}"}
    if not SCRIPT.is_file():
        return {"ok": False, "error": f"missing script: {SCRIPT}"}
    if not CONFIG_FILE.is_file():
        return {"ok": False, "error": f"missing config: {CONFIG_FILE}"}

    ensure_private_dir(LOG_DIR)
    try:
        if STOP_FILE.is_file():
            STOP_FILE.unlink()
    except OSError:
        pass

    body = "\n".join(emails) + "\n"
    atomic_write_text(INPUT_FILE, body)

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    log_path = LOG_DIR / f"batch-relogin-{timestamp}.log"
    fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    best_effort_fchmod(fd, 0o600)
    output = os.fdopen(fd, "w", encoding="utf-8")
    run_log_path, run_stats_path = _prepare_run_log()
    # seed both stdout job log and run log so panel shows something immediately
    try:
        seed = (
            f"[{time.strftime('%H:%M:%S')}] [*] 批量重登任务已启动 "
            f"count={len(emails)} mode={'only_cpa' if only_cpa else 'grok2api+cpa'} "
            f"run_log={Path(run_log_path).name}\n"
        )
        output.write(seed)
        output.flush()
        with open(run_log_path, "a", encoding="utf-8", newline="\n") as rf:
            rf.write(seed)
    except Exception:
        pass
    command = [
        str(VENV_PY),
        "-u",
        str(SCRIPT),
        "--emails-file",
        str(INPUT_FILE),
        "--config",
        str(CONFIG_FILE),
        "--report-json",
        str(REPORT_FILE),
        "--stop-file",
        str(STOP_FILE),
    ]
    if only_cpa:
        command.append("--only-cpa")
    try:
        command = wrap_browser_command(command)
    except RuntimePlatformError as exc:
        output.close()
        try:
            log_path.unlink(missing_ok=True)
        except TypeError:
            try:
                if log_path.is_file():
                    log_path.unlink()
            except OSError:
                pass
        except OSError:
            pass
        return {"ok": False, "error": str(exc)}
    child_env = {
        **os.environ,
        "PYTHONUNBUFFERED": "1",
        "GROK_RUN_LOG": run_log_path,
        "GROK_RUN_STATS": run_stats_path,
    }
    try:
        process = subprocess.Popen(
            command,
            cwd=str(ROOT),
            stdout=output,
            stderr=subprocess.STDOUT,
            env=child_env,
            **popen_group_kwargs(),
        )
    finally:
        output.close()
    write_pid_file(PID_FILE, process.pid)
    try:
        run_log_rel = str(Path(run_log_path).resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except Exception:
        run_log_rel = run_log_path
    return {
        "ok": True,
        "running": True,
        "pid": process.pid,
        "input_count": len(emails),
        "only_cpa": bool(only_cpa),
        "log": log_path.name,
        "run_log": run_log_rel,
        "command_preview": " ".join(command[:6]),
    }


def stop_relogin() -> dict:
    ensure_private_dir(LOG_DIR)
    try:
        atomic_write_text(STOP_FILE, "stop\n")
    except Exception:
        pass
    killed = terminate_managed_processes(ROOT, ("run_batch_relogin.py",))
    return {"ok": True, "killed": killed, "status": relogin_status()}
