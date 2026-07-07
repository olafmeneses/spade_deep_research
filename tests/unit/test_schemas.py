"""Tests for src.config.schemas — Pydantic schemas for structured agent output."""

import pytest
from pydantic import ValidationError

from src.config.schemas import (
    ResearchPlan,
    ResearchQuestion,
    CriticReview,
    FindingsSummary,
)


# ── ResearchPlan ──────────────────────────────────────────────────────


class TestResearchPlan:
    def test_valid_plan(self):
        plan = ResearchPlan(
            detected_language="English",
            research_goal="Understand transformers",
            research_questions=[
                ResearchQuestion(
                    question="What are transformers?",
                    source="arxiv",
                    description="Look for foundational papers",
                ),
            ],
        )
        assert plan.detected_language == "English"
        assert len(plan.research_questions) == 1

    def test_missing_required_field(self):
        with pytest.raises(ValidationError):
            ResearchPlan(
                detected_language="English",
                # research_goal missing
                research_questions=[],
            )

    def test_empty_questions_allowed(self):
        plan = ResearchPlan(
            detected_language="Spanish",
            research_goal="Goal",
            research_questions=[],
        )
        assert plan.research_questions == []


# ── ResearchQuestion ──────────────────────────────────────────────────


class TestResearchQuestion:
    def test_valid(self):
        q = ResearchQuestion(question="Q?", source="tavily", description="Desc")
        assert q.source == "tavily"

    def test_missing_source(self):
        with pytest.raises(ValidationError):
            ResearchQuestion(question="Q?", description="Desc")


# ── CriticReview ──────────────────────────────────────────────────────


class TestCriticReview:
    def test_sufficient(self):
        review = CriticReview(
            status="SUFFICIENT",
            feedback="Looks good",
        )
        assert review.status == "SUFFICIENT"
        assert review.issues == []
        assert review.missing_information == []
        assert review.writing_improvements == []

    def test_insufficient(self):
        review = CriticReview(
            status="INSUFFICIENT",
            feedback="Needs more data",
            issues=["Missing citations"],
            missing_information=["Transformer architectures"],
        )
        assert review.status == "INSUFFICIENT"
        assert len(review.issues) == 1

    def test_invalid_status_rejected(self):
        with pytest.raises(ValidationError):
            CriticReview(
                status="MAYBE",
                feedback="Hmm",
            )

    def test_field_descriptions_guide_actionable_feedback(self):
        schema = CriticReview.model_json_schema()

        issues_description = schema["properties"]["issues"]["description"]
        improvements_description = schema["properties"]["writing_improvements"]["description"]

        assert "writer review requirements" in issues_description
        assert "rewrite directives" in improvements_description
        assert "existing evidence" in improvements_description
        assert "high-level report improvements" in improvements_description
        assert "fine-grained fixes" in improvements_description
        assert "rewriting is expensive" in improvements_description
        assert "trigger writer routing" in improvements_description


# ── FindingsSummary ───────────────────────────────────────────────────


class TestFindingsSummary:
    def test_valid(self):
        fs = FindingsSummary(question="Q?", findings="Found stuff")
        assert fs.findings == "Found stuff"

    def test_missing_findings(self):
        with pytest.raises(ValidationError):
            FindingsSummary(question="Q?")
