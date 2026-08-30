# -*- coding: utf-8 -*-
from pathlib import Path

path = Path("grok_register_ttk.py")
text = path.read_text(encoding="utf-8")

# 1) CLI init fail_stats near success_count
old = """    success_count = 0
    fail_count = 0
    fail_stats = empty_fail_stats()
"""
if old not in text:
    raise SystemExit("cli init not found")
text = text.replace(
    old,
    """    success_count = 0
    fail_count = 0
    fail_stats = empty_fail_stats()
    detail_stats = empty_detail_stats()
""",
    1,
)

# 2) _cli_record_failure
old = """    def _cli_record_failure(exc):
        nonlocal fail_count
        kind = classify_failure(exc)
        fail_count += 1
        fail_stats[kind] = fail_stats.get(kind, 0) + 1
        return kind
"""
if old not in text:
    raise SystemExit("cli_record_failure not found")
text = text.replace(
    old,
    """    def _cli_record_failure(exc):
        nonlocal fail_count
        kind = classify_failure(exc)
        fail_count += 1
        fail_stats[kind] = fail_stats.get(kind, 0) + 1
        note_failure_detail(detail_stats, kind)
        return kind
""",
    1,
)

# 3) multi-worker shared dict
old = """        shared = {"success": 0, "fail": 0, "fail_stats": empty_fail_stats()}
"""
if old not in text:
    raise SystemExit("shared dict not found")
text = text.replace(
    old,
    """        shared = {
            "success": 0,
            "fail": 0,
            "fail_stats": empty_fail_stats(),
            "detail_stats": empty_detail_stats(),
        }
""",
    1,
)

# 4) multi success path finalize
old = """                        cpa_ok = finalize_sso_after_register(
                            sso, email=email, log_callback=lambda m: cli_log(f"[W{wid+1}] {m}")
                        )
                        try:
                            release_proxy_lease(wid, rewind=False)
                        except Exception:
                            pass
                        local_success += 1
                        mark_successful_account()
"""
if old not in text:
    raise SystemExit("multi finalize not found")
text = text.replace(
    old,
    """                        bump_detail_stats(detail_stats, "sso_ok", lock=stats_lock)
                        cpa_ok = finalize_sso_after_register(
                            sso,
                            email=email,
                            log_callback=lambda m: cli_log(f"[W{wid+1}] {m}"),
                            detail_stats=detail_stats,
                            detail_lock=stats_lock,
                        )
                        try:
                            release_proxy_lease(wid, rewind=False)
                        except Exception:
                            pass
                        local_success += 1
                        bump_detail_stats(detail_stats, "register_ok", lock=stats_lock)
                        mark_successful_account()
                        with stats_lock:
                            _sc = int(shared["success"]) + local_success
                            _fc = int(shared["fail"]) + local_fail
                        cli_log(
                            format_current_stats_line(_sc, _fc, detail_stats)
                        )
""",
    1,
)

# multi worker local_fail should also note detail - they use local_fail_stats and merge.
# Need local_detail or use shared detail via note on merge from fail kinds.

# 5) multi merge at end of worker
old = """                with stats_lock:
                    shared["success"] += local_success
                    shared["fail"] += local_fail
                    for k, v in local_fail_stats.items():
                        shared["fail_stats"][k] = shared["fail_stats"].get(k, 0) + v
"""
if old not in text:
    # try without local_fail line exact
    import re
    m = re.search(r"with stats_lock:\n                    shared\[\"success\"\] \+= local_success\n                    shared\[\"fail\"\] \+= local_fail\n                    for k, v in local_fail_stats.items\(\):\n                        shared\[\"fail_stats\"\]\[k\] = shared\[\"fail_stats\"\]\.get\(k, 0\) \+ v\n", text)
    if not m:
        raise SystemExit("merge block not found")
    print("found merge via regex")
else:
    text = text.replace(
        old,
        """                with stats_lock:
                    shared["success"] += local_success
                    shared["fail"] += local_fail
                    for k, v in local_fail_stats.items():
                        shared["fail_stats"][k] = shared["fail_stats"].get(k, 0) + v
                        # 把 worker 内失败 kind 汇总进 detail（sso/risk/register 等）
                        if v:
                            note_failure_detail(shared["detail_stats"], k)
                            # note_failure_detail only +1; fix for multi
                            if v > 1:
                                # re-apply remaining
                                for _ in range(v - 1):
                                    note_failure_detail(shared["detail_stats"], k)
""",
        1,
    )

# Problem: multi worker already bumps detail_stats on success path with shared detail_stats,
# but failures use local_fail_stats only. Better: use shared detail_stats for failures too via stats_lock.

# Actually success path already uses detail_stats variable - need to make sure multi workers share same detail_stats object = shared["detail_stats"]

# Find worker start local_success and ensure detail_stats = shared["detail_stats"]
# After shared = {...}, workers close over detail_stats from outer - currently detail_stats is outer empty, and we're bumping that from all workers with lock - GOOD if same object.

# Wait - on merge I'm double-counting failures if workers also call note_failure. Multi workers currently don't call note_failure on local fails. They only update local_fail_stats. So merge-time note is correct.

# But success path bumps detail_stats (outer) with lock - good.
# Risk: finalize risk_fail bumps detail_stats, then exception adds to local_fail_stats FAIL_RISK, then merge notes risk again as register_fail only for RISK (risk_fail already counted). Good for risk.
# For SSO fail: exception path doesn't bump sso_fail until merge note_failure - good.

# Double register_ok? success path bumps register_ok AND local_success merged to shared success - good once.

# 6) After multi join, set detail_stats from shared
old = """        success_count = shared["success"]
        fail_count = shared["fail"]
        fail_stats = shared["fail_stats"]
        cli_log(
            f"[*] 任务结束。成功 {success_count} | 失败 {fail_count}"
            + (f" | {format_fail_stats(fail_stats)}" if fail_count else "")
        )
"""
if old not in text:
    raise SystemExit("multi end not found")
text = text.replace(
    old,
    """        success_count = shared["success"]
        fail_count = shared["fail"]
        fail_stats = shared["fail_stats"]
        detail_stats = shared.get("detail_stats") or detail_stats
        cli_log(
            f"[*] 任务结束。成功 {success_count} | 失败 {fail_count}"
            + (f" | {format_fail_stats(fail_stats)}" if fail_count else "")
        )
        cli_log(format_current_stats_line(success_count, fail_count, detail_stats))
""",
    1,
)

# 7) single-worker success path
old = """                cpa_ok = finalize_sso_after_register(sso, email=email, log_callback=cli_log)
                try:
                    release_proxy_lease(0, rewind=False)
                except Exception:
                    pass
                success_count += 1
                mark_successful_account()
                retry_count_for_slot = 0
                i += 1
                if cpa_ok:
                    cli_log(f"[+] 注册成功: {email}")
                else:
                    cli_log(f"[+] 注册成功（SSO 已保存，CPA 入库失败）: {email}")
                record_register_result(
                    "ok",
                    email,
                    kind="success",
                    detail="cpa_ok" if cpa_ok else "cpa_fail",
                    worker="W1",
                    bot_flag=0,
                    log_callback=cli_log,
                )
                if success_count % 2 == 0:
                    single_rotate_idx += 1
                cli_log(f"[*] 当前统计: 成功 {success_count} | 失败 {fail_count}")
"""
if old not in text:
    raise SystemExit("single success not found")
text = text.replace(
    old,
    """                bump_detail_stats(detail_stats, "sso_ok")
                cpa_ok = finalize_sso_after_register(
                    sso,
                    email=email,
                    log_callback=cli_log,
                    detail_stats=detail_stats,
                )
                try:
                    release_proxy_lease(0, rewind=False)
                except Exception:
                    pass
                success_count += 1
                bump_detail_stats(detail_stats, "register_ok")
                mark_successful_account()
                retry_count_for_slot = 0
                i += 1
                if cpa_ok:
                    cli_log(f"[+] 注册成功: {email}")
                else:
                    cli_log(f"[+] 注册成功（SSO 已保存，CPA 入库失败）: {email}")
                record_register_result(
                    "ok",
                    email,
                    kind="success",
                    detail="cpa_ok" if cpa_ok else "cpa_fail",
                    worker="W1",
                    bot_flag=0,
                    log_callback=cli_log,
                )
                if success_count % 2 == 0:
                    single_rotate_idx += 1
                cli_log(format_current_stats_line(success_count, fail_count, detail_stats))
""",
    1,
)

# 8) single end summary
old = """                f"[*] 任务结束。成功 {success_count} | 失败 {fail_count}"
                + (f" | {format_fail_stats(fail_stats)}" if fail_count else "")
            )
        try:
            reason = (
"""
# There may be two end summaries - multi already patched. Find remaining simple ones.
count = text.count('f"[*] 任务结束。成功 {success_count} | 失败 {fail_count}"')
print("end count", count)

# single path finalize_run_stats - add detail_stats to update_run_stats calls
import re
def add_detail_to_update(m):
    block = m.group(0)
    if "detail_stats=" in block:
        return block
    return block.replace(
        "fail_stats=dict(fail_stats or {}),",
        "fail_stats=dict(fail_stats or {}),\n                detail_stats=dict(detail_stats or {}),",
    )

text2, n = re.subn(
    r"update_run_stats\(\s*success=int\(success_count\),.*?completed=int\(success_count\) \+ int\(fail_count\),\s*\)",
    add_detail_to_update,
    text,
    flags=re.S,
)
print("update_run_stats patched", n)
text = text2

# Append current stats line before each remaining 任务结束 for single if not already next line
needle = 'f"[*] 任务结束。成功 {success_count} | 失败 {fail_count}"'
idx = 0
while True:
    i = text.find(needle, idx)
    if i < 0:
        break
    # if already has format_current nearby after, skip
    window = text[i:i+400]
    if "format_current_stats_line(success_count, fail_count, detail_stats)" in window:
        idx = i + 10
        continue
    # insert after the cli_log( ... ) block - find closing )
    # simpler: after the line with format_fail_stats
    j = text.find("\n", i)
    # find end of cli_log statement
    k = text.find(")\n", i)
    if k > 0 and "format_current_stats_line" not in text[i:k+40]:
        insert_at = k + 2
        text = (
            text[:insert_at]
            + "        cli_log(format_current_stats_line(success_count, fail_count, detail_stats))\n"
            + text[insert_at:]
        )
        idx = insert_at + 80
    else:
        idx = i + 10

path.write_text(text, encoding="utf-8")
print("patched ok")
