"""
Tier 2: Gemini LLM Classifier

Classifies biotech headlines into categories and sentiment using
Google Gemini via the google-genai library.

Includes model fallback chain and cost tracking.
"""

import os
import json
import sys
from typing import Optional
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '..', '.env'))

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from src.db.storage import insert_classification, increment_llm_usage
from src.analysis.cost_guard import can_classify, get_model_chain

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

CLASSIFICATION_PROMPT = """You are a biotech news classifier for a trading bot. Analyze this headline and respond ONLY with valid JSON (no markdown fences, no explanation outside JSON).

Headline: "{headline}"

{extra_context}

Respond with this exact JSON structure:
{{
    "category": "<one of: Clinical Trial Result, FDA Decision, Competitive Threat, Offering/Dilution, M&A, Earnings, Partnership, Other>",
    "sentiment": "<one of: Strong Positive, Weak Positive, Neutral, Weak Negative, Strong Negative>",
    "confidence": <0-100 integer>,
    "primary_ticker": "<main ticker symbol affected, e.g. LLY>",
    "affected_tickers": ["<list of ALL ticker symbols affected, including competitors in the same therapeutic area>"],
    "reasoning": "<one sentence explanation of your classification>"
}}

IMPORTANT: For the affected_tickers field, think about which other companies in the same therapeutic area (e.g., GLP-1/obesity, gene editing, etc.) would be impacted by this news. Include them even if they are not mentioned in the headline."""


def classify_headline(
    headline: str,
    extra_context: str = "",
    db_path: str = None,
) -> Optional[dict]:
    """
    Classify a headline using Gemini.
    Returns parsed classification dict, or None if blocked by cost guard.
    """
    # Check cost guard
    guard = can_classify(headline=headline, db_path=db_path)
    if not guard["allowed"]:
        print(f"  [GEMINI] Skipped: {guard['reason']}")
        return None

    if not GEMINI_API_KEY:
        print("  [GEMINI] ERROR: GEMINI_API_KEY not set")
        return None

    from google import genai

    client = genai.Client(api_key=GEMINI_API_KEY)

    prompt = CLASSIFICATION_PROMPT.format(
        headline=headline,
        extra_context=extra_context,
    )

    # Try models in fallback order
    models = get_model_chain()
    response = None
    used_model = None

    import time as _time

    for model_name in models:
        for attempt in range(2):  # retry once on 503
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config={
                        "max_output_tokens": 300,
                        "temperature": 0.1,
                    }
                )
                used_model = model_name
                break
            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "quota" in err_str.lower():
                    print(f"  [GEMINI] Quota exhausted for {model_name}, trying next...")
                    break  # move to next model
                elif "503" in err_str or "overloaded" in err_str.lower():
                    if attempt == 0:
                        print(f"  [GEMINI] {model_name} overloaded, retrying in 3s...")
                        _time.sleep(3)
                        continue
                    else:
                        print(f"  [GEMINI] {model_name} still overloaded, trying next...")
                        break
                else:
                    print(f"  [GEMINI] Error with {model_name}: {e}")
                    break
        if used_model:
            break

    if response is None:
        print("  [GEMINI] All models exhausted or errored")
        return None

    # Parse response
    raw_text = response.text.strip()

    # Strip markdown fences if present
    json_text = raw_text
    if json_text.startswith("```"):
        lines = json_text.split("\n")
        json_text = "\n".join(lines[1:-1])

    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError:
        print(f"  [GEMINI] JSON parse error. Raw: {raw_text[:200]}")
        return None

    # Estimate token count (rough: 1 token ~= 4 chars)
    input_tokens = len(prompt) // 4
    output_tokens = len(raw_text) // 4

    # Track usage
    increment_llm_usage(
        model=used_model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        db_path=db_path,
    )

    # Add metadata
    parsed["model_used"] = used_model
    parsed["token_count"] = input_tokens + output_tokens

    return parsed


def classify_and_store(
    news_id: int,
    headline: str,
    extra_context: str = "",
    db_path: str = None,
) -> Optional[int]:
    """
    Classify a headline and store the result in the database.
    Returns the classification row ID, or None if skipped/failed.
    """
    result = classify_headline(headline, extra_context, db_path)
    if not result:
        return None

    classification_id = insert_classification(
        news_id=news_id,
        category=result.get("category", "Other"),
        sentiment=result.get("sentiment", "Neutral"),
        confidence=result.get("confidence", 0),
        primary_ticker=result.get("primary_ticker", ""),
        affected_tickers=result.get("affected_tickers", []),
        reasoning=result.get("reasoning", ""),
        model_used=result.get("model_used", ""),
        token_count=result.get("token_count", 0),
        db_path=db_path,
    )

    print(f"  [GEMINI] Classified: {result.get('category')} / {result.get('sentiment')} "
          f"(confidence: {result.get('confidence')}) -> {result.get('affected_tickers')}")

    return classification_id


if __name__ == "__main__":
    # Quick test
    from src.db.schema import init_db
    init_db()

    test = classify_headline(
        "Eli Lilly announces Phase 3 trial of oral Zepbound meets primary endpoint"
    )
    if test:
        print(json.dumps(test, indent=2))
    else:
        print("Classification skipped or failed")
