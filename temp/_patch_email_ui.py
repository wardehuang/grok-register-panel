# -*- coding: utf-8 -*-
from pathlib import Path

mp = Path(r"E:/AI/grok-register-panel/webui/monitor.py")
mt = mp.read_text(encoding="utf-8")

# imports
if "outlook_inventory_store" not in mt:
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
    from webui.outlook_inventory_store import (
        OutlookInventoryError,
        read_inventory as read_outlook_inventory,
        read_state as read_outlook_state,
        write_inventory as write_outlook_inventory,
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
    from outlook_inventory_store import (  # type: ignore
        OutlookInventoryError,
        read_inventory as read_outlook_inventory,
        read_state as read_outlook_state,
        write_inventory as write_outlook_inventory,
    )
""",
        1,
    )

# auth list GET
mt = mt.replace(
    '"/api/integrations", "/api/run-log")',
    '"/api/integrations", "/api/run-log", "/api/email-provider/outlook-inventory", "/api/email-provider/outlook-state")',
    1,
)

# GET handlers
if 'u.path == "/api/email-provider/outlook-inventory"' not in mt:
    mt = mt.replace(
        '''        if u.path == "/api/email-provider":
            try:
                self._json(200, read_email_provider_config())
            except Exception as e:
                self._json(500, {"ok": False, "error": redact_log_line(str(e))})
            return
''',
        '''        if u.path == "/api/email-provider":
            try:
                self._json(200, read_email_provider_config())
            except Exception as e:
                self._json(500, {"ok": False, "error": redact_log_line(str(e))})
            return
        if u.path == "/api/email-provider/outlook-inventory":
            try:
                qs = parse_qs(u.query or "")
                mask = str((qs.get("mask") or ["0"])[0] or "0").lower() in ("1", "true", "yes")
                self._json(200, read_outlook_inventory(mask=mask))
            except Exception as e:
                self._json(500, {"ok": False, "error": redact_log_line(str(e))})
            return
        if u.path == "/api/email-provider/outlook-state":
            try:
                qs = parse_qs(u.query or "")
                limit = int((qs.get("limit") or ["500"])[0] or 500)
                self._json(200, read_outlook_state(limit=limit))
            except Exception as e:
                self._json(500, {"ok": False, "error": redact_log_line(str(e))})
            return
''',
        1,
    )

# POST overwrite inventory
if 'u.path == "/api/email-provider/outlook-inventory"' not in mt.split("def do_POST")[1]:
    mt = mt.replace(
        '''        if u.path == "/api/email-provider":
            try:
                result = save_email_provider_config(
                    body.get("provider"),
                    body.get("settings") or {},
                    clear_secrets=body.get("clear_secrets"),
                )
                self._json(200, result)
            except ValueError as e:
                self._json(400, {"ok": False, "error": redact_log_line(str(e))})
            except Exception as e:
                self._json(500, {"ok": False, "error": redact_log_line(str(e))})
            return
''',
        '''        if u.path == "/api/email-provider":
            try:
                result = save_email_provider_config(
                    body.get("provider"),
                    body.get("settings") or {},
                    clear_secrets=body.get("clear_secrets"),
                )
                self._json(200, result)
            except ValueError as e:
                self._json(400, {"ok": False, "error": redact_log_line(str(e))})
            except Exception as e:
                self._json(500, {"ok": False, "error": redact_log_line(str(e))})
            return
        if u.path == "/api/email-provider/outlook-inventory":
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
''',
        1,
    )

# HTML panel after mail-provider-msg
html = '''
        <div class="msg mail-provider-result" id="mail-provider-msg" role="status" aria-live="polite"></div>
        <div id="outlook-inventory-panel" class="outlook-inventory-panel" hidden>
          <div class="section-head" style="margin-top:14px">
            <h2 style="font-size:1rem;margin:0">Outlook 库存 / 使用记录</h2>
            <div class="button-group">
              <button type="button" onclick="loadOutlookInventory()">查看库存</button>
              <button type="button" onclick="loadOutlookState()">查看使用记录</button>
            </div>
          </div>
          <p class="domain-format mono" id="outlook-format-hint">格式：email----password----client_id----refresh_token（每行一条，提交覆盖库存）</p>
          <div class="field">
            <label for="outlook-inventory-input">库存内容（覆盖写入）</label>
            <textarea id="outlook-inventory-input" rows="10" spellcheck="false" autocomplete="off" placeholder="email----password----client_id----refresh_token"></textarea>
          </div>
          <div class="button-group" style="margin-top:8px">
            <button type="button" class="primary" id="outlook-inventory-save" onclick="saveOutlookInventory()">覆盖写入库存</button>
            <span class="section-meta mono" id="outlook-inventory-meta"></span>
          </div>
          <div class="msg" id="outlook-inventory-msg" role="status" aria-live="polite"></div>
          <div class="section-head" style="margin-top:16px">
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
        </div>
'''
old_html = '''        <div class="msg mail-provider-result" id="mail-provider-msg" role="status" aria-live="polite"></div>
      </section>
'''
if 'id="outlook-inventory-panel"' not in mt:
    if old_html not in mt:
        raise SystemExit("html anchor missing")
    mt = mt.replace(old_html, html + "      </section>\n", 1)

# JS functions
js = r'''
function syncOutlookInventoryPanel(provider) {
  const panel = document.getElementById("outlook-inventory-panel");
  if (!panel) return;
  const show = provider === "outlook_rt";
  panel.hidden = !show;
}
async function loadOutlookInventory() {
  try {
    const j = await api("/api/email-provider/outlook-inventory?mask=0&_=" + Date.now());
    const ta = document.getElementById("outlook-inventory-input");
    if (ta) ta.value = j.text || "";
    const meta = document.getElementById("outlook-inventory-meta");
    if (meta) meta.textContent = (j.path || "") + " · " + (j.total_lines || 0) + " 条";
    setMsg("outlook-inventory-msg", "已加载库存 " + (j.total_lines || 0) + " 条", "ok");
  } catch (e) {
    setMsg("outlook-inventory-msg", String(e.message || e), "err");
  }
}
async function saveOutlookInventory() {
  const btn = document.getElementById("outlook-inventory-save");
  if (btn) btn.disabled = true;
  setMsg("outlook-inventory-msg", "正在覆盖写入…", "");
  try {
    const text = (document.getElementById("outlook-inventory-input") || {}).value || "";
    if (!String(text).trim()) throw new Error("库存内容为空");
    if (!confirm("确认覆盖库存文件？此操作不可撤销。")) {
      setMsg("outlook-inventory-msg", "已取消", "");
      if (btn) btn.disabled = false;
      return;
    }
    const j = await api("/api/email-provider/outlook-inventory", {
      method: "POST",
      body: JSON.stringify({ text }),
    });
    const meta = document.getElementById("outlook-inventory-meta");
    if (meta) meta.textContent = (j.path || "") + " · " + (j.written || j.total_lines || 0) + " 条";
    setMsg("outlook-inventory-msg", "已覆盖写入 " + (j.written || j.total_lines || 0) + " 条", "ok");
    await refreshEmailProvider(false);
  } catch (e) {
    setMsg("outlook-inventory-msg", String(e.message || e), "err");
  }
  if (btn) btn.disabled = false;
}
async function loadOutlookState() {
  try {
    const j = await api("/api/email-provider/outlook-state?limit=800&_=" + Date.now());
    const meta = document.getElementById("outlook-state-meta");
    if (meta) meta.textContent = (j.path || "outlook_state.json") + (j.exists ? "" : "（尚无文件）");
    const kpis = document.getElementById("outlook-state-kpis");
    if (kpis) {
      const items = [
        ["账号", j.account_count ?? 0],
        ["已用别名位", j.used_alias_slots ?? 0],
        ["剩余估", j.remaining_alias_slots_est ?? 0],
        ["禁用", j.disabled_count ?? 0],
        ["cursor", j.next_account_cursor ?? 0],
        ["serial", j.allocation_serial ?? 0],
      ];
      kpis.innerHTML = items.map(([k,v]) => `<span class="chip">${esc(k)}: ${esc(v)}</span>`).join(" ");
    }
    const body = document.getElementById("outlook-state-body");
    const rows = j.accounts || [];
    if (body) {
      if (!rows.length) {
        body.innerHTML = '<tr><td colspan="5" class="domain-empty">暂无使用记录</td></tr>';
      } else {
        body.innerHTML = rows.map(r => `<tr>
          <td class="mono">${esc(r.email||"")}</td>
          <td>${esc(r.next_alias_index??0)}</td>
          <td class="mono">${esc(r.last_alias||"")}</td>
          <td>${r.disabled ? "是" : ""}</td>
          <td class="mono">${esc(r.last_allocated_at||"")}</td>
        </tr>`).join("");
      }
    }
    setMsg("outlook-inventory-msg", "已加载使用记录 " + (j.total_accounts || 0) + " 账号", "ok");
  } catch (e) {
    setMsg("outlook-inventory-msg", String(e.message || e), "err");
  }
}
'''

if "async function loadOutlookInventory" not in mt:
    mt = mt.replace(
        "function renderEmailProviderFields(provider) {",
        js + "\nfunction renderEmailProviderFields(provider) {",
        1,
    )

# call sync in renderEmailProviderFields
if "syncOutlookInventoryPanel" not in mt[mt.find("function renderEmailProviderFields"):mt.find("function renderEmailProviderFields")+900]:
    mt = mt.replace(
        '''  document.getElementById("mail-provider-fields").innerHTML = (definition.fields || []).map(field =>
    `<div class="field"><label for="mail-field-${esc(field.name)}">${esc(field.label)}</label>${emailProviderFieldControl(field)}</div>`
  ).join("") || '<div class="field"><label>服务配置</label><input disabled value="该服务商没有可编辑字段"/></div>';
''',
        '''  document.getElementById("mail-provider-fields").innerHTML = (definition.fields || []).map(field =>
    `<div class="field"><label for="mail-field-${esc(field.name)}">${esc(field.label)}</label>${emailProviderFieldControl(field)}</div>`
  ).join("") || '<div class="field"><label>服务配置</label><input disabled value="该服务商没有可编辑字段"/></div>';
  syncOutlookInventoryPanel(definition.id);
''',
        1,
    )

mp.write_text(mt, encoding="utf-8", newline="\n")
print("monitor ok", mp.stat().st_size)

# compile
import py_compile
for f in [
    "webui/outlook_inventory_store.py",
    "webui/email_provider_store.py",
    "webui/monitor.py",
    "connectivity.py",
]:
    py_compile.compile(str(ROOT / f), doraise=True)
    print("compiled", f)
