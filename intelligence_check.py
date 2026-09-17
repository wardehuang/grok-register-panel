"""Grok-4.6 intelligence gate using an independent proxy pool."""

from __future__ import annotations

import ipaddress
import json
import time
from typing import Callable

import requests

from webui.quality_proxy_store import (
    claim_runtime_quality_proxy,
    has_runtime_quality_proxy,
)
from webui.security_utils import redact_log_line, redact_proxy


RESPONSES_URL = "https://cli-chat-proxy.grok.com/v1/responses"
CONNECTIVITY_TIMEOUT_SECONDS = 2.0
SOFT_TPS = 0.1
HARD_TPS = 500.0
TTFB_THRESHOLD_MS = 5000.0
GENERATION_THRESHOLD_MS = 1000.0
TOKEN_THRESHOLD = 100

_REQUEST_BODY = {
    "model": "grok-4.6",
    "input": "用中文回答：17 × 23 等于多少？只输出计算过程和答案。",
    "stream": True,
    "reasoning": {"effort": "high", "summary": "detailed"},
    "max_output_tokens": 96,
    "temperature": 0,
}

_CONNECTIVITY_ENDPOINTS = (
    "https://api.ipify.org?format=json",
    "https://ipwho.is/",
    "https://ipinfo.io/json",
)


class IntelligenceProxyPoolExhausted(RuntimeError):
    pass


class IntelligenceNetworkError(RuntimeError):
    pass


def ensure_intelligence_proxy_available() -> None:
    if not has_runtime_quality_proxy():
        raise IntelligenceProxyPoolExhausted("智商检测代理池为空")


def _log(log_callback: Callable[[str], None] | None, message: str) -> None:
    if log_callback:
        log_callback(f"[智商检测] {message}")


def _session(proxy: str) -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    session.proxies = {"http": proxy, "https": proxy}
    return session


def _check_proxy_connectivity(proxy: str) -> int:
    started = time.monotonic()
    last_error: Exception | None = None
    with _session(proxy) as session:
        for endpoint in _CONNECTIVITY_ENDPOINTS:
            elapsed = time.monotonic() - started
            remaining = CONNECTIVITY_TIMEOUT_SECONDS - elapsed
            if remaining <= 0:
                break
            try:
                response = session.get(
                    endpoint,
                    timeout=(remaining, remaining),
                    headers={"Accept": "application/json", "User-Agent": "GrokRegister/1"},
                )
                response.raise_for_status()
                payload = response.json()
                ip = str(payload.get("ip") or "").strip() if isinstance(payload, dict) else ""
                ipaddress.ip_address(ip)
                latency_ms = (time.monotonic() - started) * 1000.0
                if latency_ms > 2000.0:
                    raise IntelligenceNetworkError(f"代理延迟 {latency_ms:.0f}ms 超过 2000ms")
                return max(1, int(latency_ms))
            except Exception as exc:
                last_error = exc
    detail = redact_log_line(str(last_error or "代理连通性检测失败"))
    raise IntelligenceNetworkError(detail)


def _int_value(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _has_burst_dump(value: object) -> bool:
    if isinstance(value, dict):
        event_type = str(value.get("type") or "").strip().lower()
        if "burst_dump" in event_type or value.get("burst_dump"):
            return True
        return any(_has_burst_dump(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_burst_dump(item) for item in value)
    return False


def _classify_response(access_token: str, proxy: str, cpa_grok_version: str) -> dict:
    version = str(cpa_grok_version or "").strip()
    if not version:
        raise ValueError("CPA Grok Client Version 为空")
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "text/event-stream",
        "Content-Type": "application/json",
        "X-XAI-Token-Auth": "xai-grok-cli",
        "x-grok-client-version": version,
        "User-Agent": f"xai-grok-workspace/{version}",
    }

    request_started = time.monotonic()
    response_started_at: float | None = None
    first_data_at: float | None = None
    completed_at: float | None = None
    completed_event: dict | None = None
    summary_streams: dict[str, list[str]] = {}
    summary_texts: list[str] = []
    reasoning_items: list[dict] = []
    burst_dump = False

    try:
        with _session(proxy) as session:
            response = session.post(
                RESPONSES_URL,
                headers=headers,
                json=_REQUEST_BODY,
                stream=True,
                timeout=(5.0, 120.0),
            )
            response_started_at = time.monotonic()
            with response:
                response.raise_for_status()
                for raw_line in response.iter_lines(chunk_size=1, decode_unicode=True):
                    arrived_at = time.monotonic()
                    if isinstance(raw_line, bytes):
                        line = raw_line.decode("utf-8", errors="replace").strip()
                    else:
                        line = str(raw_line or "").strip()
                    if not line.startswith("data:"):
                        continue
                    data_text = line[5:].strip()
                    if not data_text or data_text == "[DONE]":
                        continue
                    try:
                        event = json.loads(data_text)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    if first_data_at is None:
                        first_data_at = arrived_at
                    burst_dump = burst_dump or _has_burst_dump(event)
                    event_type = str(event.get("type") or "")
                    if event_type == "response.reasoning_summary_text.delta":
                        delta = str(event.get("delta") or "")
                        if delta:
                            item_id = str(event.get("item_id") or "default")
                            summary_streams.setdefault(item_id, []).append(delta)
                    elif event_type == "response.output_item.done":
                        item = event.get("item")
                        if isinstance(item, dict) and item.get("type") == "reasoning":
                            reasoning_items.append(item)
                            for summary in item.get("summary") or []:
                                if isinstance(summary, dict):
                                    text = str(summary.get("text") or "")
                                    if text:
                                        summary_texts.append(text)
                    elif event_type == "response.completed":
                        completed_at = arrived_at
                        completed_event = event
                        break
    except Exception as exc:
        raise IntelligenceNetworkError(redact_log_line(str(exc))) from exc

    if (
        response_started_at is None
        or first_data_at is None
        or completed_at is None
        or completed_event is None
    ):
        raise IntelligenceNetworkError("SSE 未返回有效 response.completed")
    response_payload = completed_event.get("response")
    usage = response_payload.get("usage") if isinstance(response_payload, dict) else None
    if not isinstance(usage, dict) or "output_tokens" not in usage:
        raise IntelligenceNetworkError("response.completed 缺少 usage.output_tokens")

    output_tokens = _int_value(usage.get("output_tokens"))
    details = usage.get("output_tokens_details")
    reasoning_tokens = _int_value(details.get("reasoning_tokens")) if isinstance(details, dict) else 0
    evaluated_tokens = output_tokens + reasoning_tokens
    generation_ms = max(0.001, (completed_at - first_data_at) * 1000.0)
    ttfb_ms = max(0.0, (response_started_at - request_started) * 1000.0)
    tps = evaluated_tokens * 1000.0 / generation_ms

    summary_candidates = [
        "".join(parts).strip() for parts in summary_streams.values()
    ] + [text.strip() for text in summary_texts]
    summary_evidence = any(len(text) >= 32 for text in summary_candidates)
    encrypted_evidence = False
    encrypted_threshold = max(256, reasoning_tokens * 4)
    for item in reasoning_items:
        if str(item.get("status") or "") != "completed":
            continue
        encrypted = str(item.get("encrypted_content") or "")
        if len(encrypted.encode("utf-8")) >= encrypted_threshold:
            encrypted_evidence = True
            break
    is_real_thinking = summary_evidence or encrypted_evidence or burst_dump

    if tps >= HARD_TPS:
        classification = "suspected_degradation"
        quality_level = "hard"
        reason = "hard_tps"
    elif SOFT_TPS < tps < HARD_TPS and not is_real_thinking:
        classification = "suspected_degradation"
        quality_level = "soft"
        reason = "soft_tps_missing_real_thinking"
    elif (
        ttfb_ms > TTFB_THRESHOLD_MS
        and generation_ms < GENERATION_THRESHOLD_MS
        and evaluated_tokens > TOKEN_THRESHOLD
    ):
        classification = "suspected_degradation"
        quality_level = "soft"
        reason = "ttfb_downgrade"
    else:
        classification = "normal"
        quality_level = "healthy"
        reason = "within_threshold"

    return {
        "classification": classification,
        "quality_level": quality_level,
        "reason": reason,
        "ttfb_ms": ttfb_ms,
        "generation_ms": generation_ms,
        "evaluated_tokens": evaluated_tokens,
        "tps": tps,
        "is_real_thinking": is_real_thinking,
    }


def run_intelligence_check(
    access_token: str,
    cpa_grok_version: str,
    *,
    log_callback: Callable[[str], None] | None = None,
) -> bool:
    token = str(access_token or "").strip()
    if not token:
        raise ValueError("access_token 为空")

    degraded_count = 0
    while True:
        proxy = claim_runtime_quality_proxy()
        if not proxy:
            if degraded_count > 0:
                _log(log_callback, "没有下一条未尝试代理，判定智商失败")
                return False
            _log(log_callback, "代理池已耗尽，停止本次注册流程")
            raise IntelligenceProxyPoolExhausted("智商检测代理池已耗尽")

        display_proxy = redact_proxy(proxy)
        _log(log_callback, f"选择代理 {display_proxy}")
        try:
            latency_ms = _check_proxy_connectivity(proxy)
            _log(log_callback, f"代理连通，延迟 {latency_ms}ms")
        except IntelligenceNetworkError as exc:
            _log(
                log_callback,
                f"代理不连通或过慢，已删除 {display_proxy}: {redact_log_line(str(exc))}",
            )
            continue

        try:
            result = _classify_response(token, proxy, cpa_grok_version)
        except IntelligenceNetworkError as exc:
            _log(
                log_callback,
                f"请求网络错误，已删除 {display_proxy}: {redact_log_line(str(exc))}",
            )
            continue

        _log(
            log_callback,
            "结果 "
            f"classification={result['classification']} reason={result['reason']} "
            f"TTFB={result['ttfb_ms']:.0f}ms generation={result['generation_ms']:.0f}ms "
            f"tokens={result['evaluated_tokens']} TPS={result['tps']:.2f} "
            f"real_thinking={'yes' if result['is_real_thinking'] else 'no'}",
        )
        if result["classification"] == "normal":
            _log(log_callback, f"√ 【非降智】智商通过，已删除 {display_proxy}")
            return True

        degraded_count += 1
        _log(log_callback, f"× 【降智】第 {degraded_count} 次判定，已删除 {display_proxy}")
        if degraded_count >= 2:
            _log(log_callback, "连续两次降智，判定智商失败")
            return False
