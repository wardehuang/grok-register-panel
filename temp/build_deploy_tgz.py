# -*- coding: utf-8 -*-
from pathlib import Path
import tarfile
import io
import stat

root = Path(r"E:/AI/grok-register-panel")
out = root / "temp" / "grok-register-panel-deploy.tgz"
out.parent.mkdir(parents=True, exist_ok=True)

include = [
    "integrations_push.py",
    "proxy_cursor.py",
    "run_log.py",
    "grok_register_ttk.py",
    "config.example.json",
    "email_providers/outlook_alias_pool.py",
    "email_providers/outlook_rt.py",
    "email_providers/__init__.py",
    "webui/integrations_store.py",
    "webui/monitor.py",
    "accounts/outlook_accounts.txt",
    "accounts/outlook_state.json",
    "batch_traffic.py",
    "sso_to_auth_json.py",
    "common.py",
    "connectivity.py",
    "batch_supervisor.py",
    "run_batch_headless.py",
]

missing = []
with tarfile.open(out, "w:gz", format=tarfile.GNU_FORMAT) as tar:
    for rel in include:
        path = root / rel
        if not path.is_file():
            missing.append(rel)
            continue
        data = path.read_bytes()
        suffix = path.suffix.lower()
        if suffix in {".py", ".json", ".txt", ".md", ".sh"} or "example" in path.name:
            try:
                text = data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
                data = text.encode("utf-8")
            except UnicodeDecodeError:
                pass
        info = tarfile.TarInfo(name=rel)
        info.size = len(data)
        if "accounts/" in rel or rel.endswith("state.json"):
            info.mode = 0o600
        else:
            info.mode = 0o644
        tar.addfile(info, io.BytesIO(data))

print("archive", out)
print("bytes", out.stat().st_size)
print("missing", missing)
with tarfile.open(out, "r:gz") as tar:
    names = tar.getnames()
    print("members", len(names))
    crlf = []
    for name in names:
        blob = tar.extractfile(name).read()
        if b"\r\n" in blob or (b"\r" in blob and name.endswith(".py")):
            crlf.append(name)
    print("crlf", crlf or "none")
    print("lf_ok")
