"""Tests for session-scoped budget tracking."""

from src.budgets import SessionBudgetRegistry
from src.session import SessionManager


class TestSessionBudgetRegistry:
    def setup_method(self):
        SessionBudgetRegistry.reset_all()

    def test_coordinator_launch_budget_stops_at_limit(self):
        allowed, state = SessionBudgetRegistry.try_register_coordinator_launches("s1", 2, 3)
        assert allowed is True
        assert state.launches == 2
        assert state.waves == 1

        allowed, state = SessionBudgetRegistry.try_register_coordinator_launches("s1", 2, 3)
        assert allowed is False
        assert state.launches == 2
        assert state.waves == 1

    def test_tavily_session_budget_is_shared(self):
        allowed, _ = SessionBudgetRegistry.try_consume_tavily_call("s1", "a1", 2, 3)
        assert allowed is True
        allowed, _ = SessionBudgetRegistry.try_consume_tavily_call("s1", "a2", 2, 3)
        assert allowed is True

        allowed, reason = SessionBudgetRegistry.try_consume_tavily_call("s1", "a1", 2, 3)
        assert allowed is False
        assert "session call budget exhausted" in reason.lower()

    def test_tavily_agent_budget_is_per_agent(self):
        allowed, _ = SessionBudgetRegistry.try_consume_tavily_call("s1", "a1", 5, 1)
        assert allowed is True

        allowed, reason = SessionBudgetRegistry.try_consume_tavily_call("s2", "a1", 5, 1)
        assert allowed is True

        allowed, reason = SessionBudgetRegistry.try_consume_tavily_call("s1", "a1", 5, 1)
        assert allowed is False
        assert "agent call budget exhausted" in reason.lower()

    def test_remove_session_clears_session_budgets(self):
        manager = SessionManager()
        session = manager.create_session("q")

        SessionBudgetRegistry.try_register_coordinator_launches(session.session_id, 1, 2)
        SessionBudgetRegistry.try_consume_tavily_call(session.session_id, "a1", 2, 2)
        manager.remove_session(session.session_id)

        allowed, state = SessionBudgetRegistry.try_register_coordinator_launches(session.session_id, 2, 2)
        assert allowed is True
        assert state.launches == 2
