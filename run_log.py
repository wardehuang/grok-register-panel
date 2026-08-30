"""Per-launch session log under log/runs/.

One file per batch/task start (not per account, not per supervisor child restart):
  log/runs/run_batch_YYYYMMDD_HHMMSS.log
  log/runs/run_cli_YYYYMMDD_HHMMSS.log
  log/runs/run_gui_YYYYMMDD_HHMMSS.log

Parent owns the file (writes header + final summary).
Children attach via env GROK_RUN_LOG and only append.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from secure_files import atomic_write_json

_ROOT = Path(__file__).resolve().parent
LOGS_DIR = _ROOT / "log" / "runs"
RUN_LOG_ENV = "GROK_RUN_LOG"
RUN_STATS_ENV = "GROK_RUN_STATS"

_lock = threading.Lock()
_file_handle: TextIO | None = None
_log_path: Path | None = None
_stats_path: Path | None = None
_is_owner: bool = False
_kind: str = "run"
_started_mono: float | None = None
_started_wall: str = ""

_TS_PREFIX_RE = re.compile(
    r"^\[\d{1,2}:\d{2}:\d{2}\]|^\[\d{4}-\d{2}-\d{2}[ T]\d{1,2}:\d{2}:\d{2}\]"
)

# 当前统计 / RUN SUMMARY 明细标签
DETAIL_LABELS_ZH = {
    "register_ok": "注册成功",
    "register_fail": "注册失败",
    "sso_ok": "SSO成功",
    "sso_fail": "SSO失败",
    "early_skip": "早期跳过",
    "g2a_ok": "G2A成功",
    "g2a_fail": "G2A失败",
    "cpa_ok": "CPA成功",
    "cpa_fail": "CPA失败",
    "risk_ok": "风控通过",
    "risk_fail": "风控",
}
DETAIL_ORDER = tuple(DETAIL_LABELS_ZH.keys())

LABEL_TO_DETAIL_KEY = {v: k for k, v in DETAIL_LABELS_ZH.items()}
# 兼容无空格 / 旧写法
LABEL_TO_DETAIL_KEY.update(
    {
        "注册成功": "register_ok",
        "注册失败": "register_fail",
        "SSO成功": "sso_ok",
        "SSO失败": "sso_fail",
        "早期跳过": "early_skip",
        "G2A成功": "g2a_ok",
        "G2A失败": "g2a_fail",
        "CPA成功": "cpa_ok",
        "CPA失败": "cpa_fail",
        "风控通过": "risk_ok",
        "风控": "risk_fail",
        "风控拒绝": "risk_fail",
    }
)

REASON_LABELS_ZH = {
    "user_stop": "用户停止",
    "completed": "正常完成",
    "precheck_failed": "预检失败",
    "restart_limit_or_child_fail": "重启上限/子进程失败",
    "restart_limit_reached": "重启次数用尽",
    "stopped_incomplete": "未完成停止",
    "child_finished": "子进程结束",
    "process_exit": "进程退出",
    "panel_stop": "面板停止",
    "sigint": "中断信号",
    "sigterm": "终止信号",
    "keyboardinterrupt": "键盘中断",
    "dry_run_completed": "Dry Run 完成",
    "dry_run_failed": "Dry Run 失败",
}

_CURRENT_STATS_RE = re.compile(
    r"当前统计:\s*成功\s*(\d+)\s*\|\s*失败\s*(\d+)(?:\s*\|\|?\s*(.+))?$"
)
_PAIR_RE = re.compile(r"([^\d|：:=]+?)\s*[=：:]\s*(\d+)|([^\d|]+?)\s+(\d+)")
_FAIL_KIND_RE = re.compile(r"注册失败\s*\[([^\]]+)\]")
_OK_LINE_RE = re.compile(r"\[\+\]\s*注册成功")
_FAIL_LINE_RE = re.compile(r"\[\-\]\s*注册失败")
_DOMAIN_SKIP_RE = re.compile(r"邮箱域名被\s*xAI\s*拒绝|早期跳过|domain", re.I)
_SSO_FAIL_RE = re.compile(r"sso_timeout|SSO\s*超时|等待\s*sso", re.I)
_RISK_FAIL_RE = re.compile(r"注册风控|FAIL_RISK|botFlag|风控", re.I)
_G2A_OK_RE = re.compile(r"grok2api.*成功|远端\s*Web.*成功|Console.*成功", re.I)
_CPA_OK_RE = re.compile(r"CPA.*成功|写入\s*CPA|cpa_ok", re.I)


def _ensure_layout() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)


def _now_hms() -> str:
    try:
        from runtime_platform import beijing_strftime

        return beijing_strftime("%H:%M:%S")
    except Exception:
        return datetime.now().strftime("%H:%M:%S")


def _now_iso() -> str:
    try:
        from runtime_platform import beijing_strftime

        return beijing_strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return datetime.now().isoformat(timespec="seconds")


def _stamp_text(line: str) -> str:
    text = str(line or "")
    if not text:
        return ""
    ends_nl = text.endswith("\n")
    parts = text.splitlines()
    ts = _now_hms()
    out: list[str] = []
    for part in parts:
        if not part:
            out.append("")
            continue
        if _TS_PREFIX_RE.match(part):
            out.append(part)
        else:
            out.append(f"[{ts}] {part}")
    body = "\n".join(out)
    if ends_nl or body:
        body = body + "\n"
    return body


def _safe_kind(kind: str) -> str:
    return (
        "".join(
            c
            for c in str(kind or "run").strip().lower()
            if c.isalnum() or c in ("-", "_")
        )
        or "run"
    )


def _default_stats(kind: str, path: Path) -> dict[str, Any]:
    return {
        "kind": kind,
        "path": str(path),
        "started_at": _now_iso(),
        "ended_at": "",
        "reason": "",
        "reason_class": "",  # active | passive
        "target": 0,
        "completed": 0,
        "success": 0,
        "fail": 0,
        "restarts": 0,
        "fail_stats": {},
        "detail_stats": {},
        "notes": [],
    }


def _stats_file_for(log_path: Path) -> Path:
    return log_path.with_suffix(log_path.suffix + ".stats.json")


def _load_stats(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_stats(path: Path | None, data: dict[str, Any]) -> None:
    if path is None:
        return
    try:
        atomic_write_json(path, data)
    except Exception:
        try:
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except Exception:
            pass


def update_run_stats(**fields: Any) -> None:
    """Merge counters into the shared stats sidecar (multi-process safe-ish)."""
    global _stats_path
    path_raw = str(os.environ.get(RUN_STATS_ENV, "") or "").strip()
    path = Path(path_raw) if path_raw else _stats_path
    if path is None and _log_path is not None:
        path = _stats_file_for(_log_path)
    if path is None:
        return
    with _lock:
        data = _load_stats(path)
        if not data:
            data = _default_stats(_kind, _log_path or path)
        for key, value in fields.items():
            if key == "fail_stats" and isinstance(value, dict):
                merged = dict(data.get("fail_stats") or {})
                for fk, fv in value.items():
                    try:
                        merged[str(fk)] = int(merged.get(str(fk), 0) or 0) + int(fv or 0)
                    except Exception:
                        merged[str(fk)] = fv
                data["fail_stats"] = merged
            elif key == "detail_stats" and isinstance(value, dict):
                # absolute snapshot from runner (not incremental merge)
                cleaned: dict[str, Any] = {}
                for fk, fv in value.items():
                    try:
                        cleaned[str(fk)] = int(fv or 0)
                    except Exception:
                        cleaned[str(fk)] = fv
                data["detail_stats"] = cleaned
            elif key == "notes":
                notes = list(data.get("notes") or [])
                if isinstance(value, (list, tuple)):
                    notes.extend(str(v) for v in value)
                elif value:
                    notes.append(str(value))
                data["notes"] = notes[-50:]
            elif key in {"success", "fail", "completed", "restarts", "target"}:
                try:
                    # absolute set if provided as tuple ("set", n) else max/add via set
                    data[key] = int(value)
                except Exception:
                    data[key] = value
            elif value is not None:
                data[key] = value
        data["updated_at"] = _now_iso()
        _write_stats(path, data)
        _stats_path = path
        os.environ[RUN_STATS_ENV] = str(path)


def current_run_log_path() -> Path | None:
    with _lock:
        if _log_path is not None:
            return _log_path
    env = str(os.environ.get(RUN_LOG_ENV, "") or "").strip()
    return Path(env) if env else None


def attach_run_log(path: str | os.PathLike[str], kind: str | None = None) -> Path:
    """Append to an existing launch log (child / secondary process)."""
    global _file_handle, _log_path, _stats_path, _is_owner, _kind, _started_mono, _started_wall
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        if _file_handle is not None and _log_path == target:
            return target
        if _file_handle is not None:
            try:
                _file_handle.flush()
                _file_handle.close()
            except Exception:
                pass
            _file_handle = None
        handle = target.open("a", encoding="utf-8", newline="\n")
        _file_handle = handle
        _log_path = target
        _stats_path = _stats_file_for(target)
        _is_owner = False
        if kind:
            _kind = _safe_kind(kind)
        os.environ[RUN_LOG_ENV] = str(target)
        os.environ[RUN_STATS_ENV] = str(_stats_path)
        if _started_mono is None:
            _started_mono = time.monotonic()
            _started_wall = _now_iso()
        try:
            handle.write(
                f"[{_now_hms()}] ----- child attach pid={os.getpid()} kind={_kind} -----\n"
            )
            handle.flush()
        except Exception:
            pass
    return target


def start_run_log(kind: str = "gui", *, force_new: bool = False) -> Path:
    """Open the launch log.

    - If GROK_RUN_LOG is set (or a log already open) and force_new is False: attach/reuse.
    - Else create one new file for this launch (owner).
    """
    global _file_handle, _log_path, _stats_path, _is_owner, _kind, _started_mono, _started_wall
    env_path = str(os.environ.get(RUN_LOG_ENV, "") or "").strip()
    if not force_new:
        if _file_handle is not None and _log_path is not None:
            return _log_path
        if env_path:
            return attach_run_log(env_path, kind=kind)

    _ensure_layout()
    safe = _safe_kind(kind)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = LOGS_DIR / f"run_{safe}_{stamp}.log"
    stats_path = _stats_file_for(path)

    with _lock:
        if _file_handle is not None:
            try:
                _file_handle.flush()
                _file_handle.close()
            except Exception:
                pass
            _file_handle = None
        handle = path.open("a", encoding="utf-8", newline="\n")
        header = (
            f"===== run start kind={safe} "
            f"at {_now_hms()} / {_now_iso()} pid={os.getpid()} =====\n"
        )
        handle.write(header)
        handle.flush()
        _file_handle = handle
        _log_path = path
        _stats_path = stats_path
        _is_owner = True
        _kind = safe
        _started_mono = time.monotonic()
        _started_wall = _now_iso()
        os.environ[RUN_LOG_ENV] = str(path)
        os.environ[RUN_STATS_ENV] = str(stats_path)
        _write_stats(stats_path, _default_stats(safe, path))
    return path


def ensure_run_log(kind: str = "cli") -> Path:
    """Prefer attach-to-launch; create only if this process is standalone."""
    return start_run_log(kind, force_new=False)


def append_run_log(line: str) -> None:
    text = _stamp_text(line)
    if not text:
        return
    # auto-attach if env points at a file but handle closed
    if _file_handle is None:
        env_path = str(os.environ.get(RUN_LOG_ENV, "") or "").strip()
        if env_path:
            try:
                attach_run_log(env_path)
            except Exception:
                pass
    with _lock:
        handle = _file_handle
        if handle is None:
            return
        try:
            handle.write(text)
            handle.flush()
        except Exception:
            pass


def _reason_class(reason: str) -> str:
    r = str(reason or "").strip().lower()
    active = {
        "user_stop",
        "user",
        "cancel",
        "cancelled",
        "sigint",
        "sigterm",
        "panel_stop",
        "active",
        "keyboardinterrupt",
    }
    if r in active or r.startswith("user_"):
        return "active"
    return "passive"


def _reason_label_zh(reason: str) -> str:
    key = str(reason or "").strip().lower()
    if key in REASON_LABELS_ZH:
        return REASON_LABELS_ZH[key]
    if key.startswith("exit_"):
        return f"异常退出({key[5:]})"
    if key.startswith("user_"):
        return "用户停止"
    return str(reason or "未知")


def _format_duration_zh(seconds: Any) -> str:
    try:
        total = max(0, int(float(seconds)))
    except Exception:
        return "-"
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}小时{minutes}分{secs}秒（{total}秒）"
    if minutes:
        return f"{minutes}分{secs}秒（{total}秒）"
    return f"{secs}秒"


def _empty_detail() -> dict[str, int]:
    return {k: 0 for k in DETAIL_ORDER}


def _parse_detail_pairs(text: str) -> dict[str, int]:
    out = _empty_detail()
    raw = str(text or "").strip()
    if not raw:
        return out
    # split by | first
    chunks = [c.strip() for c in raw.split("|") if c.strip()]
    for chunk in chunks:
        m = re.match(r"^(.+?)\s*[=：:]\s*(\d+)\s*$", chunk)
        if not m:
            m = re.match(r"^(.+?)\s+(\d+)\s*$", chunk)
        if not m:
            continue
        label = (m.group(1) or "").strip()
        try:
            val = int(m.group(2))
        except Exception:
            continue
        key = LABEL_TO_DETAIL_KEY.get(label)
        if not key:
            # strip spaces
            key = LABEL_TO_DETAIL_KEY.get(label.replace(" ", ""))
        if key:
            out[key] = val
    return out


def _parse_current_stats_line(line: str) -> dict[str, Any] | None:
    text = str(line or "").strip()
    # drop leading timestamp
    text = _TS_PREFIX_RE.sub("", text).strip()
    text = text.lstrip("*").strip()
    if text.startswith("[*]"):
        text = text[3:].strip()
    m = _CURRENT_STATS_RE.search(text)
    if not m:
        # also accept without 当前统计 prefix leftovers
        m = re.search(
            r"成功\s*(\d+)\s*\|\s*失败\s*(\d+)(?:\s*\|\|?\s*(.+))?$",
            text,
        )
        if not m or "成功" not in text:
            return None
    try:
        success = int(m.group(1))
        fail = int(m.group(2))
    except Exception:
        return None
    detail_raw = m.group(3) if m.lastindex and m.lastindex >= 3 else ""
    detail = _parse_detail_pairs(detail_raw or "")
    if not any(detail.values()):
        # fill minimal from totals
        detail["register_ok"] = success
        detail["register_fail"] = fail
    return {
        "success": success,
        "fail": fail,
        "detail_stats": detail,
        "completed": success + fail,
    }


def recover_stats_from_log(path: Path | str | None) -> dict[str, Any]:
    """Scan run log for the latest live stats line (and light fallbacks)."""
    if not path:
        return {}
    log_path = Path(path)
    if not log_path.is_file():
        return {}
    try:
        # read tail first for speed; fall back to full if needed
        data = log_path.read_bytes()
    except Exception:
        return {}
    if not data:
        return {}
    # keep last ~1.5MB
    text = data[-1_500_000:].decode("utf-8", errors="replace")
    lines = text.splitlines()
    recovered: dict[str, Any] = {}
    for line in reversed(lines):
        if "当前统计" not in line and not (
            "成功" in line and "失败" in line and "|" in line
        ):
            continue
        parsed = _parse_current_stats_line(line)
        if parsed:
            recovered = parsed
            break
    if recovered:
        return recovered

    # fallback: count markers (coarse)
    ok = fail = 0
    detail = _empty_detail()
    fail_stats: dict[str, int] = {}
    for line in lines:
        if _OK_LINE_RE.search(line):
            ok += 1
            detail["register_ok"] += 1
            detail["sso_ok"] += 1
        if _FAIL_LINE_RE.search(line) or "邮箱域名被" in line:
            fail += 1
            kind_m = _FAIL_KIND_RE.search(line)
            kind = (kind_m.group(1).strip() if kind_m else "") or "other"
            fail_stats[kind] = fail_stats.get(kind, 0) + 1
            if _DOMAIN_SKIP_RE.search(line) or "域名" in kind:
                detail["early_skip"] += 1
            elif _SSO_FAIL_RE.search(line) or "sso" in kind.lower():
                detail["sso_fail"] += 1
                detail["register_fail"] += 1
            elif _RISK_FAIL_RE.search(line) or "风控" in kind:
                detail["risk_fail"] += 1
                detail["register_fail"] += 1
            else:
                detail["register_fail"] += 1
    if ok or fail:
        return {
            "success": ok,
            "fail": fail,
            "detail_stats": detail,
            "fail_stats": fail_stats,
            "completed": ok + fail,
        }
    return {}


def _merge_recovered_stats(data: dict[str, Any], recovered: dict[str, Any]) -> dict[str, Any]:
    """Fill zeros / missing counters from log recovery without clobbering better values."""
    if not recovered:
        return data
    out = dict(data)

    def _as_int(v: Any, default: int = 0) -> int:
        try:
            return int(v)
        except Exception:
            return default

    for key in ("success", "fail", "completed"):
        cur = _as_int(out.get(key), 0)
        got = _as_int(recovered.get(key), 0)
        if got > cur:
            out[key] = got
        elif key not in out:
            out[key] = got

    # detail_stats: prefer recovered if richer or current empty
    cur_detail = out.get("detail_stats") if isinstance(out.get("detail_stats"), dict) else {}
    got_detail = recovered.get("detail_stats") if isinstance(recovered.get("detail_stats"), dict) else {}
    cur_sum = sum(_as_int(v) for v in cur_detail.values()) if cur_detail else 0
    got_sum = sum(_as_int(v) for v in got_detail.values()) if got_detail else 0
    if got_sum > cur_sum:
        merged = _empty_detail()
        merged.update({str(k): _as_int(v) for k, v in got_detail.items()})
        out["detail_stats"] = merged
    elif not cur_detail and got_detail:
        out["detail_stats"] = dict(got_detail)

    # fail_stats merge max
    cur_fs = out.get("fail_stats") if isinstance(out.get("fail_stats"), dict) else {}
    got_fs = recovered.get("fail_stats") if isinstance(recovered.get("fail_stats"), dict) else {}
    if got_fs:
        merged_fs = dict(cur_fs or {})
        for k, v in got_fs.items():
            try:
                merged_fs[str(k)] = max(_as_int(merged_fs.get(str(k)), 0), _as_int(v))
            except Exception:
                merged_fs[str(k)] = v
        out["fail_stats"] = merged_fs
    return out


def _format_detail_block(detail_stats: dict[str, Any] | None) -> str:
    data = detail_stats if isinstance(detail_stats, dict) else {}
    if not data:
        data = _empty_detail()
    lines = []
    for key in DETAIL_ORDER:
        label = DETAIL_LABELS_ZH.get(key, key)
        try:
            val = int(data.get(key) or 0)
        except Exception:
            val = 0
        lines.append(f"  - {label}: {val}")
    # extras
    for key, val in data.items():
        if key in DETAIL_LABELS_ZH:
            continue
        lines.append(f"  - {key}: {val}")
    return "\n".join(lines)


def _format_fail_block(fail_stats: dict[str, Any] | None) -> str:
    data = fail_stats if isinstance(fail_stats, dict) else {}
    if not data:
        return "  - 无"
    # prefer stable-ish order by count desc
    items = []
    for k, v in data.items():
        try:
            items.append((str(k), int(v or 0)))
        except Exception:
            items.append((str(k), v))
    items.sort(key=lambda x: (-int(x[1] or 0), x[0]))
    return "\n".join(f"  - {k}: {v}" for k, v in items)


def _format_summary(stats: dict[str, Any]) -> str:
    reason = str(stats.get("reason") or "unknown")
    rclass = str(stats.get("reason_class") or _reason_class(reason))
    stop_type = "主动停止" if rclass == "active" else "被动停止/结束"
    reason_zh = _reason_label_zh(reason)
    duration_zh = _format_duration_zh(stats.get("duration_sec"))
    try:
        target = int(stats.get("target") or 0)
    except Exception:
        target = 0
    try:
        completed = int(stats.get("completed") or 0)
    except Exception:
        completed = 0
    try:
        success = int(stats.get("success") or 0)
    except Exception:
        success = 0
    try:
        fail = int(stats.get("fail") or 0)
    except Exception:
        fail = 0
    try:
        restarts = int(stats.get("restarts") or 0)
    except Exception:
        restarts = 0
    if completed <= 0:
        completed = success + fail
    remain = max(target - completed, 0) if target else 0
    rate = ""
    try:
        dur = float(stats.get("duration_sec") or 0)
        if dur > 0 and (success + fail) > 0:
            rate = f"{(success + fail) / (dur / 3600.0):.1f} 个/小时"
    except Exception:
        rate = ""

    notes = stats.get("notes") or []
    note_lines = ""
    if notes:
        note_lines = "\n".join(f"  - {n}" for n in notes[-12:])
        note_lines = f"\n备注:\n{note_lines}"

    detail_block = _format_detail_block(stats.get("detail_stats"))
    fail_block = _format_fail_block(stats.get("fail_stats"))
    rate_line = f"\n处理速度: {rate}" if rate else ""

    return (
        f"===== 运行汇总（{stop_type}） =====\n"
        f"结束原因: {reason_zh}（{reason}）\n"
        f"开始时间: {stats.get('started_at') or '-'}\n"
        f"结束时间: {stats.get('ended_at') or _now_iso()}\n"
        f"运行时长: {duration_zh}\n"
        f"目标数量: {target}\n"
        f"已处理槽位: {completed}\n"
        f"剩余目标: {remain}\n"
        f"总成功: {success}\n"
        f"总失败: {fail}\n"
        f"子进程重启: {restarts}"
        f"{rate_line}\n"
        f"明细统计:\n{detail_block}\n"
        f"失败分类:\n{fail_block}\n"
        f"日志文件: {stats.get('path') or ''}"
        f"{note_lines}\n"
        f"===== 汇总结束 =====\n"
    )


def finalize_run_log(
    reason: str = "completed",
    *,
    stats: dict[str, Any] | None = None,
    extra_lines: list[str] | None = None,
) -> Path | None:
    """Write end summary and close. Owner preferred; any process may finalize once."""
    global _file_handle, _log_path, _stats_path, _is_owner, _started_mono

    path = current_run_log_path()
    if path is None:
        return None

    # ensure handle
    if _file_handle is None:
        try:
            attach_run_log(path)
        except Exception:
            pass

    # flush so recovery can read latest child lines
    try:
        with _lock:
            if _file_handle is not None:
                _file_handle.flush()
    except Exception:
        pass

    stats_path = _stats_path or _stats_file_for(path)
    data = _load_stats(stats_path) or _default_stats(_kind, path)
    if stats:
        data.update({k: v for k, v in stats.items() if v is not None})

    # 子进程被 kill 时 stats.json 常为 0：从日志回填
    try:
        recovered = recover_stats_from_log(path)
        data = _merge_recovered_stats(data, recovered)
    except Exception:
        pass

    data["reason"] = str(reason or "unknown")
    data["reason_class"] = _reason_class(data["reason"])
    data["ended_at"] = _now_iso()
    if _started_mono is not None:
        data["duration_sec"] = max(0.0, time.monotonic() - _started_mono)
    elif data.get("started_at"):
        # wall-clock fallback
        try:
            started = datetime.strptime(str(data["started_at"]), "%Y-%m-%d %H:%M:%S")
            ended = datetime.strptime(str(data["ended_at"]), "%Y-%m-%d %H:%M:%S")
            data["duration_sec"] = max(0.0, (ended - started).total_seconds())
        except Exception:
            data["duration_sec"] = None
    data["path"] = str(path)
    # ensure completed
    try:
        if int(data.get("completed") or 0) <= 0:
            data["completed"] = int(data.get("success") or 0) + int(data.get("fail") or 0)
    except Exception:
        pass
    _write_stats(stats_path, data)

    summary = _format_summary(data)
    with _lock:
        handle = _file_handle
        try:
            if handle is not None:
                if extra_lines:
                    for line in extra_lines:
                        handle.write(_stamp_text(line))
                handle.write(summary)
                handle.write(
                    f"===== 运行结束 {_now_hms()} / {_now_iso()} "
                    f"原因={_reason_label_zh(data['reason'])}({data['reason']}) =====\n"
                )
                handle.flush()
                handle.close()
        except Exception:
            try:
                if handle is not None:
                    handle.close()
            except Exception:
                pass
        _file_handle = None
        # keep env so late readers still know path; clear owner
        _is_owner = False
        _started_mono = None
    return path


def stop_run_log() -> Path | None:
    """Close local handle.

    Owner finalizes with reason=process_exit if no prior finalize.
    Non-owner (child) only closes handle so parent keeps one log.
    """
    global _file_handle, _is_owner
    with _lock:
        owner = _is_owner
        path = _log_path
        handle = _file_handle
    if owner:
        # avoid double summary if already finalized
        stats_path = _stats_path or (_stats_file_for(path) if path else None)
        data = _load_stats(stats_path)
        if data.get("ended_at"):
            with _lock:
                if _file_handle is not None:
                    try:
                        _file_handle.close()
                    except Exception:
                        pass
                    _file_handle = None
            return path
        return finalize_run_log("process_exit")
    with _lock:
        if handle is not None:
            try:
                handle.flush()
                handle.close()
            except Exception:
                pass
            _file_handle = None
        _is_owner = False
    return path
