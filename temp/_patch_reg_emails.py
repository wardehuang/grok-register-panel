# -*- coding: utf-8 -*-
from pathlib import Path
import re

mp = Path(r"E:/AI/grok-register-panel/webui/monitor.py")
t = mp.read_text(encoding="utf-8")
orig = t

# imports try branch
a = """    from webui.auth_files_store import (
        AuthFilesError,
        list_auth_files,
        read_auth_file,
        read_auth_zip,
    )"""
b = """    from webui.auth_files_store import (
        AuthFilesError,
        list_auth_files,
        read_auth_file,
        read_auth_zip,
    )
    from webui.registered_accounts_store import (
        RegisteredAccountsError,
        collect_registered_accounts,
        export_registered_emails_text,
        read_one_registered_line,
    )"""
if "registered_accounts_store" not in t:
    if a not in t:
        raise SystemExit("auth_files import missing")
    t = t.replace(a, b, 1)

# except branch
a2 = """    from auth_files_store import (  # type: ignore
        AuthFilesError,
        list_auth_files,
        read_auth_file,
        read_auth_zip,
    )"""
b2 = """    from auth_files_store import (  # type: ignore
        AuthFilesError,
        list_auth_files,
        read_auth_file,
        read_auth_zip,
    )
    from registered_accounts_store import (  # type: ignore
        RegisteredAccountsError,
        collect_registered_accounts,
        export_registered_emails_text,
        read_one_registered_line,
    )"""
if a2 in t and "from registered_accounts_store import" not in t:
    t = t.replace(a2, b2, 1)

# HTML after auth-files-card
html_anchor = """  <section class="card panel">
    <div class="section-head">
      <h2>Run 滚动日志</h2>"""
html = """  <section class="card panel" id="registered-emails-card">
    <div class="section-head">
      <h2>已注册账号 registered_emails</h2>
      <div class="button-group">
        <button type="button" class="primary" onclick="refreshRegisteredEmails()">刷新</button>
        <button type="button" onclick="downloadRegisteredEmails()">下载全部</button>
        <button type="button" onclick="copyRegisteredEmails()">复制全部</button>
      </div>
    </div>
    <div class="msg" id="reg-emails-msg" role="status" aria-live="polite"></div>
    <div class="chips" id="reg-emails-kpis" style="margin-top:8px"></div>
    <div class="section-meta mono" id="reg-emails-meta" style="margin-top:6px">格式: email----password----sso</div>
    <textarea id="reg-emails-text" class="mono" rows="10" style="width:100%;margin-top:10px;white-space:pre;overflow:auto" placeholder="刷新后显示 email----password----sso" spellcheck="false"></textarea>
    <div style="overflow:auto;max-height:280px;margin-top:10px">
      <table class="data" id="reg-emails-table">
        <thead>
          <tr>
            <th>邮箱</th>
            <th>来源文件</th>
            <th>mtime</th>
            <th></th>
          </tr>
        </thead>
        <tbody id="reg-emails-body">
          <tr><td colspan="4" class="domain-empty">点刷新加载 accounts/*.txt</td></tr>
        </tbody>
      </table>
    </div>
  </section>
  <section class="card panel">
    <div class="section-head">
      <h2>Run 滚动日志</h2>"""
if 'id="registered-emails-card"' not in t:
    if html_anchor not in t:
        raise SystemExit("html anchor missing")
    t = t.replace(html_anchor, html, 1)

# JS
js_anchor = "let authFilesCache = {cpa: [], g2a: []};"
js = r'''let registeredEmailsText = "";
let registeredEmailsRows = [];

function formatRegMtime(ts){
  if(!ts) return "";
  try { return new Date(Number(ts)*1000).toLocaleString(); } catch(e){ return String(ts); }
}
async function refreshRegisteredEmails(){
  setMsg("reg-emails-msg", "加载已注册账号…", "");
  try{
    const j = await api("/api/registered-emails?limit=20000&_=" + Date.now());
    registeredEmailsText = j.export_text || "";
    registeredEmailsRows = j.accounts || [];
    const ta = document.getElementById("reg-emails-text");
    if(ta) ta.value = registeredEmailsText;
    const meta = document.getElementById("reg-emails-meta");
    if(meta) meta.textContent = (j.format || "email----password----sso") + " · " + (j.count||0) + " 个 · 源文件 " + (j.source_files||0);
    const kpis = document.getElementById("reg-emails-kpis");
    if(kpis){
      const items = [
        ["数量", j.count ?? 0],
        ["目录", j.path || "accounts/"],
      ];
      kpis.innerHTML = items.map(([k,v]) => `<span class="chip">${esc(k)}: ${esc(v)}</span>`).join(" ");
    }
    const body = document.getElementById("reg-emails-body");
    if(body){
      const rows = registeredEmailsRows;
      if(!rows.length){
        body.innerHTML = '<tr><td colspan="4" class="domain-empty">暂无已注册账号</td></tr>';
      } else {
        body.innerHTML = rows.map(r => {
          const em = r.email || "";
          const q = encodeURIComponent(em);
          return `<tr>
            <td class="mono">${esc(em)}</td>
            <td class="mono">${esc(r.source||"")}</td>
            <td class="mono">${esc(formatRegMtime(r.mtime))}</td>
            <td><button type="button" data-email="${q}" onclick="downloadOneRegistered(decodeURIComponent(this.dataset.email))">下载</button></td>
          </tr>`;
        }).join("");
      }
    }
    setMsg("reg-emails-msg", "已加载 " + (j.count||0) + " 个 registered_emails", "ok");
  }catch(e){ setMsg("reg-emails-msg", String(e.message||e), "err"); }
}
async function downloadRegisteredEmails(){
  try{
    const j = await api("/api/registered-emails/export?_=" + Date.now());
    downloadTextFile(j.filename || "registered_emails.txt", j.text || "");
    setMsg("reg-emails-msg", "已下载 " + (j.count||0) + " 行", "ok");
  }catch(e){ setMsg("reg-emails-msg", String(e.message||e), "err"); }
}
async function downloadOneRegistered(email){
  try{
    const j = await api("/api/registered-emails/one?email=" + encodeURIComponent(email) + "&_=" + Date.now());
    const name = (j.email || email || "account").replace(/[\\\\/]/g,"_") + ".txt";
    downloadTextFile(name, (j.line || "") + "\\n");
    setMsg("reg-emails-msg", "已下载 " + (j.email || email), "ok");
  }catch(e){ setMsg("reg-emails-msg", String(e.message||e), "err"); }
}
async function copyRegisteredEmails(){
  try{
    let text = registeredEmailsText;
    if(!text){
      const j = await api("/api/registered-emails/export?_=" + Date.now());
      text = j.text || "";
      registeredEmailsText = text;
      const ta = document.getElementById("reg-emails-text");
      if(ta) ta.value = text;
    }
    if(navigator.clipboard && navigator.clipboard.writeText){
      await navigator.clipboard.writeText(text);
    } else {
      const ta = document.getElementById("reg-emails-text");
      if(ta){ ta.focus(); ta.select(); document.execCommand("copy"); }
    }
    setMsg("reg-emails-msg", "已复制到剪贴板", "ok");
  }catch(e){ setMsg("reg-emails-msg", String(e.message||e), "err"); }
}

'''
if "function refreshRegisteredEmails" not in t:
    if js_anchor not in t:
        # try insert before downloadTextFile
        if "function downloadTextFile" in t:
            t = t.replace("function downloadTextFile", js + "function downloadTextFile", 1)
        else:
            raise SystemExit("js anchor missing")
    else:
        t = t.replace(js_anchor, js + js_anchor, 1)

# boot
if "refreshRegisteredEmails()" not in t:
    t = t.replace("refreshAuthFiles('cpa');\n", "refreshAuthFiles('cpa');\nrefreshRegisteredEmails();\n", 1)
    t = t.replace(
        "refreshAuthFiles('cpa'); refreshRunLog()\"",
        "refreshAuthFiles('cpa'); refreshRegisteredEmails(); refreshRunLog()\"",
        1,
    )

# allowlist
old_a = '"/api/auth-files", "/api/auth-files/raw", "/api/auth-files/zip"'
new_a = '"/api/auth-files", "/api/auth-files/raw", "/api/auth-files/zip", "/api/registered-emails", "/api/registered-emails/export", "/api/registered-emails/one"'
if '"/api/registered-emails"' not in t:
    if old_a not in t:
        raise SystemExit("allowlist missing")
    t = t.replace(old_a, new_a, 1)

# routes after auth-files/zip
if 'u.path == "/api/registered-emails"' not in t:
    marker = 'if u.path == "/api/auth-files/zip":'
    idx = t.find(marker)
    if idx < 0:
        raise SystemExit("zip route missing")
    rest = t[idx+5:]
    m = re.search(r"\n        if u\.path == ", rest)
    if not m:
        # end of block - find return after zip
        m2 = re.search(r"\n            return\n", rest)
        if not m2:
            raise SystemExit("insert point missing")
        insert_at = idx + 5 + m2.end()
    else:
        insert_at = idx + 5 + m.start()
    handler = '''
        if u.path == "/api/registered-emails":
            try:
                qs = parse_qs(u.query or "")
                limit = int((qs.get("limit") or ["20000"])[0] or 20000)
            except Exception:
                limit = 20000
            try:
                self._json(200, collect_registered_accounts(limit=limit))
            except RegisteredAccountsError as exc:
                self._json(400, {"ok": False, "error": str(exc)})
            except Exception as exc:
                self._json(500, {"ok": False, "error": str(exc)})
            return
        if u.path == "/api/registered-emails/export":
            try:
                self._json(200, export_registered_emails_text())
            except RegisteredAccountsError as exc:
                self._json(400, {"ok": False, "error": str(exc)})
            except Exception as exc:
                self._json(500, {"ok": False, "error": str(exc)})
            return
        if u.path == "/api/registered-emails/one":
            try:
                qs = parse_qs(u.query or "")
                email = (qs.get("email") or [""])[0]
            except Exception:
                email = ""
            try:
                self._json(200, read_one_registered_line(email))
            except RegisteredAccountsError as exc:
                self._json(400, {"ok": False, "error": str(exc)})
            except Exception as exc:
                self._json(500, {"ok": False, "error": str(exc)})
            return
'''
    t = t[:insert_at] + handler + t[insert_at:]

if t == orig:
    raise SystemExit("no changes")
mp.write_text(t, encoding="utf-8", newline="\n")
print("delta", len(t)-len(orig))
for s in ["registered-emails-card", "refreshRegisteredEmails", "/api/registered-emails", "collect_registered_accounts"]:
    print(s, s in t)
