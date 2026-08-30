from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "browser_session.py"
OUT = ROOT / "temp" / "browser-session-owner-thread-hotfix.tar.gz"

if not SOURCE.is_file():
    raise SystemExit(f"missing rollout file: {SOURCE}")

text = SOURCE.read_text(encoding="utf-8")
data = text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
with tarfile.open(OUT, "w:gz") as archive:
    info = tarfile.TarInfo("browser_session.py")
    info.size = len(data)
    info.mode = 0o644
    archive.addfile(info, io.BytesIO(data))

with tarfile.open(OUT, "r:gz") as archive:
    members = archive.getmembers()
    if [member.name for member in members] != ["browser_session.py"]:
        raise SystemExit(f"unexpected archive members: {[member.name for member in members]}")
    payload = archive.extractfile(members[0]).read()
    if b"\r" in payload:
        raise SystemExit("CRLF in archive")

print(f"archive={OUT}")
print(f"sha256={hashlib.sha256(OUT.read_bytes()).hexdigest()}")
print(f"bytes={OUT.stat().st_size}")
