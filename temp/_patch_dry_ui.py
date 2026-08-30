# -*- coding: utf-8 -*-
from pathlib import Path

mp = Path(r"E:/AI/grok-register-panel/webui/monitor.py")
t = mp.read_text(encoding="utf-8")

# imports
old_imp = """    from webui.integrations_store import (
        latest_run_log_tail,
        read_public_config as read_integration_config,
        save_integration_config,
        IntegrationConfigError,
    )
"""
new_imp = """    from webui.integrations_store import (
        latest_run_log_tail,
        read_public_config as read_integration_config,
        save_integration_config,
        IntegrationConfigError,
        test_cpa_connectivity,
        test_g2a_connectivity,
        run_dry_run,
    )
"""
if old_imp in t:
    t = t.replace(old_imp, new_imp, 1)
else:
    # fallback local import style
    old_imp2 = """    from integrations_store import (  # type: ignore
        latest_run_log_tail,
        read_public_config as read_integration_config,
        save_integration_config,
        IntegrationConfigError,
    )
"""
    new_imp2 = """    from integrations_store import (  # type: ignore
        latest_run_log_tail,
        read_public_config as read_integration_config,
        save_integration_config,
        IntegrationConfigError,
        test_cpa_connectivity,
        test_g2a_connectivity,
        run_dry_run,
    )
"""
    if old_imp2 in t:
        t = t.replace(old_imp2, new_imp2, 1)
    if "test_cpa_connectivity" not in t:
        # package import path only
        if "from webui.integrations_store import" in t and "test_cpa_connectivity" not in t:
            t = t.replace(
                "save_integration_config,\n        IntegrationConfigError,\n    )",
                "save_integration_config,\n        IntegrationConfigError,\n        test_cpa_connectivity,\n        test_g2a_connectivity,\n        run_dry_run,\n    )",
                1,
            )
        if "from integrations_store import" in t and "test_cpa_connectivity" not in t.split("from integrations_store")[1][:400]:
            t = t.replace(
                "save_integration_config,\n        IntegrationConfigError,\n    )",
                "save_integration_config,\n        IntegrationConfigError,\n        test_cpa_connectivity,\n        test_g2a_connectivity,\n        run_dry_run,\n    )",
            )

# HTML buttons
old_html = """      <div class=\"button-group\">
        <button type=\"button\" onclick=\"refreshIntegrations()\">刷新</button>
        <button type=\"button\" class=\"primary\" onclick=\"saveIntegrations()\">保存</button>
      </div>
    </div>
    <div class=\"msg\" id=\"int-msg\" role=\"status\" aria-live=\"polite\"></div>
"""
new_html = """      <div class=\"button-group\">
        <button type=\"button\" onclick=\"refreshIntegrations()\">刷新</button>
        <button type=\"button\" class=\"primary\" onclick=\"saveIntegrations()\">保存</button>
        <button type=\"button\" onclick=\"testCpaConnectivity()\">CPA连通</button>
        <button type=\"button\" onclick=\"testG2aConnectivity()\">G2A连通</button>
        <button type=\"button\" onclick=\"runIntegrationsDryRun()\">Dry Run</button>
      </div>
    </div>
    <div class=\"msg\" id=\"int-msg\" role=\"status\" aria-live=\"polite\"></div>
"""
if old_html not in t:
    raise SystemExit("html buttons anchor missing")
t = t.replace(old_html, new_html, 1)

# chips after secrets - add dry log box
old_tail = """    <div class=\"chips\" id=\"int-secrets\" style=\"margin-top:8px\"></div>
  </section>
  <section class=\"card panel\">
    <div class=\"section-head\">
      <h2>Run 滚动日志</h2>
"""
new_tail = """    <div class=\"chips\" id=\"int-secrets\" style=\"margin-top:8px\"></div>
    <div class=\"section-head\" style=\"margin-top:12px\">
      <h2 style=\"font-size:1rem;margin:0\">Dry Run / 连通日志</h2>
      <span class=\"section-meta mono\" id=\"int-dry-meta\"></span>
    </div>
    <div class=\"tail mono\" id=\"int-dry-log\" style=\"max-height:260px;overflow:auto;white-space:pre-wrap;margin-top:8px\"></div>
  </section>
  <section class=\"card panel\">
    <div class=\"section-head\">
      <h2>Run 滚动日志</h2>
"""
if 'id="int-dry-log"' not in t:
    if old_tail not in t:
        raise SystemExit("dry log anchor missing")
    t = t.replace(old_tail, new_tail, 1)

# JS helpers after saveIntegrations
js = r'''
function collectIntegrationSettings(){
  return {
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
}
function showDryLines(lines, meta){
  const box = document.getElementById("int-dry-log");
  const m = document.getElementById("int-dry-meta");
  if(m) m.textContent = meta || "";
  if(box){
    box.textContent = (lines||[]).join("\n");
    box.scrollTop = box.scrollHeight;
  }
}
async function testCpaConnectivity(){
  setMsg("int-msg", "正在测 CPA 连通…", "");
  try{
    const settings = collectIntegrationSettings();
    const j = await api("/api/integrations/test-cpa", {method:"POST", body: JSON.stringify({settings})});
    if(j.ok){
      setMsg("int-msg", j.detail || "CPA 连通 OK", "ok");
      showDryLines([`[CPA] ${j.detail||"OK"}`], "CPA");
    }else{
      setMsg("int-msg", j.error || "CPA 不通", "err");
      showDryLines([`[CPA] FAIL ${j.error||""}`], "CPA");
    }
  }catch(e){ setMsg("int-msg", String(e.message||e), "err"); }
}
async function testG2aConnectivity(){
  setMsg("int-msg", "正在测 Grok2API 连通…", "");
  try{
    const settings = collectIntegrationSettings();
    const j = await api("/api/integrations/test-g2a", {method:"POST", body: JSON.stringify({settings})});
    if(j.ok){
      setMsg("int-msg", j.detail || "Grok2API 连通 OK", "ok");
      showDryLines([`[G2A] ${j.detail||"OK"}`], "G2A");
    }else{
      setMsg("int-msg", j.error || "Grok2API 不通", "err");
      showDryLines([`[G2A] FAIL ${j.error||""}`], "G2A");
    }
  }catch(e){ setMsg("int-msg", String(e.message||e), "err"); }
}
async function runIntegrationsDryRun(){
  setMsg("int-msg", "正在 Dry Run…", "");
  showDryLines(["正在执行 Dry Run…"], "");
  try{
    // save current form first so dry uses same params
    await saveIntegrations();
    const workers = Number((document.getElementById("workers-input")||{}).value || 1);
    const batch_count = Number((document.getElementById("batch_count")||{}).value || 1);
    const j = await api("/api/integrations/dry-run", {
      method:"POST",
      body: JSON.stringify({ workers, target_count: batch_count, settings: collectIntegrationSettings() }),
    });
    showDryLines(j.lines || [], j.path || "dry-run");
    if(j.ok){
      setMsg("int-msg", "Dry Run 完成 · " + (j.path||""), "ok");
      await refreshRunLog();
    }else{
      setMsg("int-msg", j.error || "Dry Run 失败", "err");
    }
  }catch(e){ setMsg("int-msg", String(e.message||e), "err"); }
}
'''

if "async function testCpaConnectivity" not in t:
    t = t.replace(
        "async function refreshRunLog(){",
        js + "\nasync function refreshRunLog(){",
        1,
    )

# also refactor saveIntegrations to use collect if present - optional
if "const settings = {\n      account_interval: document.getElementById(\"int-account-interval\").value," in t:
    t = t.replace(
        """async function saveIntegrations(){
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
""",
        """async function saveIntegrations(){
  try{
    const settings = collectIntegrationSettings();
""",
        1,
    )

# POST routes after /api/integrations
post_block = '''
        if u.path == "/api/integrations/test-cpa":
            try:
                settings = (body or {}).get("settings") if isinstance(body, dict) else {}
                result = test_cpa_connectivity(settings or {})
                self._json(200 if result.get("ok") else 424, result)
            except Exception as e:
                self._json(500, {"ok": False, "error": redact_log_line(str(e))})
            return
        if u.path == "/api/integrations/test-g2a":
            try:
                settings = (body or {}).get("settings") if isinstance(body, dict) else {}
                result = test_g2a_connectivity(settings or {})
                self._json(200 if result.get("ok") else 424, result)
            except Exception as e:
                self._json(500, {"ok": False, "error": redact_log_line(str(e))})
            return
        if u.path == "/api/integrations/dry-run":
            try:
                settings = (body or {}).get("settings") if isinstance(body, dict) else {}
                target_count = (body or {}).get("target_count") if isinstance(body, dict) else None
                workers = (body or {}).get("workers") if isinstance(body, dict) else None
                result = run_dry_run(
                    target_count=target_count,
                    workers=workers,
                    settings=settings or {},
                )
                self._json(200 if result.get("ok") else 500, result)
            except Exception as e:
                self._json(500, {"ok": False, "error": redact_log_line(str(e))})
            return
'''

if 'u.path == "/api/integrations/test-cpa"' not in t:
    anchor = '''        if u.path == "/api/integrations":
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
    if anchor not in t:
        raise SystemExit("post integrations anchor missing")
    t = t.replace(anchor, anchor + post_block, 1)

mp.write_text(t, encoding="utf-8", newline="\n")
print("monitor patched", mp.stat().st_size)

import py_compile
for f in ["webui/monitor.py", "webui/integrations_store.py"]:
    py_compile.compile(str(Path(r"E:/AI/grok-register-panel")/f), doraise=True)
    print("ok", f)
