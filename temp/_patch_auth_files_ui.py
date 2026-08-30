# -*- coding: utf-8 -*-
from pathlib import Path
import re

mp = Path(r"E:/AI/grok-register-panel/webui/monitor.py")
t = mp.read_text(encoding="utf-8")
orig = t

# --- imports ---
imp_a = """from webui.integrations_store import (
    IntegrationConfigError,
    latest_run_log_tail,
    read_public_config as read_integrations_config,
    run_dry_run as integrations_run_dry_run,
    save_integration_config,
    test_cpa_connectivity,
    test_g2a_connectivity,
)"""
imp_b = """from webui.integrations_store import (
    IntegrationConfigError,
    latest_run_log_tail,
    read_public_config as read_integrations_config,
    run_dry_run as integrations_run_dry_run,
    save_integration_config,
    test_cpa_connectivity,
    test_g2a_connectivity,
)
from webui.auth_files_store import (
    AuthFilesError,
    list_auth_files,
    read_auth_file,
    read_auth_zip,
)"""
if "from webui.auth_files_store import" not in t:
    if imp_a in t:
        t = t.replace(imp_a, imp_b, 1)
    else:
        # try shorter
        m = re.search(r"from webui\.integrations_store import \([^\)]+\)", t)
        if not m:
            raise SystemExit("integrations import not found")
        t = t[: m.end()] + "\nfrom webui.auth_files_store import (\n    AuthFilesError,\n    list_auth_files,\n    read_auth_file,\n    read_auth_zip,\n)" + t[m.end():]

# --- HTML section after integrations-card ---
html_old = """  </section>
  <section class="card panel">
    <div class="section-head">
      <h2>Run 滚动日志</h2>"""

html_new = """  </section>
  <section class="card panel" id="auth-files-card">
    <div class="section-head">
      <h2>本地 Auth JSON</h2>
      <div class="button-group">
        <button type="button" class="primary" onclick="refreshAuthFiles('cpa')">刷新 CPA</button>
        <button type="button" onclick="refreshAuthFiles('g2a')">刷新 G2A</button>
        <button type="button" onclick="downloadAuthZip('cpa')">打包下载 CPA</button>
        <button type="button" onclick="downloadAuthZip('g2a')">打包下载 G2A</button>
      </div>
    </div>
    <div class="msg" id="auth-files-msg" role="status" aria-live="polite"></div>
    <div class="chips" id="auth-files-kpis" style="margin-top:8px"></div>
    <div class="section-meta mono" id="auth-files-meta" style="margin-top:6px"></div>
    <div style="overflow:auto;max-height:360px;margin-top:10px">
      <table class="data" id="auth-files-table">
        <thead>
          <tr>
            <th>类型</th>
            <th>文件名</th>
            <th>邮箱</th>
            <th>大小</th>
            <th>mtime</th>
            <th></th>
          </tr>
        </thead>
        <tbody id="auth-files-body">
          <tr><td colspan="6" class="domain-empty">点刷新加载 cpa_auth / grok2api_auth</td></tr>
        </tbody>
      </table>
    </div>
  </section>
  <section class="card panel">
    <div class="section-head">
      <h2>Run 滚动日志</h2>"""

if 'id="auth-files-card"' not in t:
    if html_old not in t:
        raise SystemExit("html anchor missing")
    t = t.replace(html_old, html_new, 1)

# --- JS helpers ---
js_anchor = "function downloadTextFile(filename, text){"
js_extra = r'''let authFilesCache = {cpa: [], g2a: []};
let authFilesKind = "cpa";

function formatBytes(n){
  n = Number(n||0);
  if(n < 1024) return n + " B";
  if(n < 1048576) return (n/1024).toFixed(1) + " KB";
  return (n/1048576).toFixed(2) + " MB";
}
function formatMtime(ts){
  if(!ts) return "";
  try { return new Date(Number(ts)*1000).toLocaleString(); } catch(e){ return String(ts); }
}
function renderAuthFiles(kind, j){
  authFilesKind = kind || "cpa";
  const files = j.files || [];
  authFilesCache[authFilesKind] = files;
  const meta = document.getElementById("auth-files-meta");
  if(meta) meta.textContent = (j.path || "") + " · " + (j.count ?? files.length) + " 个" + (j.truncated ? "（已截断）" : "");
  const kpis = document.getElementById("auth-files-kpis");
  if(kpis){
    const items = [
      ["当前", authFilesKind.toUpperCase()],
      ["数量", j.count ?? files.length],
      ["目录", j.path || "-"],
    ];
    kpis.innerHTML = items.map(([k,v]) => `<span class="chip">${esc(k)}: ${esc(v)}</span>`).join(" ");
  }
  const body = document.getElementById("auth-files-body");
  if(!body) return;
  if(!files.length){
    body.innerHTML = '<tr><td colspan="6" class="domain-empty">目录为空或不存在</td></tr>';
    return;
  }
  body.innerHTML = files.map(f => {
    const name = f.name || "";
    const dis = f.disabled === true ? ' <span class="chip">disabled</span>' : '';
    return `<tr>
      <td>${esc(authFilesKind.toUpperCase())}</td>
      <td class="mono">${esc(name)}${dis}</td>
      <td class="mono">${esc(f.email||"")}</td>
      <td>${esc(formatBytes(f.bytes))}</td>
      <td class="mono">${esc(formatMtime(f.mtime))}</td>
      <td><button type="button" onclick="downloadAuthFile('${authFilesKind}', '${String(name).replace(/\\\\/g,'\\\\').replace(/'/g,"\\\\'")}')">下载</button></td>
    </tr>`;
  }).join("");
}
async function refreshAuthFiles(kind){
  const k = kind || authFilesKind || "cpa";
  setMsg("auth-files-msg", "加载 " + k + " …", "");
  try{
    const j = await api("/api/auth-files?kind=" + encodeURIComponent(k) + "&limit=5000&_=" + Date.now());
    renderAuthFiles(k, j);
    setMsg("auth-files-msg", "已加载 " + k.toUpperCase() + " " + (j.count ?? 0) + " 个", "ok");
  }catch(e){ setMsg("auth-files-msg", String(e.message||e), "err"); }
}
async function downloadAuthFile(kind, name){
  try{
    const j = await api("/api/auth-files/raw?kind=" + encodeURIComponent(kind) + "&name=" + encodeURIComponent(name) + "&_=" + Date.now());
    downloadTextFile(j.filename || name, j.text || "");
    setMsg("auth-files-msg", "已下载 " + (j.filename || name), "ok");
  }catch(e){ setMsg("auth-files-msg", String(e.message||e), "err"); }
}
function downloadBase64File(filename, b64, mime){
  const bin = atob(b64 || "");
  const bytes = new Uint8Array(bin.length);
  for(let i=0;i<bin.length;i++) bytes[i] = bin.charCodeAt(i);
  const blob = new Blob([bytes], {type: mime || "application/zip"});
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename || "auth.zip";
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
async function downloadAuthZip(kind){
  const k = kind || authFilesKind || "cpa";
  setMsg("auth-files-msg", "打包 " + k + " …", "");
  try{
    const j = await api("/api/auth-files/zip?kind=" + encodeURIComponent(k) + "&_=" + Date.now());
    downloadBase64File(j.filename || (k + "_auth.zip"), j.content_b64 || "", "application/zip");
    setMsg("auth-files-msg", "已打包下载 " + (j.count||0) + " 个 " + k.toUpperCase(), "ok");
  }catch(e){ setMsg("auth-files-msg", String(e.message||e), "err"); }
}

'''
if "function refreshAuthFiles" not in t:
    if js_anchor not in t:
        raise SystemExit("js anchor missing")
    t = t.replace(js_anchor, js_extra + js_anchor, 1)

# boot: load cpa list once
boot_old = "refreshIntegrations();\nrefreshRunLog();"
boot_new = "refreshIntegrations();\nrefreshAuthFiles('cpa');\nrefreshRunLog();"
if "refreshAuthFiles('cpa')" not in t and boot_old in t:
    t = t.replace(boot_old, boot_new, 1)

# token onchange
if "refreshAuthFiles" not in t.split("onchange=\"getToken()")[1][:400]:
    t = t.replace(
        "refreshIntegrations(); refreshRunLog()\" onblur=\"getToken()\"",
        "refreshIntegrations(); refreshAuthFiles('cpa'); refreshRunLog()\" onblur=\"getToken()\"",
        1,
    )

# --- GET auth routes allowlist ---
old_allow = '"/api/email-provider/outlook-state", "/api/email-provider/outlook-state/raw"'
new_allow = '"/api/email-provider/outlook-state", "/api/email-provider/outlook-state/raw", "/api/auth-files", "/api/auth-files/raw", "/api/auth-files/zip"'
if '"/api/auth-files"' not in t:
    if old_allow not in t:
        raise SystemExit("allowlist anchor missing")
    t = t.replace(old_allow, new_allow, 1)

# --- route handlers after outlook-state/raw ---
route_marker = 'if u.path == "/api/email-provider/outlook-state/raw":'
if "if u.path == \"/api/auth-files\":" not in t:
    idx = t.find(route_marker)
    if idx < 0:
        raise SystemExit("route marker missing")
    # find end of this if block - next "if u.path" after it
    rest = t[idx:]
    m = re.search(r"\n        if u\.path == ", rest[5:])
    if not m:
        raise SystemExit("next route not found")
    insert_at = idx + 5 + m.start()
    handler = '''
        if u.path == "/api/auth-files":
            try:
                qs = parse_qs(u.query or "")
                kind = (qs.get("kind") or ["cpa"])[0]
                limit = int((qs.get("limit") or ["2000"])[0] or 2000)
            except Exception:
                kind, limit = "cpa", 2000
            try:
                self._json(200, list_auth_files(kind, limit=limit))
            except AuthFilesError as exc:
                self._json(400, {"ok": False, "error": str(exc)})
            except Exception as exc:
                self._json(500, {"ok": False, "error": str(exc)})
            return
        if u.path == "/api/auth-files/raw":
            try:
                qs = parse_qs(u.query or "")
                kind = (qs.get("kind") or ["cpa"])[0]
                name = (qs.get("name") or [""])[0]
            except Exception:
                kind, name = "cpa", ""
            try:
                self._json(200, read_auth_file(kind, name))
            except AuthFilesError as exc:
                self._json(400, {"ok": False, "error": str(exc)})
            except Exception as exc:
                self._json(500, {"ok": False, "error": str(exc)})
            return
        if u.path == "/api/auth-files/zip":
            try:
                qs = parse_qs(u.query or "")
                kind = (qs.get("kind") or ["cpa"])[0]
            except Exception:
                kind = "cpa"
            try:
                self._json(200, read_auth_zip(kind))
            except AuthFilesError as exc:
                self._json(400, {"ok": False, "error": str(exc)})
            except Exception as exc:
                self._json(500, {"ok": False, "error": str(exc)})
            return
'''
    t = t[:insert_at] + handler + t[insert_at:]

if t == orig:
    raise SystemExit("no changes applied")
mp.write_text(t, encoding="utf-8", newline="\n")
print("patched", len(t) - len(orig))
# sanity
for s in ["auth-files-card", "refreshAuthFiles", "/api/auth-files", "list_auth_files"]:
    print(s, s in t)
