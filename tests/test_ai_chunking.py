"""A3 — the AI prompt must never silently drop invoice lines on long statements.

The old code cut each document at a fixed 6000 chars, so every line past ~6 KB on a long
multi-country statement vanished before the model ever saw it. `_doc_blocks` now CHUNKS
instead of truncating; a document beyond the chunk budget is capped with a VISIBLE marker.
These tests are pure (no network)."""
import extract as EX


def _bodies(blocks):
    """The text body of each block (everything after the first '[label]\\n')."""
    return [b.split("\n", 1)[1] for b in blocks]


def test_short_document_single_block_full_text(monkeypatch):
    monkeypatch.setattr(EX, "AI_DOC_CHAR_BUDGET", 100)
    monkeypatch.setattr(EX, "AI_DOC_MAX_CHUNKS", 8)
    text = "line1\nline2\nshort"
    blocks = EX._doc_blocks([("inv.pdf", text)])
    assert len(blocks) == 1
    assert _bodies(blocks)[0] == text
    assert "part" not in blocks[0]                       # no chunk labels for short docs


def test_long_document_chunked_without_loss(monkeypatch):
    monkeypatch.setattr(EX, "AI_DOC_CHAR_BUDGET", 1000)
    monkeypatch.setattr(EX, "AI_DOC_MAX_CHUNKS", 8)
    # 30 distinct lines, ~50 chars each => well over one 1000-char chunk, under the cap
    text = "\n".join(f"DE-{i:04d}  Germany  net {i*10}.00  vat {i*1}.90" for i in range(30))
    blocks = EX._doc_blocks([("big.pdf", text)])
    assert len(blocks) > 1                                # actually chunked
    # EVERY character survives: the concatenated chunk bodies reproduce the original
    assert "".join(_bodies(blocks)) == text
    # the last line (would have been dropped by the old [:6000]/[:1000] cut) is present
    assert "DE-0029" in "".join(_bodies(blocks))


def test_oversized_document_capped_with_visible_marker(monkeypatch):
    monkeypatch.setattr(EX, "AI_DOC_CHAR_BUDGET", 100)
    monkeypatch.setattr(EX, "AI_DOC_MAX_CHUNKS", 3)
    text = "X" * 1000                                     # 10 chunks of 100, cap at 3
    blocks = EX._doc_blocks([("huge.pdf", text)])
    assert len(blocks) == 3                               # hard-capped
    joined = "".join(_bodies(blocks))
    assert "TRUNCATED" in joined                          # truncation is NOT silent
    # 3 chunks * 100 chars of real content retained before the marker
    assert joined.count("X") == 300


def test_ai_content_contains_prompt_and_all_docs(monkeypatch):
    monkeypatch.setattr(EX, "AI_DOC_CHAR_BUDGET", 60000)
    content = EX._ai_content([("a.pdf", "alpha"), ("b.pdf", "beta")])
    assert content.startswith(EX.PROMPT)
    assert "alpha" in content and "beta" in content
    assert "[a.pdf]" in content and "[b.pdf]" in content
