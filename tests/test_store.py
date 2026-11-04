"""State store: atomicity, recovery, and concurrent increments."""

from __future__ import annotations

import json
import multiprocessing as mp
import os

import pytest

from oath_relay.store import StateError, StateStore


def _take_many(args: tuple[str, str, int]) -> list[int]:
    path, name, count = args
    store = StateStore(path)
    return [store.take(name, 0) for _ in range(count)]


def test_take_returns_current_then_increments(tmp_path) -> None:
    store = StateStore(tmp_path / "state.json")
    assert [store.take("t") for _ in range(3)] == [0, 1, 2]
    assert store.counter("t") == 3


def test_counter_start_is_used_until_first_take(tmp_path) -> None:
    store = StateStore(tmp_path / "state.json")
    assert store.counter("t", default=7) == 7
    assert store.take("t", 7) == 7
    assert store.counter("t", default=7) == 8


def test_set_counter_forces_value(tmp_path) -> None:
    store = StateStore(tmp_path / "state.json")
    store.take("t")
    store.set_counter("t", 42)
    assert store.counter("t") == 42
    assert store.take("t") == 42


def test_state_file_is_private_and_valid_json(tmp_path) -> None:
    path = tmp_path / "nested" / "state.json"
    store = StateStore(path)
    store.take("t")
    assert path.stat().st_mode & 0o777 == 0o600
    data = json.loads(path.read_text())
    assert data["t"]["counter"] == 1
    assert "updated_at" in data["t"]


def test_no_temp_files_left_behind(tmp_path) -> None:
    store = StateStore(tmp_path / "state.json")
    for _ in range(5):
        store.take("t")
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".state-")]
    assert leftovers == []


def test_corrupt_state_raises(tmp_path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{not json")
    with pytest.raises(StateError):
        StateStore(path).counter("t")


def test_non_object_state_raises(tmp_path) -> None:
    path = tmp_path / "state.json"
    path.write_text("[1, 2, 3]")
    with pytest.raises(StateError):
        StateStore(path).counter("t")


def test_negative_counter_in_state_raises(tmp_path) -> None:
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"t": {"counter": -5}}))
    with pytest.raises(StateError):
        StateStore(path).counter("t")


def test_empty_file_is_treated_as_empty_state(tmp_path) -> None:
    path = tmp_path / "state.json"
    path.write_text("")
    assert StateStore(path).counter("t") == 0


def test_removed_token_keeps_its_state(tmp_path) -> None:
    """Dropping a token from the config must not silently reset its counter."""
    store = StateStore(tmp_path / "state.json")
    for _ in range(4):
        store.take("gone")
    assert store.snapshot()["gone"]["counter"] == 4


@pytest.mark.parametrize(("workers", "per_worker"), [(8, 25)])
def test_concurrent_takes_are_serialized(tmp_path, workers: int, per_worker: int) -> None:
    path = str(tmp_path / "state.json")
    ctx = mp.get_context("fork")
    with ctx.Pool(workers) as pool:
        results = pool.map(_take_many, [(path, "t", per_worker)] * workers)

    issued = [value for batch in results for value in batch]
    total = workers * per_worker
    assert len(issued) == total
    assert sorted(issued) == list(range(total)), "counters must be unique and gap-free"
    assert StateStore(path).counter("t") == total


def test_parallel_stores_share_one_lock_file(tmp_path) -> None:
    path = tmp_path / "state.json"
    a = StateStore(path)
    b = StateStore(path)
    assert a.lock_path == b.lock_path
    assert a.lock_path.exists() is False
    a.take("t")
    assert os.path.exists(a.lock_path)
