# -*- coding: utf-8 -*-
from pathlib import Path

ROOT = Path(r"E:/AI/grok-register-panel")

# ── email_provider_store fields ─────────────────────────────────────────────
p = ROOT / "webui" / "email_provider_store.py"
t = p.read_text(encoding="utf-8")
t = t.replace(
    '"outlook_rt": "Outlook RT 库存"',
    '"outlook_rt": "Outlook / Hotmail 别名库存"',
)

old = '''    "outlook_rt_inventory": {
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
new = '''    "outlook_accounts_file": {
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
}
'''
if old not in t:
    if "outlook_accounts_file" not in t:
        raise SystemExit("fields block missing")
else:
    t = t.replace(old, new, 1)

t = t.replace(
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

old_norm = '''    if name in {"outlook_rt_inventory", "outlook_rt_used_path"}:
        text = _string(value)
        if text and any(ch in text for ch in "\\n\\r\\0"):
            raise EmailProviderConfigError("库存路径无效")
        return text
'''
new_norm = '''    if name in {
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
'''
if old_norm not in t:
    if "outlook_aliases_per_account" not in t or "别名上限必须是整数" not in t:
        # try without double escape
        old_norm2 = old_norm.replace("\\\\n", "\\n").replace("\\\\r", "\\r").replace("\\\\0", "\\0")
        # actual file has real newlines in string
        import re
        m = re.search(
            r'if name in \{"outlook_rt_inventory", "outlook_rt_used_path"\}:.*?return text\n',
            t,
            re.S,
        )
        if m and "outlook_accounts_file" not in m.group(0):
            t = t[: m.start()] + new_norm + t[m.end() :]
            print("norm via regex")
        elif "outlook_accounts_file" in t and "别名上限必须是整数" in t:
            print("norm already")
        else:
            print("WARN norm not patched", bool(m))
    else:
        print("norm already present")
else:
    t = t.replace(old_norm, new_norm, 1)

old_cfg = '''    if provider == "outlook_rt":
        inventory = str(values.get("outlook_rt_inventory") or "").strip()
        return bool(inventory and Path(inventory).expanduser().is_file())
'''
new_cfg = '''    if provider == "outlook_rt":
        accounts = str(values.get("outlook_accounts_file") or "").strip()
        if not accounts:
            accounts = str(values.get("outlook_rt_inventory") or "").strip()
        if not accounts:
            return False
        path = Path(accounts).expanduser()
        if not path.is_absolute():
            path = (ROOT / path).resolve()
        return path.is_file()
'''
if old_cfg in t:
    t = t.replace(old_cfg, new_cfg, 1)
elif "outlook_accounts_file" in t and "outlook_rt" in t:
    print("configured check maybe already")
else:
    print("WARN configured")

# save also sets outlook_use_alias_pool when saving outlook_rt
if "outlook_use_alias_pool" not in t[t.find("def save_email_provider_config"): t.find("def save_email_provider_config")+800]:
    t = t.replace(
        '''        updated = _candidate_config(raw, provider, settings, clear_secrets)
        atomic_write_json(CONFIG_PATH, updated)
''',
        '''        updated = _candidate_config(raw, provider, settings, clear_secrets)
        if str(updated.get("email_provider") or "") == "outlook_rt":
            updated["outlook_use_alias_pool"] = True
            if not str(updated.get("outlook_accounts_file") or "").strip():
                updated["outlook_accounts_file"] = "accounts/outlook_accounts.txt"
            if not str(updated.get("outlook_state_file") or "").strip():
                updated["outlook_state_file"] = "accounts/outlook_state.json"
        atomic_write_json(CONFIG_PATH, updated)
''',
        1,
    )

p.write_text(t, encoding="utf-8", newline="\n")
print("email_provider_store ok")

# ── connectivity ────────────────────────────────────────────────────────────
c = ROOT / "connectivity.py"
ct = c.read_text(encoding="utf-8")
old_c = '''        if provider == "outlook_rt":
            from email_providers import outlook_rt as outlook_rt_provider

            inv = str(config.get("outlook_rt_inventory") or "").strip()
            if not inv:
                return "邮箱API", False, "未配置 outlook_rt_inventory"
            used = str(config.get("outlook_rt_used_path") or "").strip()
            client_id = str(config.get("outlook_rt_client_id") or "").strip()
            detail = outlook_rt_provider.probe_inventory(
'''
# read actual block
idx = ct.find('if provider == "outlook_rt":')
print("connectivity idx", idx)
if idx > 0:
    snippet = ct[idx:idx+500]
    print(snippet[:400])

# simpler replace for inventory path only
if '未配置 outlook_rt_inventory' in ct:
    ct = ct.replace(
        '''            inv = str(config.get("outlook_rt_inventory") or "").strip()
            if not inv:
                return "邮箱API", False, "未配置 outlook_rt_inventory"
            used = str(config.get("outlook_rt_used_path") or "").strip()
            client_id = str(config.get("outlook_rt_client_id") or "").strip()
            detail = outlook_rt_provider.probe_inventory(
''',
        '''            inv = str(
                config.get("outlook_accounts_file")
                or config.get("outlook_rt_inventory")
                or ""
            ).strip()
            if not inv:
                return "邮箱API", False, "未配置 outlook_accounts_file"
            # alias pool uses outlook_state.json; probe still accepts used path for legacy jsonl mode
            used = str(
                config.get("outlook_state_file")
                or config.get("outlook_rt_used_path")
                or ""
            ).strip()
            client_id = str(config.get("outlook_rt_client_id") or "").strip()
            # alias inventory is txt not jsonl — short-circuit count check
            from pathlib import Path as _P
            inv_path = _P(inv)
            if not inv_path.is_absolute():
                inv_path = (_P(__file__).resolve().parent / inv_path)
            if inv_path.suffix.lower() in {".txt", ""} or "outlook_accounts" in inv_path.name:
                if not inv_path.is_file():
                    return "邮箱API", False, f"库存文件不存在: {inv}"
                lines = [
                    ln.strip()
                    for ln in inv_path.read_text(encoding="utf-8", errors="replace").splitlines()
                    if ln.strip() and not ln.strip().startswith("#")
                ]
                valid = [ln for ln in lines if ln.count("----") >= 3 and "@" in ln.split("----", 1)[0]]
                return (
                    "邮箱API",
                    bool(valid),
                    f"Outlook 别名库存 {len(valid)} 条" + ("" if valid else "（空）"),
                )
            detail = outlook_rt_provider.probe_inventory(
''',
        1,
    )
    c.write_text(ct, encoding="utf-8", newline="\n")
    print("connectivity ok")
else:
    print("connectivity already or missing")
