import time
import uuid

import pytest

from docupipe_manager.platform.cache import UserLRUCache


def test_get_hit():
    cache = UserLRUCache(capacity=100, ttl_seconds=300)
    uid = uuid.uuid4()
    cache.set(uid, {"username": "test"})
    result = cache.get(uid)
    assert result == {"username": "test"}


def test_get_miss():
    cache = UserLRUCache(capacity=100, ttl_seconds=300)
    result = cache.get(uuid.uuid4())
    assert result is None


def test_eviction_when_full():
    cache = UserLRUCache(capacity=2, ttl_seconds=300)
    uid1 = uuid.uuid4()
    uid2 = uuid.uuid4()
    uid3 = uuid.uuid4()
    cache.set(uid1, {"id": uid1})
    cache.set(uid2, {"id": uid2})
    cache.set(uid3, {"id": uid3})
    assert cache.get(uid1) is None
    assert cache.get(uid2) is not None
    assert cache.get(uid3) is not None


def test_expiry_after_ttl():
    cache = UserLRUCache(capacity=100, ttl_seconds=0)
    uid = uuid.uuid4()
    cache.set(uid, {"username": "test"})
    time.sleep(0.01)
    result = cache.get(uid)
    assert result is None


def test_set_updates_order():
    cache = UserLRUCache(capacity=3, ttl_seconds=300)
    uid1 = uuid.uuid4()
    uid2 = uuid.uuid4()
    uid3 = uuid.uuid4()
    cache.set(uid1, {})
    cache.set(uid2, {})
    cache.set(uid3, {})
    cache.get(uid1)
    cache.set(uuid.uuid4(), {})
    assert cache.get(uid1) is not None


def test_batch_set():
    cache = UserLRUCache(capacity=100, ttl_seconds=300)
    items = [(uuid.uuid4(), {"n": i}) for i in range(10)]
    cache.batch_set(items)
    for uid, val in items:
        assert cache.get(uid) == val


def test_clear():
    cache = UserLRUCache(capacity=100, ttl_seconds=300)
    uid = uuid.uuid4()
    cache.set(uid, {})
    cache.clear()
    assert cache.get(uid) is None
