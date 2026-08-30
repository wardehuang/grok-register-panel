# -*- coding: utf-8 -*-
"""Remote push helpers: grok2api Web/Console SSO import + CPA management upload."""
from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Optional

LogFn = Optional[Callable[[str], None]]

_PROXY_ENV_KEYS = (
    "http_proxy",
    "https_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "all_proxy",
)


@contextmanager
def _direct_network() -> Iterator[None]:
    """CPA/Grok2API management APIs must not use registration SOCKS / env proxy."""
    saved = {k: os.environ.pop(k) for k in _PROXY_ENV_KEYS if k in os.environ}
    try:
        yield
    finally:
        os.environ.update(saved)


def _direct_curl_session():
    from curl_cffi import requests as curl_requests

    session = curl_requests.Session()
    if hasattr(session, "trust_env"):
        session.trust_env = False
    session.proxies = {}
    return session


def _log(log_callback: LogFn, message: str) -> None:
    if log_callback:
        log_callback(str(message))


def normalize_sso_token(raw_token: Any) -> str:
    token = str(raw_token or "").strip()
    if token.startswith("sso="):
        token = token[4:]
    return token.strip()


def grok2api_admin_v1_root(raw_base: Any) -> str:
    base = str(raw_base or "").strip().rstrip("/")
    if not base:
        return ""
    if "://" not in base:
        base = f"https://{base}"
    for suffix in ("/api/admin/v1", "/admin/api", "/admin"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    return base.rstrip("/")


def is_grok2api_remote_configured(config: dict) -> bool:
    base = grok2api_admin_v1_root(config.get("grok2api_remote_base", ""))
    username = str(config.get("grok2api_remote_username", "") or "").strip()
    password = _grok2api_password(config)
    return bool(base and username and password)


def is_cpa_remote_configured(config: dict) -> bool:
    url = str(config.get("cpa_remote_url", "") or "").strip()
    key = str(config.get("cpa_management_key", "") or "").strip()
    return bool(url and key)


def _grok2api_password(config: dict) -> str:
    password = str(config.get("grok2api_remote_password", "") or "").strip()
    if password:
        return password
    return str(config.get("grok2api_remote_app_key", "") or "").strip()


def _snippet(response: Any, limit: int = 400) -> str:
    try:
        text = response.text
    except Exception:
        text = ""
    text = str(text or "").strip().replace("\n", " ")
    if len(text) > limit:
        return text[:limit] + "..."
    return text


def _parse_import_sse(text: str) -> dict:
    event_name = "message"
    data_lines: list[str] = []
    complete: dict | None = None

    def dispatch() -> None:
        nonlocal event_name, data_lines, complete
        if not data_lines:
            event_name = "message"
            return
        raw = "\n".join(data_lines).strip()
        data_lines = []
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"message": raw}
        if event_name == "error":
            message = payload.get("message") if isinstance(payload, dict) else raw
            code = payload.get("code") if isinstance(payload, dict) else "importError"
            raise RuntimeError(f"{code}: {message}")
        if event_name == "complete":
            complete = payload if isinstance(payload, dict) else {}
        event_name = "message"

    for line in str(text or "").splitlines():
        line = line.rstrip("\r")
        if not line:
            dispatch()
            continue
        if line.startswith("event:"):
            event_name = line[6:].strip() or "message"
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].strip())
    dispatch()
    if complete is None:
        raise RuntimeError("grok2api 导入响应缺少 complete 事件")
    return complete


def _login(
    http_post: Callable,
    base: str,
    username: str,
    password: str,
    *,
    log_callback: LogFn = None,
) -> str:
    _log(log_callback, "[*] grok2api 远端上传：登录管理后台（直连，不走代理）")
    with _direct_network():
        session = _direct_curl_session()
        resp = session.post(
            f"{base}/api/admin/v1/auth/login",
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            json={"username": username, "password": password},
            timeout=30,
            proxies={},
            impersonate="chrome",
        )
    if int(getattr(resp, "status_code", 0) or 0) >= 400:
        raise RuntimeError(
            f"grok2api 登录失败 HTTP {resp.status_code}: {_snippet(resp)}"
        )
    payload = resp.json()
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        payload = payload["data"]
    tokens = payload.get("tokens") if isinstance(payload, dict) else None
    access_token = ""
    if isinstance(tokens, dict):
        access_token = str(tokens.get("accessToken") or "").strip()
    if not access_token and isinstance(payload, dict):
        access_token = str(payload.get("accessToken") or "").strip()
    if not access_token:
        raise RuntimeError("grok2api 登录响应缺少 accessToken")
    _log(log_callback, "[+] grok2api 远端登录成功")
    return access_token


def push_sso_to_grok2api_kind(
    http_post: Callable,
    config: dict,
    raw_token: str,
    *,
    email: str = "",
    import_kind: str = "web",
    log_callback: LogFn = None,
) -> bool:
    """Push one SSO into grok2api Web or Console import API."""
    token = normalize_sso_token(raw_token)
    if not token:
        return False
    base = grok2api_admin_v1_root(config.get("grok2api_remote_base", ""))
    username = str(config.get("grok2api_remote_username", "") or "").strip()
    password = _grok2api_password(config)
    if not base or not username or not password:
        _log(log_callback, "[Debug] grok2api 远端未配置地址/用户名/密码，跳过")
        return False

    kind = str(import_kind or "web").strip().lower()
    if kind not in {"web", "console"}:
        raise ValueError(f"unsupported import_kind: {import_kind!r}")

    account_name = str(email or "").strip() or (
        "Grok Console auto" if kind == "console" else "Grok Web auto"
    )
    if kind == "console":
        upload_payload = {
            "provider": "grok_console",
            "accounts": [
                {
                    "name": account_name,
                    "email": account_name if "@" in account_name else "",
                    "sso_token": token,
                }
            ],
        }
        import_path = "/api/admin/v1/accounts/console/import"
        import_label = "Console SSO"
    else:
        upload_payload = {
            "provider": "grok_web",
            "accounts": [
                {
                    "name": account_name,
                    "sso_token": token,
                    "tier": "basic",
                }
            ],
        }
        import_path = "/api/admin/v1/accounts/web/import"
        import_label = "Web SSO"

    filename = f"{account_name}.json".replace("/", "_").replace("\\", "_")
    try:
        retries = max(1, int(config.get("grok2api_remote_retries", 3) or 3))
    except Exception:
        retries = 3
    try:
        retry_sleep = max(
            0.0, float(config.get("grok2api_remote_retry_sleep_sec", 2.0) or 0.0)
        )
    except Exception:
        retry_sleep = 2.0

    from curl_cffi import CurlMime

    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            if retries > 1:
                _log(log_callback, f"[*] grok2api {import_label} 上传尝试 {attempt}/{retries}")
            access_token = _login(
                http_post, base, username, password, log_callback=log_callback
            )
            multipart = CurlMime()
            multipart.addpart(
                name="files",
                filename=filename,
                content_type="application/json",
                data=json.dumps(upload_payload, ensure_ascii=False).encode("utf-8"),
            )
            _log(log_callback, f"[*] grok2api 远端上传：调用 {import_label} 导入接口（直连）")
            try:
                with _direct_network():
                    session = _direct_curl_session()
                    resp = session.post(
                        f"{base}{import_path}",
                        headers={
                            "Accept": "text/event-stream",
                            "Authorization": f"Bearer {access_token}",
                        },
                        multipart=multipart,
                        timeout=120,
                        proxies={},
                        impersonate="chrome",
                    )
            finally:
                try:
                    multipart.close()
                except Exception:
                    pass
            if int(getattr(resp, "status_code", 0) or 0) >= 400:
                raise RuntimeError(
                    f"grok2api 导入失败 HTTP {resp.status_code}: {_snippet(resp)}"
                )
            result = _parse_import_sse(getattr(resp, "text", "") or "")
            created = int(result.get("created", 0) or 0)
            updated = int(result.get("updated", 0) or 0)
            synced = int(result.get("synced", 0) or 0)
            failed = int(result.get("syncFailed", 0) or 0)
            _log(log_callback, "【推送Grok2API 成功】")
            _log(
                log_callback,
                f"[+] 已上传 {import_label}: 新增={created}, 更新={updated}, "
                f"同步={synced}, 失败={failed}",
            )
            return True
        except Exception as exc:
            last_exc = exc
            if attempt >= retries:
                break
            _log(log_callback, f"[!] grok2api {import_label} 失败，将重试: {exc}")
            if retry_sleep:
                time.sleep(retry_sleep)
    if last_exc:
        raise last_exc
    return False


def push_sso_to_grok2api_web_and_console(
    http_post: Callable,
    config: dict,
    raw_token: str,
    *,
    email: str = "",
    log_callback: LogFn = None,
) -> dict:
    """Force-push SSO to both remote Web and Console pools (independent failures)."""
    result = {
        "remote_web_ok": False,
        "remote_console_ok": False,
        "skipped": False,
        "errors": [],
    }
    if not is_grok2api_remote_configured(config):
        result["skipped"] = True
        _log(log_callback, "[*] grok2api 远端未配置，跳过 Web/Console 推送")
        return result
    if not bool(config.get("grok2api_auto_add_remote", True)):
        result["skipped"] = True
        _log(log_callback, "[*] grok2api_auto_add_remote=false，跳过远端推送")
        return result

    try:
        result["remote_web_ok"] = bool(
            push_sso_to_grok2api_kind(
                http_post,
                config,
                raw_token,
                email=email,
                import_kind="web",
                log_callback=log_callback,
            )
        )
        if not result["remote_web_ok"]:
            result["errors"].append("Web 远端导入未成功")
    except Exception as exc:
        result["remote_web_ok"] = False
        result["errors"].append(f"Web: {exc}")
        _log(log_callback, f"[!] 写入 grok2api 远端 Web 失败: {exc}")

    try:
        result["remote_console_ok"] = bool(
            push_sso_to_grok2api_kind(
                http_post,
                config,
                raw_token,
                email=email,
                import_kind="console",
                log_callback=log_callback,
            )
        )
        if not result["remote_console_ok"]:
            result["errors"].append("Console 远端导入未成功")
    except Exception as exc:
        result["remote_console_ok"] = False
        result["errors"].append(f"Console: {exc}")
        _log(log_callback, f"[!] 写入 grok2api 远端 Console 失败: {exc}")
    return result
