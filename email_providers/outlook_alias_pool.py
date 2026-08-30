#!/usr/bin/env python3
"""Outlook plus-alias allocation pool (state-compatible with lite-patch).

Pure Python — no GUI. Persists durable cursor/serial under accounts state JSON.
Uses panel ``secure_files`` locks/atomic JSON when available.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional, Union

try:
    from secure_files import atomic_write_json, exclusive_file_lock
except Exception:  # pragma: no cover - import path / bootstrap
    try:
        from filelock import FileLock as _FileLock
    except Exception:  # pragma: no cover
        _FileLock = None  # type: ignore[misc, assignment]

    atomic_write_json = None  # type: ignore[assignment]
    exclusive_file_lock = None  # type: ignore[assignment]
else:
    _FileLock = None  # type: ignore[misc, assignment]


class OutlookAliasPoolError(Exception):
    """Base error for Outlook alias pool operations."""


class OutlookAccountPoolExhausted(OutlookAliasPoolError):
    """No imported account has another available alias."""


@dataclass(frozen=True)
class OutlookAccount:
    email: str
    password: str
    client_id: str
    refresh_token: str

    @property
    def key(self) -> str:
        return self.email.casefold()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


@contextmanager
def _state_lock(state_file: Path) -> Iterator[None]:
    lock_path = Path(f"{state_file}.lock")
    if exclusive_file_lock is not None:
        with exclusive_file_lock(lock_path):
            yield
        return
    if _FileLock is not None:
        with _FileLock(str(lock_path)):
            yield
        return
    # Last-resort process-local lock only.
    yield


class OutlookAliasPool:
    """Allocate plus-address aliases from imported Outlook accounts."""

    def __init__(
        self,
        accounts_file: Union[str, Path],
        state_file: Union[str, Path],
        aliases_per_account: int = 5,
        accounts: Optional[list[OutlookAccount]] = None,
    ) -> None:
        self.accounts_file = Path(accounts_file)
        self.state_file = Path(state_file)
        self.aliases_per_account = max(1, int(aliases_per_account))
        self._lock = threading.RLock()
        self._accounts = list(accounts) if accounts is not None else self._load_accounts()
        if not self._accounts:
            raise OutlookAliasPoolError("Outlook 账号列表为空")
        self._state = self._load_state()
        self._leases: dict[str, dict[str, Any]] = {}

    @classmethod
    def build(
        cls,
        accounts_file: Union[str, Path],
        state_file: Union[str, Path],
        *,
        aliases_per_account: int = 5,
    ) -> "OutlookAliasPool":
        """Construct a pool from on-disk accounts + state paths."""
        return cls(
            accounts_file=accounts_file,
            state_file=state_file,
            aliases_per_account=aliases_per_account,
        )

    @property
    def account_count(self) -> int:
        return len(self._accounts)

    def remaining_alias_slots(self) -> int:
        """How many plus-aliases can still be allocated (not disabled, under cap)."""
        with self._lock, _state_lock(self.state_file):
            self._state = self._load_state()
            remaining_total = 0
            for account in self._accounts:
                account_state = self._get_account_state(account)
                if bool(account_state.get("disabled", False)):
                    continue
                next_alias_index = int(account_state.get("next_alias_index", 0) or 0)
                free_slots = self.aliases_per_account - next_alias_index
                if free_slots > 0:
                    remaining_total += free_slots
            return remaining_total

    def is_exhausted(self) -> bool:
        return self.remaining_alias_slots() <= 0

    def allocate_alias(self) -> tuple[str, str]:
        """Reserve the next address; return (alias_email, lease_token)."""
        with self._lock, _state_lock(self.state_file):
            self._state = self._load_state()
            account, account_index = self._select_available_account()
            account_state = self._get_account_state(account)
            alias_index = int(account_state.get("next_alias_index", 0) or 0)
            alias_email = self._build_alias(account.email, alias_index)
            previous_cursor = int(self._state.get("next_account_cursor", 0) or 0)
            allocation_serial = int(self._state.get("allocation_serial", 0) or 0) + 1

            account_state["next_alias_index"] = alias_index + 1
            account_state["last_alias"] = alias_email
            account_state["last_allocated_at"] = _utc_now()
            self._state["next_account_cursor"] = (account_index + 1) % len(self._accounts)
            self._state["allocation_serial"] = allocation_serial
            self._save_state()

            lease_token = f"outlook:{secrets.token_urlsafe(24)}"
            self._leases[lease_token] = {
                "account_key": account.key,
                "account_index": account_index,
                "alias_index": alias_index,
                "alias_email": alias_email,
                "allocated_at": time.time(),
                "allocation_serial": allocation_serial,
                "previous_cursor": previous_cursor,
            }
            return alias_email, lease_token

    def release_alias(self, lease_token: str, alias_email: str) -> bool:
        """Rollback an unsubmitted allocation when it is latest for its account."""
        token = str(lease_token or "")
        target_email = str(alias_email or "").casefold()
        with self._lock, _state_lock(self.state_file):
            lease = self._leases.pop(token, None)
            if not lease:
                return False
            if str(lease.get("alias_email", "")).casefold() != target_email:
                return False

            self._state = self._load_state()
            allocation_serial = int(lease.get("allocation_serial", 0) or 0)
            current_serial = int(self._state.get("allocation_serial", 0) or 0)
            is_global_latest = allocation_serial == current_serial

            account = self._account_by_key(str(lease.get("account_key", "")))
            account_state = self._get_account_state(account)
            alias_index = int(lease.get("alias_index", -1))
            next_alias_index = int(account_state.get("next_alias_index", 0) or 0)
            if next_alias_index != alias_index + 1:
                return False
            if str(account_state.get("last_alias", "")).casefold() != target_email:
                return False

            account_state["next_alias_index"] = alias_index
            account_state.pop("last_alias", None)
            account_state.pop("last_allocated_at", None)
            if is_global_latest:
                self._state["next_account_cursor"] = int(
                    lease.get("previous_cursor", lease.get("account_index", 0)) or 0
                )
                self._state["allocation_serial"] = max(0, allocation_serial - 1)
            self._save_state()
            return True

    def get_account_for_alias_lease(
        self,
        lease_token: str,
        alias_email: str = "",
    ) -> OutlookAccount:
        """Return the primary mailbox account bound to an in-process lease."""
        with self._lock:
            lease = self._leases.get(str(lease_token or ""))
            if not lease:
                raise OutlookAliasPoolError(
                    "Outlook 邮箱 lease 已失效，请重新分配邮箱后再试"
                )
            if alias_email and str(lease.get("alias_email", "")).casefold() != str(
                alias_email
            ).casefold():
                raise OutlookAliasPoolError("Outlook 邮箱 lease 与目标地址不匹配")
            return self._account_by_key(str(lease.get("account_key", "")))

    def _load_accounts(self) -> list[OutlookAccount]:
        if not self.accounts_file.is_file():
            raise OutlookAliasPoolError(
                f"Outlook 账号池文件不存在: {self.accounts_file}"
            )

        accounts_by_key: dict[str, OutlookAccount] = {}
        ordered_keys: list[str] = []
        with self.accounts_file.open("r", encoding="utf-8-sig") as accounts_handle:
            for line_number, raw_line in enumerate(accounts_handle, start=1):
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                # 允许尾部附加字段；OAuth 只用前 4 段。
                fields = [field.strip() for field in line.split("----") if field.strip()]
                if len(fields) < 4:
                    raise OutlookAliasPoolError(
                        f"Outlook 账号池第 {line_number} 行格式错误，"
                        "应为 邮箱----密码----ClientID----RefreshToken"
                        "（后面可再跟辅助字段）"
                    )
                email, password, client_id, refresh_token = fields[:4]
                if "@" not in email or not client_id or not refresh_token:
                    raise OutlookAliasPoolError(
                        f"Outlook 账号池第 {line_number} 行缺少有效邮箱、"
                        "ClientID 或 RefreshToken"
                    )
                account = OutlookAccount(
                    email=email,
                    password=password,
                    client_id=client_id,
                    refresh_token=refresh_token,
                )
                if account.key not in accounts_by_key:
                    ordered_keys.append(account.key)
                accounts_by_key[account.key] = account

        accounts = [accounts_by_key[key] for key in ordered_keys]
        if not accounts:
            raise OutlookAliasPoolError(f"Outlook 账号池为空: {self.accounts_file}")
        return accounts

    def _load_state(self) -> dict[str, Any]:
        if not self.state_file.exists():
            return {
                "version": 1,
                "next_account_cursor": 0,
                "allocation_serial": 0,
                "accounts": {},
            }
        try:
            with self.state_file.open("r", encoding="utf-8") as state_handle:
                loaded = json.load(state_handle)
        except Exception as exc:
            raise OutlookAliasPoolError(
                f"读取 Outlook 状态文件失败: {self.state_file}: {exc}"
            ) from exc
        if not isinstance(loaded, dict):
            raise OutlookAliasPoolError(f"Outlook 状态文件格式错误: {self.state_file}")
        loaded.setdefault("version", 1)
        loaded.setdefault("next_account_cursor", 0)
        loaded.setdefault("allocation_serial", 0)
        loaded.setdefault("accounts", {})
        if not isinstance(loaded["accounts"], dict):
            raise OutlookAliasPoolError("Outlook 状态文件 accounts 字段格式错误")
        return loaded

    def _save_state(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        if atomic_write_json is not None:
            atomic_write_json(self.state_file, self._state)
            return
        temporary_path = self.state_file.with_name(
            f".{self.state_file.name}.{secrets.token_hex(6)}.tmp"
        )
        try:
            temporary_path.write_text(
                json.dumps(self._state, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            temporary_path.replace(self.state_file)
        finally:
            try:
                temporary_path.unlink(missing_ok=True)
            except Exception:
                pass

    def _select_available_account(self) -> tuple[OutlookAccount, int]:
        cursor = int(self._state.get("next_account_cursor", 0) or 0)
        account_total = len(self._accounts)
        for offset in range(account_total):
            account_index = (cursor + offset) % account_total
            account = self._accounts[account_index]
            account_state = self._get_account_state(account)
            if bool(account_state.get("disabled", False)):
                continue
            next_alias_index = int(account_state.get("next_alias_index", 0) or 0)
            if next_alias_index < self.aliases_per_account:
                return account, account_index
        raise OutlookAccountPoolExhausted(
            "Outlook 账号池已耗尽：所有账号均已禁用或达到每账号别名上限"
        )

    def _get_account_state(self, account: OutlookAccount) -> dict[str, Any]:
        accounts_state = self._state.setdefault("accounts", {})
        # Prefer exact email key if present (lite-patch often stores full email).
        if account.email in accounts_state and isinstance(
            accounts_state[account.email], dict
        ):
            account_state = accounts_state[account.email]
        else:
            account_state = accounts_state.setdefault(
                account.key,
                {
                    "email": account.email,
                    "next_alias_index": 0,
                    "disabled": False,
                },
            )
        if not isinstance(account_state, dict):
            account_state = {
                "email": account.email,
                "next_alias_index": 0,
                "disabled": False,
            }
            accounts_state[account.key] = account_state
        account_state["email"] = account.email
        account_state.setdefault("next_alias_index", 0)
        account_state.setdefault("disabled", False)
        return account_state

    def _account_by_key(self, account_key: str) -> OutlookAccount:
        key = str(account_key or "").casefold()
        for account in self._accounts:
            if account.key == key:
                return account
        raise OutlookAliasPoolError("Outlook lease 对应的主邮箱已不在账号池中")

    @staticmethod
    def _build_alias(email: str, alias_index: int) -> str:
        if alias_index <= 0:
            return email
        local_part, domain = email.rsplit("@", 1)
        return f"{local_part}+{alias_index}@{domain}"
