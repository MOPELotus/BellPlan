"""Password lock persistence, verification and throttling."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from bellplan.security import KEY, OperationLock
from bellplan.storage import Store


@pytest.fixture
def lock():
    with TemporaryDirectory() as directory:
        store = Store(Path(directory) / "lock.sqlite3")
        yield OperationLock(store)
        store.close()


def test_persisted_password_starts_locked_without_exposing_credentials(lock):
    lock.set_password(None, "password", "password")
    first = lock.store.get_setting(KEY)
    assert "password" not in str(first)
    assert lock.state() == {"configured": True, "locked": False}
    restarted = OperationLock(lock.store)
    assert restarted.state() == {"configured": True, "locked": True}
    restarted.unlock("password")
    assert not restarted.locked
    restarted.set_password("password", "password", "password")
    assert restarted.record != first


def test_change_remove_and_failures_preserve_configuration(lock):
    lock.set_password(None, "original", "original")
    original = dict(lock.record)
    for current, password, confirmation in [("wrong", "new", "new"), ("original", "x", "y")]:
        with pytest.raises(ValueError):
            lock.set_password(current, password, confirmation)
        assert lock.record == original
    with pytest.raises(ValueError):
        lock.remove_password("wrong")
    lock.set_password("original", "new", "new")
    with pytest.raises(ValueError):
        lock.unlock("original")
    lock.remove_password("new")
    assert OperationLock(lock.store).state() == {"configured": False, "locked": False}


def test_lock_and_throttle(lock):
    lock.set_password(None, "secret", "secret")
    lock.lock()
    with patch("bellplan.security.time.monotonic", return_value=100):
        for _ in range(5):
            with pytest.raises(ValueError, match="密码不正确"):
                lock.unlock("wrong")
        with pytest.raises(ValueError, match="频繁"):
            lock.unlock("secret")
    assert lock.locked
    with patch("bellplan.security.time.monotonic", return_value=131):
        lock.unlock("secret")
    assert not lock.locked


@pytest.mark.parametrize("password", [None, 123, "", "x" * 257, [], {}])
def test_reject_invalid_password_without_saving(lock, password):
    with pytest.raises(ValueError):
        lock.set_password(None, password, password)
    assert not lock.state()["configured"]


def test_corrupt_credentials_fail_closed(lock):
    lock.store.set_setting(KEY, {"salt": "broken"})
    restarted = OperationLock(lock.store)
    assert restarted.locked
    with pytest.raises(ValueError):
        restarted.unlock("anything")
