"""Tests for src.tools.session_aware — SessionAwareToolMixin."""

from src.tools.session_aware import SessionAwareToolMixin


class TestSessionAwareToolMixin:
    def test_default_session_id_is_none(self):
        mixin = SessionAwareToolMixin()
        assert mixin.get_session_id() is None

    def test_set_and_get(self):
        mixin = SessionAwareToolMixin()
        mixin.set_session_id("s123")
        assert mixin.get_session_id() == "s123"

    def test_overwrite(self):
        mixin = SessionAwareToolMixin()
        mixin.set_session_id("s1")
        mixin.set_session_id("s2")
        assert mixin.get_session_id() == "s2"
