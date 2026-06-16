"""Multilingual-OCR language resolution: EXTRACT_OCR_LANGS is intersected with the
Tesseract language packs actually installed, so a missing pack degrades gracefully."""
from extract import _pick_langs


def test_pick_intersects_installed_preserving_order():
    assert _pick_langs("eng+deu+pol", {"eng", "deu", "fra"}) == "eng+deu"


def test_pick_drops_missing_and_keeps_requested_order():
    assert _pick_langs("lit+lav+est+eng", {"eng", "lav", "lit"}) == "lit+lav+eng"


def test_pick_falls_back_to_eng_when_none_requested_installed():
    assert _pick_langs("xxx+yyy", {"eng", "deu"}) == "eng"


def test_pick_returns_none_when_nothing_usable():
    assert _pick_langs("xxx", {"deu"}) is None
    assert _pick_langs("", {"eng"}) == "eng"
