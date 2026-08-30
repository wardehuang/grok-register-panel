# -*- coding: utf-8 -*-
"""Canonical registered_emails store: email----password----sso (sso may be empty)."""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
ACCOUNTS_DIR = Path(os.environ.get("ACCOUNTS_DIR", str(ROOT / "accounts")))
CANONICAL_NAME = "registered_emails.txt"
CANONICAL_FILE = ACCOUNTS_DIR / CANONICAL_NAME

_SKIP_NAMES = {
    ".gitkeep",
    "mail_credentials.txt",
    "sso_pending.txt",
    "sso_risk_rejected.txt",
    "sso_bfs_flagged.txt",
    "outlook_accounts.txt",
    "outlook_state.json",
    "outlook_rt_inventory.txt",
    "outlook_rt_inventory.txt.used",
    CANONICAL_NAME,
}
_SKIP_PREFIXES = (
    "outlook_",
    "sso_",
)
_SKIP_SUFFIXES = (
    ".lock",
    ".used",
    ".claims",
)

try:
    from secure_files import (
        atomic_write_text,
        ensure_private_dir,
        exclusive_file_lock,
    )
except ImportError:  # pragma: no cover
    import sys

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from secure_files import (  # type: ignore
        atomic_write_text,
        ensure_private_dir,
        exclusive_file_lock,
    )


class RegisteredAccountsError(ValueError):
    pass


def canonical_path() -> Path:
    return CANONICAL_FILE


def _lock_path() -> Path:
    return CANONICAL_FILE.with_suffix(CANONICAL_FILE.suffix + ".lock")


def _is_skipped(path: Path) -> bool:
    name = path.name
    if name in _SKIP_NAMES:
        return True
    if name.startswith("."):
        return True
    for p in _SKIP_PREFIXES:
        if name.startswith(p):
            return True
    for s in _SKIP_SUFFIXES:
        if name.endswith(s):
            return True
    if path.is_dir():
        return True
    return False


def _parse_line(line: str) -> dict[str, str] | None:
    """Parse email----password----sso.

    SSO may be empty. Also accept email----password.
    Uses first/last '----' so password may end with '-' (e.g. ...PASS-----sso).
    """
    raw = str(line or "").strip()
    if not raw or raw.startswith("#"):
        return None
    first = raw.find("----")
    if first < 0:
        return None
    email = raw[:first].strip()
    rest = raw[first + 4 :]
    if not rest:
        return None
    last = rest.rfind("----")
    if last < 0:
        password = rest
        sso = ""
    else:
        password = rest[:last]
        sso = rest[last + 4 :]
    sso = str(sso or "").strip()
    if sso.startswith("sso="):
        sso = sso[4:].strip()
    # password keep as-is (no strip of internal/edge spaces that might be intentional);
    # only reject empty password
    if "@" not in email or password == "":
        return None
    return {
        "email": email,
        "password": password,
        "sso": sso,
        "line": f"{email}----{password}----{sso}",
    }


def format_line(email: str, password: str, sso: str = "") -> str:
    return f"{str(email or '').strip()}----{str(password or '')}----{str(sso or '').strip()}"


def _iter_legacy_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    out: list[Path] = []
    for path in directory.iterdir():
        if not path.is_file():
            continue
        if _is_skipped(path):
            continue
        if path.suffix.lower() != ".txt":
            continue
        out.append(path)
    return sorted(out, key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)


def _read_file_records(path: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = None
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        item = _parse_line(line)
        if not item:
            continue
        rows.append({**item, "source": path.name, "mtime": mtime})
    return rows


def _load_canonical_unlocked() -> dict[str, dict[str, Any]]:
    by_email: dict[str, dict[str, Any]] = {}
    if not CANONICAL_FILE.is_file():
        return by_email
    for row in _read_file_records(CANONICAL_FILE):
        key = str(row["email"]).casefold()
        by_email[key] = row
    return by_email


def _write_canonical_unlocked(by_email: dict[str, dict[str, Any]]) -> int:
    ensure_private_dir(ACCOUNTS_DIR)
    rows = sorted(
        by_email.values(),
        key=lambda r: str(r.get("email") or "").lower(),
    )
    lines = [format_line(r["email"], r.get("password") or "", r.get("sso") or "") for r in rows]
    text = "\n".join(lines) + ("\n" if lines else "")
    atomic_write_text(CANONICAL_FILE, text)
    return len(lines)


def _merge_legacy_into(by_email: dict[str, dict[str, Any]]) -> int:
    """Fill missing emails from per-account txt files; do not override canonical."""
    added = 0
    for path in _iter_legacy_files(ACCOUNTS_DIR):
        for row in _read_file_records(path):
            key = str(row["email"]).casefold()
            prev = by_email.get(key)
            if prev is None:
                by_email[key] = row
                added += 1
                continue
            # backfill empty fields from legacy
            if not prev.get("password") and row.get("password"):
                prev["password"] = row["password"]
            if not prev.get("sso") and row.get("sso"):
                prev["sso"] = row["sso"]
                prev["line"] = format_line(prev["email"], prev["password"], prev["sso"])
    return added


def collect_registered_accounts(*, limit: int = 20000, include_legacy: bool = True) -> dict[str, Any]:
    keep = max(1, min(100000, int(limit or 20000)))
    ensure_private_dir(ACCOUNTS_DIR)
    with exclusive_file_lock(_lock_path()):
        by_email = _load_canonical_unlocked()
        legacy_sources = 0
        if include_legacy:
            legacy_sources = len(_iter_legacy_files(ACCOUNTS_DIR))
            _merge_legacy_into(by_email)
        canonical_exists = CANONICAL_FILE.is_file()
        try:
            c_mtime = CANONICAL_FILE.stat().st_mtime if canonical_exists else None
        except OSError:
            c_mtime = None

    rows = sorted(
        by_email.values(),
        key=lambda r: (r.get("mtime") or 0, str(r.get("email") or "").lower()),
        reverse=True,
    )
    total = len(rows)
    rows = rows[:keep]
    lines = [format_line(r["email"], r.get("password") or "", r.get("sso") or "") for r in rows]
    with_sso = sum(1 for r in by_email.values() if r.get("sso"))
    without_sso = max(0, total - with_sso)
    public_rows = [
        {
            "email": r["email"],
            "source": r.get("source") or CANONICAL_NAME,
            "mtime": r.get("mtime") if r.get("mtime") is not None else c_mtime,
            "has_password": bool(r.get("password")),
            "has_sso": bool(r.get("sso")),
            "sso_preview": (str(r.get("sso") or "")[:18] + "…") if r.get("sso") else "",
        }
        for r in rows
    ]
    return {
        "ok": True,
        "path": f"accounts/{CANONICAL_NAME}",
        "abs_path": str(CANONICAL_FILE.resolve()),
        "exists": canonical_exists or total > 0,
        "count": total,
        "with_sso": with_sso,
        "without_sso": max(0, without_sso),
        "source_files": (1 if canonical_exists else 0) + legacy_sources,
        "truncated": total > len(rows),
        "accounts": public_rows,
        "export_filename": CANONICAL_NAME,
        "export_text": "\n".join(lines) + ("\n" if lines else ""),
        "format": "email----password----sso",
    }


def export_registered_emails_text(*, limit: int = 20000) -> dict[str, Any]:
    data = collect_registered_accounts(limit=limit)
    return {
        "ok": True,
        "filename": data["export_filename"],
        "count": data["count"],
        "bytes": len(data["export_text"].encode("utf-8")),
        "text": data["export_text"],
        "format": data["format"],
    }


def read_one_registered_line(email: str) -> dict[str, Any]:
    target = str(email or "").strip()
    if not target or "@" not in target:
        raise RegisteredAccountsError("email 无效")
    item = lookup_registered_account(target)
    if not item:
        raise RegisteredAccountsError("未找到该账号")
    return {
        "ok": True,
        "email": item["email"],
        "line": format_line(item["email"], item.get("password") or "", item.get("sso") or ""),
        "source": item.get("source") or CANONICAL_NAME,
        "has_sso": bool(item.get("sso")),
    }


def lookup_registered_account(email: str) -> dict[str, Any] | None:
    target = str(email or "").strip()
    if not target or "@" not in target:
        return None
    key = target.casefold()
    ensure_private_dir(ACCOUNTS_DIR)
    with exclusive_file_lock(_lock_path()):
        by_email = _load_canonical_unlocked()
        _merge_legacy_into(by_email)
        row = by_email.get(key)
        if not row:
            return None
        return {
            "email": row["email"],
            "password": row.get("password") or "",
            "sso": row.get("sso") or "",
            "source": row.get("source") or CANONICAL_NAME,
            "mtime": row.get("mtime"),
        }


def upsert_registered_account(
    email: str,
    password: str = "",
    *,
    sso: str | None = None,
    keep_existing_sso_if_empty: bool = True,
) -> dict[str, Any]:
    """Insert/update one account in registered_emails.txt.

    sso=None → keep existing SSO
    sso="" → clear only when keep_existing_sso_if_empty is False or no existing SSO
    sso non-empty → always update
    password empty → keep existing password
    """
    em = str(email or "").strip()
    if not em or "@" not in em:
        raise RegisteredAccountsError("email 无效")
    pwd_in = str(password or "")
    ensure_private_dir(ACCOUNTS_DIR)
    with exclusive_file_lock(_lock_path()):
        by_email = _load_canonical_unlocked()
        _merge_legacy_into(by_email)
        key = em.casefold()
        prev = by_email.get(key) or {}
        pwd = pwd_in if pwd_in else str(prev.get("password") or "")
        if not pwd:
            raise RegisteredAccountsError("password 为空且无历史密码")
        prev_sso = str(prev.get("sso") or "")
        if sso is None:
            new_sso = prev_sso
        else:
            sso_s = str(sso or "").strip()
            if sso_s:
                new_sso = sso_s
            elif keep_existing_sso_if_empty and prev_sso:
                new_sso = prev_sso
            else:
                new_sso = ""
        row = {
            "email": em,
            "password": pwd,
            "sso": new_sso,
            "line": format_line(em, pwd, new_sso),
            "source": CANONICAL_NAME,
            "mtime": time.time(),
        }
        by_email[key] = row
        count = _write_canonical_unlocked(by_email)
    return {
        "ok": True,
        "email": em,
        "has_sso": bool(new_sso),
        "count": count,
        "path": f"accounts/{CANONICAL_NAME}",
    }


def overwrite_registered_emails(text: str) -> dict[str, Any]:
    """Full replace canonical file from paste/upload text. One account per line."""
    raw = str(text or "")
    # strip UTF-8 BOM
    if raw.startswith("\ufeff"):
        raw = raw[1:]
    by_email: dict[str, dict[str, Any]] = {}
    bad = 0
    for line in raw.splitlines():
        item = _parse_line(line)
        if not item:
            if str(line or "").strip() and not str(line).lstrip().startswith("#"):
                bad += 1
            continue
        key = item["email"].casefold()
        by_email[key] = {
            **item,
            "source": CANONICAL_NAME,
            "mtime": time.time(),
        }
    if not by_email and raw.strip():
        raise RegisteredAccountsError("没有可解析的账号行（需要 email----password----sso）")
    ensure_private_dir(ACCOUNTS_DIR)
    with exclusive_file_lock(_lock_path()):
        count = _write_canonical_unlocked(by_email)
    with_sso = sum(1 for r in by_email.values() if r.get("sso"))
    return {
        "ok": True,
        "count": count,
        "with_sso": with_sso,
        "without_sso": count - with_sso,
        "skipped_bad_lines": bad,
        "path": f"accounts/{CANONICAL_NAME}",
        "format": "email----password----sso",
    }


def parse_email_list(text: str) -> list[str]:
    """Extract emails one per line (ignore blanks/comments)."""
    out: list[str] = []
    seen: set[str] = set()
    for line in str(text or "").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        # allow full registered line → take email field
        if "----" in raw:
            raw = raw.split("----", 1)[0].strip()
        if "@" not in raw:
            continue
        key = raw.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(raw)
    return out
