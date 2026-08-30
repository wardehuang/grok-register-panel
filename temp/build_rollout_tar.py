from __future__ import annotations

import hashlib
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    Path("browser_session.py"),
    Path("grok_register_ttk.py"),
    Path("run_batch_headless.py"),
    Path("deploy/grok-register-panel.service.example"),
)
OUT = ROOT / "temp" / "grok-register-panel-process-cleanup.tar.gz"

missing = [str(path) for path in FILES if not (ROOT / path).is_file()]
if missing:
    raise SystemExit(f"missing rollout files: {missing}")

with tarfile.open(OUT, "w:gz") as archive:
    for relative in FILES:
        source = ROOT / relative
        text = source.read_text(encoding="utf-8")
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        data = normalized.encode("utf-8")
        info = tarfile.TarInfo(relative.as_posix())
        info.size = len(data)
        info.mode = 0o644
        archive.addfile(info, __import__("io").BytesIO(data))

with tarfile.open(OUT, "r:gz") as archive:
    names = archive.getnames()
    expected = [path.as_posix() for path in FILES]
    if names != expected:
        raise SystemExit(f"unexpected archive members: {names}")
    bad = []
    for member in archive.getmembers():
        if b"\r\n" in archive.extractfile(member).read():
            bad.append(member.name)
    if bad:
        raise SystemExit(f"CRLF in archive members: {bad}")

sha256 = hashlib.sha256(OUT.read_bytes()).hexdigest()
print(f"archive={OUT}")
print(f"members={','.join(path.as_posix() for path in FILES)}")
print(f"sha256={sha256}")
print(f"bytes={OUT.stat().st_size}")
