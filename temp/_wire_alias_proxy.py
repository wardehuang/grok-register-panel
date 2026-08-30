# -*- coding: utf-8 -*-
"""Wire alias pool + proxy cursor + run log + config defaults into panel."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(r"E:/AI/grok-register-panel")

# ── 1) outlook_rt bind_session + alias-aware mark ────────────────────────────
ort = ROOT / "email_providers" / "outlook_rt.py"
ot = ort.read_text(encoding="utf-8")
if "def bind_external_session" not in ot:
    helper = '''

def bind_external_session(
    *,
    alias_email: str,
    account: Dict[str, str],
    lease_token: str = "",
    default_client_id: str = "",
    alias_mode: bool = True,
) -> str:
    """Bind a pre-allocated alias session into the RT token map.

    account must include primary mailbox email + refresh_token (+ client_id).
    alias_email is the address submitted to xAI.
    """
    email = str(alias_email or "").strip()
    if not email or "@" not in email:
        raise Exception("alias_email 无效")
    acc = dict(account or {})
    if not acc.get("email"):
        raise Exception("primary account.email 缺失")
    if not acc.get("refresh_token"):
        raise Exception("primary refresh_token 缺失")
    if not acc.get("client_id") and default_client_id:
        acc["client_id"] = default_client_id
    token_key = "outlook_alias:" + secrets.token_urlsafe(12)
    with _lock:
        _token_map[token_key] = {
            "account": acc,
            "email": email,
            "inventory_path": "",
            "used_path": "",
            "default_client_id": default_client_id or DEFAULT_CLIENT_ID,
            "created_at": time.time(),
            "refresh_failures": 0,
            "alias_mode": bool(alias_mode),
            "lease_token": str(lease_token or ""),
            "primary_email": str(acc.get("email") or ""),
        }
        _reserved.add(email.lower())
    return token_key


def peek_session(token_key: str = "", email: str = "") -> Dict[str, Any]:
    info = _resolve_session(token_key, email)
    return dict(info)

'''
    # insert before take_mailbox
    anchor = "\ndef take_mailbox(\n"
    if anchor not in ot:
        raise SystemExit("take_mailbox anchor missing")
    ot = ot.replace(anchor, helper + anchor, 1)

    # mark_used skip when alias_mode in wait path - patch _retire and mark_on_success
    ot = ot.replace(
        "    def _retire(reason: str) -> None:\n"
        "        if not inventory_path:\n"
        "            return\n",
        "    def _retire(reason: str) -> None:\n"
        "        if info.get(\"alias_mode\"):\n"
        "            release_reservation(token_key, email)\n"
        "            return\n"
        "        if not inventory_path:\n"
        "            return\n",
        1,
    )
    ot = ot.replace(
        "        if code:\n"
        "            if mark_on_success and inventory_path:\n",
        "        if code:\n"
        "            if mark_on_success and inventory_path and not info.get(\"alias_mode\"):\n",
        1,
    )
    ort.write_text(ot, encoding="utf-8", newline="\n")
    print("outlook_rt bind_external_session OK")

# ── 2) grok_register_ttk outlook_alias + proxy cursor + run_log hooks ────────
gpath = ROOT / "grok_register_ttk.py"
gt = gpath.read_text(encoding="utf-8")

if "from email_providers import outlook_alias_pool" not in gt:
    gt = gt.replace(
        "from email_providers import outlook_rt as outlook_rt_provider\n",
        "from email_providers import outlook_rt as outlook_rt_provider\n"
        "from email_providers import outlook_alias_pool as outlook_alias_pool_mod\n"
        "import proxy_cursor as proxy_cursor_mod\n",
        1,
    )

# alias take helpers after get_outlook_rt_client_id
if "def outlook_alias_take_mailbox" not in gt:
    block = '''
_alias_pool_singleton = None
_proxy_cursor_singleton = None
_proxy_cursor_leases = {}  # worker_id -> index
_proxy_cursor_lease_lock = threading.Lock()


def _resolve_path_cfg(key: str, default: str = "") -> str:
    raw = str(config.get(key, default) or default).strip()
    if not raw:
        return ""
    if os.path.isabs(raw):
        return raw
    return os.path.join(APP_DIR, raw)


def _get_outlook_alias_pool():
    global _alias_pool_singleton
    if _alias_pool_singleton is not None:
        return _alias_pool_singleton
    accounts = _resolve_path_cfg("outlook_accounts_file", "accounts/outlook_accounts.txt")
    state = _resolve_path_cfg("outlook_state_file", "accounts/outlook_state.json")
    try:
        cap = int(config.get("outlook_aliases_per_account", 10) or 10)
    except Exception:
        cap = 10
    _alias_pool_singleton = outlook_alias_pool_mod.OutlookAliasPool.build(
        accounts, state, aliases_per_account=max(1, cap)
    )
    return _alias_pool_singleton


def outlook_alias_take_mailbox():
    pool = _get_outlook_alias_pool()
    alias_email, lease = pool.allocate_alias()
    primary = pool.get_account_for_alias_lease(lease, alias_email)
    account = {
        "email": primary.email,
        "password": primary.password,
        "client_id": primary.client_id or get_outlook_rt_client_id(),
        "refresh_token": primary.refresh_token,
    }
    token_key = outlook_rt_provider.bind_external_session(
        alias_email=alias_email,
        account=account,
        lease_token=lease,
        default_client_id=get_outlook_rt_client_id(),
        alias_mode=True,
    )
    # optional refresh precheck
    try:
        outlook_rt_provider.refresh_access_token(
            http_post,
            account,
            default_client_id=get_outlook_rt_client_id(),
        )
    except Exception as exc:
        try:
            pool.release_alias(lease, alias_email)
        except Exception:
            pass
        try:
            outlook_rt_provider.release_reservation(token_key, alias_email)
        except Exception:
            pass
        raise Exception(f"Outlook 别名主号 refresh 失败: {exc}") from exc
    return alias_email, token_key


def release_outlook_alias_if_needed(email: str = "", dev_token: str = "") -> None:
    """Early-fail: roll back latest alias allocation when possible."""
    try:
        info = outlook_rt_provider.peek_session(dev_token, email)
    except Exception:
        return
    if not info.get("alias_mode"):
        try:
            outlook_rt_provider.release_reservation(dev_token, email)
        except Exception:
            pass
        return
    lease = str(info.get("lease_token") or "")
    alias = str(info.get("email") or email or "")
    try:
        pool = _get_outlook_alias_pool()
        if lease and alias:
            pool.release_alias(lease, alias)
    except Exception:
        pass
    try:
        outlook_rt_provider.release_reservation(dev_token, email)
    except Exception:
        pass


def _get_proxy_cursor():
    global _proxy_cursor_singleton
    if not config.get("proxy_cursor_enabled", True):
        return None
    pool_file = _resolve_path_cfg("proxy_pool_file", "proxies.txt")
    state_file = _resolve_path_cfg("proxy_pool_state_file", "log/proxy_pool_state.json")
    if not pool_file or not os.path.isfile(pool_file):
        return None
    if _proxy_cursor_singleton is None:
        try:
            _proxy_cursor_singleton = proxy_cursor_mod.ProxyCursor(pool_file, state_file)
        except Exception:
            return None
    return _proxy_cursor_singleton


def release_proxy_cursor_for_worker(worker_id: int, log_callback=None) -> None:
    with _proxy_cursor_lease_lock:
        idx = _proxy_cursor_leases.pop(int(worker_id), None)
    if idx is None:
        return
    cursor = _get_proxy_cursor()
    if cursor is None:
        return
    try:
        cursor.release_allocation(int(idx), log_callback=log_callback)
    except Exception:
        pass


'''
    anchor = "def outlook_rt_take_mailbox():\n"
    if anchor not in gt:
        raise SystemExit("outlook_rt_take_mailbox missing")
    gt = gt.replace(anchor, block + anchor, 1)

# change outlook_rt_take_mailbox body start
old_take = '''def outlook_rt_take_mailbox():
    inv = get_outlook_rt_inventory()
'''
new_take = '''def outlook_rt_take_mailbox():
    if config.get("outlook_use_alias_pool", True):
        accounts = _resolve_path_cfg("outlook_accounts_file", "accounts/outlook_accounts.txt")
        state = _resolve_path_cfg("outlook_state_file", "accounts/outlook_state.json")
        if accounts and state and os.path.isfile(accounts):
            return outlook_alias_take_mailbox()
    inv = get_outlook_rt_inventory()
'''
if old_take not in gt:
    print("WARN take body not replaced")
else:
    gt = gt.replace(old_take, new_take, 1)

# pick_proxy_for_worker: try cursor first
old_pick = '''def pick_proxy_for_worker(worker_id: int, rotate_idx: int = 0) -> str:
    """账号边界选口：跳过别人占用的，优先 40 分钟内没出现过的出口 IP。

    风控前置：当前口的 IP 若在窗口内用过，即使 rotate_idx=0 也换一条冷 IP。
    """
    pool = load_proxy_pool()
'''
new_pick = '''def pick_proxy_for_worker(worker_id: int, rotate_idx: int = 0) -> str:
    """账号边界选口：优先顺序游标一号一口；否则走托管池冷 IP 逻辑。"""
    cursor = _get_proxy_cursor()
    if cursor is not None:
        wid = max(0, int(worker_id))
        # rotate: release previous cursor slot then allocate next
        if int(rotate_idx or 0) > 0:
            release_proxy_cursor_for_worker(wid)
        try:
            url, index = cursor.allocate()
        except Exception as exc:
            raise RuntimeError(f"代理游标分配失败: {exc}") from exc
        with _proxy_cursor_lease_lock:
            _proxy_cursor_leases[wid] = int(index)
        with _proxy_lease_lock:
            _proxy_leases[wid] = url
        return url
    pool = load_proxy_pool()
'''
if old_pick not in gt:
    print("WARN pick_proxy not replaced")
else:
    gt = gt.replace(old_pick, new_pick, 1)

# parse_account_interval: also honor register_interval_sec
if "register_interval_sec" not in gt[gt.find("def parse_account_interval"):gt.find("def parse_account_interval")+800]:
    gt = gt.replace(
        '    raw = str(config.get("account_interval", "0") or "0").strip()\n',
        '    raw = str(config.get("account_interval", "0") or "0").strip()\n'
        '    if (not raw or raw == "0") and config.get("register_interval_sec"):\n'
        '        raw = str(config.get("register_interval_sec") or "0").strip()\n',
        1,
    )

# hook append_run_log into redact log path used by cli - find common print path
# Patch record_register_result log_callback wrappers is hard; instead patch a central helper if any.
# Wrap controller cli_log at definition sites is many. Add to redact_sensitive_log_line callers?
# Simpler: monkeypatch after cli_log assignment in main - look for "def _cli_print"
if "append_run_log(line)" not in gt:
    # After import run_log already; patch common.py? 
    # Inject into browser log via:
    pass

# Ensure start_run_log near CLI start if missing
if "start_run_log(" not in gt:
    gt = gt.replace(
        'cli_log(f"[*] 终端模式启动，目标数量: {count}',
        '_rl = start_run_log("cli")\n'
        '    if _rl:\n'
        '        cli_log(f"[*] run log -> {_rl}")\n'
        '    cli_log(f"[*] 终端模式启动，目标数量: {count}',
        1,
    )

# Make cli_log append to run log - find "def cli_log" in main block
# Search pattern used in headless
import re
m = re.search(r"def cli_log\(([^)]*)\):\n(\s+)(.*\n(?:\2.*\n){0,8})", gt)
if m and "append_run_log" not in m.group(0):
    # find first simple cli_log
    pass

# Patch: after "def cli_log(msg):" body first line insert append
gt2 = gt
# multiple cli_log defs - use replace_all carefully
if gt.count("def cli_log(") >= 1:
    # only wrap by patching a shared helper near top after imports
    if "def _emit_cli_log(" not in gt:
        gt = gt.replace(
            "def finalize_sso_after_register(raw_token, email=\"\", log_callback=None) -> bool:\n",
            "def _tee_run_log(message: str) -> None:\n"
            "    try:\n"
            "        append_run_log(str(message or \"\"))\n"
            "    except Exception:\n"
            "        pass\n\n\n"
            "def finalize_sso_after_register(raw_token, email=\"\", log_callback=None) -> bool:\n",
            1,
        )
    # In finalize_sso, wrap log_callback - optional

gpath.write_text(gt, encoding="utf-8", newline="\n")
print("grok_register_ttk integration size", gpath.stat().st_size)

# ── 3) config.json / example ────────────────────────────────────────────────
cfg_path = ROOT / "config.json"
cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
cfg.update({
    "email_provider": "outlook_rt",
    "outlook_use_alias_pool": True,
    "outlook_accounts_file": "accounts/outlook_accounts.txt",
    "outlook_state_file": "accounts/outlook_state.json",
    "outlook_aliases_per_account": 10,
    "account_interval": "60-120",
    "register_interval_sec": 0,
    "proxy_cursor_enabled": True,
    "proxy_pool_file": "proxies.txt",
    "proxy_pool_state_file": "log/proxy_pool_state.json",
    "skip_connectivity_precheck": True,
    "skip_xai_signup_precheck": True,
    "cpa_auto_add": True,
    "cpa_remote_url": cfg.get("cpa_remote_url") or "",
    "cpa_management_key": cfg.get("cpa_management_key") or "",
    "grok2api_auto_add_remote": True,
    "grok2api_remote_base": cfg.get("grok2api_remote_base") or "",
    "grok2api_remote_username": cfg.get("grok2api_remote_username") or "",
    "grok2api_remote_password": cfg.get("grok2api_remote_password") or "",
    "grok2api_remote_app_key": cfg.get("grok2api_remote_app_key") or "",
    "grok2api_remote_retries": 3,
    "grok2api_remote_retry_sleep_sec": 2.0,
})
cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
print("config.json updated")

ex = ROOT / "config.example.json"
exd = json.loads(ex.read_text(encoding="utf-8"))
exd.update({
    "outlook_use_alias_pool": True,
    "outlook_accounts_file": "accounts/outlook_accounts.txt",
    "outlook_state_file": "accounts/outlook_state.json",
    "outlook_aliases_per_account": 10,
    "register_interval_sec": 0,
    "proxy_cursor_enabled": True,
    "proxy_pool_file": "proxies.txt",
    "proxy_pool_state_file": "log/proxy_pool_state.json",
    "skip_connectivity_precheck": True,
    "skip_xai_signup_precheck": True,
    "grok2api_auto_add_remote": True,
    "grok2api_remote_base": "",
    "grok2api_remote_username": "",
    "grok2api_remote_password": "",
    "grok2api_remote_app_key": "",
    "grok2api_remote_retries": 3,
    "grok2api_remote_retry_sleep_sec": 2.0,
})
ex.write_text(json.dumps(exd, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
print("config.example.json updated")
print("DONE")
