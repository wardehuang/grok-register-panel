#!/usr/bin/env python3
from __future__ import annotations

import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import intelligence_check
from webui import quality_proxy_store


class IsolatedQualityStore:
    def __enter__(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.previous = (
            quality_proxy_store.STATE_PATH,
            quality_proxy_store.LOCK_PATH,
            quality_proxy_store.LEGACY_PATH,
        )
        quality_proxy_store.STATE_PATH = base / "log" / "quality_proxy_pool.json"
        quality_proxy_store.LOCK_PATH = base / "log" / "quality_proxy_pool.json.lock"
        quality_proxy_store.LEGACY_PATH = base / "proxies.txt"
        return base

    def __exit__(self, exc_type, exc, tb):
        quality_proxy_store.STATE_PATH, quality_proxy_store.LOCK_PATH, quality_proxy_store.LEGACY_PATH = (
            self.previous
        )
        self.temp.cleanup()


def _normal_result() -> dict:
    return {
        "classification": "normal",
        "quality_level": "healthy",
        "reason": "within_threshold",
        "ttfb_ms": 120.0,
        "generation_ms": 1800.0,
        "evaluated_tokens": 40,
        "tps": 22.2,
        "is_real_thinking": True,
    }


def _degraded_result() -> dict:
    return {
        "classification": "suspected_degradation",
        "quality_level": "hard",
        "reason": "hard_tps",
        "ttfb_ms": 400.0,
        "generation_ms": 500.0,
        "evaluated_tokens": 1600,
        "tps": 3200.0,
        "is_real_thinking": False,
    }


class _PatchedChecks:
    def __init__(self, classify, connectivity=None):
        self.classify = classify
        self.connectivity = connectivity or (lambda proxy: 12)
        self._previous = None

    def __enter__(self):
        self._previous = (
            intelligence_check._check_proxy_connectivity,
            intelligence_check._classify_response,
        )
        intelligence_check._check_proxy_connectivity = self.connectivity
        intelligence_check._classify_response = self.classify
        return self

    def __exit__(self, exc_type, exc, tb):
        (
            intelligence_check._check_proxy_connectivity,
            intelligence_check._classify_response,
        ) = self._previous


def _remaining_urls() -> list[str]:
    return quality_proxy_store.runtime_quality_proxy_snapshot()


def test_normal_result_deletes_used_proxy():
    with IsolatedQualityStore():
        quality_proxy_store.import_quality_proxies(
            "\n".join(
                [
                    "http://proxy.example:8001",
                    "http://proxy.example:8002",
                ]
            )
        )
        used = []

        def classify(token, proxy, version):
            used.append(proxy)
            return _normal_result()

        logs = []
        with _PatchedChecks(classify):
            ok = intelligence_check.run_intelligence_check(
                "token-a",
                "1.0.0",
                log_callback=logs.append,
            )
        assert ok is True
        assert used == ["http://proxy.example:8001"]
        assert _remaining_urls() == ["http://proxy.example:8002"]
        assert any("非降智" in line and "已删除" in line for line in logs)


def test_accounts_use_distinct_proxies_sequentially():
    with IsolatedQualityStore():
        quality_proxy_store.import_quality_proxies(
            "\n".join(
                [
                    "http://proxy.example:8001",
                    "http://proxy.example:8002",
                ]
            )
        )
        used = []

        def classify(token, proxy, version):
            used.append((token, proxy))
            return _normal_result()

        with _PatchedChecks(classify):
            first = intelligence_check.run_intelligence_check("token-a", "1.0.0")
            second = intelligence_check.run_intelligence_check("token-b", "1.0.0")
        assert first is True
        assert second is True
        assert used == [
            ("token-a", "http://proxy.example:8001"),
            ("token-b", "http://proxy.example:8002"),
        ]
        assert _remaining_urls() == []


def test_degraded_result_also_deletes_proxy():
    with IsolatedQualityStore():
        quality_proxy_store.import_quality_proxies(
            "\n".join(
                [
                    "http://proxy.example:8001",
                    "http://proxy.example:8002",
                    "http://proxy.example:8003",
                ]
            )
        )
        used = []

        def classify(token, proxy, version):
            used.append(proxy)
            return _degraded_result()

        logs = []
        with _PatchedChecks(classify):
            ok = intelligence_check.run_intelligence_check(
                "token-a",
                "1.0.0",
                log_callback=logs.append,
            )
        assert ok is False
        assert used == [
            "http://proxy.example:8001",
            "http://proxy.example:8002",
        ]
        assert _remaining_urls() == ["http://proxy.example:8003"]
        assert any("降智" in line and "已删除" in line for line in logs)


def test_concurrent_accounts_claim_distinct_proxies():
    with IsolatedQualityStore():
        quality_proxy_store.import_quality_proxies(
            "\n".join(
                [
                    "http://proxy.example:8001",
                    "http://proxy.example:8002",
                ]
            )
        )
        used = []
        lock = threading.Lock()
        barrier = threading.Barrier(2)

        def classify(token, proxy, version):
            barrier.wait(timeout=5)
            with lock:
                used.append(proxy)
            return _normal_result()

        results = []
        errors = []

        def worker(token):
            try:
                results.append(
                    intelligence_check.run_intelligence_check(token, "1.0.0")
                )
            except Exception as exc:
                errors.append(exc)

        with _PatchedChecks(classify):
            threads = [
                threading.Thread(target=worker, args=("token-a",)),
                threading.Thread(target=worker, args=("token-b",)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)
        assert errors == []
        assert results == [True, True]
        assert sorted(used) == [
            "http://proxy.example:8001",
            "http://proxy.example:8002",
        ]
        assert _remaining_urls() == []


def test_claim_skips_disabled_and_pops_first_enabled():
    with IsolatedQualityStore():
        imported = quality_proxy_store.import_quality_proxies(
            "\n".join(
                [
                    "http://proxy.example:8001",
                    "http://proxy.example:8002",
                ]
            )
        )
        disabled_id = imported["imported_ids"][0]
        quality_proxy_store.update_quality_proxy(disabled_id, enabled=False)
        claimed = quality_proxy_store.claim_runtime_quality_proxy()
        assert claimed == "http://proxy.example:8002"
        remaining = quality_proxy_store.read_quality_proxy_pool()["items"]
        assert [item["id"] for item in remaining] == [disabled_id]
        assert remaining[0]["enabled"] is False
        assert quality_proxy_store.claim_runtime_quality_proxy() is None


def test_clear_quality_proxies_empties_pool():
    with IsolatedQualityStore():
        quality_proxy_store.import_quality_proxies(
            "http://proxy.example:8001\nhttp://proxy.example:8002"
        )
        result = quality_proxy_store.clear_quality_proxies()
        assert result["ok"] is True
        assert result["deleted_count"] == 2
        assert result["summary"]["total"] == 0
        assert result["items"] == []
        empty = quality_proxy_store.clear_quality_proxies()
        assert empty["ok"] is True
        assert empty["deleted_count"] == 0
        assert quality_proxy_store.read_quality_proxy_pool()["summary"]["total"] == 0


def test_clear_quality_proxies_refuses_while_test_job_running():
    with IsolatedQualityStore():
        quality_proxy_store.import_quality_proxies("http://proxy.example:8001")
        previous = dict(quality_proxy_store._TEST_JOB)
        try:
            quality_proxy_store._TEST_JOB["running"] = True
            blocked = quality_proxy_store.clear_quality_proxies()
            assert blocked["ok"] is False
            assert "正在运行" in blocked["error"]
            assert quality_proxy_store.read_quality_proxy_pool()["summary"]["total"] == 1
        finally:
            quality_proxy_store._TEST_JOB.clear()
            quality_proxy_store._TEST_JOB.update(previous)


if __name__ == "__main__":
    test_normal_result_deletes_used_proxy()
    test_accounts_use_distinct_proxies_sequentially()
    test_degraded_result_also_deletes_proxy()
    test_concurrent_accounts_claim_distinct_proxies()
    test_claim_skips_disabled_and_pops_first_enabled()
    test_clear_quality_proxies_empties_pool()
    test_clear_quality_proxies_refuses_while_test_job_running()
    print("OK intelligence check proxy consume")
