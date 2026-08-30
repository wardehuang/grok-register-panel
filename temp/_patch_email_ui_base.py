# -*- coding: utf-8 -*-
"""Patch email provider for outlook accounts inventory + state viewer."""
from pathlib import Path
import re

ROOT = Path(r"E:/AI/grok-register-panel")

# ── 1) email_provider_store.py ──────────────────────────────────────────────
store = ROOT / "webui" / "email_provider_store.py"
st = store.read_text(encoding="utf-8")

st = st.replace(
    '    "outlook_rt": "Outlook RT 库存",\n',
    '    "outlook_rt": "Outlook / Hotmail 别名库存",\n',
)

old_fields = '''    "outlook_rt_inventory": {
        "label": "库存文件路径",
        "type": "text",
        "placeholder": "/path/to/outlook_latest_50_with_rt.jsonl",
    },
    "outlook_rt_used_path": {
        "label": "已用记录路径（可选）",
        "type": "text",
        "placeholder": "默认 inventory.used",
    },
    "outlook_rt_client_id": {
        "label": "Client ID（可选）",
        "type": "text",
        "default": "9e5f94bc-e8a4-4e73-b8be-63364c29d753",
        "placeholder": "默认 Microsoft Authentication Broker",
    },
}
'''
new_fields = '''    "outlook_accounts_file": {
        "label": "库存文件路径",
        "type": "text",
        "default": "accounts/outlook_accounts.txt",
        "placeholder": "accounts/outlook_accounts.txt",
    },
    "outlook_state_file": {
        "label": "使用记录 outlook_state.json",
        "type": "text",
        "default": "accounts/outlook_state.json",
        "placeholder": "accounts/outlook_state.json",
    },
    "outlook_aliases_per_account": {
        "label": "每账号别名上限",
        "type": "text",
        "default": 10,
        "placeholder": "10",
    },
    "outlook_rt_client_id": {
        "label": "Client ID（可选）",
        "type": "text",
        "default": "9e5f94bc-e8a4-4e73-b8be-63364c29d753",
        "placeholder": "默认 Microsoft Authentication Broker",
    },
    # legacy keys kept for migration / connectivity fallback
    "outlook_rt_inventory": {
        "label": "旧库存路径（兼容）",
        "type": "text",
        "placeholder": "已弃用，请用 outlook_accounts_file",
    },
    "outlook_rt_used_path": {
        "label": "旧已用路径（兼容）",
        "type": "text",
        "placeholder": "已弃用，请用 outlook_state.json",
    },
}
'''
if old_fields not in st:
    raise SystemExit("field block missing")
st = st.replace(old_fields, new_fields, 1)

st = st.replace(
    '''    "outlook_rt": (
        "outlook_rt_inventory",
        "outlook_rt_used_path",
        "outlook_rt_client_id",
    ),
}
''',
    '''    "outlook_rt": (
        "outlook_accounts_file",
        "outlook_state_file",
        "outlook_aliases_per_account",
        "outlook_rt_client_id",
    ),
}
''',
    1,
)

st = st.replace(
    '''    if name in {"outlook_rt_inventory", "outlook_rt_used_path"}:
        text = _string(value)
        if text and any(ch in text for ch in "\\n\\r\\0"):
            raise EmailProviderConfigError("库存路径无效")
        return text
''',
    '''    if name in {
        "outlook_rt_inventory",
        "outlook_rt_used_path",
        "outlook_accounts_file",
        "outlook_state_file",
    }:
        text = _string(value)
        if text and any(ch in text for ch in "\\n\\r\\0"):
            raise EmailProviderConfigError("路径无效")
        return text
    if name == "outlook_aliases_per_account":
        text = _string(value) or str(definition.get("default") or 10)
        try:
            n = int(float(text))
        except Exception as exc:
            raise EmailProviderConfigError("别名上限必须是整数") from exc
        if n < 1 or n > 100:
            raise EmailProviderConfigError("别名上限范围 1-100")
        return n
''',
    1,
)

st = st.replace(
    '''    if provider == "outlook_rt":
        inventory = str(values.get("outlook_rt_inventory") or "").strip()
        return bool(inventory and Path(inventory).expanduser().is_file())
''',
    '''    if provider == "outlook_rt":
        accounts = str(values.get("outlook_accounts_file") or "").strip()
        if not accounts:
            accounts = str(values.get("outlook_rt_inventory") or "").strip()
        if not accounts:
            return False
        path = Path(accounts).expanduser()
        if not path.is_absolute():
            path = (ROOT / path).resolve()
        return path.is_file()
''',
    1,
)

# Append inventory helpers at end of file
if "def read_outlook_inventory" not in st:
    st = st.rstrip() + '''


# ── Outlook alias inventory / state (panel managed) ─────────────────────────

_ACCOUNT_LINE_RE = re.compile(r"^[^\\s@]+@[^\\s@]+\\.[^\\s@]+----.+----.+----.+$")


def _resolve_data_path(raw: object, default_rel: str) -> Path:
    text = str(raw or "").strip() or default_rel
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    else:
        path = path.resolve()
    # stay under project root for safety
    root = ROOT.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise EmailProviderConfigError("路径必须位于项目目录内") from exc
    return path


def _current_outlook_paths(raw: dict | None = None) -> tuple[Path, Path, int]:
    if raw is None:
        raw, _ = _read_unlocked()
    values = _merged(raw or {})
    accounts = _resolve_data_path(
        values.get("outlook_accounts_file") or values.get("outlook_rt_inventory"),
        "accounts/outlook_accounts.txt",
    )
    state = _resolve_data_path(
        values.get("outlook_state_file") or "accounts/outlook_state.json",
        "accounts/outlook_state.json",
    )
    try:
        cap = int(values.get("outlook_aliases_per_account") or 10)
    except Exception:
        cap = 10
    return accounts, state, max(1, min(100, cap))


def _mask_account_line(line: str) -> str:
    parts = [p.strip() for p in line.split("----")]
    if len(parts) < 4:
        return line
    email, password, client_id, refresh = parts[:0], parts[0], parts[1], parts[2], parts[3]
    # fix unpack
    email, password, client_id, refresh = parts[0], parts[1], parts[2], parts[3]
    pw = (password[:2] + "***") if password else "***"
    rt = refresh
    if len(rt) > 16:
        rt = rt[:8] + "..." + rt[-6:]
    return f"{email}----{pw}----{client_id}----{rt}"


def _parse_inventory_text(text: object) -> tuple[list[str], list[str]]:
    raw = str(text or "").replace("\\r\\n", "\\n").replace("\\r", "\\n")
    lines_out: list[str] = []
    errors: list[str] = []
    seen: set[str] = set()
    for idx, line in enumerate(raw.split("\\n"), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = [f.strip() for f in stripped.split("----") if f.strip() != "" or True]
        # keep empty middle fields out; require >=4 non-empty essential
        parts = [p.strip() for p in stripped.split("----")]
        if len(parts) < 4:
            errors.append(f"第 {idx} 行格式错误，需要 邮箱----密码----ClientID----RefreshToken")
            continue
        email, password, client_id, refresh = parts[:4]
        if "@" not in email or not client_id or not refresh:
            errors.append(f"第 {idx} 行缺少有效邮箱/ClientID/RefreshToken")
            continue
        key = email.casefold()
        if key in seen:
            errors.append(f"第 {idx} 行重复邮箱: {email}")
            continue
        seen.add(key)
        # preserve optional trailing fields
        lines_out.append("----".join(p.strip() for p in parts if p is not None))
    if not lines_out and not errors:
        errors.append("库存为空")
    return lines_out, errors


def read_outlook_inventory(*, mask: bool = False) -> dict:
    with exclusive_file_lock(LOCK_PATH):
        raw, error = _read_unlocked()
    if error:
        raise RuntimeError(f"config.json 无法读取: {error}")
    accounts_path, state_path, cap = _current_outlook_paths(raw)
    exists = accounts_path.is_file()
    text = accounts_path.read_text(encoding="utf-8") if exists else ""
    text = text.replace("\\r\\n", "\\n").replace("\\r", "\\n")
    valid_lines = []
    for line in text.split("\\n"):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        parts = [p.strip() for p in s.split("----")]
        if len(parts) >= 4 and "@" in parts[0]:
            valid_lines.append(s)
    display_lines = [_mask_account_line(x) for x in valid_lines] if mask else list(valid_lines)
    return {
        "ok": True,
        "path": str(accounts_path.relative_to(ROOT)).replace("\\\\", "/"),
        "abs_path": str(accounts_path),
        "exists": exists,
        "total_lines": len(valid_lines),
        "aliases_per_account": cap,
        "state_path": str(state_path.relative_to(ROOT)).replace("\\\\", "/"),
        "format": "email----password----client_id----refresh_token",
        "lines": display_lines,
        "text": "\\n".join(valid_lines) + ("\\n" if valid_lines else ""),
        "mtime": accounts_path.stat().st_mtime if exists else None,
    }


def write_outlook_inventory(text: object) -> dict:
    lines, errors = _parse_inventory_text(text)
    if errors and not lines:
        raise EmailProviderConfigError(errors[0])
    if errors:
        # hard fail on any bad line to avoid partial silent drop
        raise EmailProviderConfigError(errors[0])
    with exclusive_file_lock(LOCK_PATH):
        raw, error = _read_unlocked()
        if error:
            raise RuntimeError(f"config.json 无法读取: {error}")
        accounts_path, state_path, cap = _current_outlook_paths(raw)
        # ensure config points at alias files
        raw = dict(raw)
        raw["email_provider"] = "outlook_rt"
        raw["outlook_use_alias_pool"] = True
        raw["outlook_accounts_file"] = str(accounts_path.relative_to(ROOT)).replace("\\\\", "/")
        raw["outlook_state_file"] = str(state_path.relative_to(ROOT)).replace("\\\\", "/")
        raw["outlook_aliases_per_account"] = cap
        accounts_path.parent.mkdir(parents=True, exist_ok=True)
        payload = "\\n".join(lines) + "\\n"
        tmp = accounts_path.with_suffix(accounts_path.suffix + ".tmp")
        tmp.write_text(payload, encoding="utf-8", newline="\\n")
        tmp.replace(accounts_path)
        try:
            accounts_path.chmod(0o600)
        except Exception:
            pass
        atomic_write_json(CONFIG_PATH, raw)
    result = read_outlook_inventory(mask=False)
    result["written"] = len(lines)
    result["saved_at"] = _utc_now()
    return result


def read_outlook_state(*, limit: int = 500) -> dict:
    with exclusive_file_lock(LOCK_PATH):
        raw, error = _read_unlocked()
    if error:
        raise RuntimeError(f"config.json 无法读取: {error}")
    accounts_path, state_path, cap = _current_outlook_paths(raw)
    exists = state_path.is_file()
    data = {
        "version": 1,
        "next_account_cursor": 0,
        "allocation_serial": 0,
        "accounts": {},
    }
    if exists:
        import json

        loaded = json.loads(state_path.read_text(encoding="utf-8") or "{}")
        if isinstance(loaded, dict):
            data.update(loaded)
            if not isinstance(data.get("accounts"), dict):
                data["accounts"] = {}
    rows = []
    used_slots = 0
    disabled = 0
    for key, item in (data.get("accounts") or {}).items():
        if not isinstance(item, dict):
            continue
        email = str(item.get("email") or key)
        nxt = int(item.get("next_alias_index", 0) or 0)
        dis = bool(item.get("disabled", False))
        if dis:
            disabled += 1
        used_slots += min(max(nxt, 0), cap)
        rows.append(
            {
                "email": email,
                "next_alias_index": nxt,
                "last_alias": str(item.get("last_alias") or ""),
                "disabled": dis,
                "last_allocated_at": str(item.get("last_allocated_at") or ""),
            }
        )
    rows.sort(key=lambda r: (r["disabled"], -(r["next_alias_index"] or 0), r["email"].lower()))
    keep = max(20, min(5000, int(limit or 500)))
    return {
        "ok": True,
        "path": str(state_path.relative_to(ROOT)).replace("\\\\", "/"),
        "abs_path": str(state_path),
        "exists": exists,
        "format": "outlook_state.json",
        "aliases_per_account": cap,
        "next_account_cursor": int(data.get("next_account_cursor", 0) or 0),
        "allocation_serial": int(data.get("allocation_serial", 0) or 0),
        "account_count": len(rows),
        "disabled_count": disabled,
        "used_alias_slots": used_slots,
        "remaining_alias_slots_est": max(0, len(rows) * cap - used_slots) if rows else 0,
        "accounts": rows[:keep],
        "truncated": len(rows) > keep,
        "total_accounts": len(rows),
        "mtime": state_path.stat().st_mtime if exists else None,
    }
'''
    # Fix the botched mask function and escape issues - I used double escaped newlines in strings which is wrong for file content
    # Let me rewrite the append section cleanly by writing a separate module instead
    store.write_text(st.split("# ── Outlook alias inventory")[0].rstrip() + "\n", encoding="utf-8", newline="\n")
    print("store base fields updated; helpers go to separate file")
else:
    store.write_text(st, encoding="utf-8", newline="\n")
    print("store updated with helpers already present?")

print("store path", store)
