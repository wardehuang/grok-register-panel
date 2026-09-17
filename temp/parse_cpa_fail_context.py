#!/usr/bin/env python3
"""Dump context around the two CPA intelligence failures. No secrets."""
from __future__ import annotations

import json
import re
from pathlib import Path

LOG = Path("/home/ubuntu/grok-register-panel/log/runs/run_batch_20260901_072504.log")
ORCH = Path("/home/ubuntu/grok-register-panel/log/batch-orch-20260901-072504-n2000.log")
RESULTS = Path("/home/ubuntu/grok-register-panel/log/register_results.jsonl")

SECRET_RE = re.compile(
    r"(sso=)\S+|([A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})"
)


def redact(text: str) -> str:
    text = SECRET_RE.sub(r"\1<redacted>", text)
    text = re.sub(r"(socks5h?://)([^/@\s]+@)", r"\1<redacted>@", text, flags=re.I)
    text = re.sub(r"(https?://)([^/@\s]+@)", r"\1<redacted>@", text, flags=re.I)
    return text


def dump_window(path: Path, needle: str, before: int = 50, after: int = 8) -> None:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    hits = [i for i, line in enumerate(lines) if needle in line]
    print(f"\n===== {path.name} needle={needle!r} hits={len(hits)} =====")
    for i in hits:
        start = max(0, i - before)
        end = min(len(lines), i + after + 1)
        print(f"--- window {start+1}-{end} around {i+1} ---")
        for j in range(start, end):
            mark = ">>" if j == i else "  "
            print(f"{mark} {j+1}|{redact(lines[j])}")


def main() -> None:
    dump_window(LOG, "[智商检测] 智商失败，不写入 CPA 本地/远程", before=60, after=12)
    dump_window(ORCH, "[智商检测] 智商失败，不写入 CPA 本地/远程", before=60, after=12)

    print("\n===== register_results.jsonl around fail UTC windows =====")
    # 16:46:46 CST = 08:46:46Z; 17:13:23 CST = 09:13:23Z
    windows = [("2026-09-01T08:40", "2026-09-01T08:55"), ("2026-09-01T09:05", "2026-09-01T09:20")]
    for lo, hi in windows:
        print(f"\n-- {lo} .. {hi} --")
        for raw in RESULTS.read_text(encoding="utf-8", errors="replace").splitlines():
            if not raw.strip():
                continue
            try:
                rec = json.loads(raw)
            except Exception:
                continue
            ts = str(rec.get("ts") or "")
            if lo <= ts <= hi + "Z" or (lo <= ts <= hi):
                print(
                    json.dumps(
                        {
                            "ts": rec.get("ts"),
                            "status": rec.get("status"),
                            "email": rec.get("email"),
                            "kind": rec.get("kind"),
                            "detail": redact(str(rec.get("detail") or ""))[:240],
                            "bfs": rec.get("bfs"),
                            "bot_flag": rec.get("bot_flag"),
                            "risk": rec.get("risk"),
                        },
                        ensure_ascii=False,
                    )
                )

    print("\n===== grep 入库失败 / 智商失败 / 连续两次降智 =====")
    for path in (LOG, ORCH):
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        keys = ("CPA 入库失败", "智商失败", "连续两次降智", "判定智商失败", "× 【降智】")
        for i, line in enumerate(lines):
            if any(k in line for k in keys):
                print(f"{path.name}:{i+1}|{redact(line)}")


if __name__ == "__main__":
    main()
