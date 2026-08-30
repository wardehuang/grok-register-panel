# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

ROOT = Path(r"E:/AI/grok-register-panel")
path = ROOT / "grok_register_ttk.py"
text = path.read_text(encoding="utf-8")

# 1) import integrations_push near other imports
needle = "from batch_traffic import mark_successful_account\n"
insert = (
    "from batch_traffic import mark_successful_account\n"
    "import integrations_push\n"
    "try:\n"
    "    from run_log import append_run_log, start_run_log, stop_run_log\n"
    "except Exception:  # pragma: no cover\n"
    "    def start_run_log(kind=\"cli\"):\n"
    "        return None\n"
    "    def append_run_log(line):\n"
    "        return None\n"
    "    def stop_run_log():\n"
    "        return None\n"
)
if "import integrations_push" not in text:
    if needle not in text:
        raise SystemExit("import anchor missing")
    text = text.replace(needle, insert, 1)

# 2) DEFAULT_CONFIG fields
old_cfg = '''    "outlook_rt_inventory": "",
    "outlook_rt_used_path": "",
    "outlook_rt_client_id": outlook_rt_provider.DEFAULT_CLIENT_ID,
    # 账号间注册间隔（秒），0=不等待。填一个整数=N秒固定等待，填区间"60-120"=随机等待
    "account_interval": "60-120",
}
'''
new_cfg = '''    "outlook_rt_inventory": "",
    "outlook_rt_used_path": "",
    "outlook_rt_client_id": outlook_rt_provider.DEFAULT_CLIENT_ID,
    # Outlook/Hotmail plus 别名（lite-patch 兼容 state）
    "outlook_accounts_file": "accounts/outlook_accounts.txt",
    "outlook_state_file": "accounts/outlook_state.json",
    "outlook_aliases_per_account": 10,
    "outlook_use_alias_pool": True,
    # 账号间注册间隔（秒），0=不等待。填一个整数=N秒固定等待，填区间"60-120"=随机等待
    "account_interval": "60-120",
    # 创建间隔（秒）别名；优先 account_interval
    "register_interval_sec": 0,
    # 顺序 IP 游标（一号一口）；早失败可回退
    "proxy_cursor_enabled": True,
    "proxy_pool_file": "proxies.txt",
    "proxy_pool_state_file": "log/proxy_pool_state.json",
    # 启动不测活 / 不拦 xAI 预检
    "skip_connectivity_precheck": True,
    "skip_xai_signup_precheck": True,
    # grok2api 远端 Web/Console SSO 推送
    "grok2api_auto_add_remote": True,
    "grok2api_remote_base": "",
    "grok2api_remote_username": "",
    "grok2api_remote_password": "",
    "grok2api_remote_app_key": "",
    "grok2api_remote_retries": 3,
    "grok2api_remote_retry_sleep_sec": 2.0,
}
'''
if old_cfg not in text:
    raise SystemExit("DEFAULT_CONFIG block missing")
text = text.replace(old_cfg, new_cfg, 1)

# 3) Replace add_sso_to_cpa docstring path + insert pipeline helper before it
pipeline = '''
def push_sso_to_grok2api_remotes(raw_token, email="", log_callback=None) -> dict:
    """拿到 SSO 后立刻推 grok2api Web + Console（不换 CPA token）。"""
    return integrations_push.push_sso_to_grok2api_web_and_console(
        http_post,
        config,
        raw_token,
        email=email,
        log_callback=log_callback,
    )


def finalize_sso_after_register(raw_token, email="", log_callback=None) -> bool:
    """SSO 后处理顺序：
    1) 推送 grok2api Web + Console
    2) 风控检查 botFlagSource
    3) 通过才 Device Flow 换 token 并写/推 CPA
    风控失败：已推 g2a，不推 CPA，抛 RegistrationRiskDenied
    """
    sso = _normalize_sso_token(raw_token)
    if not sso:
        return False
    try:
        push_sso_to_grok2api_remotes(sso, email=email, log_callback=log_callback)
    except Exception as exc:
        if log_callback:
            log_callback(f"[!] grok2api 推送异常（继续风控/CPA）: {exc}")
    # 风控：失败则不再 CPA
    ensure_sso_oauth_eligible(sso, email=email, log_callback=log_callback)
    return add_sso_to_cpa(sso, email=email, log_callback=log_callback)


'''

marker = "def add_sso_to_cpa(raw_token, email=\"\", log_callback=None) -> bool:\n"
if "def finalize_sso_after_register" not in text:
    if marker not in text:
        raise SystemExit("add_sso_to_cpa missing")
    text = text.replace(marker, pipeline + marker, 1)

# 4) Rewrite add_sso_to_cpa docstring to CPA-only after risk
text = text.replace(
    '    """SSO → Device Flow（失败回退授权码）换 token → 写入 CPA / Grok2API。\n\n'
    "    返回 True 表示入库成功（或未开启/无需转换）；False 表示转换失败（SSO 仍可能已写入 accounts）。\n"
    '    """\n',
    '    """SSO → Device Flow 换 token → 写入 CPA（本地/远程）。\n\n'
    "    调用前应已完成 grok2api SSO 推送与风控检查。\n"
    "    返回 True 表示 CPA 入库成功（或未开启）；False 表示转换失败。\n"
    '    """\n',
    1,
)

# 5) In add_sso_to_cpa, skip local grok2api_auth_dir write as primary g2a path
# Keep local g2a write optional but after CPA - leave as is for now

# 6) Replace call sites: ensure + add_sso -> finalize
replacements = [
    (
        "                    ensure_sso_oauth_eligible(sso, email=email, log_callback=wlog)\n"
        "                    if config.get(\"enable_nsfw\", True):\n",
        "                    if config.get(\"enable_nsfw\", True):\n",
    ),
    (
        "                    cpa_ok = add_sso_to_cpa(sso, email=email, log_callback=wlog)\n"
        "                    self._record_success()\n",
        "                    cpa_ok = finalize_sso_after_register(sso, email=email, log_callback=wlog)\n"
        "                    self._record_success()\n",
    ),
    (
        "                        ensure_sso_oauth_eligible(\n"
        "                            sso,\n"
        "                            email=email,\n"
        "                            log_callback=lambda m: cli_log(f\"[W{wid+1}] {m}\"),\n"
        "                        )\n"
        "                        if config.get(\"enable_nsfw\", True):\n",
        "                        if config.get(\"enable_nsfw\", True):\n",
    ),
    (
        "                        cpa_ok = add_sso_to_cpa(\n"
        "                            sso, email=email, log_callback=lambda m: cli_log(f\"[W{wid+1}] {m}\")\n"
        "                        )\n",
        "                        cpa_ok = finalize_sso_after_register(\n"
        "                            sso, email=email, log_callback=lambda m: cli_log(f\"[W{wid+1}] {m}\")\n"
        "                        )\n",
    ),
    (
        "                ensure_sso_oauth_eligible(sso, email=email, log_callback=cli_log)\n"
        "                if config.get(\"enable_nsfw\", True):\n",
        "                if config.get(\"enable_nsfw\", True):\n",
    ),
    (
        "                cpa_ok = add_sso_to_cpa(sso, email=email, log_callback=cli_log)\n"
        "                success_count += 1\n",
        "                cpa_ok = finalize_sso_after_register(sso, email=email, log_callback=cli_log)\n"
        "                success_count += 1\n",
    ),
]
for old, new in replacements:
    if old not in text:
        print("WARN missing block:", old[:80].replace("\n", " "))
        continue
    text = text.replace(old, new, 1)

# 7) skip connectivity / xai precheck in CLI startup
old_pre = '''        startup_checks = _conn.run_connectivity_checks(startup_config, http_get, http_post)
'''
# find unique context around require_xai
if "skip_connectivity_precheck" not in text:
    # wrap CLI precheck section
    old_block = '''        startup_checks = _conn.run_connectivity_checks(startup_config, http_get, http_post)
        for name, ok, detail in startup_checks:
            mark = "OK" if ok else "FAIL"
            cli_log(f"[检查] [{mark}] {name}: {detail}")
        try:
            _record_proxy_precheck_failure(
'''
    # softer search
    idx = text.find("startup_checks = _conn.run_connectivity_checks")
    if idx < 0:
        print("WARN no startup_checks")
    else:
        # insert guard before this line once
        guard = (
            "        if config.get(\"skip_connectivity_precheck\", True):\n"
            "            cli_log(\"[*] 已跳过启动连通性/测活预检 (skip_connectivity_precheck)\")\n"
            "            startup_checks = []\n"
            "        else:\n"
            "            startup_checks = _conn.run_connectivity_checks(startup_config, http_get, http_post)\n"
        )
        # replace only first occurrence carefully
        line_end = text.find("\n", idx)
        old_line = text[idx:line_end+1]
        # Need to indent the for loop under else - too invasive. Simpler: short-circuit require_xai
        text = text.replace(
            old_line,
            "        startup_checks = [] if config.get(\"skip_connectivity_precheck\", True) else _conn.run_connectivity_checks(startup_config, http_get, http_post)\n"
            "        if config.get(\"skip_connectivity_precheck\", True):\n"
            "            cli_log(\"[*] 已跳过启动连通性/测活预检\")\n",
            1,
        )
        text = text.replace(
            "            _conn.require_xai_signup(startup_checks)\n",
            "            if not config.get(\"skip_xai_signup_precheck\", True):\n"
            "                _conn.require_xai_signup(startup_checks)\n"
            "            else:\n"
            "                cli_log(\"[*] 已跳过 xAI 注册页预检\")\n",
            1,
        )

# 8) hook cli_log / GUI log to run_log if present - find def cli_log
if "append_run_log(" not in text:
    # patch common log path used by record - find "def cli_log" may not exist; controller logs
    # Add wrapper after imports is enough if we patch places that define local cli_log
    pass

# 9) start_run_log at CLI batch start
if "start_run_log(" not in text:
    # near terminal mode start message
    anchor = 'cli_log(f"[*] 终端模式启动，目标数量: {count}'
    pos = text.find(anchor)
    if pos >= 0:
        # find beginning of line
        line_start = text.rfind("\n", 0, pos) + 1
        indent = text[line_start:pos]
        text = text[:line_start] + f'{indent}_run_log_path = start_run_log("cli")\n{indent}if _run_log_path:\n{indent}    cli_log(f"[*] run log -> {{_run_log_path}}")\n' + text[line_start:]
    # stop at end - search finally near main
    if "stop_run_log()" not in text:
        # add atexit
        text = text.replace(
            "import integrations_push\n",
            "import integrations_push\nimport atexit\natexit.register(lambda: stop_run_log())\n",
            1,
        )

path.write_text(text, encoding="utf-8", newline="\n")
print("patched grok_register_ttk.py", path.stat().st_size)
