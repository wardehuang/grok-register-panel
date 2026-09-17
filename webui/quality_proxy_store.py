"""Independent proxy pool used only by account intelligence checks."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from secure_files import atomic_write_json, exclusive_file_lock

try:
    from webui.proxy_store import normalize_proxy, probe_proxy
    from webui.security_utils import redact_log_line, redact_proxy
except ImportError:  # running from webui/
    from proxy_store import normalize_proxy, probe_proxy  # type: ignore
    from security_utils import redact_log_line, redact_proxy  # type: ignore


ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = Path(
    os.environ.get(
        "QUALITY_PROXY_POOL_STATE_FILE",
        str(ROOT / "log" / "quality_proxy_pool.json"),
    )
)
LOCK_PATH = STATE_PATH.with_suffix(STATE_PATH.suffix + ".lock")
LEGACY_PATH = Path(
    os.environ.get("QUALITY_PROXY_POOL_LEGACY_FILE", str(ROOT / "proxies.txt"))
)
DEFAULT_TEST_TIMEOUT = 8.0
ALLOWED_STATUSES = {"unknown", "healthy", "unhealthy"}

_TEST_LOCK = threading.RLock()
_TEST_JOB = {
    "running": False,
    "job_id": None,
    "total": 0,
    "completed": 0,
    "healthy": 0,
    "failed": 0,
    "started_at": None,
    "finished_at": None,
    "testing_ids": [],
}


class QualityProxyValidationError(ValueError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clean_text(value: object, limit: int = 180) -> str:
    text = redact_log_line(str(value or ""))
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _proxy_id(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]


def _default_state() -> dict:
    return {"version": 1, "items": [], "updated_at": _utc_now()}


def _normalize_item(raw: object) -> dict | None:
    if not isinstance(raw, dict):
        return None
    try:
        url = normalize_proxy(raw.get("url"))
    except ValueError:
        return None
    status = str(raw.get("status") or "unknown").strip().lower()
    if status not in ALLOWED_STATUSES:
        status = "unknown"
    latency = raw.get("latency_ms")
    try:
        latency = max(0, int(latency)) if latency not in (None, "") else None
    except (TypeError, ValueError):
        latency = None
    asn = raw.get("asn")
    try:
        asn = int(asn) if asn not in (None, "") else None
    except (TypeError, ValueError):
        asn = None
    return {
        "id": _proxy_id(url),
        "url": url,
        "enabled": bool(raw.get("enabled", True)),
        "status": status,
        "exit_ip": _clean_text(raw.get("exit_ip"), 64),
        "asn": asn,
        "asn_org": _clean_text(raw.get("asn_org"), 120),
        "latency_ms": latency,
        "last_checked_at": _clean_text(raw.get("last_checked_at"), 40),
        "last_error": _clean_text(raw.get("last_error"), 180),
        "failure_count": _safe_int(raw.get("failure_count")),
        "source": _clean_text(raw.get("source") or "panel", 32),
        "created_at": _clean_text(raw.get("created_at"), 40) or _utc_now(),
    }


def _normalize_state(raw: object) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("quality proxy pool state must be an object")
    items_by_id = {}
    for candidate in raw.get("items") or []:
        item = _normalize_item(candidate)
        if item:
            items_by_id[item["id"]] = item
    return {
        "version": 1,
        "items": list(items_by_id.values()),
        "updated_at": _clean_text(raw.get("updated_at"), 40) or _utc_now(),
    }


def _read_unlocked() -> tuple[dict, list[str]]:
    if not STATE_PATH.exists():
        return _default_state(), []
    try:
        raw = json.loads(STATE_PATH.read_text(encoding="utf-8") or "{}")
        return _normalize_state(raw), []
    except Exception as exc:
        return _default_state(), [_clean_text(exc)]


def _write_unlocked(state: dict) -> None:
    state["updated_at"] = _utc_now()
    atomic_write_json(STATE_PATH, _normalize_state(state))


def _legacy_info() -> dict:
    count = 0
    try:
        if LEGACY_PATH.is_file():
            for line in LEGACY_PATH.read_text(encoding="utf-8").splitlines():
                text = line.strip()
                if text and not text.startswith("#"):
                    count += 1
    except OSError:
        pass
    return {
        "available": count > 0,
        "count": count,
        "filename": LEGACY_PATH.name,
    }


def _public_item(item: dict, testing_ids: set[str]) -> dict:
    parsed = urlsplit(item["url"])
    return {
        "id": item["id"],
        "display_url": redact_proxy(item["url"]),
        "scheme": parsed.scheme,
        "host": parsed.hostname or "",
        "port": parsed.port,
        "has_auth": parsed.username is not None,
        "enabled": item["enabled"],
        "status": "testing" if item["id"] in testing_ids else item["status"],
        "stored_status": item["status"],
        "exit_ip": item.get("exit_ip") or "",
        "asn": item.get("asn"),
        "asn_org": item.get("asn_org") or "",
        "latency_ms": item.get("latency_ms"),
        "last_checked_at": item.get("last_checked_at") or "",
        "last_error": item.get("last_error") or "",
        "failure_count": item.get("failure_count", 0),
        "cooldown_until": "",
        "cooldown_reason": "",
        "cooldown_remaining_seconds": 0,
        "last_used_at": "",
        "success_count": 0,
        "risk_count": 0,
        "source": item.get("source") or "panel",
        "created_at": item.get("created_at") or "",
    }


def quality_proxy_test_status() -> dict:
    with _TEST_LOCK:
        return {
            key: (list(value) if isinstance(value, list) else value)
            for key, value in _TEST_JOB.items()
        }


def read_quality_proxy_pool() -> dict:
    with exclusive_file_lock(LOCK_PATH):
        state, errors = _read_unlocked()
    job = quality_proxy_test_status()
    testing_ids = set(job.get("testing_ids") or [])
    items = [_public_item(item, testing_ids) for item in state["items"]]
    summary = {
        "total": len(items),
        "enabled": sum(1 for item in items if item["enabled"]),
        "healthy": sum(1 for item in items if item["stored_status"] == "healthy"),
        "unhealthy": sum(1 for item in items if item["stored_status"] == "unhealthy"),
        "cooldown": 0,
        "unknown": sum(1 for item in items if item["stored_status"] == "unknown"),
        "usable": sum(1 for item in items if item["enabled"]),
    }
    try:
        mtime = STATE_PATH.stat().st_mtime
    except OSError:
        mtime = None
    return {
        "ok": not errors,
        "error": errors[0] if errors else None,
        "errors": errors,
        "summary": summary,
        "items": items,
        "test_job": job,
        "legacy": _legacy_info(),
        "updated_at": state.get("updated_at") or "",
        "mtime": mtime,
    }


def _input_lines(values: object) -> list[str]:
    if isinstance(values, str):
        return values.splitlines()
    if isinstance(values, (list, tuple)):
        return [str(value or "") for value in values]
    return []


def import_quality_proxies(values: object, *, source: str = "panel") -> dict:
    candidates = []
    errors = []
    seen = set()
    for line_number, line in enumerate(_input_lines(values), 1):
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        try:
            normalized = normalize_proxy(text)
        except ValueError as exc:
            errors.append({"line": line_number, "error": str(exc)})
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        candidates.append(normalized)
    if not candidates:
        return {
            "ok": False,
            "error": "没有可导入的有效代理",
            "errors": errors,
            "imported_count": 0,
            "duplicate_count": 0,
        }

    imported_ids = []
    duplicate_count = 0
    with exclusive_file_lock(LOCK_PATH):
        state, _ = _read_unlocked()
        existing = {item["id"]: item for item in state["items"]}
        for url in candidates:
            item_id = _proxy_id(url)
            if item_id in existing:
                duplicate_count += 1
                continue
            item = _normalize_item(
                {
                    "url": url,
                    "enabled": True,
                    "status": "unknown",
                    "source": source,
                    "created_at": _utc_now(),
                }
            )
            if item:
                existing[item_id] = item
                imported_ids.append(item_id)
        state["items"] = list(existing.values())
        _write_unlocked(state)

    result = read_quality_proxy_pool()
    result.update(
        {
            "ok": True,
            "imported_count": len(imported_ids),
            "duplicate_count": duplicate_count,
            "imported_ids": imported_ids,
            "errors": errors,
        }
    )
    return result


def import_legacy_quality_proxies() -> dict:
    try:
        text = LEGACY_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "error": _clean_text(exc), "imported_count": 0}
    return import_quality_proxies(text, source="proxies.txt")


def update_quality_proxy(proxy_id: str, *, enabled: object | None = None) -> dict:
    proxy_id = str(proxy_id or "").strip()
    found = False
    with exclusive_file_lock(LOCK_PATH):
        state, _ = _read_unlocked()
        for item in state["items"]:
            if item["id"] != proxy_id:
                continue
            found = True
            if enabled is not None:
                if not isinstance(enabled, bool):
                    raise QualityProxyValidationError("enabled 必须是布尔值")
                item["enabled"] = enabled
            break
        if not found:
            return {"ok": False, "error": "代理不存在"}
        _write_unlocked(state)
    return read_quality_proxy_pool()


def delete_quality_proxy(proxy_id: str) -> dict:
    proxy_id = str(proxy_id or "").strip()
    with exclusive_file_lock(LOCK_PATH):
        state, _ = _read_unlocked()
        before = len(state["items"])
        state["items"] = [item for item in state["items"] if item["id"] != proxy_id]
        if len(state["items"]) == before:
            return {"ok": False, "error": "代理不存在"}
        _write_unlocked(state)
    result = read_quality_proxy_pool()
    result["deleted_id"] = proxy_id
    return result


def clear_quality_proxies() -> dict:
    with _TEST_LOCK:
        if _TEST_JOB.get("running"):
            return {
                "ok": False,
                "error": "已有智商检测代理任务正在运行",
                **quality_proxy_test_status(),
            }
        deleted_count = 0
        with exclusive_file_lock(LOCK_PATH):
            state, _ = _read_unlocked()
            deleted_count = len(state["items"])
            if deleted_count:
                state["items"] = []
                _write_unlocked(state)
    result = read_quality_proxy_pool()
    result["deleted_count"] = deleted_count
    return result


def reorder_quality_proxies(proxy_ids: object) -> dict:
    if not isinstance(proxy_ids, list):
        raise QualityProxyValidationError("ids 必须是代理 ID 数组")
    requested = [str(value or "").strip() for value in proxy_ids]
    if any(not value for value in requested):
        raise QualityProxyValidationError("代理 ID 不能为空")
    if len(requested) != len(set(requested)):
        raise QualityProxyValidationError("代理 ID 不能重复")
    changed = False
    with exclusive_file_lock(LOCK_PATH):
        state, _ = _read_unlocked()
        current_ids = [item["id"] for item in state["items"]]
        if len(requested) != len(current_ids) or set(requested) != set(current_ids):
            raise QualityProxyValidationError("代理池已变化，请刷新后重试")
        changed = requested != current_ids
        if changed:
            items_by_id = {item["id"]: item for item in state["items"]}
            state["items"] = [items_by_id[item_id] for item_id in requested]
            _write_unlocked(state)
    result = read_quality_proxy_pool()
    result["reordered"] = changed
    return result


def runtime_quality_proxy_snapshot(*, exclude_urls: set[str] | None = None) -> list[str]:
    excluded = set(exclude_urls or set())
    with exclusive_file_lock(LOCK_PATH):
        state, _ = _read_unlocked()
        return [
            item["url"]
            for item in state["items"]
            if item["enabled"] and item["url"] not in excluded
        ]


def has_runtime_quality_proxy() -> bool:
    return bool(runtime_quality_proxy_snapshot())


def remove_runtime_quality_proxy(url: object) -> bool:
    try:
        normalized = normalize_proxy(url)
    except ValueError:
        return False
    removed = False
    with exclusive_file_lock(LOCK_PATH):
        state, _ = _read_unlocked()
        kept = []
        for item in state["items"]:
            if item["url"] == normalized:
                removed = True
                continue
            kept.append(item)
        if removed:
            state["items"] = kept
            _write_unlocked(state)
    return removed


def claim_runtime_quality_proxy() -> str | None:
    claimed = None
    with exclusive_file_lock(LOCK_PATH):
        state, _ = _read_unlocked()
        kept = []
        for item in state["items"]:
            if claimed is None and item["enabled"]:
                claimed = item["url"]
                continue
            kept.append(item)
        if claimed is not None:
            state["items"] = kept
            _write_unlocked(state)
    return claimed


def _apply_probe_result(proxy_id: str, result: dict) -> None:
    with exclusive_file_lock(LOCK_PATH):
        state, _ = _read_unlocked()
        changed = False
        for item in state["items"]:
            if item["id"] != proxy_id:
                continue
            changed = True
            item["last_checked_at"] = result.get("checked_at") or _utc_now()
            if result.get("ok"):
                item["status"] = "healthy"
                item["exit_ip"] = _clean_text(result.get("exit_ip"), 64)
                item["asn"] = result.get("asn")
                item["asn_org"] = _clean_text(result.get("asn_org"), 120)
                item["latency_ms"] = result.get("latency_ms")
                item["last_error"] = ""
            else:
                item["status"] = "unhealthy"
                item["latency_ms"] = None
                item["last_error"] = _clean_text(result.get("error")) or "代理探测失败"
                item["failure_count"] += 1
            break
        if changed:
            _write_unlocked(state)


def _probe_task(proxy_id: str, url: str, timeout: float) -> tuple[str, dict]:
    try:
        return proxy_id, probe_proxy(url, timeout=timeout)
    except Exception as exc:
        return proxy_id, {
            "ok": False,
            "error": _clean_text(exc) or "代理探测失败",
            "checked_at": _utc_now(),
        }


def _run_test_job(job_id: str, selected: list[tuple[str, str]], timeout: float) -> None:
    try:
        workers = min(4, max(1, len(selected)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="quality-proxy-test") as executor:
            futures = [
                executor.submit(_probe_task, proxy_id, url, timeout)
                for proxy_id, url in selected
            ]
            for future in as_completed(futures):
                proxy_id, result = future.result()
                _apply_probe_result(proxy_id, result)
                with _TEST_LOCK:
                    if _TEST_JOB.get("job_id") != job_id:
                        continue
                    _TEST_JOB["completed"] += 1
                    _TEST_JOB["healthy" if result.get("ok") else "failed"] += 1
                    _TEST_JOB["testing_ids"] = [
                        value for value in _TEST_JOB["testing_ids"] if value != proxy_id
                    ]
    finally:
        with _TEST_LOCK:
            if _TEST_JOB.get("job_id") == job_id:
                _TEST_JOB["running"] = False
                _TEST_JOB["finished_at"] = _utc_now()
                _TEST_JOB["testing_ids"] = []


def start_quality_proxy_tests(
    ids: object = None,
    *,
    scope: str = "all",
    timeout: float = DEFAULT_TEST_TIMEOUT,
) -> dict:
    if scope not in {"all", "attention"}:
        raise QualityProxyValidationError("检测范围无效")
    requested = {
        str(value or "").strip()
        for value in (ids if isinstance(ids, (list, tuple, set)) else [])
        if str(value or "").strip()
    }
    with _TEST_LOCK:
        if _TEST_JOB.get("running"):
            return {"ok": False, "error": "已有智商检测代理任务正在运行", **quality_proxy_test_status()}
        with exclusive_file_lock(LOCK_PATH):
            state, _ = _read_unlocked()
            selected = [
                (item["id"], item["url"])
                for item in state["items"]
                if (
                    item["id"] in requested
                    if requested
                    else (
                        item["status"] in {"unknown", "unhealthy", "cooldown"}
                        if scope == "attention"
                        else item["enabled"]
                    )
                )
            ]
        if not selected:
            return {"ok": False, "error": "没有可检测的代理"}
        job_id = hashlib.sha256(f"{time.time_ns()}:{len(selected)}".encode()).hexdigest()[:12]
        _TEST_JOB.update(
            {
                "running": True,
                "job_id": job_id,
                "total": len(selected),
                "completed": 0,
                "healthy": 0,
                "failed": 0,
                "started_at": _utc_now(),
                "finished_at": None,
                "testing_ids": [proxy_id for proxy_id, _ in selected],
            }
        )
        thread = threading.Thread(
            target=_run_test_job,
            args=(job_id, selected, max(2.0, min(float(timeout), 20.0))),
            name=f"quality-proxy-test-{job_id}",
            daemon=True,
        )
        thread.start()
        return {"ok": True, **quality_proxy_test_status()}
