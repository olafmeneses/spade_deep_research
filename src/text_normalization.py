"""Text normalization helpers."""

import re


def normalize_report_text(text: str) -> str:
    """Normalize model-generated report text for downstream markdown handling."""
    normalized = (text or "").replace("\u202f", " ").replace("\u00a0", " ")
    normalized = re.sub(r"(?<=\d)\s+(?=[%‰‱])", "", normalized)
    normalized = re.sub(r"(?<=\d)\s+\+(?=\W|$)", "+", normalized)
    normalized = re.sub(r"([±+−])\s+(?=\d)", r"\1", normalized)
    return normalized
