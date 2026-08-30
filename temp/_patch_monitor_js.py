# -*- coding: utf-8 -*-
from pathlib import Path

path = Path("webui/monitor.py")
text = path.read_text(encoding="utf-8")
start = text.index("async function refreshRegisteredEmails(){")
end = text.index("let authFilesCache = {cpa: [], g2a: []};")
new_js = r'''async function refreshRegisteredEmails(){
  setMsg("reg-emails-msg", "加载已注册账号…", "");
  try{
    const j = await api("/api/registered-emails?limit=20000&_=" + Date.now());
    registeredEmailsText = j.export_text || "";
    registeredEmailsRows = j.accounts || [];
    const ta = document.getElementById("reg-emails-text");
    if(ta) ta.value = registeredEmailsText;
    const meta = document.getElementById("reg-emails-meta");
    if(meta) meta.textContent = (j.format || "email----password----sso") + " · " + (j.count||0) + " 个 · 有SSO " + (j.with_sso||0) + " · 无SSO " + (j.without_sso||0);
    const kpis = document.getElementById("reg-emails-kpis");
    if(kpis){
      const items = [
        ["数量", j.count ?? 0],
        ["有SSO", j.with_sso ?? 0],
        ["无SSO", j.without_sso ?? 0],
        ["文件", j.path || "accounts/registered_emails.txt"],
      ];
      kpis.innerHTML = items.map(([k,v]) => `<span class="chip">${esc(k)}: ${esc(v)}</span>`).join(" ");
    }
    const body = document.getElementById("reg-emails-body");
    if(body){
      const rows = registeredEmailsRows;
      if(!rows.length){
        body.innerHTML = '<tr><td colspan="5" class="domain-empty">暂无已注册账号</td></tr>';
      } else {
        body.innerHTML = rows.map(r => {
          const em = r.email || "";
          const q = encodeURIComponent(em);
          const ssoMark = r.has_sso ? "有" : "空";
          return `<tr>
            <td class="mono">${esc(em)}</td>
            <td class="mono">${esc(ssoMark)}</td>
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
async function importRegisteredEmails(){
  const ta = document.getElementById("reg-emails-text");
  const text = ta ? ta.value : "";
  if(!String(text||"").trim()){
    setMsg("reg-emails-msg", "文本框为空，请粘贴 email----password----sso", "err");
    return;
  }
  if(!confirm("将用文本框内容【整体覆盖】accounts/registered_emails.txt，确认？")) return;
  setMsg("reg-emails-msg", "正在覆盖写入…", "");
  try{
    const j = await api("/api/registered-emails/import", {
      method: "POST",
      body: JSON.stringify({ text }),
    });
    setMsg("reg-emails-msg", "已覆盖 " + (j.count||0) + " 行（有SSO " + (j.with_sso||0) + "）", "ok");
    await refreshRegisteredEmails();
  }catch(e){ setMsg("reg-emails-msg", String(e.message||e), "err"); }
}
async function uploadRegisteredEmailsFile(ev){
  try{
    const file = ev && ev.target && ev.target.files && ev.target.files[0];
    if(!file) return;
    const text = await file.text();
    if(!String(text||"").trim()){
      setMsg("reg-emails-msg", "文件为空", "err");
      return;
    }
    if(!confirm("上传文件将【整体覆盖】registered_emails.txt，确认？\n" + file.name)) return;
    setMsg("reg-emails-msg", "正在上传覆盖…", "");
    const j = await api("/api/registered-emails/import", {
      method: "POST",
      body: JSON.stringify({ text }),
    });
    setMsg("reg-emails-msg", "已覆盖 " + (j.count||0) + " 行", "ok");
    if(ev && ev.target) ev.target.value = "";
    await refreshRegisteredEmails();
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
    const name = (j.email || email || "account").replace(/[\\/]/g,"_") + ".txt";
    downloadTextFile(name, (j.line || "") + "\n");
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

function renderReloginStatus(data){
  const st = document.getElementById("relogin-status");
  if(st) st.textContent = data.running ? ("重登中 #" + (data.pid || "?")) : "空闲";
  const startBtn = document.getElementById("relogin-start");
  const stopBtn = document.getElementById("relogin-stop");
  if(startBtn) startBtn.disabled = !!data.running;
  if(stopBtn) stopBtn.disabled = !data.running;
  const rep = data.last_report || {};
  const kpis = document.getElementById("relogin-kpis");
  if(kpis){
    const items = [
      ["输入", rep.input_count ?? 0],
      ["成功", rep.success_count ?? 0],
      ["失败", rep.fail_count ?? 0],
      ["跳过", rep.skipped_count ?? 0],
    ];
    kpis.innerHTML = items.map(([k,v]) => `<span class="chip">${esc(k)}: ${esc(v)}</span>`).join(" ");
  }
  const pre = document.getElementById("relogin-report");
  if(pre){
    const lines = [];
    if(rep.started_at) lines.push("started: " + rep.started_at);
    if(rep.finished_at) lines.push("finished: " + rep.finished_at);
    if(rep.cancelled) lines.push("cancelled: true");
    if(rep.error) lines.push("error: " + rep.error);
    for(const it of (rep.items || [])){
      lines.push((it.ok ? "[+]" : "[-]") + " " + (it.email || "") + " · " + (it.reason || ""));
    }
    pre.textContent = lines.join("\n") || "暂无报告";
  }
}
async function refreshBatchRelogin(){
  try{
    const data = await api("/api/batch-relogin?_=" + Date.now(), { authHelp: false });
    renderReloginStatus(data || {});
  }catch(e){
    const st = document.getElementById("relogin-status");
    if(st) st.textContent = "检查失败";
  }
}
async function startBatchRelogin(){
  const ta = document.getElementById("relogin-emails");
  const text = ta ? ta.value : "";
  if(!String(text||"").trim()){
    setMsg("relogin-msg", "请输入至少一个邮箱", "err");
    return;
  }
  if(!confirm("开始批量重登？将按账号间隔依次浏览器登录。")) return;
  setMsg("relogin-msg", "正在启动…", "");
  try{
    const data = await api("/api/batch-relogin/start", {
      method: "POST",
      body: JSON.stringify({ text }),
    });
    setMsg("relogin-msg", "已启动，共 " + (data.input_count || 0) + " 个 · log=" + (data.log || ""), "ok");
    await refreshBatchRelogin();
  }catch(e){ setMsg("relogin-msg", String(e.message||e), "err"); }
}
async function stopBatchRelogin(){
  try{
    const data = await api("/api/batch-relogin/stop", { method: "POST", body: "{}" });
    setMsg("relogin-msg", "已请求停止 " + JSON.stringify(data.killed || []), "ok");
    await refreshBatchRelogin();
  }catch(e){ setMsg("relogin-msg", String(e.message||e), "err"); }
}

'''
path.write_text(text[:start] + new_js + text[end:], encoding="utf-8")
print("ok", start, end)
