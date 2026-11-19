"""Local operation lock; credentials never enter UI preferences or backups."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

KEY = "operation_lock"
ITERATIONS = 210_000


def _password(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 256:
        raise ValueError("密码须为 1 至 256 个字符")
    return value.encode("utf-8")


class OperationLock:
    def __init__(self, store):
        self.store = store
        self.record = store.get_setting(KEY)
        self.locked = self.record is not None
        self.failures = 0
        self.retry_at = 0.0

    def state(self):
        return {"configured": self.record is not None, "locked": self.locked}

    def _verify(self, password):
        if time.monotonic() < self.retry_at:
            raise ValueError("密码尝试过于频繁，请稍后重试")
        raw = _password(password)
        try:
            salt = bytes.fromhex(self.record["salt"])
            expected = bytes.fromhex(self.record["hash"])
            valid = (
                len(salt) == 16
                and len(expected) == 32
                and hmac.compare_digest(
                    hashlib.pbkdf2_hmac("sha256", raw, salt, ITERATIONS), expected
                )
            )
        except (TypeError, KeyError, ValueError):
            valid = False
        if not valid:
            self.failures += 1
            if self.failures >= 5:
                self.retry_at = time.monotonic() + 30
            raise ValueError("密码不正确")
        self.failures = 0
        self.retry_at = 0.0

    def unlock(self, password):
        if self.record is None:
            raise ValueError("尚未设置锁定密码")
        self._verify(password)
        self.locked = False

    def lock(self):
        if self.record is None:
            raise ValueError("请先设置锁定密码")
        self.locked = True

    def set_password(self, current_password, new_password, confirmation):
        raw = _password(new_password)
        if new_password != confirmation:
            raise ValueError("两次输入的新密码不一致")
        if self.record is not None:
            self._verify(current_password)
        salt = secrets.token_bytes(16)
        record = {
            "salt": salt.hex(),
            "hash": hashlib.pbkdf2_hmac("sha256", raw, salt, ITERATIONS).hex(),
        }
        self.store.set_setting(KEY, record)
        self.record = record
        self.failures = 0
        self.retry_at = 0.0

    def remove_password(self, password):
        if self.record is None:
            raise ValueError("尚未设置锁定密码")
        self._verify(password)
        self.store.set_setting(KEY, None)
        self.record = None
        self.locked = False
