# -*- coding: utf-8 -*-
"""Outlook / Hotmail alias inventory + outlook_state.json panel ops."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from secure_files import atomic_write_json, exclusive_file_lock

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.environ.get("EMAIL_PROVIDER_CONFIG_FILE", str(ROOT / "config.json")))
LOCK_PATH = CONFIG_PATH.with_suffix(CONFIG_PATH.suffix + ".lock")

DEFAULT_ACCOUNTS = "accounts/outlook_accounts.txt"
DEFAULT_STATE = "accounts/outlook_state.json"


class OutlookInventoryError(ValueError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _read_config() -> dict:
    if not CONFIG_PATH.is_file():
        return {}
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8") or "{}")
    except Exception as exc:
        raise OutlookInventoryError(f"config.json 无法读取: {exc}") from exc
    if not isinstance(data, dict):
        raise OutlookInventoryError("config.json 必须是对象")
    return data


def _resolve_under_root(raw: object, default_rel: str) -> Path:
    text = str(raw or "").strip() or default_rel
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    else:
        path = path.resolve()
    root = ROOT.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise OutlookInventoryError("路径必须位于项目目录内") from exc
    return path


def current_paths(cfg: dict | None = None) -> tuple[Path, Path, int]:
    data = cfg if isinstance(cfg, dict) else _read_config()
    accounts = _resolve_under_root(
        data.get("outlook_accounts_file") or data.get("outlook_rt_inventory"),
        DEFAULT_ACCOUNTS,
    )
    state = _resolve_under_root(
        data.get("outlook_state_file") or DEFAULT_STATE,
        DEFAULT_STATE,
    )
    try:
        cap = int(data.get("outlook_aliases_per_account") or 10)
    except Exception:
        cap = 10
    return accounts, state, max(1, min(100, cap))


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except Exception:
        return str(path)


def _mask_line(line: str) -> str:
    parts = [p.strip() for p in line.split("----")]
    if len(parts) < 4:
        return line
    email, password, client_id, refresh = parts[0], parts[1], parts[2], parts[3]
    pw = (password[:2] + "***") if password else "***"
    rt = refresh
    if len(rt) > 18:
        rt = rt[:8] + "..." + rt[-6:]
    return f"{email}----{pw}----{client_id}----{rt}"


def parse_inventory_text(text: object) -> list[str]:
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    out: list[str] = []
    seen: set[str] = set()
    for idx, line in enumerate(raw.split("\n"), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = [p.strip() for p in stripped.split("----")]
        if len(parts) < 4:
            raise OutlookInventoryError(
                f"第 {idx} 行格式错误，需要 邮箱----密码----ClientID----RefreshToken"
            )
        email, password, client_id, refresh = parts[:4]
        if "@" not in email or not password or not client_id or not refresh:
            raise OutlookInventoryError(
                f"第 {idx} 行缺少有效邮箱/密码/ClientID/RefreshToken"
            )
        key = email.casefold()
        if key in seen:
            raise OutlookInventoryError(f"第 {idx} 行重复邮箱: {email}")
        seen.add(key)
        out.append("----".join(parts))
    if not out:
        raise OutlookInventoryError("库存为空")
    return out


def read_inventory(*, mask: bool = False) -> dict[str, Any]:
    with exclusive_file_lock(LOCK_PATH):
        cfg = _read_config()
        accounts_path, state_path, cap = current_paths(cfg)
    exists = accounts_path.is_file()
    text = ""
    if exists:
        text = accounts_path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    valid: list[str] = []
    for line in text.split("\n"):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        parts = [p.strip() for p in s.split("----")]
        if len(parts) >= 4 and "@" in parts[0]:
            valid.append(s)
    display = [_mask_line(x) for x in valid] if mask else list(valid)
    return {
        "ok": True,
        "path": _rel(accounts_path),
        "abs_path": str(accounts_path),
        "exists": exists,
        "total_lines": len(valid),
        "aliases_per_account": cap,
        "state_path": _rel(state_path),
        "format": "email----password----client_id----refresh_token",
        "lines": display,
        "text": ("\n".join(valid) + "\n") if valid else "",
        "mtime": accounts_path.stat().st_mtime if exists else None,
    }


def write_inventory(text: object) -> dict[str, Any]:
    lines = parse_inventory_text(text)
    with exclusive_file_lock(LOCK_PATH):
        cfg = _read_config()
        accounts_path, state_path, cap = current_paths(cfg)
        cfg = dict(cfg)
        cfg["email_provider"] = "outlook_rt"
        cfg["outlook_use_alias_pool"] = True
        cfg["outlook_accounts_file"] = _rel(accounts_path)
        cfg["outlook_state_file"] = _rel(state_path)
        cfg["outlook_aliases_per_account"] = cap
        accounts_path.parent.mkdir(parents=True, exist_ok=True)
        payload = "\n".join(lines) + "\n"
        tmp = accounts_path.with_name(f".{accounts_path.name}.tmp")
        tmp.write_text(payload, encoding="utf-8", newline="\n")
        tmp.replace(accounts_path)
        try:
            accounts_path.chmod(0o600)
        except Exception:
            pass
        atomic_write_json(CONFIG_PATH, cfg)
    result = read_inventory(mask=False)
    result["written"] = len(lines)
    result["saved_at"] = _utc_now()
    return result


def read_state(*, limit: int = 500) -> dict[str, Any]:
    with exclusive_file_lock(LOCK_PATH):
        cfg = _read_config()
        accounts_path, state_path, cap = current_paths(cfg)
    exists = state_path.is_file()
    data: dict[str, Any] = {
        "version": 1,
        "next_account_cursor": 0,
        "allocation_serial": 0,
        "accounts": {},
    }
    if exists:
        loaded = json.loads(state_path.read_text(encoding="utf-8") or "{}")
        if isinstance(loaded, dict):
            data.update(loaded)
            if not isinstance(data.get("accounts"), dict):
                data["accounts"] = {}

    # inventory emails (source of truth for allocatable pool)
    inv_emails: list[str] = []
    inv_set: set[str] = set()
    if accounts_path.is_file():
        for line in accounts_path.read_text(encoding="utf-8", errors="replace").splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            parts = [p.strip() for p in s.split("----")]
            if len(parts) >= 4 and "@" in parts[0]:
                email = parts[0]
                key = email.casefold()
                if key in inv_set:
                    continue
                inv_set.add(key)
                inv_emails.append(email)

    state_accounts = data.get("accounts") or {}
    state_by_key: dict[str, dict] = {}
    for key, item in state_accounts.items():
        if not isinstance(item, dict):
            continue
        email = str(item.get("email") or key or "").strip()
        if not email:
            continue
        state_by_key[email.casefold()] = item

    rows: list[dict[str, Any]] = []
    used_slots = 0
    disabled = 0
    remaining = 0
    # Prefer inventory order for remaining/used (matches OutlookAliasPool)
    seen_row: set[str] = set()
    for email in inv_emails:
        key = email.casefold()
        item = state_by_key.get(key) or {}
        nxt = int(item.get("next_alias_index", 0) or 0)
        dis = bool(item.get("disabled", False))
        if dis:
            disabled += 1
        else:
            used_slots += min(max(nxt, 0), cap)
            free = cap - nxt
            if free > 0:
                remaining += free
        rows.append(
            {
                "email": str(item.get("email") or email),
                "next_alias_index": nxt,
                "last_alias": str(item.get("last_alias") or ""),
                "disabled": dis,
                "last_allocated_at": str(item.get("last_allocated_at") or ""),
                "in_inventory": True,
                "in_state": key in state_by_key,
            }
        )
        seen_row.add(key)

    # orphan state entries (not in inventory) — show but do not count remaining
    orphan = 0
    for key, item in state_by_key.items():
        if key in seen_row:
            continue
        orphan += 1
        email = str(item.get("email") or key)
        nxt = int(item.get("next_alias_index", 0) or 0)
        dis = bool(item.get("disabled", False))
        if dis:
            disabled += 1
        rows.append(
            {
                "email": email,
                "next_alias_index": nxt,
                "last_alias": str(item.get("last_alias") or ""),
                "disabled": dis,
                "last_allocated_at": str(item.get("last_allocated_at") or ""),
                "in_inventory": False,
                "in_state": True,
            }
        )

    rows.sort(
        key=lambda r: (
            r["disabled"],
            not r.get("in_inventory", True),
            -(r["next_alias_index"] or 0),
            str(r["email"]).lower(),
        )
    )
    keep = max(20, min(5000, int(limit or 500)))
    inv_count = len(inv_emails)
    state_count = len(state_by_key)
    return {
        "ok": True,
        "path": _rel(state_path),
        "abs_path": str(state_path),
        "exists": exists,
        "format": "outlook_state.json",
        "aliases_per_account": cap,
        "next_account_cursor": int(data.get("next_account_cursor", 0) or 0),
        "allocation_serial": int(data.get("allocation_serial", 0) or 0),
        # pool-aligned metrics (same as Dry Run / OutlookAliasPool)
        "inventory_account_count": inv_count,
        "account_count": inv_count,  # primary mailboxes in inventory
        "state_account_count": state_count,
        "state_only_count": orphan,
        "inventory_only_count": len(inv_set - set(state_by_key.keys())),
        "disabled_count": disabled,
        "used_alias_slots": used_slots,
        "remaining_alias_slots": remaining,
        "remaining_alias_slots_est": remaining,
        "accounts": rows[:keep],
        "truncated": len(rows) > keep,
        "total_accounts": len(rows),
        "mtime": state_path.stat().st_mtime if exists else None,
        "inventory_path": _rel(accounts_path),
    }


def read_state_raw() -> dict[str, Any]:
    """Return full outlook_state.json text for download."""
    with exclusive_file_lock(LOCK_PATH):
        cfg = _read_config()
        _accounts_path, state_path, _cap = current_paths(cfg)
    exists = state_path.is_file()
    if exists:
        text = state_path.read_text(encoding="utf-8")
        # normalize pretty for download if valid json
        try:
            obj = json.loads(text or "{}")
            if isinstance(obj, dict):
                text = json.dumps(obj, ensure_ascii=False, indent=2) + "\n"
        except Exception:
            pass
    else:
        text = json.dumps(
            {
                "version": 1,
                "next_account_cursor": 0,
                "allocation_serial": 0,
                "accounts": {},
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n"
    return {
        "ok": True,
        "path": _rel(state_path),
        "abs_path": str(state_path),
        "exists": exists,
        "filename": state_path.name or "outlook_state.json",
        "text": text,
        "bytes": len(text.encode("utf-8")),
        "mtime": state_path.stat().st_mtime if exists else None,
    }


def _normalize_state_payload(raw: object) -> dict[str, Any]:
    if isinstance(raw, dict):
        data = raw
    else:
        text = str(raw or "").strip()
        if not text:
            raise OutlookInventoryError("outlook_state.json 内容为空")
        try:
            data = json.loads(text)
        except Exception as exc:
            raise OutlookInventoryError(f"JSON 解析失败: {exc}") from exc
    if not isinstance(data, dict):
        raise OutlookInventoryError("outlook_state.json 必须是 JSON 对象")
    accounts = data.get("accounts", {})
    if accounts is None:
        accounts = {}
    if not isinstance(accounts, dict):
        raise OutlookInventoryError("accounts 必须是对象")
    cleaned_accounts: dict[str, Any] = {}
    for key, item in accounts.items():
        if not isinstance(item, dict):
            raise OutlookInventoryError(f"accounts.{key} 必须是对象")
        email = str(item.get("email") or key or "").strip()
        if not email or "@" not in email:
            raise OutlookInventoryError(f"accounts.{key} 缺少有效 email")
        try:
            nxt = int(item.get("next_alias_index", 0) or 0)
        except Exception as exc:
            raise OutlookInventoryError(f"accounts.{email} next_alias_index 无效") from exc
        if nxt < 0 or nxt > 1000:
            raise OutlookInventoryError(f"accounts.{email} next_alias_index 超范围")
        entry = {
            "email": email,
            "next_alias_index": nxt,
            "disabled": bool(item.get("disabled", False)),
        }
        last_alias = str(item.get("last_alias") or "").strip()
        if last_alias:
            entry["last_alias"] = last_alias
        last_at = str(item.get("last_allocated_at") or "").strip()
        if last_at:
            entry["last_allocated_at"] = last_at
        # preserve unknown keys lightly
        for k, v in item.items():
            if k not in entry:
                entry[k] = v
        cleaned_accounts[email.casefold()] = entry
    try:
        version = int(data.get("version", 1) or 1)
    except Exception:
        version = 1
    try:
        cursor = int(data.get("next_account_cursor", 0) or 0)
    except Exception as exc:
        raise OutlookInventoryError("next_account_cursor 必须是整数") from exc
    try:
        serial = int(data.get("allocation_serial", 0) or 0)
    except Exception as exc:
        raise OutlookInventoryError("allocation_serial 必须是整数") from exc
    out: dict[str, Any] = {
        "version": version,
        "next_account_cursor": max(0, cursor),
        "allocation_serial": max(0, serial),
        "accounts": cleaned_accounts,
    }
    # keep optional top-level extras except accounts
    for k, v in data.items():
        if k in out or k == "accounts":
            continue
        out[k] = v
    return out


def write_state(raw: object) -> dict[str, Any]:
    """Overwrite outlook_state.json (full replace)."""
    payload = _normalize_state_payload(raw)
    with exclusive_file_lock(LOCK_PATH):
        cfg = _read_config()
        _accounts_path, state_path, _cap = current_paths(cfg)
        cfg = dict(cfg)
        cfg["email_provider"] = "outlook_rt"
        cfg["outlook_use_alias_pool"] = True
        cfg["outlook_state_file"] = _rel(state_path)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(state_path, payload)
        try:
            state_path.chmod(0o600)
        except Exception:
            pass
        atomic_write_json(CONFIG_PATH, cfg)
    summary = read_state(limit=50)
    summary["written_accounts"] = len(payload.get("accounts") or {})
    summary["saved_at"] = _utc_now()
    summary["ok"] = True
    return summary

