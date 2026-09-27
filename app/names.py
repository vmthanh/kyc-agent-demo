"""Deterministic name comparison for identity evidence.

Encodes a senior Vietnamese KYC reviewer's heuristic as a *grounded fact*,
not as model text: poor ID captures routinely lose Vietnamese diacritics
("Trần" -> "Tran") and confuse glyphs ("i"/"l", "rn"/"m"). A mismatch that is
fully explained by those effects warrants a clearer re-upload, not a full
manual review. Whether that relaxation applies is decided by an ontology rule;
this module only measures the difference.

Note the domain subtlety the rule must respect: diacritics can distinguish
real Vietnamese names ("Bình" vs "Bính"). That is why the remedy is only a
*re-capture request*, re-verified on the next cycle -- never an approval.
"""
from __future__ import annotations

import unicodedata
from typing import Any

# Multi-character glyph confusions first, then single characters.
_CONFUSABLE_SEQUENCES = (("rn", "m"), ("vv", "w"), ("cl", "d"))
_CONFUSABLE_CHARS = str.maketrans({"l": "i", "1": "i", "|": "i", "0": "o", "5": "s", "8": "b"})


def fold_diacritics(text: str) -> str:
    """Strip Vietnamese diacritics and case: 'Trần Đức' -> 'tran duc'."""
    decomposed = unicodedata.normalize("NFD", text.replace("đ", "d").replace("Đ", "D"))
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn").casefold()


def ocr_canonical(token: str) -> str:
    """Collapse common OCR glyph confusions so 'mlnh' and 'minh' compare equal."""
    for seq, replacement in _CONFUSABLE_SEQUENCES:
        token = token.replace(seq, replacement)
    return token.translate(_CONFUSABLE_CHARS)


def name_diff(declared: str, document: str) -> dict[str, Any]:
    """Measure how a declared legal name differs from the name read off the ID.

    - `tokens_differing`: tokens that still differ after diacritic folding.
    - `diacritic_only`: names differ, but only by diacritics.
    - `ocr_explainable`: diacritic-only, or exactly one folded token differs and
      the two spellings are identical under OCR glyph canonicalisation.
    - `declared_token` / `document_token`: the differing pair, for audit display.
    """
    raw_a, raw_b = declared.split(), document.split()
    a, b = [fold_diacritics(t) for t in raw_a], [fold_diacritics(t) for t in raw_b]
    if len(a) != len(b):
        return {
            "tokens_differing": max(len(a), len(b)), "diacritic_only": False, "ocr_explainable": False,
            "declared_token": declared, "document_token": document,
        }
    folded_diffs = [i for i in range(len(a)) if a[i] != b[i]]
    raw_diffs = [i for i in range(len(raw_a)) if raw_a[i].casefold() != raw_b[i].casefold()]
    diacritic_only = bool(raw_diffs) and not folded_diffs
    single_ocr = len(folded_diffs) == 1 and ocr_canonical(a[folded_diffs[0]]) == ocr_canonical(b[folded_diffs[0]])
    shown = folded_diffs[0] if folded_diffs else (raw_diffs[0] if raw_diffs else None)
    return {
        "tokens_differing": len(folded_diffs),
        "diacritic_only": diacritic_only,
        "ocr_explainable": diacritic_only or single_ocr,
        "declared_token": raw_a[shown] if shown is not None else None,
        "document_token": raw_b[shown] if shown is not None else None,
    }
