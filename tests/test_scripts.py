"""Scripts d'exploitation : un seul cycle à la fois, tableau de bord sans effet sur le trading."""

from __future__ import annotations

import os
import sys
import time

from conftest import ROOT

sys.path.insert(0, str(ROOT / "scripts"))
import run_simulation  # noqa: E402


def test_only_one_cycle_at_a_time(tmp_path):
    lock = tmp_path / "state" / "cycle.lock"
    assert run_simulation.acquire_lock(lock)
    assert not run_simulation.acquire_lock(lock)  # un 2ᵉ cycle simultané abandonne
    lock.unlink()
    assert run_simulation.acquire_lock(lock)


def test_stale_lock_from_a_killed_cycle_is_released(tmp_path):
    """Un cycle tué (runner perdu) laisse un verrou : il est ignoré après 15 minutes."""
    lock = tmp_path / "cycle.lock"
    lock.write_text("1234")
    old = time.time() - run_simulation.STALE_LOCK_SECONDS - 60
    os.utime(lock, (old, old))
    assert run_simulation.acquire_lock(lock)


class _Report:
    def __init__(self, exit_code=0):
        self.exit_code = exit_code


def test_dashboard_failure_never_stops_the_cycle(tmp_path):
    settings = {"dashboard": {"output_dir": str(tmp_path / "site"), "every_minutes": 30},
                "paths": {"ledger": str(tmp_path / "absent.jsonl"), "bankruptcy_lock": str(tmp_path / "x.lock")}}
    result = run_simulation.maybe_build_dashboard(settings, b"k" * 40, None, _Report())
    assert result.startswith("échec")


def test_dashboard_is_not_built_from_an_unreadable_ledger(tmp_path):
    assert run_simulation.maybe_build_dashboard({}, b"k" * 40, None, _Report(exit_code=2)).startswith("non généré")
