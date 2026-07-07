"""Tests for src.utils.cost_tracker — CostTrackerCallback."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.utils.cost_tracker import CostTrackerCallback, SessionCostInfo


class TestCostTrackerExtractAndTrack:
    """Tests for _extract_and_track with mock kwargs/response objects."""

    def _make_kwargs(self, session_id: str | None = "sess-1") -> dict:
        metadata = {}
        if session_id is not None:
            metadata["session_id"] = session_id
        return {
            "litellm_params": {"metadata": metadata},
        }

    def _make_response(self, prompt_tokens=10, completion_tokens=5, total_tokens=15):
        usage = SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )
        return SimpleNamespace(usage=usage, _hidden_params={})

    @patch("src.utils.cost_tracker.litellm.completion_cost", return_value=0.001)
    def test_tracks_single_call(self, mock_cost, cost_tracker_instance: CostTrackerCallback):
        kwargs = self._make_kwargs("s1")
        kwargs["model"] = "openai/gpt-5-mini"
        resp = self._make_response()
        cost_tracker_instance._extract_and_track(kwargs, resp)

        info = cost_tracker_instance.get_session_cost("s1")
        assert info is not None
        assert info.total_cost == pytest.approx(0.001)
        assert info.prompt_tokens == 10
        assert info.completion_tokens == 5
        assert info.total_tokens == 15
        assert info.call_count == 1
        mock_cost.assert_called_once_with(
            completion_response=resp,
            base_model="openai/gpt-5-mini",
        )

    @patch("src.utils.cost_tracker.litellm.completion_cost", return_value=0.002)
    def test_accumulates_across_calls(self, mock_cost, cost_tracker_instance: CostTrackerCallback):
        kwargs = self._make_kwargs("s1")
        kwargs["model"] = "openai/gpt-5-mini"
        resp = self._make_response()
        cost_tracker_instance._extract_and_track(kwargs, resp)
        cost_tracker_instance._extract_and_track(kwargs, resp)

        info = cost_tracker_instance.get_session_cost("s1")
        assert info.call_count == 2
        assert info.total_cost == pytest.approx(0.004)
        assert info.prompt_tokens == 20

    @patch("src.utils.cost_tracker.litellm.completion_cost", return_value=0.0)
    def test_no_session_id_skipped(self, mock_cost, cost_tracker_instance: CostTrackerCallback):
        kwargs = self._make_kwargs(session_id=None)
        resp = self._make_response()
        cost_tracker_instance._extract_and_track(kwargs, resp)
        assert cost_tracker_instance.get_session_cost("s1") is None

    @patch("src.utils.cost_tracker.litellm.completion_cost", side_effect=Exception("bad"))
    def test_cost_calculation_failure_uses_fallback(self, mock_cost, cost_tracker_instance: CostTrackerCallback):
        kwargs = self._make_kwargs("s1")
        kwargs["model"] = "openai/gpt-5-mini"
        kwargs["response_cost"] = 0.005
        resp = self._make_response()
        cost_tracker_instance._extract_and_track(kwargs, resp)
        info = cost_tracker_instance.get_session_cost("s1")
        assert info is not None
        assert info.total_cost == pytest.approx(0.005)
        assert info.cost_source == "litellm_callback_fallback"

    @patch("src.utils.cost_tracker.litellm.completion_cost", return_value=0.001)
    def test_uses_requested_model_for_pricing(self, mock_cost, cost_tracker_instance: CostTrackerCallback):
        kwargs = self._make_kwargs("s1")
        kwargs["model"] = "openai/gpt-5-mini"
        resp = self._make_response()
        resp.model = "gpt-4o-mini"

        cost_tracker_instance._extract_and_track(kwargs, resp)

        mock_cost.assert_called_once_with(
            completion_response=resp,
            base_model="openai/gpt-5-mini",
        )

    @patch("src.utils.cost_tracker.litellm.completion_cost")
    def test_prefers_provider_response_cost_when_available(self, mock_cost, cost_tracker_instance: CostTrackerCallback):
        kwargs = self._make_kwargs("s1")
        kwargs["model"] = "openai/gpt-5-mini"
        resp = self._make_response()
        resp._hidden_params = {"response_cost": 0.123456}

        cost_tracker_instance._extract_and_track(kwargs, resp)

        info = cost_tracker_instance.get_session_cost("s1")
        assert info is not None
        assert info.total_cost == pytest.approx(0.123456)
        assert info.cost_source == "litellm_response_cost"
        mock_cost.assert_not_called()


class TestCostTrackerManagement:
    def test_clear_session(self, cost_tracker_instance: CostTrackerCallback):
        cost_tracker_instance._session_costs["s1"] = SessionCostInfo(total_cost=1.0)
        cost_tracker_instance.clear_session("s1")
        assert cost_tracker_instance.get_session_cost("s1") is None

    def test_clear_nonexistent_session(self, cost_tracker_instance: CostTrackerCallback):
        cost_tracker_instance.clear_session("no-such")  # should not raise

    def test_get_all_sessions(self, cost_tracker_instance: CostTrackerCallback):
        cost_tracker_instance._session_costs["s1"] = SessionCostInfo()
        cost_tracker_instance._session_costs["s2"] = SessionCostInfo()
        all_sessions = cost_tracker_instance.get_all_sessions()
        assert set(all_sessions.keys()) == {"s1", "s2"}

    def test_get_session_cost_not_found(self, cost_tracker_instance: CostTrackerCallback):
        assert cost_tracker_instance.get_session_cost("nope") is None


class TestCostTrackerRegister:
    @patch("src.utils.cost_tracker.litellm")
    def test_register_once(self, mock_litellm, cost_tracker_instance: CostTrackerCallback):
        mock_litellm.callbacks = []
        cost_tracker_instance.register()
        assert cost_tracker_instance in mock_litellm.callbacks
        assert cost_tracker_instance._registered is True

    @patch("src.utils.cost_tracker.litellm")
    def test_register_idempotent(self, mock_litellm, cost_tracker_instance: CostTrackerCallback):
        mock_litellm.callbacks = []
        cost_tracker_instance.register()
        cost_tracker_instance.register()  # second call
        assert mock_litellm.callbacks.count(cost_tracker_instance) == 1
