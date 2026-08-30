# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

ROOT = Path(r"E:/AI/grok-register-panel")

# ── grok_register_ttk: proxy lease rewind + alias release + run log tee ──────
gpath = ROOT / "grok_register_ttk.py"
gt = gpath.read_text(encoding="utf-8")

old_rel = '''def release_proxy_lease(worker_id: int | None = None) -> None:
    """释放某个 worker 占用的出口；worker_id 为空则清空。"""
    with _proxy_lease_lock:
        if worker_id is None:
            _proxy_leases.clear()
            return
        _proxy_leases.pop(int(worker_id), None)
'''
new_rel = '''def release_proxy_lease(worker_id: int | None = None, *, rewind: bool = True) -> None:
    """释放 worker 代理占用。

    rewind=True（默认，早失败）：回退 IP 游标。
    rewind=False（注册成功）：只清占用，不回退游标。
    """
    if worker_id is None:
        with _proxy_lease_lock:
            _proxy_leases.clear()
        with _proxy_cursor_lease_lock:
            idxs = list(_proxy_cursor_leases.items())
            _proxy_cursor_leases.clear()
        if rewind:
            for wid, idx in idxs:
                try:
                    cur = _get_proxy_cursor()
                    if cur is not None:
                        cur.release_allocation(int(idx))
                except Exception:
                    pass
        return
    wid = int(worker_id)
    with _proxy_lease_lock:
        _proxy_leases.pop(wid, None)
    if rewind:
        release_proxy_cursor_for_worker(wid)
    else:
        with _proxy_cursor_lease_lock:
            _proxy_cursor_leases.pop(wid, None)
'''
if old_rel not in gt:
    print("WARN release_proxy_lease block missing")
else:
    gt = gt.replace(old_rel, new_rel, 1)

# On success paths, commit proxy without rewind before next account
# GUI success after finalize:
old_ok_gui = '''                    cpa_ok = finalize_sso_after_register(sso, email=email, log_callback=wlog)
                    self._record_success()
'''
new_ok_gui = '''                    cpa_ok = finalize_sso_after_register(sso, email=email, log_callback=wlog)
                    try:
                        release_proxy_lease(getattr(self, "_worker_id", 0), rewind=False)
                    except Exception:
                        pass
                    self._record_success()
'''
if old_ok_gui in gt:
    gt = gt.replace(old_ok_gui, new_ok_gui, 1)
else:
    print("WARN gui success block missing")

# CLI multiworker success
old_ok_mw = '''                        cpa_ok = finalize_sso_after_register(
                            sso, email=email, log_callback=lambda m: cli_log(f"[W{wid+1}] {m}")
                        )
'''
# find after that commit
if "finalize_sso_after_register" in gt:
    # insert commit after multiworker finalize - more careful
    needle = '''                        cpa_ok = finalize_sso_after_register(
                            sso, email=email, log_callback=lambda m: cli_log(f"[W{wid+1}] {m}")
                        )
'''
    if needle in gt and "release_proxy_lease(wid, rewind=False)" not in gt:
        gt = gt.replace(
            needle,
            needle + "                        try:\n                            release_proxy_lease(wid, rewind=False)\n                        except Exception:\n                            pass\n",
            1,
        )

# single CLI success
old_ok_cli = '''                cpa_ok = finalize_sso_after_register(sso, email=email, log_callback=cli_log)
                success_count += 1
'''
if old_ok_cli in gt and "release_proxy_lease(0, rewind=False)" not in gt:
    gt = gt.replace(
        old_ok_cli,
        '''                cpa_ok = finalize_sso_after_register(sso, email=email, log_callback=cli_log)
                try:
                    release_proxy_lease(0, rewind=False)
                except Exception:
                    pass
                success_count += 1
''',
        1,
    )

# mail retry: release alias
old_mail_gui = '''                            if ("未收到验证码" in msg or "验证码" in msg) and mail_try < max_mail_retry:
                                wlog(f"[!] 本邮箱未取到验证码，自动更换新邮箱重试: {msg}")
                                restart_browser(log_callback=wlog)
'''
new_mail_gui = '''                            if ("未收到验证码" in msg or "验证码" in msg) and mail_try < max_mail_retry:
                                wlog(f"[!] 本邮箱未取到验证码，自动更换新邮箱重试: {msg}")
                                try:
                                    release_outlook_alias_if_needed(email, dev_token)
                                except Exception:
                                    pass
                                restart_browser(log_callback=wlog)
'''
if old_mail_gui in gt:
    gt = gt.replace(old_mail_gui, new_mail_gui, 1)

old_mail_cli = '''                        if ("未收到验证码" in msg or "验证码" in msg) and mail_try < max_mail_retry:
                            cli_log(f"[!] 本邮箱未取到验证码，自动更换新邮箱重试: {msg}")
                            restart_browser(log_callback=cli_log)
'''
# may appear twice
if old_mail_cli in gt:
    gt = gt.replace(
        old_mail_cli,
        '''                        if ("未收到验证码" in msg or "验证码" in msg) and mail_try < max_mail_retry:
                            cli_log(f"[!] 本邮箱未取到验证码，自动更换新邮箱重试: {msg}")
                            try:
                                release_outlook_alias_if_needed(email, dev_token)
                            except Exception:
                                pass
                            restart_browser(log_callback=cli_log)
''',
    )

# failure paths release alias if reserved
# GUI except Exception after fail:
old_fail_gui = '''                except Exception as exc:
                    kind = self._record_failure(exc)
                    retry_count_for_slot = 0
                    i += 1
                    wlog(
                        f"[-] 注册失败 [{FAIL_LABELS.get(kind, kind)}]: "
                        f"{redact_sensitive_log_line(str(exc))}"
                    )
'''
if old_fail_gui in gt:
    gt = gt.replace(
        old_fail_gui,
        '''                except Exception as exc:
                    kind = self._record_failure(exc)
                    retry_count_for_slot = 0
                    i += 1
                    try:
                        release_outlook_alias_if_needed(locals().get("email") or "", locals().get("dev_token") or "")
                    except Exception:
                        pass
                    try:
                        release_proxy_lease(getattr(self, "_worker_id", 0), rewind=True)
                    except Exception:
                        pass
                    wlog(
                        f"[-] 注册失败 [{FAIL_LABELS.get(kind, kind)}]: "
                        f"{redact_sensitive_log_line(str(exc))}"
                    )
''',
        1,
    )

# cli_log tee
old_cli_log = '''def cli_log(message):
    if not should_emit_log(message):
        return
'''
if old_cli_log in gt and "_tee_run_log(message)" not in gt[gt.find("def cli_log"):gt.find("def cli_log")+200]:
    # read next lines
    idx = gt.find(old_cli_log)
    after = gt[idx:idx+400]
    if "_tee_run_log" not in after:
        gt = gt.replace(
            old_cli_log,
            '''def cli_log(message):
    if not should_emit_log(message):
        return
    try:
        _tee_run_log(message)
    except Exception:
        pass
''',
            1,
        )

# App.log tee
old_app_log = "    def log(self, message):\n"
idx = gt.find(old_app_log)
if idx > 0 and "_tee_run_log" not in gt[idx:idx+250]:
    # insert after def line
    line_end = gt.find("\n", idx + len(old_app_log))
    insert_at = line_end + 1
    gt = (
        gt[:insert_at]
        + "        try:\n            _tee_run_log(message)\n        except Exception:\n            pass\n"
        + gt[insert_at:]
    )

# start_run_log at CLI entry and GUI registration start
if 'start_run_log("cli")' not in gt:
    # near terminal mode
    if 'cli_log(f"[*] 终端模式启动' in gt:
        gt = gt.replace(
            'cli_log(f"[*] 终端模式启动',
            '_rlp = start_run_log("cli")\n    if _rlp:\n        cli_log(f"[*] run log -> {_rlp}")\n    cli_log(f"[*] 终端模式启动',
            1,
        )
if 'start_run_log("gui")' not in gt and 'start_run_log("batch")' not in gt:
    # find _run_registration_entry or start registration
    for anchor in (
        'def _run_registration_entry(self',
        'def start_registration(self',
        'def _start_workers(self',
    ):
        if anchor in gt:
            # insert after first line of function body is hard; use nearby log
            pass
    # GUI path uses App - search "开始注册"
    if 'self.log("[*] 开始' in gt:
        gt = gt.replace(
            'self.log("[*] 开始',
            '_rlp = start_run_log("gui")\n        if _rlp:\n            self.log(f"[*] run log -> {_rlp}")\n        self.log("[*] 开始',
            1,
        )
    elif 'wlog("[*] 开始注册' in gt:
        gt = gt.replace(
            'wlog("[*] 开始注册',
            '_rlp = start_run_log("gui")\n        if _rlp:\n            wlog(f"[*] run log -> {_rlp}")\n        wlog("[*] 开始注册',
            1,
        )

# ensure atexit stop
if "atexit.register" not in gt:
    if "import integrations_push" in gt:
        gt = gt.replace(
            "import integrations_push\n",
            "import integrations_push\nimport atexit\natexit.register(lambda: stop_run_log())\n",
            1,
        )

gpath.write_text(gt, encoding="utf-8", newline="\n")
print("ttk patched", gpath.stat().st_size)

# ── monitor.py: integrations API + UI card + run log ─────────────────────────
mpath = ROOT / "webui" / "monitor.py"
mt = mpath.read_text(encoding="utf-8")

# imports
if "integrations_store" not in mt:
    mt = mt.replace(
        """    from webui.email_provider_store import (
        read_email_provider_config,
        save_email_provider_config,
        test_email_provider_config,
    )
""",
        """    from webui.email_provider_store import (
        read_email_provider_config,
        save_email_provider_config,
        test_email_provider_config,
    )
    from webui.integrations_store import (
        latest_run_log_tail,
        read_public_config as read_integration_config,
        save_integration_config,
        IntegrationConfigError,
    )
""",
        1,
    )
    mt = mt.replace(
        """    from email_provider_store import (  # type: ignore
        read_email_provider_config,
        save_email_provider_config,
        test_email_provider_config,
    )
""",
        """    from email_provider_store import (  # type: ignore
        read_email_provider_config,
        save_email_provider_config,
        test_email_provider_config,
    )
    from integrations_store import (  # type: ignore
        latest_run_log_tail,
        read_public_config as read_integration_config,
        save_integration_config,
        IntegrationConfigError,
    )
""",
        1,
    )

# GET auth list
mt = mt.replace(
    'if u.path in ("/api/status", "/api/blacklist", "/api/stats", "/api/control", "/api/recovery", "/api/proxies", "/api/email-provider", "/api/email-domains", "/api/bfs", "/api/sso-state"):',
    'if u.path in ("/api/status", "/api/blacklist", "/api/stats", "/api/control", "/api/recovery", "/api/proxies", "/api/email-provider", "/api/email-domains", "/api/bfs", "/api/sso-state", "/api/integrations", "/api/run-log"):',
    1,
)

# GET handlers before 404
if 'u.path == "/api/integrations"' not in mt:
    mt = mt.replace(
        '        if u.path == "/api/email-domains":\n'
        '            try:\n'
        '                self._json(200, read_email_domain_pool())\n'
        '            except Exception as e:\n'
        '                self._json(500, {"ok": False, "error": redact_log_line(str(e))})\n'
        '            return\n'
        '        self._send(404, b"not found", "text/plain")\n',
        '        if u.path == "/api/email-domains":\n'
        '            try:\n'
        '                self._json(200, read_email_domain_pool())\n'
        '            except Exception as e:\n'
        '                self._json(500, {"ok": False, "error": redact_log_line(str(e))})\n'
        '            return\n'
        '        if u.path == "/api/integrations":\n'
        '            try:\n'
        '                self._json(200, {"ok": True, "config": read_integration_config()})\n'
        '            except Exception as e:\n'
        '                self._json(500, {"ok": False, "error": redact_log_line(str(e))})\n'
        '            return\n'
        '        if u.path == "/api/run-log":\n'
        '            try:\n'
        '                qs = parse_qs(u.query or "")\n'
        '                lines = int((qs.get("lines") or ["300"])[0] or 300)\n'
        '                self._json(200, {"ok": True, **latest_run_log_tail(lines)})\n'
        '            except Exception as e:\n'
        '                self._json(500, {"ok": False, "error": redact_log_line(str(e))})\n'
        '            return\n'
        '        self._send(404, b"not found", "text/plain")\n',
        1,
    )

# POST save integrations - after email-provider post if any
if 'u.path == "/api/integrations"' not in mt.split("def do_POST")[1][:8000]:
    # find email-provider POST
    if 'if u.path == "/api/email-provider":' in mt:
        # append after that block is complex; insert before proxies/import or at start of POST handlers after control
        insert_post = '''
        if u.path == "/api/integrations":
            try:
                settings = (body or {}).get("settings") if isinstance(body, dict) else body
                clear_secrets = (body or {}).get("clear_secrets") if isinstance(body, dict) else []
                cfg = save_integration_config(settings or {}, clear_secrets=clear_secrets or [])
                self._json(200, {"ok": True, "config": cfg})
            except IntegrationConfigError as e:
                self._json(400, {"ok": False, "error": str(e)})
            except Exception as e:
                self._json(500, {"ok": False, "error": redact_log_line(str(e))})
            return
'''
        mt = mt.replace(
            '        if u.path == "/api/control":\n',
            insert_post + '        if u.path == "/api/control":\n',
            1,
        )

# HTML card before log tail
if 'id="integrations-card"' not in mt:
    card = r'''
  <section class="card panel" id="integrations-card">
    <div class="section-head">
      <h2>推送 / 间隔 / 别名</h2>
      <div class="button-group">
        <button type="button" onclick="refreshIntegrations()">刷新</button>
        <button type="button" class="primary" onclick="saveIntegrations()">保存</button>
      </div>
    </div>
    <div class="msg" id="int-msg" role="status" aria-live="polite"></div>
    <div class="form-grid" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px;margin-top:10px">
      <label>创建间隔 account_interval<br/><input id="int-account-interval" placeholder="60-120 或 30"/></label>
      <label>别名上限 / 账号<br/><input id="int-alias-cap" type="number" min="1" value="10"/></label>
      <label>顺序 IP 游标<br/>
        <select id="int-proxy-cursor"><option value="true">启用</option><option value="false">关闭</option></select>
      </label>
      <label>Outlook 别名池<br/>
        <select id="int-alias-pool"><option value="true">启用</option><option value="false">关闭</option></select>
      </label>
      <label>CPA Remote URL<br/><input id="int-cpa-url" placeholder="https://cpa.example.com"/></label>
      <label>CPA Management Key<br/><input id="int-cpa-key" type="password" placeholder="留空=不改；已配置会显示 *"/></label>
      <label>Grok2API Base<br/><input id="int-g2a-base" placeholder="https://g2a.example.com"/></label>
      <label>Grok2API 用户名<br/><input id="int-g2a-user"/></label>
      <label>Grok2API 密码<br/><input id="int-g2a-pass" type="password" placeholder="留空=不改"/></label>
      <label>Grok2API App Key<br/><input id="int-g2a-appkey" type="password" placeholder="可选；可当密码"/></label>
    </div>
    <div class="chips" id="int-secrets" style="margin-top:8px"></div>
  </section>
  <section class="card panel">
    <div class="section-head">
      <h2>Run 滚动日志</h2>
      <div class="button-group">
        <button type="button" onclick="refreshRunLog()">刷新</button>
        <span class="section-meta" id="run-log-meta"></span>
      </div>
    </div>
    <div class="tail mono" id="run-log" style="max-height:320px;overflow:auto;white-space:pre-wrap"></div>
  </section>
'''
    mt = mt.replace(
        '  <section class="card panel">\n    <div class="section-head"><h2>日志尾部</h2></div>\n    <div class="tail mono" id="tail"></div>\n  </section>\n',
        card + '  <section class="card panel">\n    <div class="section-head"><h2>日志尾部</h2></div>\n    <div class="tail mono" id="tail"></div>\n  </section>\n',
        1,
    )

# JS functions before refresh()
js = r'''
async function refreshIntegrations(authHelp=false){
  try{
    const j = await api("/api/integrations?_="+Date.now(), {authHelp});
    const c = (j && j.config) || {};
    const set = (id,v)=>{ const el=document.getElementById(id); if(el && document.activeElement!==el) el.value = (v==null?"":String(v)); };
    set("int-account-interval", c.account_interval||"");
    set("int-alias-cap", c.outlook_aliases_per_account||10);
    set("int-proxy-cursor", c.proxy_cursor_enabled===false?"false":"true");
    set("int-alias-pool", c.outlook_use_alias_pool===false?"false":"true");
    set("int-cpa-url", c.cpa_remote_url||"");
    set("int-g2a-base", c.grok2api_remote_base||"");
    set("int-g2a-user", c.grok2api_remote_username||"");
    // secrets: leave blank, show chips
    const sc = c.secret_configured||{};
    const chips = document.getElementById("int-secrets");
    if(chips){
      chips.innerHTML = ["cpa_management_key","grok2api_remote_password","grok2api_remote_app_key"].map(k=>{
        const ok = !!(sc[k]);
        return `<span class="chip ${ok?"ok":""}">${k}: ${ok?"已配置":"未配置"}</span>`;
      }).join(" ");
    }
  }catch(e){ setMsg("int-msg", String(e.message||e), "err"); }
}
async function saveIntegrations(){
  try{
    const settings = {
      account_interval: document.getElementById("int-account-interval").value,
      outlook_aliases_per_account: Number(document.getElementById("int-alias-cap").value||10),
      proxy_cursor_enabled: document.getElementById("int-proxy-cursor").value === "true",
      outlook_use_alias_pool: document.getElementById("int-alias-pool").value === "true",
      cpa_remote_url: document.getElementById("int-cpa-url").value,
      grok2api_remote_base: document.getElementById("int-g2a-base").value,
      grok2api_remote_username: document.getElementById("int-g2a-user").value,
      cpa_management_key: document.getElementById("int-cpa-key").value,
      grok2api_remote_password: document.getElementById("int-g2a-pass").value,
      grok2api_remote_app_key: document.getElementById("int-g2a-appkey").value,
      cpa_auto_add: true,
      grok2api_auto_add_remote: true,
    };
    const j = await api("/api/integrations", {method:"POST", body: JSON.stringify({settings})});
    if(j.ok===false) throw new Error(j.error||"save failed");
    document.getElementById("int-cpa-key").value = "";
    document.getElementById("int-g2a-pass").value = "";
    document.getElementById("int-g2a-appkey").value = "";
    setMsg("int-msg", "已保存推送/间隔/别名配置", "ok");
    await refreshIntegrations();
  }catch(e){ setMsg("int-msg", String(e.message||e), "err"); }
}
async function refreshRunLog(){
  try{
    const j = await api("/api/run-log?lines=400&_="+Date.now(), {authHelp:false});
    const box = document.getElementById("run-log");
    const meta = document.getElementById("run-log-meta");
    if(meta) meta.textContent = j.path || (j.missing?"暂无 run 日志":"");
    if(box){
      const lines = j.lines || [];
      box.textContent = lines.join("\n");
      box.scrollTop = box.scrollHeight;
    }
  }catch(e){
    const box=document.getElementById("run-log");
    if(box) box.textContent = String(e.message||e);
  }
}
'''
if "async function refreshIntegrations" not in mt:
    mt = mt.replace("async function refresh() {", js + "\nasync function refresh() {", 1)

# call refreshIntegrations/runlog in bootstrap
if "refreshIntegrations()" not in mt:
    # find setInterval refresh
    if "setInterval(refresh," in mt:
        mt = mt.replace(
            "setInterval(refresh,",
            "refreshIntegrations(); refreshRunLog(); setInterval(refreshRunLog, 3000); setInterval(refresh,",
            1,
        )
    elif "refresh();" in mt:
        mt = mt.replace("refresh();", "refresh(); refreshIntegrations(); refreshRunLog(); setInterval(refreshRunLog, 3000);", 1)

# Enable tail by default if env not set - optional leave as is
# Also include run log lines into snapshot.tail when PANEL_INCLUDE_TAIL
if "latest_run_log_tail" in mt and "run_log_tail" not in mt:
    mt = mt.replace(
        '        "tail": (parsed.get("tail") or []) if PANEL_INCLUDE_TAIL else ["(raw log tail disabled; set PANEL_INCLUDE_TAIL=1)"],\n'
        '    }\n',
        '        "tail": (parsed.get("tail") or []) if PANEL_INCLUDE_TAIL else ["(raw log tail disabled; set PANEL_INCLUDE_TAIL=1)"],\n'
        '        "run_log": latest_run_log_tail(250),\n'
        '    }\n',
        1,
    )

# parse_qs import
if "from urllib.parse import" in mt and "parse_qs" not in mt.split("from urllib.parse")[1][:80]:
    mt = mt.replace("from urllib.parse import urlparse", "from urllib.parse import parse_qs, urlparse", 1)

mpath.write_text(mt, encoding="utf-8", newline="\n")
print("monitor patched", mpath.stat().st_size)
print("OK")
