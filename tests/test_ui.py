"""Smoke tests for the Streamlit demo (the `ui` dependency group)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest  # noqa: E402

APP_PATH = str(Path(__file__).resolve().parent.parent / "ui" / "app.py")


def _app() -> AppTest:
    at = AppTest.from_file(APP_PATH)
    at.run(timeout=30)
    assert not at.exception
    return at


def _run_scenario(at: AppTest, scenario: str) -> AppTest:
    at.radio[0].set_value(scenario).run(timeout=30)
    next(b for b in at.button if b.label == "Send 200 requests").click().run(timeout=30)
    return at


def test_app_loads_without_exceptions():
    _app()


def test_traffic_splits_and_assignment_is_sticky():
    at = _app()
    next(b for b in at.button if b.label == "Send traffic").click().run(timeout=30)
    assert not at.exception
    assert any("sticky" in s.value for s in at.success)


def test_a_bad_canary_is_rolled_back():
    at = _run_scenario(_app(), "Bad canary — v2 is broken, v1 is healthy")
    assert not at.exception
    assert any("Rolled back" in w.value for w in at.warning)


def test_a_shared_upstream_outage_is_not_rolled_back():
    """The distinction the whole repo is built around, exercised through the UI."""
    at = _run_scenario(_app(), "Upstream outage — both v1 and v2 are broken")
    assert not at.exception
    assert any("No rollback" in s.value for s in at.success)


def test_a_healthy_deployment_is_left_alone():
    at = _run_scenario(_app(), "Everything healthy")
    assert not at.exception
    assert any("No rollback" in s.value for s in at.success)
