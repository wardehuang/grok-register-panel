# -*- coding: utf-8 -*-
"""Panel settings for CPA remote + grok2api remote + intervals/alias caps."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from secure_files import atomic_write_json, exclusive_file_lock

try:
    from webui.proxy_store import sync_worker_proxy_file
except ImportError:
    from proxy_store import sync_worker_proxy_file  # type: ignore

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.environ.get("EMAIL_PROVIDER_CONFIG_FILE", str(ROOT / "config.json")))
LOCK_PATH = CONFIG_PATH.with_suffix(CONFIG_PATH.suffix + ".lock")

SECRET_KEYS = {
    "cpa_management_key",
    "grok2api_remote_password",
    "grok2api_remote_app_key",
}

# blank form value means keep existing (avoid wipe when UI not yet loaded)
KEEP_IF_BLANK_KEYS = {
    "account_interval",
    "cpa_remote_url",
    "cpa_grok_version",
    "cpa_auth_dir",
    "grok2api_remote_base",
    "grok2api_remote_username",
    "grok2api_auth_dir",
    "outlook_accounts_file",
    "outlook_state_file",
    "proxy_pool_file",
    "proxy_pool_state_file",
}

PUBLIC_KEYS = [
    "account_interval",
    "register_interval_sec",
    "outlook_aliases_per_account",
    "outlook_use_alias_pool",
    "outlook_accounts_file",
    "outlook_state_file",
    "proxy_cursor_enabled",
    "proxy_pool_file",
    "proxy_pool_state_file",
    "skip_connectivity_precheck",
    "skip_xai_signup_precheck",
    "cpa_auto_add",
    "cpa_remote_url",
    "cpa_grok_version",
    "cpa_auth_dir",
    "grok2api_auto_add_remote",
    "grok2api_remote_base",
    "grok2api_remote_username",
    "grok2api_auth_dir",
]


class IntegrationConfigError(ValueError):
    pass


def _load() -> dict:
    if not CONFIG_PATH.is_file():
        return {}
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        raise IntegrationConfigError(f"读取 config.json 失败: {exc}") from exc
    if not isinstance(raw, dict):
        raise IntegrationConfigError("config.json 不是对象")
    return raw


def read_public_config() -> dict:
    data = _load()
    out: dict[str, Any] = {}
    for key in PUBLIC_KEYS:
        out[key] = data.get(key, "")
    out["secret_configured"] = {k: bool(str(data.get(k) or "").strip()) for k in SECRET_KEYS}
    # never return secrets
    return out


def save_integration_config(settings: dict, clear_secrets: list | None = None) -> dict:
    if not isinstance(settings, dict):
        raise IntegrationConfigError("settings 必须是对象")
    clear = {str(x).strip() for x in (clear_secrets or []) if str(x).strip()}
    with exclusive_file_lock(LOCK_PATH):
        data = _load()
        for key in PUBLIC_KEYS:
            if key not in settings:
                continue
            val = settings.get(key)
            if key in {
                "outlook_use_alias_pool",
                "proxy_cursor_enabled",
                "skip_connectivity_precheck",
                "skip_xai_signup_precheck",
                "cpa_auto_add",
                "grok2api_auto_add_remote",
            }:
                if isinstance(val, str):
                    data[key] = val.strip().lower() in ("1", "true", "yes", "on")
                else:
                    data[key] = bool(val)
            elif key in {"outlook_aliases_per_account", "register_interval_sec", "grok2api_remote_retries"}:
                try:
                    data[key] = int(val or 0)
                except Exception as exc:
                    raise IntegrationConfigError(f"{key} 必须是整数") from exc
            else:
                text = str(val or "").strip()
                if not text and key in KEEP_IF_BLANK_KEYS:
                    continue
                data[key] = text
        for key in SECRET_KEYS:
            if key in clear:
                data[key] = ""
                continue
            if key not in settings:
                continue
            val = str(settings.get(key) or "").strip()
            if val:
                data[key] = val
            # empty means keep existing
        atomic_write_json(CONFIG_PATH, data)
    return read_public_config()


def latest_run_log_tail(max_lines: int = 200) -> dict:
    """Newest session log for panel scroll view.

    Prefers log/runs/run_*.log, also considers batch-relogin / batch-orch stdout logs
    so relogin jobs appear while scrolling even if they only wrote job files.
    """
    candidates: list[Path] = []
    runs = ROOT / "log" / "runs"
    log_dir = ROOT / "log"
    if runs.is_dir():
        candidates.extend(runs.glob("run_*.log"))
    if log_dir.is_dir():
        candidates.extend(log_dir.glob("batch-relogin-*.log"))
        candidates.extend(log_dir.glob("batch-orch-*.log"))
    files: list[Path] = []
    for path in candidates:
        try:
            if path.is_file() and path.stat().st_size >= 0:
                files.append(path)
        except OSError:
            continue
    if not files:
        return {"path": "", "lines": [], "missing": True}
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    path = files[0]
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return {"path": str(path), "lines": [f"read error: {exc}"], "missing": False}
    lines = text.splitlines()
    keep = max(20, min(2000, int(max_lines or 200)))
    rel = str(path)
    try:
        rel = str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except Exception:
        pass
    return {
        "path": rel,
        "lines": lines[-keep:],
        "missing": False,
        "total_lines": len(lines),
    }


def _ts() -> str:
    from datetime import datetime

    return datetime.now().strftime("%H:%M:%S")


def _line(msg: str) -> str:
    return f"[{_ts()}] {msg}"


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve())).replace("\\", "/")
    except Exception:
        return str(path)


def _resolve_cfg_path(raw: object, default_rel: str) -> Path:
    text = str(raw or "").strip() or default_rel
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    return path


def _http_get(url: str, *, headers: dict | None = None, timeout: int = 12):
    from curl_cffi import requests as curl_requests
    import os

    proxy_env_keys = (
        "http_proxy",
        "https_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "all_proxy",
    )
    saved = {k: os.environ.pop(k) for k in proxy_env_keys if k in os.environ}
    try:
        session = curl_requests.Session()
        if hasattr(session, "trust_env"):
            session.trust_env = False
        session.proxies = {}
        return session.get(
            url,
            headers=headers or {},
            timeout=timeout,
            impersonate="chrome",
            proxies={},
        )
    finally:
        os.environ.update(saved)


def test_cpa_connectivity(settings: dict | None = None) -> dict:
    """Probe CPA management API with saved (or override) settings. No write."""
    cfg = _load()
    if isinstance(settings, dict):
        for key in ("cpa_remote_url", "cpa_management_key"):
            val = str(settings.get(key) or "").strip()
            if val:
                cfg[key] = val
    remote = str(cfg.get("cpa_remote_url") or "").strip().rstrip("/")
    key = str(cfg.get("cpa_management_key") or "").strip()
    if not remote:
        return {"ok": False, "target": "cpa", "error": "未配置 CPA Remote URL"}
    if not key:
        return {"ok": False, "target": "cpa", "error": "未配置 CPA Management Key"}
    try:
        from urllib.parse import urlparse

        u = urlparse(remote if "://" in remote else f"http://{remote}")
        host = u.hostname or ""
        port = u.port or (443 if u.scheme == "https" else 80)
        import socket

        with socket.create_connection((host, port), timeout=5):
            pass
        resp = _http_get(
            f"{remote}/v0/management/auth-files",
            headers={"Authorization": f"Bearer {key}"},
            timeout=12,
        )
        code = int(getattr(resp, "status_code", 0) or 0)
        if code in (401, 403):
            return {
                "ok": False,
                "target": "cpa",
                "status_code": code,
                "error": f"管理密钥无效 HTTP {code}",
            }
        if code >= 500:
            return {
                "ok": False,
                "target": "cpa",
                "status_code": code,
                "error": f"CPA 服务异常 HTTP {code}",
            }
        # 200/404 both mean auth often accepted depending on CPA version
        return {
            "ok": True,
            "target": "cpa",
            "status_code": code,
            "detail": f"CPA 连通 OK HTTP {code} · {host}:{port}",
        }
    except Exception as exc:
        return {"ok": False, "target": "cpa", "error": str(exc)[:300]}


def test_g2a_connectivity(settings: dict | None = None) -> dict:
    """Login grok2api admin API. No account import."""
    cfg = _load()
    if isinstance(settings, dict):
        for key in (
            "grok2api_remote_base",
            "grok2api_remote_username",
            "grok2api_remote_password",
            "grok2api_remote_app_key",
        ):
            val = str(settings.get(key) or "").strip()
            if val:
                cfg[key] = val
    try:
        from integrations_push import (
            _login,
            grok2api_admin_v1_root,
            is_grok2api_remote_configured,
        )
    except Exception:
        from integrations_push import (  # type: ignore
            _login,
            grok2api_admin_v1_root,
            is_grok2api_remote_configured,
        )
    if not is_grok2api_remote_configured(cfg):
        return {
            "ok": False,
            "target": "g2a",
            "error": "未配置完整 Grok2API Base / 用户名 / 密码(或 App Key)",
        }
    base = grok2api_admin_v1_root(cfg.get("grok2api_remote_base"))
    username = str(cfg.get("grok2api_remote_username") or "").strip()
    password = str(cfg.get("grok2api_remote_password") or "").strip() or str(
        cfg.get("grok2api_remote_app_key") or ""
    ).strip()
    try:
        from curl_cffi import requests as curl_requests

        token = _login(curl_requests.post, base, username, password, log_callback=None)
        # light follow-up: list accounts endpoint if present
        detail = "登录成功"
        try:
            resp = curl_requests.get(
                f"{base}/api/admin/v1/accounts",
                headers={"Authorization": f"Bearer {token}"},
                timeout=12,
                impersonate="chrome",
            )
            code = int(getattr(resp, "status_code", 0) or 0)
            detail = f"登录成功 · accounts HTTP {code}"
        except Exception:
            pass
        return {"ok": True, "target": "g2a", "detail": f"Grok2API 连通 OK · {detail} · {base}"}
    except Exception as exc:
        return {"ok": False, "target": "g2a", "error": str(exc)[:300]}


def _tcp_open(host: str, port: int, timeout: float = 4.0) -> bool:
    import socket

    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except Exception:
        return False


def _proxy_host_port(proxy_url: str) -> tuple[str, int] | None:
    from urllib.parse import urlparse

    text = str(proxy_url or "").strip()
    if not text:
        return None
    if "://" not in text:
        text = "http://" + text
    try:
        u = urlparse(text)
    except Exception:
        return None
    host = u.hostname or ""
    if not host:
        return None
    port = u.port
    if not port:
        scheme = (u.scheme or "").lower()
        port = 443 if scheme in {"https", "socks5", "socks5h"} else 80
    return host, int(port)


def _export_managed_proxy_pool_if_needed(cfg: dict) -> Path | None:
    """Sync managed proxy order; use the legacy file only without a managed pool."""
    pool_file = _resolve_cfg_path(cfg.get("proxy_pool_file"), "proxies.txt")
    return sync_worker_proxy_file(pool_file)


def run_dry_run(
    *,
    target_count: int | None = None,
    workers: int | None = None,
    settings: dict | None = None,
) -> dict:
    """Walk configured params for one simulated account. No register / no permanent consume."""
    from run_log import append_run_log, finalize_run_log, start_run_log, update_run_stats
    from proxy_cursor import ProxyCursor, mask_proxy

    cfg = _load()
    if isinstance(settings, dict) and settings:
        # ephemeral overrides only (do not persist unless caller saved first)
        for key, val in settings.items():
            if key in SECRET_KEYS:
                text = str(val or "").strip()
                if text:
                    cfg[key] = text
            elif key in PUBLIC_KEYS or key in {
                "account_interval",
                "outlook_aliases_per_account",
                "proxy_cursor_enabled",
                "outlook_use_alias_pool",
            }:
                cfg[key] = val

    # control defaults
    try:
        control_path = ROOT / "log" / "control.json"
        control = {}
        if control_path.is_file():
            control = json.loads(control_path.read_text(encoding="utf-8") or "{}") or {}
    except Exception:
        control = {}
    count = int(target_count or control.get("batch_count") or 1)
    count = max(1, min(2000, count))
    thr = int(workers or control.get("workers") or 1)
    thr = max(1, min(24, thr))
    interval = str(cfg.get("account_interval") or cfg.get("register_interval_sec") or "0")

    lines: list[str] = []
    log_path = start_run_log("dry")

    def emit(msg: str) -> None:
        text = _line(msg)
        lines.append(text)
        append_run_log(text)

    try:
        # proxy pool
        pool_file = _export_managed_proxy_pool_if_needed(cfg)
        state_file = _resolve_cfg_path(cfg.get("proxy_pool_state_file"), "log/proxy_pool_state.json")
        next_idx = 0
        pool_size = 0
        cursor = None
        if pool_file and pool_file.is_file():
            try:
                cursor = ProxyCursor(pool_file, state_file)
                pool_size = cursor.size
                next_idx = cursor.next_index
                emit(f"[*] 代理池就绪: {pool_size} 条，下次从第 {next_idx + 1} 条开始")
            except Exception as exc:
                emit(f"[!] 代理池读取失败: {exc}")
        else:
            emit("[!] 代理池为空（无 proxies.txt / 托管代理）")

        # outlook alias pool
        accounts_file = _resolve_cfg_path(
            cfg.get("outlook_accounts_file") or cfg.get("outlook_rt_inventory"),
            "accounts/outlook_accounts.txt",
        )
        state_outlook = _resolve_cfg_path(
            cfg.get("outlook_state_file"),
            "accounts/outlook_state.json",
        )
        try:
            cap = int(cfg.get("outlook_aliases_per_account") or 10)
        except Exception:
            cap = 10
        primary = 0
        remaining = 0
        alias_pool = None
        if bool(cfg.get("outlook_use_alias_pool", True)):
            try:
                from email_providers.outlook_alias_pool import OutlookAliasPool

                alias_pool = OutlookAliasPool(
                    accounts_file=accounts_file,
                    state_file=state_outlook,
                    aliases_per_account=cap,
                )
                primary = int(getattr(alias_pool, "account_count", 0) or len(getattr(alias_pool, "_accounts", []) or []))
                remaining = int(alias_pool.remaining_alias_slots())
                emit(f"[*] Outlook 账号池：主邮箱 {primary} 个，剩余可分配别名 {remaining}")
            except Exception as exc:
                emit(f"[!] Outlook 别名池读取失败: {exc}")
                alias_pool = None
        else:
            emit("[*] Outlook 别名池未启用")

        emit(f"[*] 本次运行日志: {_rel(log_path)}")
        emit(
            f"[*] 配置已保存，开始执行（DRY RUN）。目标数量: {count}，并发线程: {thr}，创建间隔: {interval} 秒"
        )
        auth_dir = str(cfg.get("cpa_auth_dir") or "cpa_auth").strip() or "cpa_auth"
        emit(f"[*] 成功账号将实时保存到注册库: {auth_dir} （dry 不写）")
        if primary:
            emit(f"[*] Outlook 账号池：主邮箱 {primary} 个，剩余可分配别名 {remaining}")

        # only simulate first account
        emit(f"[T1] --- 开始第 1/{count} 个账号 ---")
        if alias_pool is not None:
            alias_email = ""
            lease = ""
            try:
                alias_email, lease = alias_pool.allocate_alias()
                primary_acc = ""
                try:
                    acc = alias_pool.get_account_for_alias_lease(lease, alias_email)
                    primary_acc = str(getattr(acc, "email", "") or "")
                except Exception:
                    primary_acc = ""
                if primary_acc and primary_acc.casefold() != str(alias_email).casefold():
                    emit(
                        f"[T1] [*] 本账号邮箱(dry): {alias_email}  · 主号: {primary_acc}"
                    )
                else:
                    emit(f"[T1] [*] 本账号邮箱(dry): {alias_email}")
            except Exception as exc:
                emit(f"[T1] [!] 邮箱预取失败: {exc}")
            finally:
                if lease and alias_email:
                    try:
                        rolled = alias_pool.release_alias(lease, alias_email)
                        if rolled:
                            emit(f"[T1] [*] DRY：邮箱别名已返还 {alias_email}")
                        else:
                            emit(f"[T1] [!] DRY：邮箱别名返还失败 {alias_email}")
                    except Exception as exc:
                        emit(f"[T1] [!] DRY：邮箱别名返还异常: {exc}")
        else:
            emit("[T1] [!] 无别名池，跳过邮箱预取")

        if cursor is not None and pool_size > 0:
            proxy_url = ""
            index = -1
            try:
                # peek without permanent advance: allocate then always release
                show_idx = cursor.next_index
                emit(
                    f"[T1] [*] 代理池检测 [{show_idx + 1}/{pool_size}]: "
                    f"{mask_proxy(cursor._proxies[show_idx]) if hasattr(cursor, '_proxies') else ''}"
                )
                proxy_url, index = cursor.allocate()
                hp = _proxy_host_port(proxy_url)
                ok = bool(hp and _tcp_open(hp[0], hp[1], timeout=4.0))
                if ok:
                    emit(f"[T1] [+] 代理可用 [{index + 1}/{pool_size}]: {mask_proxy(proxy_url)}")
                else:
                    emit(
                        f"[T1] [!] 代理 TCP 未通（dry 仍展示分配）[{index + 1}/{pool_size}]: "
                        f"{mask_proxy(proxy_url)}"
                    )
                emit(
                    f"[T1] [*] 本账号使用代理池 [{index + 1}/{pool_size}]: {mask_proxy(proxy_url)}"
                )
            finally:
                if index >= 0:
                    try:
                        cursor.release_allocation(index)
                        emit(f"[T1] [*] DRY：代理游标已返还 [{index + 1}/{pool_size}]")
                    except Exception as exc:
                        emit(f"[T1] [!] DRY：代理游标返还失败: {exc}")
        else:
            emit("[T1] [!] 无可用代理，跳过分配")

        emit("[T1] [*] DRY：跳过邮箱取号 / 注册 / SSO / 推送")
        emit("[*] DRY RUN 完成（未注册任何账号，未消耗别名）")
        return {
            "ok": True,
            "dry_run": True,
            "path": _rel(log_path),
            "lines": lines,
            "target_count": count,
            "workers": thr,
            "account_interval": interval,
        }
    except Exception as exc:
        emit(f"[!] DRY RUN 失败: {exc}")
        return {
            "ok": False,
            "dry_run": True,
            "path": _rel(log_path),
            "lines": lines,
            "error": str(exc)[:400],
        }
    finally:
        try:
            update_run_stats(target=count, success=0, fail=0, completed=0)
            finalize_run_log("dry_run_completed" if lines else "dry_run_failed")
        except Exception:
            pass
