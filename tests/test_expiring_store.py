"""Bounded memory for bundles, archives and test sessions (Phase 5 plan, step 1.1)."""
import threading

import pytest

import bundle_service
import selftest
from app import MAX_REQUEST_BYTES, app, bundles
from expiring_store import ExpiringStore
from test_runner import service


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


def test_an_entry_expires_after_its_ttl(clock):
    store = ExpiringStore(ttl=60, max_items=10, clock=clock)
    store["a"] = 1
    clock.now += 59.9
    assert store.get("a") == 1 and "a" in store
    clock.now += 0.1
    assert store.get("a") is None
    assert "a" not in store
    with pytest.raises(KeyError):
        store["a"]
    assert len(store) == 0


def test_expired_entries_are_removed_on_insert_even_if_never_read_again(clock):
    store = ExpiringStore(ttl=60, max_items=10, clock=clock)
    for key in "abc":
        store[key] = key
    clock.now += 61
    store["d"] = "d"
    assert list(store._entries) == ["d"]


def test_the_oldest_entries_go_first_past_the_item_cap(clock):
    store = ExpiringStore(ttl=60, max_items=3, clock=clock)
    for key in "abcd":
        store[key] = key
        clock.now += 1
    assert list(store) == ["b", "c", "d"]


def test_replacing_an_entry_restarts_its_lifetime(clock):
    store = ExpiringStore(ttl=60, max_items=2, clock=clock)
    store["a"] = 1
    store["b"] = 2
    clock.now += 30
    store["a"] = 3
    clock.now += 40
    assert store.get("a") == 3
    assert store.get("b") is None


def test_the_byte_cap_measures_entries_again_after_they_grow(clock):
    store = ExpiringStore(ttl=60, max_items=10, max_bytes=10, size_of=lambda v: len(v["data"]), clock=clock)
    first = {"data": ""}
    store["a"] = first
    first["data"] = "x" * 8  # grows after being stored, like a bundle being processed
    store["b"] = {"data": "y" * 5}
    assert list(store) == ["b"]


def test_the_entry_just_stored_is_kept_even_alone_over_the_byte_cap(clock):
    store = ExpiringStore(ttl=60, max_items=10, max_bytes=4, size_of=len, clock=clock)
    store["a"] = "ab"
    store["b"] = "too large"
    assert list(store) == ["b"]


def test_add_refuses_a_live_key_but_accepts_an_expired_one(clock):
    store = ExpiringStore(ttl=60, max_items=10, clock=clock)
    assert store.add("a", 1)
    assert not store.add("a", 2)
    assert store["a"] == 1
    clock.now += 60
    assert store.add("a", 3)
    assert store["a"] == 3


def test_on_evict_runs_for_expiry_and_caps_but_not_for_pop_or_clear(clock):
    evicted = []
    store = ExpiringStore(ttl=60, max_items=2, clock=clock, on_evict=lambda k, v: evicted.append(k))
    store["a"] = 1
    store["b"] = 2
    store["c"] = 3          # over the cap: a goes
    clock.now += 60
    assert store.get("b") is None   # expired: b goes
    store.pop("c")
    store["d"] = 4
    store.clear()
    assert evicted == ["a", "b"]


def test_invalid_limits_are_rejected():
    with pytest.raises(ValueError):
        ExpiringStore(ttl=0, max_items=1)
    with pytest.raises(ValueError):
        ExpiringStore(ttl=1, max_items=0)
    with pytest.raises(ValueError):
        ExpiringStore(ttl=1, max_items=1, max_bytes=10)


def test_concurrent_writers_never_exceed_the_cap():
    store = ExpiringStore(ttl=60, max_items=50)

    def write(prefix):
        for i in range(500):
            store[f"{prefix}-{i}"] = i

    threads = [threading.Thread(target=write, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(store) == 50


# The stores the services actually use

@pytest.fixture
def system(monkeypatch):
    bundles.clear()
    bundle_service.archives.clear()
    app.config["PROCESS_BUNDLES_INLINE"] = True
    yield app.test_client()
    app.config["PROCESS_BUNDLES_INLINE"] = False
    bundles.clear()
    bundle_service.archives.clear()


def test_an_expired_bundle_and_its_archive_answer_404(system, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(bundles, "_clock", clock)
    monkeypatch.setattr(bundle_service.archives, "_clock", clock)
    request = {"product_id": "prd-88", "release_version": "v1.2"}
    bundle_id = system.post("/bundles", json=request).get_json()["bundle_id"]
    assert system.get(f"/bundles/{bundle_id}").status_code == 200
    assert system.get(f"/bundles/{bundle_id}/download").status_code == 200

    clock.now += bundle_service.BUNDLE_TTL_SECONDS
    assert system.get(f"/bundles/{bundle_id}").status_code == 404
    assert system.get(f"/bundles/{bundle_id}/download").status_code == 404
    assert bundle_service.archives.get(bundle_id) is None


def test_a_bundle_removed_by_the_cap_takes_its_archive_with_it(system, monkeypatch):
    monkeypatch.setattr(bundles, "max_items", 1)
    first = system.post("/bundles", json={"product_id": "prd-88", "release_version": "v1.2"}).get_json()["bundle_id"]
    system.post("/bundles", json={"product_id": "prd-88", "release_version": "v1.2"})
    assert system.get(f"/bundles/{first}").status_code == 404
    assert system.get(f"/bundles/{first}/download").status_code == 404


def test_the_services_bound_every_store():
    for store in (bundles, bundle_service.archives, selftest._sessions, service._sessions):
        assert isinstance(store, ExpiringStore)
        assert store.ttl <= 3600
    assert bundles.max_bytes is not None and bundle_service.archives.max_bytes is not None


@pytest.mark.parametrize("client, path", [
    (app.test_client(), "/bundles"),
    (app.test_client(), "/test/setup"),
    (service.app.test_client(), "/test/setup"),
])
def test_an_oversized_request_body_is_refused(client, path):
    body = b'{"x": "' + b"a" * MAX_REQUEST_BYTES + b'"}'
    response = client.post(path, data=body, content_type="application/json")
    assert response.status_code == 413
