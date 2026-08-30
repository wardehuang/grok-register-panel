# -*- coding: utf-8 -*-
from pathlib import Path
import re

mp = Path(r"E:/AI/grok-register-panel/webui/monitor.py")
t = mp.read_text(encoding="utf-8")

# imports - both styles
for old, new in [
(
"""    from webui.outlook_inventory_store import (
        OutlookInventoryError,
        read_inventory as read_outlook_inventory,
        read_state as read_outlook_state,
        write_inventory as write_outlook_inventory,
    )
""",
"""    from webui.outlook_inventory_store import (
        OutlookInventoryError,
        read_inventory as read_outlook_inventory,
        read_state as read_outlook_state,
        read_state_raw as read_outlook_state_raw,
        write_inventory as write_outlook_inventory,
        write_state as write_outlook_state,
    )
"""
),
(
"""    from outlook_inventory_store import (  # type: ignore
        OutlookInventoryError,
        read_inventory as read_outlook_inventory,
        read_state as read_outlook_state,
        write_inventory as write_outlook_inventory,
    )
""",
"""    from outlook_inventory_store import (  # type: ignore
        OutlookInventoryError,
        read_inventory as read_outlook_inventory,
        read_state as read_outlook_state,
        read_state_raw as read_outlook_state_raw,
        write_inventory as write_outlook_inventory,
        write_state as write_outlook_state,
    )
"""
),
]:
    if old in t:
        t = t.replace(old, new, 1)

if "read_state_raw as read_outlook_state_raw" not in t:
    raise SystemExit("import patch failed")

# auth GET list
t = t.replace(
    '"/api/email-provider/outlook-inventory", "/api/email-provider/outlook-state")',
    '"/api/email-provider/outlook-inventory", "/api/email-provider/outlook-state", "/api/email-provider/outlook-state/raw")',
    1,
)

# GET raw handler after outlook-state
old_get = '''        if u.path == "/api/email-provider/outlook-state":
            try:
                qs = parse_qs(u.query or "")
                limit = int((qs.get("limit") or ["500"])[0] or 500)
                self._json(200, read_outlook_state(limit=limit))
            except Exception as e:
                self._json(500, {"ok": False, "error": redact_log_line(str(e))})
            return
'''
new_get = '''        if u.path == "/api/email-provider/outlook-state":
            try:
                qs = parse_qs(u.query or "")
                limit = int((qs.get("limit") or ["500"])[0] or 500)
                self._json(200, read_outlook_state(limit=limit))
            except Exception as e:
                self._json(500, {"ok": False, "error": redact_log_line(str(e))})
            return
        if u.path == "/api/email-provider/outlook-state/raw":
            try:
                self._json(200, read_outlook_state_raw())
            except Exception as e:
                self._json(500, {"ok": False, "error": redact_log_line(str(e))})
            return
'''
if old_get not in t:
    raise SystemExit("get handler missing")
if "/api/email-provider/outlook-state/raw" not in t.split("def do_GET")[1][:8000]:
    t = t.replace(old_get, new_get, 1)

# POST write state after inventory post
old_post = '''        if u.path == "/api/email-provider/outlook-inventory":
            try:
                text = (body or {}).get("text") if isinstance(body, dict) else ""
                result = write_outlook_inventory(text)
                self._json(200, result)
            except OutlookInventoryError as e:
                self._json(400, {"ok": False, "error": str(e)})
            except ValueError as e:
                self._json(400, {"ok": False, "error": redact_log_line(str(e))})
            except Exception as e:
                self._json(500, {"ok": False, "error": redact_log_line(str(e))})
            return
'''
new_post = old_post + '''        if u.path == "/api/email-provider/outlook-state":
            try:
                payload = body or {}
                raw = payload.get("text")
                if raw is None and isinstance(payload.get("data"), (dict, list)):
                    raw = payload.get("data")
                if raw is None:
                    raw = payload
                result = write_outlook_state(raw)
                self._json(200, result)
            except OutlookInventoryError as e:
                self._json(400, {"ok": False, "error": str(e)})
            except ValueError as e:
                self._json(400, {"ok": False, "error": redact_log_line(str(e))})
            except Exception as e:
                self._json(500, {"ok": False, "error": redact_log_line(str(e))})
            return
'''
# only in do_POST section - check if already there
post_part = t.split("def do_POST")[1] if "def do_POST" in t else ""
if 'u.path == "/api/email-provider/outlook-state"' not in post_part:
    if old_post not in t:
        raise SystemExit("post inventory anchor missing")
    t = t.replace(old_post, new_post, 1)

# HTML UI for state section
old_html = '''          <div class="section-head" style="margin-top:16px">
            <h2 style="font-size:1rem;margin:0">使用记录 outlook_state.json</h2>
            <span class="section-meta mono" id="outlook-state-meta"></span>
          </div>
          <div class="chips" id="outlook-state-kpis"></div>
          <div class="table-scroll" style="max-height:280px;overflow:auto;margin-top:8px">
            <table>
              <thead><tr><th>邮箱</th><th>next</th><th>last_alias</th><th>disabled</th><th>last_at</th></tr></thead>
              <tbody id="outlook-state-body"><tr><td colspan="5" class="domain-empty">点击「查看使用记录」</td></tr></tbody>
            </table>
          </div>
'''
new_html = '''          <div class="section-head" style="margin-top:16px">
            <h2 style="font-size:1rem;margin:0">使用记录 outlook_state.json</h2>
            <div class="button-group">
              <button type="button" onclick="loadOutlookState()">查看使用记录</button>
              <button type="button" onclick="downloadOutlookState()">下载 state</button>
              <button type="button" onclick="document.getElementById('outlook-state-file').click()">选择上传文件</button>
              <button type="button" class="primary" id="outlook-state-save" onclick="uploadOutlookState()">覆盖写入 state</button>
              <span class="section-meta mono" id="outlook-state-meta"></span>
            </div>
          </div>
          <input id="outlook-state-file" type="file" accept="application/json,.json" hidden onchange="onOutlookStateFilePicked(this)"/>
          <div class="field" style="margin-top:8px">
            <label for="outlook-state-input">state JSON（覆盖写入）</label>
            <textarea id="outlook-state-input" rows="8" spellcheck="false" autocomplete="off" placeholder='{"version":1,"next_account_cursor":0,"allocation_serial":0,"accounts":{...}}'></textarea>
          </div>
          <div class="chips" id="outlook-state-kpis"></div>
          <div class="table-scroll" style="max-height:280px;overflow:auto;margin-top:8px">
            <table>
              <thead><tr><th>邮箱</th><th>next</th><th>last_alias</th><th>disabled</th><th>last_at</th></tr></thead>
              <tbody id="outlook-state-body"><tr><td colspan="5" class="domain-empty">点击「查看使用记录」</td></tr></tbody>
            </table>
          </div>
'''
if 'id="outlook-state-input"' not in t:
    if old_html not in t:
        raise SystemExit("html state block missing")
    t = t.replace(old_html, new_html, 1)

# Also remove duplicate "查看使用记录" from top toolbar if still there - keep both ok
# JS functions after loadOutlookState
js = r'''
function downloadTextFile(filename, text){
  const blob = new Blob([text || ""], {type: "application/json;charset=utf-8"});
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename || "outlook_state.json";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
async function downloadOutlookState(){
  try{
    const j = await api("/api/email-provider/outlook-state/raw?_=" + Date.now());
    const ta = document.getElementById("outlook-state-input");
    if(ta) ta.value = j.text || "";
    downloadTextFile(j.filename || "outlook_state.json", j.text || "");
    const meta = document.getElementById("outlook-state-meta");
    if(meta) meta.textContent = (j.path || "outlook_state.json") + " · " + (j.bytes || 0) + " B";
    setMsg("outlook-inventory-msg", "已下载 " + (j.filename || "outlook_state.json"), "ok");
  }catch(e){ setMsg("outlook-inventory-msg", String(e.message||e), "err"); }
}
function onOutlookStateFilePicked(input){
  const file = input && input.files && input.files[0];
  if(!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    const ta = document.getElementById("outlook-state-input");
    if(ta) ta.value = String(reader.result || "");
    setMsg("outlook-inventory-msg", "已载入文件 " + file.name + "，确认后点覆盖写入", "ok");
  };
  reader.onerror = () => setMsg("outlook-inventory-msg", "读取文件失败", "err");
  reader.readAsText(file, "utf-8");
  input.value = "";
}
async function uploadOutlookState(){
  const btn = document.getElementById("outlook-state-save");
  if(btn) btn.disabled = true;
  setMsg("outlook-inventory-msg", "正在覆盖写入 outlook_state.json…", "");
  try{
    const text = (document.getElementById("outlook-state-input") || {}).value || "";
    if(!String(text).trim()) throw new Error("state JSON 为空");
    JSON.parse(String(text)); // client-side sanity
    if(!confirm("确认覆盖 outlook_state.json？此操作不可撤销。")){
      setMsg("outlook-inventory-msg", "已取消", "");
      if(btn) btn.disabled = false;
      return;
    }
    const j = await api("/api/email-provider/outlook-state", {
      method: "POST",
      body: JSON.stringify({ text }),
    });
    setMsg("outlook-inventory-msg", "已覆盖写入 state · " + (j.written_accounts ?? j.total_accounts ?? 0) + " 账号", "ok");
    await loadOutlookState();
  }catch(e){ setMsg("outlook-inventory-msg", String(e.message||e), "err"); }
  if(btn) btn.disabled = false;
}
'''

if "async function downloadOutlookState" not in t:
    t = t.replace(
        "async function loadOutlookState() {",
        js + "\nasync function loadOutlookState() {",
        1,
    )

# enhance loadOutlookState to also fill textarea optionally? skip

mp.write_text(t, encoding="utf-8", newline="\n")
print("monitor ok")

import py_compile
root = Path(r"E:/AI/grok-register-panel")
for f in ["webui/outlook_inventory_store.py", "webui/monitor.py"]:
    py_compile.compile(str(root/f), doraise=True)
    print("compile", f)

# smoke
from webui.outlook_inventory_store import read_state_raw, write_state, read_state
raw = read_state_raw()
print("raw bytes", raw["bytes"], raw["path"])
# roundtrip no-op write of same content should work
summary = write_state(raw["text"])
print("write", summary.get("written_accounts"), summary.get("total_accounts"))
print("ok")
