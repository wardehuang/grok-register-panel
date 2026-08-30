#!/usr/bin/env python3
"""Sequential durable proxy cursor (no health check).

Pool file: one proxy URL per line (# comments / blanks ignored).
State file: next_index (and bookkeeping fields).

allocate() returns (proxy_url, index) and advances the cursor.
release_allocation(index) rewinds the cursor on early fail.
"""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Optional, Union
from urllib.parse import urlparse

try:
    from secure_files import atomic_write_json, exclusive_file_lock
except Exception:  # pragma: no cover
    try:
        from filelock import FileLock as _FileLock
    except Exception:  # pragma: no cover
        _FileLock = None  # type: ignore[misc, assignment]

    atomic_write_json = None  # type: ignore[assignment]
    exclusive_file_lock = None  # type: ignore[assignment]
else:
    _FileLock = None  # type: ignore[misc, assignment]


LogCallback = Callable[[str], None]


class ProxyCursorError(Exception):
    """Base error for proxy cursor operations."""


class ProxyCursorExhausted(ProxyCursorError):
    """Pool is empty (no entries to allocate)."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _normalize_proxy_url(raw: str) -> str:
    text = str(raw or "").strip()
    if not text or text.startswith("#"):
        return ""
    if "://" not in text:
        text = "http://" + text
    return text


def mask_proxy(proxy_url: str, *, keep_credentials: bool = False) -> str:
    """Mask userinfo in proxy URL for safer logs (optional helper)."""
    text = str(proxy_url or "").strip()
    if not text:
        return ""
    if keep_credentials:
        return text
    try:
        parsed = urlparse(text if "://" in text else "http://" + text)
    except Exception:
        return text
    if not parsed.username and not parsed.password:
        return text
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    user = parsed.username or ""
    masked_user = (user[:2] + "***") if user else "***"
    auth = f"{masked_user}:***@"
    path = parsed.path or ""
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{parsed.scheme}://{auth}{host}{path}{query}"


# Back-compat alias used by some call sites / lite-patch naming.
mask_proxy_for_log = mask_proxy


@contextmanager
def _state_lock(state_file: Path) -> Iterator[None]:
    lock_path = Path(f"{state_file}.lock")
    if exclusive_file_lock is not None:
        with exclusive_file_lock(lock_path):
            yield
        return
    if _FileLock is not None:
        with _FileLock(str(lock_path)):
            yield
        return
    yield


class ProxyCursor:
    """Allocate proxies in file order; persist next cursor; no live probe."""

    def __init__(
        self,
        pool_file: Union[str, Path],
        state_file: Union[str, Path],
    ) -> None:
        self.pool_file = Path(pool_file)
        self.state_file = Path(state_file)
        self._lock = threading.RLock()
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self._proxies = self._load_proxies()
        if not self._proxies:
            raise ProxyCursorError(f"代理池为空或文件不存在: {self.pool_file}")
        self._state = self._load_state()

    @property
    def size(self) -> int:
        return len(self._proxies)

    @property
    def next_index(self) -> int:
        with self._lock, _state_lock(self.state_file):
            self._state = self._load_state()
            return int(self._state.get("next_index", 0) or 0) % max(self.size, 1)

    def reload(self) -> int:
        """Re-read pool file from disk. Keep cursor (clamped to new size)."""
        with self._lock, _state_lock(self.state_file):
            self._proxies = self._load_proxies()
            if not self._proxies:
                raise ProxyCursorError(f"代理池为空或文件不存在: {self.pool_file}")
            self._state = self._load_state()
            next_index = int(self._state.get("next_index", 0) or 0)
            if next_index >= len(self._proxies):
                next_index = 0
                self._state["next_index"] = 0
                self._state["updated_at"] = _utc_now()
                self._save_state()
            return len(self._proxies)

    def reset_cursor(self) -> None:
        """Clear durable cursor so the next allocate starts at index 0."""
        with self._lock, _state_lock(self.state_file):
            self._state = {
                "next_index": 0,
                "pool_file": str(self.pool_file),
                "pool_size": len(self._proxies),
                "updated_at": _utc_now(),
                "last_allocated": "",
                "last_allocated_index": -1,
            }
            self._save_state()

    def release_allocation(
        self,
        index: int,
        log_callback: Optional[LogCallback] = None,
    ) -> int:
        """Rewind durable cursor to a previously allocated index."""
        with self._lock, _state_lock(self.state_file):
            self._proxies = self._load_proxies()
            if not self._proxies:
                raise ProxyCursorError(f"代理池为空或文件不存在: {self.pool_file}")
            pool_size = len(self._proxies)
            release_index = int(index) % pool_size
            self._state = self._load_state()
            previous_next = int(self._state.get("next_index", 0) or 0) % pool_size
            self._state["next_index"] = release_index
            self._state["pool_file"] = str(self.pool_file)
            self._state["pool_size"] = pool_size
            self._state["updated_at"] = _utc_now()
            if int(self._state.get("last_allocated_index", -1) or -1) == release_index:
                self._state["last_allocated"] = ""
                self._state["last_allocated_index"] = -1
            self._save_state()
            if log_callback:
                display = mask_proxy(self._proxies[release_index])
                log_callback(
                    f"[*] 代理游标回退 [{release_index + 1}/{pool_size}] "
                    f"(原 next={previous_next + 1}): {display}"
                )
            return release_index

    def allocate(
        self,
        log_callback: Optional[LogCallback] = None,
    ) -> tuple[str, int]:
        """Take the next proxy in order (no health check) and advance cursor."""
        with self._lock, _state_lock(self.state_file):
            self._proxies = self._load_proxies()
            if not self._proxies:
                raise ProxyCursorExhausted(
                    f"代理池为空或文件不存在: {self.pool_file}"
                )
            pool_size = len(self._proxies)
            self._state = self._load_state()
            index = int(self._state.get("next_index", 0) or 0) % pool_size
            proxy_url = self._proxies[index]
            next_index = (index + 1) % pool_size
            self._state["next_index"] = next_index
            self._state["pool_file"] = str(self.pool_file)
            self._state["pool_size"] = pool_size
            self._state["updated_at"] = _utc_now()
            self._state["last_allocated"] = proxy_url
            self._state["last_allocated_index"] = index
            self._save_state()
            if log_callback:
                display = mask_proxy(proxy_url)
                log_callback(
                    f"[+] 代理分配 [{index + 1}/{pool_size}]: {display}"
                )
            return proxy_url, index

    def _load_proxies(self) -> list[str]:
        if not self.pool_file.is_file():
            return []
        lines = self.pool_file.read_text(encoding="utf-8", errors="replace").splitlines()
        proxies: list[str] = []
        seen: set[str] = set()
        for line in lines:
            normalized = _normalize_proxy_url(line)
            if not normalized:
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            proxies.append(normalized)
        return proxies

    def _load_state(self) -> dict:
        default = {
            "next_index": 0,
            "pool_file": str(self.pool_file),
            "pool_size": len(self._proxies),
            "updated_at": "",
            "last_allocated": "",
            "last_allocated_index": -1,
        }
        if not self.state_file.is_file():
            return dict(default)
        try:
            raw = json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception:
            return dict(default)
        if not isinstance(raw, dict):
            raw = {}
        next_index = int(raw.get("next_index", 0) or 0)
        if self._proxies:
            next_index = next_index % len(self._proxies)
        else:
            next_index = 0
        return {
            "next_index": next_index,
            "pool_file": str(raw.get("pool_file") or self.pool_file),
            "pool_size": int(raw.get("pool_size", len(self._proxies)) or len(self._proxies)),
            "updated_at": str(raw.get("updated_at") or ""),
            "last_allocated": str(raw.get("last_allocated") or ""),
            "last_allocated_index": int(raw.get("last_allocated_index", -1) or -1),
        }

    def _save_state(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(self._state)
        if atomic_write_json is not None:
            atomic_write_json(self.state_file, payload)
            return
        tmp_path = self.state_file.with_suffix(self.state_file.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        tmp_path.replace(self.state_file)


# ── process-level singleton helpers ──────────────────────────────────────────

_cursor_singleton: Optional[ProxyCursor] = None
_cursor_signature: Optional[tuple] = None
_cursor_singleton_lock = threading.Lock()


def get_proxy_cursor(
    pool_file: Union[str, Path],
    state_file: Union[str, Path],
    *,
    force_reload: bool = False,
) -> ProxyCursor:
    global _cursor_singleton, _cursor_signature
    signature = (
        str(Path(pool_file).resolve()),
        str(Path(state_file).resolve()),
    )
    with _cursor_singleton_lock:
        if (
            force_reload
            or _cursor_singleton is None
            or _cursor_signature != signature
        ):
            _cursor_singleton = ProxyCursor(
                pool_file=pool_file,
                state_file=state_file,
            )
            _cursor_signature = signature
        return _cursor_singleton


def reset_proxy_cursor_singleton() -> None:
    global _cursor_singleton, _cursor_signature
    with _cursor_singleton_lock:
        _cursor_singleton = None
        _cursor_signature = None
