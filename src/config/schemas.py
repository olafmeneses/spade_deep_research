"""Pydantic schemas for structured output in deep research agents."""

from typing import List, Literal
from pydantic import BaseModel, Field

from src.extensions import extension_registry


class ResearchQuestion(BaseModel):
    """A single research question within a research plan."""

    question: str = Field(..., description="Core question to research and answer (in English)")
    source: str = Field(..., description=f"Source to search. One of: {', '.join(extension_registry.source_names)}")
    description: str = Field(..., description="Description of what information is needed (in English)")


class ResearchPlan(BaseModel):
    """Structured research plan output."""
    
    detected_language: str = Field(
        ..., 
        description="The detected language of the original query (e.g., 'Spanish', 'Brazilian Portuguese', 'French', 'Traditional Chinese')"
    )
    research_goal: str = Field(..., description="The overall goal of the research (in English)")
    research_questions: List[ResearchQuestion] = Field(
        ..., 
        description="List of research questions to investigate (all in English)"
    )

class FindingsSummary(BaseModel):
    """Summary of findings from a research agent."""
    
    question: str = Field(..., description="The research question addressed")
    findings: str = Field(..., description="Findings that answer the question")
    # Note: sources are captured programmatically from tool calls via ReferenceRegistry

class CriticReview(BaseModel):
    """Structured critique output."""
    
    status: Literal["SUFFICIENT", "INSUFFICIENT"] = Field(
        ..., 
        description=(
            "Whether the report is sufficient to answer the query. Use INSUFFICIENT "
            "when major task-alignment, evidence, coverage, or analysis gaps remain."
        )
    )
    feedback: str = Field(
        ...,
        description=(
            "Concise overall assessment of report quality, tied to the query, review "
            "checklist, and writer requirements. Do not hide actionable items here "
            "when they belong in issues, missing_information, or writing_improvements."
        ),
    )
    issues: List[str] = Field(
        default_factory=list,
        description=(
            "Specific diagnosed problems in the report. Include task-alignment, "
            "coverage, analytical-depth, structure, evidence, citation, limitation, "
            "comparison, recommendation, or final-product issues identified from the "
            "critic checklist and writer review requirements. Each item should name "
            "the problem clearly enough to route into a rewrite brief."
        ),
    )
    missing_information: List[str] = Field(
        default_factory=list,
        description=(
            "Topics, facts, sources, entities, figures, dates, or comparisons that need "
            "additional research data before the report can be improved. Use only for "
            "gaps that cannot be fixed from the current report, research context, and "
            "reference map. May be populated even when status is SUFFICIENT if "
            "actionable research gaps remain."
        ),
    )
    writing_improvements: List[str] = Field(
        default_factory=list,
        description=(
            "Actionable rewrite directives fixable with existing evidence. Include both "
            "high-level report improvements and fine-grained fixes when useful: coverage, "
            "section depth, synthesis, structure, narrative flow, mechanisms, evidence "
            "discipline, citation placement, comparisons, limitations, recommendations, "
            "decision usefulness, and removal of final-product violations. Because "
            "rewriting is expensive, prioritize changes that materially improve the final "
            "report, not only small polish details. Long lists are acceptable when each "
            "item is specific, actionable, and quality-improving. These trigger writer "
            "routing, so make them specific enough for the writer to execute directly."
        ),
    )
