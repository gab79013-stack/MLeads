"""
utils/humanize_text.py
─────────────────────
Post-processes AI-generated text to remove telltale AI writing patterns.

Ported from the humanizer skill (blader/humanizer) — 29 patterns across
content, language, style, and communication categories.

Used automatically in BaseMarketingAgent._generate_content() for all
non-JSON content (blog posts, emails, social copy, PR text).
"""

import re

# (pattern, replacement) pairs — applied in order
_PHRASE_REPLACEMENTS = [
    # ── Transition filler words ────────────────────────────────────────
    (r'\bMoreover,?\s*', ''),
    (r'\bFurthermore,?\s*', ''),
    (r'\bAdditionally,?\s*', 'Also, '),
    (r'\bIn conclusion,?\s*', ''),
    (r'\bIn summary,?\s*', ''),
    (r'\bTo summarize,?\s*', ''),
    (r'\bIt\'s worth noting that\s*', ''),
    (r'\bIt is worth noting that\s*', ''),
    (r'\bNotably,?\s*', ''),
    (r'\bSignificantly,?\s*', ''),
    (r'\bInterestingly,?\s*', ''),
    # ── Sycophancy / chatbot remnants ─────────────────────────────────
    (r'\bGreat question[.!]*\s*', ''),
    (r'\bExcellent question[.!]*\s*', ''),
    (r'\bAbsolutely[.!]*\s*', ''),
    (r'\bCertainly[.!]*\s*', ''),
    (r'\bOf course[.!]*\s*', ''),
    (r'I hope this (?:email )?(?:finds you well|helps)[.!]*\s*', ''),
    (r'As an AI(?:\s+language model)?,?\s*', ''),
    # ── Inflated significance ─────────────────────────────────────────
    (r'\bpivotal moment\b', 'moment'),
    (r'\bIt\'s a testament to\b', 'This shows'),
    (r'\bIt is a testament to\b', 'This shows'),
    (r'\bserves as\b', 'is'),
    (r'\bunderscore\b', 'show'),
    (r'\bnavigate\b', 'handle'),
    (r'\bfoster\b', 'build'),
    (r'\bleverage\b', 'use'),
    (r'\bsynergy\b', 'teamwork'),
    (r'\bdelve\b', 'look'),
    (r'\bunlock\b', 'reach'),
    # ── Overused marketing adjectives ────────────────────────────────
    (r'\bgroundbreaking\b', 'new'),
    (r'\bcutting-edge\b', 'modern'),
    (r'\bstate-of-the-art\b', 'modern'),
    (r'\bseamlessly\b', ''),
    (r'\brobust\b', 'solid'),
    (r'\bcomprehensive\b', 'complete'),
    (r'\btailored\b', 'custom'),
]

_EM_DASH = re.compile(r'\s*—\s*')
_MULTI_EXCLAIM = re.compile(r'!{2,}')
_DOUBLE_SPACE = re.compile(r'  +')
_MULTI_NEWLINE = re.compile(r'\n{3,}')
# Leading comma/space artifacts after phrase removal
_LEADING_PUNCT = re.compile(r'^[,\s]+', re.MULTILINE)


def humanize(text: str) -> str:
    """Remove AI writing tells from text. Safe to call on any string."""
    if not text:
        return text

    # Replace em dashes with commas (most common natural replacement)
    text = _EM_DASH.sub(', ', text)

    # Apply phrase replacements
    for pattern, replacement in _PHRASE_REPLACEMENTS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    # Reduce multiple exclamation marks to one
    text = _MULTI_EXCLAIM.sub('!', text)

    # Clean up artifacts from phrase removal
    text = _DOUBLE_SPACE.sub(' ', text)
    text = _MULTI_NEWLINE.sub('\n\n', text)

    return text.strip()
