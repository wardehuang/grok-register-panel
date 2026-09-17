# -*- coding: utf-8 -*-
"""Batch re-login: lookup password, get SSO, then push g2a/CPA."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from secure_files import atomic_write_json, ensure_private_dir  # noqa: E402


def _install_tkinter_stub() -> None:
    """Headless servers often lack tkinter; grok_register_ttk imports it at module level."""
    try:
        import tkinter  # noqa: F401
        return
    except ImportError:
        pass
    import types

    tk = types.ModuleType("tkinter")

    class _NullWidget:
        def __init__(self, *args, **kwargs):
            pass

        def __getattr__(self, _name):
            return lambda *args, **kwargs: None

    for name in ("Tk", "StringVar", "IntVar", "BooleanVar"):
        setattr(tk, name, _NullWidget)
    tk.END = "end"
    tk.DISABLED = "disabled"
    tk.NORMAL = "normal"
    tk.LEFT = "left"
    tk.RIGHT = "right"
    tk.BOTH = "both"
    tk.X = "x"
    tk.Y = "y"
    tk.W = "w"
    tk.E = "e"
    tk.N = "n"
    tk.S = "s"

    ttk_module = types.ModuleType("tkinter.ttk")
    for name in (
        "Frame",
        "Label",
        "Button",
        "Entry",
        "Combobox",
        "Spinbox",
        "Checkbutton",
        "Notebook",
        "Style",
        "Scrollbar",
        "Treeview",
    ):
        setattr(ttk_module, name, _NullWidget)
    scrolled_text = types.ModuleType("tkinter.scrolledtext")
    scrolled_text.ScrolledText = _NullWidget
    messagebox = types.ModuleType("tkinter.messagebox")
    messagebox.showinfo = lambda *args, **kwargs: None
    messagebox.showerror = messagebox.showinfo
    messagebox.showwarning = messagebox.showinfo
    messagebox.askyesno = messagebox.showinfo
    sys.modules["tkinter"] = tk
    sys.modules["tkinter.ttk"] = ttk_module
    sys.modules["tkinter.scrolledtext"] = scrolled_text
    sys.modules["tkinter.messagebox"] = messagebox


def _load_emails(path: Path | None, inline: str) -> list[str]:
    text = ""
    if path is not None and path.is_file():
        text = path.read_text(encoding="utf-8", errors="replace")
    if inline:
        text = (text + "\n" if text else "") + inline
    from webui.registered_accounts_store import parse_email_list

    return parse_email_list(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Batch re-login xAI accounts for SSO")
    parser.add_argument("--emails-file", default="", help="File with one email per line")
    parser.add_argument("--emails-text", default="", help="Inline emails text")
    parser.add_argument("--config", default=str(ROOT / "config.json"))
    parser.add_argument("--report-json", default=str(ROOT / "log" / "batch_relogin_report.json"))
    parser.add_argument("--stop-file", default=str(ROOT / "log" / "batch_relogin.stop"))
    parser.add_argument(
        "--only-cpa",
        action="store_true",
        help="仅推送 CPA，跳过 Grok2API Web/Console",
    )
    args = parser.parse_args(argv)

    emails_path = Path(args.emails_file) if args.emails_file else None
    emails = _load_emails(emails_path, args.emails_text)
    if not emails:
        print("[!] no emails to process", flush=True)
        return 2

    stop_file = Path(args.stop_file)
    report_path = Path(args.report_json)
    ensure_private_dir(report_path.parent)
    try:
        if stop_file.is_file():
            stop_file.unlink()
    except OSError:
        pass

    # Import after path setup; load config into grok module
    _install_tkinter_stub()
    import grok_register_ttk as gr
    from register_flow import login_and_get_sso
    from browser_session import stop_browser, restart_browser
    from webui.registered_accounts_store import (
        lookup_registered_account,
        upsert_registered_account,
    )

    gr.load_config()
    # ensure config path if custom
    cfg_path = Path(args.config)
    if cfg_path.is_file() and str(cfg_path.resolve()) != str(Path(gr.CONFIG_FILE).resolve()):
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                gr.config.update(data)
        except Exception as exc:
            print(f"[!] load config failed: {exc}", flush=True)

    def should_stop() -> bool:
        if stop_file.is_file():
            return True
        return False

    def log(msg: str) -> None:
        line = str(msg or "")
        print(line, flush=True)
        # 只写一次 run_log；gr.append_run_log 就是 run_log.append_run_log，再调会成对重复
        try:
            from run_log import append_run_log

            append_run_log(line)
        except Exception:
            pass

    try:
        from run_log import ensure_run_log, finalize_run_log, start_run_log, update_run_stats

        # Panel sets GROK_RUN_LOG so we attach; standalone creates new file.
        if str(os.environ.get("GROK_RUN_LOG", "") or "").strip():
            ensure_run_log("relogin")
        else:
            start_run_log("relogin", force_new=True)
        update_run_stats(target=len(emails), success=0, fail=0, completed=0)
    except Exception:
        finalize_run_log = None  # type: ignore
        update_run_stats = None  # type: ignore

    report: dict = {
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "finished_at": "",
        "input_count": len(emails),
        "success_count": 0,
        "fail_count": 0,
        "skipped_count": 0,
        "items": [],
        "cancelled": False,
        "only_cpa": bool(args.only_cpa),
        "error": "",
    }

    rotate_idx = 0
    success = 0
    fail = 0
    skipped = 0

    mode = "仅推送CPA" if args.only_cpa else "Grok2API + CPA"
    log(f"[*] 批量重登开始，共 {len(emails)} 个账号，模式={mode}")
    try:
        for idx, email in enumerate(emails, start=1):
            if should_stop():
                report["cancelled"] = True
                log("[!] 收到停止信号，结束批量重登")
                break

            log(f"--- 重登 {idx}/{len(emails)}: {email} ---")
            item = {
                "email": email,
                "ok": False,
                "reason": "",
                "has_sso": False,
            }
            row = lookup_registered_account(email)
            if not row:
                skipped += 1
                item["reason"] = "not_in_registered_emails"
                report["items"].append(item)
                log(f"[-] 跳过：registered_emails 无此账号")
                continue
            password = str(row.get("password") or "")
            if not password:
                skipped += 1
                item["reason"] = "empty_password"
                report["items"].append(item)
                log(f"[-] 跳过：密码为空")
                continue

            try:
                px = gr.pick_proxy_for_worker(0, rotate_idx)
                gr.set_thread_proxy(px)
                log(f"[*] 代理: {gr.redact_proxy(px) if hasattr(gr, 'redact_proxy') else px}")
            except Exception as proxy_exc:
                fail += 1
                item["reason"] = f"proxy: {proxy_exc}"
                report["items"].append(item)
                log(f"[-] 代理分配失败: {proxy_exc}")
                break

            sso = ""
            try:
                try:
                    restart_browser(log_callback=log)
                except Exception:
                    pass
                sso = login_and_get_sso(
                    email,
                    password,
                    log_callback=log,
                    cancel_callback=should_stop,
                    timeout=120,
                )
                if not sso:
                    raise RuntimeError("empty sso")
                item["has_sso"] = True
                upsert_registered_account(
                    email,
                    password,
                    sso=sso,
                    keep_existing_sso_if_empty=False,
                )
                try:
                    gr.persist_registered_email(
                        email,
                        password,
                        sso=sso,
                        keep_existing_sso_if_empty=False,
                        also_account_file=True,
                        log_callback=log,
                    )
                except Exception:
                    pass
                log(f"[+] SSO 已写入 registered_emails: {email}")

                # push g2a → risk → CPA (same as register); only_cpa skips g2a
                cpa_ok = gr.finalize_sso_after_register(
                    sso,
                    email=email,
                    log_callback=log,
                    only_cpa=args.only_cpa,
                )
                try:
                    gr.release_proxy_lease(0, rewind=False)
                except Exception:
                    pass
                success += 1
                item["ok"] = True
                item["reason"] = "cpa_ok" if cpa_ok else "sso_ok_cpa_fail"
                report["items"].append(item)
                log(f"[+] 重登成功: {email} ({item['reason']})")
                rotate_idx += 1
            except gr.RegistrationRiskDenied as risk_exc:
                # SSO got + g2a maybe pushed; risk fail → stop CPA already inside finalize
                fail += 1
                item["has_sso"] = bool(sso)
                item["reason"] = f"risk: {risk_exc}"
                report["items"].append(item)
                log(f"[-] 风控失败: {risk_exc}")
                try:
                    gr.release_proxy_lease(0, rewind=True)
                except Exception:
                    pass
            except Exception as exc:
                fail += 1
                item["has_sso"] = bool(sso)
                item["reason"] = str(exc)[:300]
                report["items"].append(item)
                log(f"[-] 重登失败: {exc}")
                try:
                    gr.release_proxy_lease(0, rewind=True)
                except Exception:
                    pass
                if should_stop():
                    report["cancelled"] = True
                    break
            finally:
                try:
                    stop_browser(force=True)
                except Exception:
                    pass

            if update_run_stats:
                try:
                    update_run_stats(
                        target=len(emails),
                        success=success,
                        fail=fail,
                        completed=success + fail + skipped,
                    )
                except Exception:
                    pass

            # account interval between accounts (reuse main config)
            if idx < len(emails) and item["has_sso"] and not should_stop():
                wait_sec = gr.parse_account_interval()
                if wait_sec > 0:
                    log(f"[*] 下一个账号前等待 {wait_sec:.0f} 秒...")
                    gr.sleep_with_cancel(
                        wait_sec,
                        should_stop,
                        log_callback=log,
                        heartbeat_sec=60.0,
                    )
            elif not item["has_sso"]:
                gr.release_proxy_lease(0, rewind=True)

    except Exception as fatal:
        report["error"] = str(fatal)[:400]
        log(f"[!] 批量重登异常: {fatal}")
        traceback.print_exc()
    finally:
        try:
            stop_browser(force=True)
        except Exception:
            pass
        report["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        report["success_count"] = success
        report["fail_count"] = fail
        report["skipped_count"] = skipped
        try:
            atomic_write_json(report_path, report)
        except Exception as wexc:
            log(f"[!] 写报告失败: {wexc}")
        log(
            f"[*] 批量重登结束 成功={success} 失败={fail} 跳过={skipped} "
            f"cancel={report['cancelled']}"
        )
        if finalize_run_log:
            try:
                reason = "user_stop" if report["cancelled"] else "completed"
                finalize_run_log(
                    reason,
                    stats={
                        "target": len(emails),
                        "success": success,
                        "fail": fail,
                        "completed": success + fail + skipped,
                    },
                )
            except Exception:
                pass

    return 0 if fail == 0 and not report.get("error") else 1


if __name__ == "__main__":
    raise SystemExit(main())
