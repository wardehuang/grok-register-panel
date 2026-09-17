#!/usr/bin/env python3
from pathlib import Path
import os

os.chdir(str(Path(__file__).resolve().parent))

import json
import secrets
import sys
import time
import types

from batch_supervisor import (
    DEFAULT_IDLE_TIMEOUT,
    DEFAULT_MAX_RESTARTS,
    read_completed,
    run_supervisor,
)
from batch_traffic import (
    BATCH_ID_ENV,
    HISTORY_FILE_ENV,
    TRAFFIC_FILE_ENV,
    archive_batch,
    finalize_batch,
    initialize_batch,
)
from retry_policy import PRECHECK_EXIT_CODE
from run_log import (
    RUN_LOG_ENV,
    RUN_STATS_ENV,
    append_run_log,
    finalize_run_log,
    start_run_log,
    update_run_stats,
)
from secure_files import atomic_write_json, ensure_private_dir


ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "log"


def _install_tkinter_stub() -> None:
    try:
        import tkinter  # noqa: F401
        return
    except ImportError:
        pass

    tk = types.ModuleType("tkinter")

    class _NullWidget:
        def __init__(self, *args, **kwargs):
            pass

        def __getattr__(self, _name):
            return lambda *args, **kwargs: None

    for name in ("Tk", "StringVar", "IntVar", "BooleanVar"):
        setattr(tk, name, _NullWidget)
    tk.END = "end"
    tk.DISABLED = "disabled"
    tk.NORMAL = "normal"
    tk.LEFT = "left"
    tk.RIGHT = "right"
    tk.BOTH = "both"
    tk.X = "x"
    tk.Y = "y"
    tk.W = "w"
    tk.E = "e"
    tk.N = "n"
    tk.S = "s"

    ttk_module = types.ModuleType("tkinter.ttk")
    for name in (
        "Frame",
        "Label",
        "Button",
        "Entry",
        "Combobox",
        "Spinbox",
        "Checkbutton",
        "Notebook",
        "Style",
        "Scrollbar",
        "Treeview",
    ):
        setattr(ttk_module, name, _NullWidget)
    scrolled_text = types.ModuleType("tkinter.scrolledtext")
    scrolled_text.ScrolledText = _NullWidget
    messagebox = types.ModuleType("tkinter.messagebox")
    messagebox.showinfo = lambda *args, **kwargs: None
    messagebox.showerror = messagebox.showinfo
    messagebox.showwarning = messagebox.showinfo
    messagebox.askyesno = messagebox.showinfo
    sys.modules["tkinter"] = tk
    sys.modules["tkinter.ttk"] = ttk_module
    sys.modules["tkinter.scrolledtext"] = scrolled_text
    sys.modules["tkinter.messagebox"] = messagebox


def _redact_proxy(url: object) -> str:
    text = str(url or "")
    if "://" in text and "@" in text.split("://", 1)[-1]:
        scheme, rest = text.split("://", 1)
        _credentials, host = rest.rsplit("@", 1)
        return f"{scheme}://***@{host}"
    return text


def _run_child(count: int, workers: int) -> int:
    _install_tkinter_stub()
    sys.path.insert(0, str(ROOT))
    for key in (
        "http_proxy",
        "https_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "all_proxy",
    ):
        os.environ.pop(key, None)

    import connectivity
    import grok_register_ttk as app

    config_path = ROOT / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["register_count"] = count
    config["register_workers"] = workers
    atomic_write_json(config_path, config)
    print(
        f"[env] DISPLAY={os.environ.get('DISPLAY')!r} time={time.strftime('%F %T')}",
        flush=True,
    )
    app.load_config()
    app._wire_runtime_modules()
    print(
        f"[batch] count={count} workers={workers} proxy={_redact_proxy(app.config.get('proxy'))}",
        flush=True,
    )
    try:
        app.run_registration_cli(count)
    except app.IntelligenceProxyPoolExhausted:
        print("[batch] 智商检测代理池耗尽；批次停止", flush=True)
        return PRECHECK_EXIT_CODE
    except connectivity.XaiSignupPrecheckFailed:
        print("[batch] xAI registration page precheck failed; batch stopped", flush=True)
        return PRECHECK_EXIT_CODE
    print("[batch] finished", flush=True)
    return 0


def _child_command(remaining: int, workers: int) -> list[str]:
    return [
        sys.executable,
        "-u",
        str(ROOT / "run_batch_headless.py"),
        "--batch-child",
        str(remaining),
        str(workers),
    ]


def _env_int(name: str, default: int, minimum: int) -> int:
    try:
        return max(minimum, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


def _account_interval_ceiling_sec() -> int:
    """Upper bound of account_interval from config (for supervisor idle timeout)."""
    try:
        cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    except Exception:
        return 0
    raw = str(cfg.get("account_interval", "") or "").strip()
    if (not raw or raw == "0") and cfg.get("register_interval_sec"):
        raw = str(cfg.get("register_interval_sec") or "0").strip()
    if not raw or raw == "0":
        return 0
    try:
        if "-" in raw:
            parts = raw.split("-", 1)
            lo = max(int(parts[0].strip()), 0)
            hi = max(int(parts[1].strip()), lo)
            return hi
        return max(0, int(float(raw)))
    except Exception:
        return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    child_mode = bool(args and args[0] == "--batch-child")
    if child_mode:
        args.pop(0)
    count = int(args[0]) if args else 9
    workers = int(args[1]) if len(args) > 1 else 3
    count = max(1, count)
    workers = max(1, min(workers, 24, count))
    if child_mode:
        return _run_child(count, workers)

    ensure_private_dir(LOG_DIR)
    progress_file = LOG_DIR / f".batch-progress-{os.getpid()}.json"
    interval_hi = _account_interval_ceiling_sec()
    # Browser is closed before the account gap. Keep only a small grace period
    # beyond the intentional sleep so a stuck child is still reclaimed quickly.
    idle_floor = max(DEFAULT_IDLE_TIMEOUT, interval_hi + 60)
    idle_timeout = _env_int(
        "GROK_BATCH_IDLE_TIMEOUT",
        idle_floor,
        60,
    )
    if idle_timeout < idle_floor and str(os.environ.get("GROK_BATCH_IDLE_TIMEOUT", "") or "").strip() == "":
        idle_timeout = idle_floor
    # if env explicitly set lower than interval, still raise to safe floor
    if idle_timeout < interval_hi + 60:
        idle_timeout = interval_hi + 60
    max_restarts = _env_int(
        "GROK_BATCH_MAX_RESTARTS",
        DEFAULT_MAX_RESTARTS,
        0,
    )
    print(
        f"[supervisor] idle_timeout={idle_timeout}s account_interval_hi={interval_hi}s "
        f"max_restarts={max_restarts}",
        flush=True,
    )
    batch_id = str(os.environ.get(BATCH_ID_ENV, "") or "").strip()
    if not batch_id:
        batch_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}-{secrets.token_hex(3)}"
    traffic_file = Path(
        str(os.environ.get(TRAFFIC_FILE_ENV, "") or LOG_DIR / "batch_traffic.json")
    ).resolve()
    history_file = Path(
        str(
            os.environ.get(HISTORY_FILE_ENV, "")
            or LOG_DIR / "batch_traffic_history.json"
        )
    ).resolve()
    # One run log for this whole launch (children attach via env).
    run_path = start_run_log("batch", force_new=True)
    update_run_stats(target=count, notes=f"batch_id={batch_id}")
    append_run_log(
        f"[supervisor] launch count={count} workers={workers} "
        f"idle_timeout={idle_timeout}s max_restarts={max_restarts} log={run_path}"
    )
    initialize_batch(
        traffic_file,
        batch_id,
        target=count,
        workers=workers,
    )
    child_env = {
        BATCH_ID_ENV: batch_id,
        TRAFFIC_FILE_ENV: str(traffic_file),
        RUN_LOG_ENV: str(run_path),
        RUN_STATS_ENV: str(run_path.with_suffix(run_path.suffix + ".stats.json")),
    }
    result = 1
    reason = "stopped_incomplete"
    try:
        result = run_supervisor(
            count,
            workers,
            _child_command,
            progress_file=progress_file,
            idle_timeout=idle_timeout,
            max_restarts=max_restarts,
            child_env=child_env,
        )
        completed = read_completed(progress_file)
        if result == 130:
            reason = "user_stop"
        elif result == PRECHECK_EXIT_CODE:
            reason = "precheck_failed"
        elif result == 0 and completed >= count:
            reason = "completed"
        elif result == 1 and completed < count:
            reason = "restart_limit_or_child_fail"
        elif result == 0:
            reason = "completed"
        else:
            reason = f"exit_{result}"
        update_run_stats(completed=completed, target=count, notes=f"supervisor_rc={result}")
        append_run_log(
            f"[supervisor] finished rc={result} completed={completed}/{count} reason={reason}"
        )
        return result
    except KeyboardInterrupt:
        reason = "user_stop"
        append_run_log("[supervisor] KeyboardInterrupt")
        result = 130
        return result
    finally:
        try:
            completed = read_completed(progress_file)
            update_run_stats(completed=completed, target=count)
            finalize_run_log(reason)
        except Exception as exc:
            print(f"[supervisor] finalize run log failed: {exc}", flush=True)
        finalized = finalize_batch(traffic_file, batch_id, result)
        if finalized.get("batch_id") == batch_id:
            archive_batch(history_file, finalized)


if __name__ == "__main__":
    raise SystemExit(main())
