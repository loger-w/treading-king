"""Unit tests for backend/services/user_context.py."""
from __future__ import annotations

import importlib
import sys

import pytest


def _reload_user_context():
    """Re-import user_context to reset lru_cache after env mutation."""
    if "services.user_context" in sys.modules:
        importlib.reload(sys.modules["services.user_context"])
    from services import user_context
    return user_context


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("USER_LABEL", raising=False)
    monkeypatch.delenv("CACHE_JOB_OWNER", raising=False)
    yield


def test_valid_label(monkeypatch):
    monkeypatch.setenv("USER_LABEL", "loger")
    uc = _reload_user_context()
    assert uc.get_user_label() == "loger"


def test_valid_label_with_underscore_and_digits(monkeypatch):
    monkeypatch.setenv("USER_LABEL", "alice_2")
    uc = _reload_user_context()
    assert uc.get_user_label() == "alice_2"


@pytest.mark.parametrize("bad", ["", " ", "Loger", "foo bar", "x", "a" * 21, "user@host"])
def test_invalid_label_raises(monkeypatch, bad):
    monkeypatch.setenv("USER_LABEL", bad)
    uc = _reload_user_context()
    with pytest.raises(RuntimeError, match="USER_LABEL invalid"):
        uc.get_user_label()


def test_cache_owner_match(monkeypatch):
    monkeypatch.setenv("USER_LABEL", "loger")
    monkeypatch.setenv("CACHE_JOB_OWNER", "loger")
    uc = _reload_user_context()
    assert uc.is_cache_job_owner() is True


def test_cache_owner_mismatch(monkeypatch):
    monkeypatch.setenv("USER_LABEL", "alice")
    monkeypatch.setenv("CACHE_JOB_OWNER", "loger")
    uc = _reload_user_context()
    assert uc.is_cache_job_owner() is False


def test_cache_owner_unset(monkeypatch):
    monkeypatch.setenv("USER_LABEL", "alice")
    uc = _reload_user_context()
    assert uc.is_cache_job_owner() is False
