#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import tarfile
import time
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "temp"
FILES = (
    "intelligence_check.py",
    "webui/quality_proxy_store.py",
    "webui/monitor.py",
)


def lf_bytes(path: Path) -> bytes:
    data = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    if bytes((13, 10)) in data:
        raise SystemExit(f"CRLF remains in {path}")
    return data


def main() -> None:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    archive = OUT_DIR / f"iq-consume-rollout-{stamp}.tar.gz"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    crlf = bytes((13, 10))
    with tarfile.open(archive, "w:gz", format=tarfile.GNU_FORMAT) as tar:
        for rel in FILES:
            src = ROOT / rel
            if not src.is_file():
                raise SystemExit(f"missing {rel}")
            payload = lf_bytes(src)
            if crlf in payload:
                raise SystemExit(f"archive member still CRLF: {rel}")
            info = tarfile.TarInfo(name=rel)
            info.size = len(payload)
            info.mtime = int(src.stat().st_mtime)
            info.mode = 0o644
            tar.addfile(info, BytesIO(payload))
    raw = archive.read_bytes()
    print(f"archive={archive}")
    print(f"archive_sha256={hashlib.sha256(raw).hexdigest()}")
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            data = tar.extractfile(member).read()
            if crlf in data:
                raise SystemExit(f"packed CRLF: {member.name}")
            print(
                f"member={member.name} size={member.size} "
                f"sha256={hashlib.sha256(data).hexdigest()}"
            )
    print("pack_ok")


if __name__ == "__main__":
    main()
