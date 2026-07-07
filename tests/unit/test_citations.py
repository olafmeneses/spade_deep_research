"""Tests for validate_report_citations and resolve_citations in src.agents.specialized."""

from src.agents.specialized import validate_report_citations, resolve_citations
from src.session import Reference, ReferenceSource


def _refs(n: int) -> list[Reference]:
    """Create *n* dummy references."""
    return [
        Reference(identifier=f"http://ref{i}.com", source_type=ReferenceSource.TAVILY)
        for i in range(1, n + 1)
    ]


def _titled_refs() -> list[Reference]:
    """Create references with titles for resolve_citations tests."""
    return [
        Reference(identifier="http://example.com/a", source_type=ReferenceSource.TAVILY, title="Example A"),
        Reference(identifier="https://arxiv.org/abs/2401.00001", source_type=ReferenceSource.ARXIV, title="ArXiv Paper"),
        Reference(identifier="http://example.com/c", source_type=ReferenceSource.TAVILY),
    ]


class TestValidateReportCitations:
    def test_clean_report(self):
        report = "Transformers are great [1]. See also [2]."
        issues, invalid = validate_report_citations(report, _refs(3))
        assert issues == []
        assert invalid == []

    def test_agent_mention_detected(self):
        report = "According to arxiv@localhost, transformers are useful."
        issues, invalid = validate_report_citations(report, _refs(1))
        assert len(issues) >= 1
        assert "arxiv@localhost" in issues[0]

    def test_multiple_agent_mentions(self):
        report = "tavily@localhost found this. coordinator@myhost confirmed."
        issues, _ = validate_report_citations(report, _refs(1))
        assert len(issues) >= 2

    def test_citation_out_of_range(self):
        report = "See [5] for details."
        _, invalid = validate_report_citations(report, _refs(2))
        assert len(invalid) == 1
        assert "[5]" in invalid[0]

    def test_citation_zero_invalid(self):
        report = "See [0] for details."
        _, invalid = validate_report_citations(report, _refs(2))
        assert len(invalid) == 1

    def test_no_citations(self):
        report = "A report without any citations."
        issues, invalid = validate_report_citations(report, _refs(3))
        assert issues == []
        assert invalid == []

    def test_empty_report(self):
        issues, invalid = validate_report_citations("", _refs(0))
        assert issues == []
        assert invalid == []

    def test_mixed_valid_and_invalid(self):
        report = "Valid [1] and invalid [99]."
        _, invalid = validate_report_citations(report, _refs(2))
        assert len(invalid) == 1
        assert "[99]" in invalid[0]


class TestResolveCitations:
    """Tests for resolve_citations — [n] → markdown link substitution."""

    def test_basic_replacement_with_title(self):
        refs = _titled_refs()
        report = "See [1] for details."
        result = resolve_citations(report, refs)
        assert result == "See [[1: Example A]](http://example.com/a) for details."

    def test_replacement_without_title(self):
        refs = _titled_refs()
        report = "See [3] for details."
        result = resolve_citations(report, refs)
        assert result == "See [[3]](http://example.com/c) for details."

    def test_multiple_citations(self):
        refs = _titled_refs()
        report = "First [1] and second [2]."
        result = resolve_citations(report, refs)
        assert "[[1: Example A]](http://example.com/a)" in result
        assert "[[2: ArXiv Paper]](https://arxiv.org/abs/2401.00001)" in result

    def test_out_of_range_left_untouched(self):
        refs = _titled_refs()
        report = "Valid [1] but [99] is invalid."
        result = resolve_citations(report, refs)
        assert "[[1: Example A]](http://example.com/a)" in result
        assert "[99]" in result  # untouched

    def test_zero_citation_left_untouched(self):
        report = "See [0] here."
        result = resolve_citations(report, _refs(2))
        assert "[0]" in result

    def test_empty_report(self):
        assert resolve_citations("", _refs(2)) == ""

    def test_no_references(self):
        assert resolve_citations("Some text [1].", []) == "Some text [1]."

    def test_none_report(self):
        assert resolve_citations("", []) == ""

    def test_no_citations_in_text(self):
        refs = _titled_refs()
        report = "No citations here."
        assert resolve_citations(report, refs) == "No citations here."

    def test_repeated_citation(self):
        refs = _titled_refs()
        report = "First mention [1], second mention [1]."
        result = resolve_citations(report, refs)
        assert result.count("[[1: Example A]](http://example.com/a)") == 2

    def test_adjacent_citations(self):
        refs = _titled_refs()
        report = "Multiple sources [1][2]."
        result = resolve_citations(report, refs)
        assert "[[1: Example A]](http://example.com/a)" in result
        assert "[[2: ArXiv Paper]](https://arxiv.org/abs/2401.00001)" in result
