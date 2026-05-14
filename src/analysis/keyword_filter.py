"""
Tier 1: Keyword Filter (Cost: $0)

Fast regex/set-based filter that catches biotech-relevant headlines
before they reach the LLM. Discards 70-80% of irrelevant noise.

Returns a match score and matched keywords so Tier 2 can decide
whether to spend an LLM call.
"""

import re
from dataclasses import dataclass, field


@dataclass
class FilterResult:
    """Result of keyword filtering."""
    is_relevant: bool
    score: int  # 0-100, higher = more relevant
    direction: str  # "positive", "negative", "competitive", "neutral"
    matched_keywords: list = field(default_factory=list)
    headline: str = ""


# Keyword sets -- all lowercased for matching
POSITIVE_KEYWORDS = {
    "phase 3", "phase iii", "fda approval", "pdufa", "primary endpoint met",
    "breakthrough therapy", "fast track", "orphan drug", "accelerated approval",
    "priority review", "met primary", "statistically significant",
    "positive results", "positive data", "approved", "clearance",
    "complete response",  # contextual -- can be positive (met) or negative (letter)
}

NEGATIVE_KEYWORDS = {
    "crl", "complete response letter", "clinical hold", "partial hold",
    "safety signal", "adverse events", "adverse event", "dilutive offering",
    "secondary offering", "shelf registration", "failed endpoint",
    "discontinued", "did not meet", "failed to meet", "negative results",
    "terminated", "voluntary pause", "death", "deaths", "serious adverse",
    "liver toxicity", "hepatotoxicity", "black box warning",
}

COMPETITIVE_KEYWORDS = {
    "price war", "compounded", "generic", "biosimilar", "patent expiry",
    "patent expiration", "loe", "loss of exclusivity", "undercut",
    "cheaper alternative", "price cut", "price reduction", "competition",
    "competitive threat", "market share",
}

# Compile a single regex for each category for fast matching
def _build_pattern(keywords: set) -> re.Pattern:
    # Sort by length (longest first) to match longer phrases before substrings
    sorted_kw = sorted(keywords, key=len, reverse=True)
    escaped = [re.escape(kw) for kw in sorted_kw]
    return re.compile(r'\b(' + '|'.join(escaped) + r')\b', re.IGNORECASE)


POSITIVE_PATTERN = _build_pattern(POSITIVE_KEYWORDS)
NEGATIVE_PATTERN = _build_pattern(NEGATIVE_KEYWORDS)
COMPETITIVE_PATTERN = _build_pattern(COMPETITIVE_KEYWORDS)


def filter_headline(headline: str) -> FilterResult:
    """
    Run the keyword filter on a headline.
    Returns a FilterResult with relevance score and direction.
    """
    text = headline.lower()
    matched = []

    pos_matches = POSITIVE_PATTERN.findall(text)
    neg_matches = NEGATIVE_PATTERN.findall(text)
    comp_matches = COMPETITIVE_PATTERN.findall(text)

    matched.extend([(kw, "positive") for kw in pos_matches])
    matched.extend([(kw, "negative") for kw in neg_matches])
    matched.extend([(kw, "competitive") for kw in comp_matches])

    if not matched:
        return FilterResult(
            is_relevant=False,
            score=0,
            direction="neutral",
            matched_keywords=[],
            headline=headline,
        )

    # Score: each keyword match adds points
    # Negative keywords score higher (research shows stronger signal)
    score = 0
    pos_count = len(pos_matches)
    neg_count = len(neg_matches)
    comp_count = len(comp_matches)

    score += pos_count * 20
    score += neg_count * 30  # negative bias per research
    score += comp_count * 25
    score = min(score, 100)

    # Determine primary direction
    if neg_count > pos_count and neg_count >= comp_count:
        direction = "negative"
    elif comp_count > pos_count and comp_count > neg_count:
        direction = "competitive"
    elif pos_count > 0:
        direction = "positive"
    else:
        direction = "neutral"

    return FilterResult(
        is_relevant=True,
        score=score,
        direction=direction,
        matched_keywords=matched,
        headline=headline,
    )


# Minimum score to proceed to LLM classification
RELEVANCE_THRESHOLD = 15


def should_classify(result: FilterResult) -> bool:
    """Decide if a headline is worth sending to the LLM."""
    return result.is_relevant and result.score >= RELEVANCE_THRESHOLD


if __name__ == "__main__":
    # Quick test with sample headlines
    test_headlines = [
        "Eli Lilly announces Phase 3 trial meets primary endpoint for oral Zepbound",
        "CRISPR Therapeutics receives Complete Response Letter from FDA for sickle cell therapy",
        "Hims & Hers launches $49 compounded Wegovy alternative, undercutting Novo Nordisk",
        "Vertex Pharmaceuticals reports Q4 earnings, beats revenue estimates",
        "Novo Nordisk announces secondary offering of 10 million shares",
        "Apple releases new iPhone model with improved camera",  # irrelevant
    ]

    for h in test_headlines:
        result = filter_headline(h)
        classify = should_classify(result)
        print(f"\n{'='*60}")
        print(f"Headline: {h}")
        print(f"Relevant: {result.is_relevant} | Score: {result.score} | Direction: {result.direction}")
        print(f"Keywords: {result.matched_keywords}")
        print(f"Send to LLM: {classify}")
