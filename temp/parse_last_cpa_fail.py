#!/usr/bin/env python3
"""Parse last grok-register-panel batch run for CPA fail accounts. No secrets."""
from __future__ import annotations

import json
import re
from pathlib import Path

LOG = Path("/home/ubuntu/grok-register-panel/log/runs/run_batch_20260901_072504.log")
RESULTS = Path("/home/ubuntu/grok-register-panel/log/register_results.jsonl")
STATS = Path(str(LOG) + ".stats.json")
ORCH = Path("/home/ubuntu/grok-register-panel/log/batch-orch-20260901-072504-n2000.log")

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
REGISTERED_RE = re.compile(r"registered_emails 已更新:\s+(\S+)\s+sso=(\S+)")
CPA_FAIL_EMAIL_RE = re.compile(r"CPA 入库失败:\s*(\S+)")
WORKER_RE = re.compile(r"\[W(\d+)\]")
OK_EMAIL_RE = re.compile(r"注册成功(?:（SSO 已保存，CPA 入库失败）)?:\s*(\S+)")

FAIL_MARKERS = (
    "换 token 失败",
    "智商失败",
    "代理池耗尽",
    "不写入 CPA",
    "CPA 本地写入失败",
    "CPA 远程上传失败",
    "均未写入成功",
    "直出失败",
    "CPA 入库失败",
    "bfs_skip_cpa",
    "待重转 SSO",
    "写入 sso_pending 失败",
)

SECRET_RE = re.compile(
    r"(sso=)\S+|([A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})"
)


def redact(text: str) -> str:
    text = SECRET_RE.sub(r"\1<redacted>", text)
    text = re.sub(r"(socks5h?://)([^/@\s]+@)", r"\1<redacted>@", text, flags=re.I)
    return text[:500]


def worker_of(line: str) -> str:
    m = WORKER_RE.search(line)
    return m.group(1) if m else "g"


def main() -> None:
    stats = json.loads(STATS.read_text(encoding="utf-8"))
    print("=== STATS ===")
    print(
        json.dumps(
            {
                "path": stats.get("path"),
                "started_at": stats.get("started_at"),
                "ended_at": stats.get("ended_at"),
                "reason": stats.get("reason"),
                "success": stats.get("success"),
                "fail": stats.get("fail"),
                "detail_stats": stats.get("detail_stats"),
                "fail_stats_cpa": (stats.get("fail_stats") or {}).get("cpa"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    print(f"\n=== LOG lines={len(lines)} size={LOG.stat().st_size} ===")

    worker_email: dict[str, str] = {}
    last_email = ""
    explicit: list[dict] = []
    cpa_events: list[dict] = []

    for i, line in enumerate(lines):
        wid = worker_of(line)
        m = REGISTERED_RE.search(line)
        if m:
            worker_email[wid] = m.group(1)
            last_email = m.group(1)
        m_ok = OK_EMAIL_RE.search(line)
        if m_ok:
            worker_email[wid] = m_ok.group(1)
            last_email = m_ok.group(1)

        m_fail = CPA_FAIL_EMAIL_RE.search(line)
        if m_fail:
            email = m_fail.group(1)
            # collect nearby CPA lines
            nearby = []
            for j in range(max(0, i - 40), i + 1):
                if "[CPA]" in lines[j] or "智商" in lines[j] or "CPA" in lines[j]:
                    nearby.append({"n": j + 1, "t": redact(lines[j])})
            explicit.append(
                {
                    "line": i + 1,
                    "email": email,
                    "worker": wid,
                    "text": redact(line),
                    "nearby_cpa": nearby[-12:],
                }
            )

        if "[CPA]" in line and any(k in line for k in FAIL_MARKERS):
            email = worker_email.get(wid) or last_email
            cpa_events.append(
                {
                    "line": i + 1,
                    "worker": wid,
                    "email": email,
                    "text": redact(line),
                }
            )

    print("\n=== EXPLICIT CPA入库失败 ===")
    print(json.dumps(explicit, ensure_ascii=False, indent=2))
    print("\n=== CPA FAIL-MARKER EVENTS ===")
    print(json.dumps(cpa_events, ensure_ascii=False, indent=2))

    # jsonl: this run window 2026-09-01 07:25 UTC-ish through 2026-09-02 00:32 UTC
    # started_at in stats is 2026-09-01 15:25:04 which is Beijing (UTC+8) => 07:25 UTC
    if RESULTS.is_file():
        interesting = []
        for raw in RESULTS.read_text(encoding="utf-8", errors="replace").splitlines():
            if not raw.strip():
                continue
            try:
                rec = json.loads(raw)
            except Exception:
                continue
            ts = str(rec.get("ts") or "")
            if not (ts.startswith("2026-09-01T") or ts.startswith("2026-09-02T")):
                continue
            kind = str(rec.get("kind") or "")
            detail = str(rec.get("detail") or "")
            status = str(rec.get("status") or "")
            if any(
                k in (kind + " " + detail + " " + status).lower()
                for k in ("cpa", "智商", "intelligence", "bfs", "token")
            ):
                interesting.append(
                    {
                        "ts": ts,
                        "status": status,
                        "email": rec.get("email"),
                        "kind": kind,
                        "detail": redact(detail),
                        "bfs": rec.get("bfs"),
                    }
                )
        print("\n=== register_results.jsonl interesting in window ===")
        print("count", len(interesting))
        print(json.dumps(interesting[-30:], ensure_ascii=False, indent=2))

    # orch log cpa mentions
    if ORCH.is_file():
        orch_hits = []
        for i, line in enumerate(ORCH.read_text(encoding="utf-8", errors="replace").splitlines()):
            if "CPA" in line or "智商" in line:
                orch_hits.append({"n": i + 1, "t": redact(line)})
        print("\n=== orch CPA/智商 lines ===")
        print("count", len(orch_hits))
        print(json.dumps(orch_hits[-40:], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
