"""Full read-text on the draft + the co-located capture folder (original + captured text)."""
import os
import shutil

import capture_folder
import extract


SHA = "b" * 64


def _clean():
    shutil.rmtree(os.path.join(capture_folder.ROOT, SHA), ignore_errors=True)


def test_join_source_text_keeps_every_line():
    txt = extract._join_source_text([("a.pdf", "L1\nL2\nL3"), ("b.pdf", "X")])
    assert "L1" in txt and "L2" in txt and "L3" in txt and "X" in txt
    assert "a.pdf" in txt and "b.pdf" in txt          # per-file headers
    # never truncates
    big = "row\n" * 5000
    assert extract._join_source_text([("big.pdf", big)]).count("row") == 5000


def test_plain_draft_attaches_source_text():
    texts = [("inv.pdf", "ACME FUEL\nDiesel 100 L\nEUR 150.00\nVAT 31.50")]
    d = extract._plain_draft(texts, [], "parser", "inv.pdf", strict=False)
    assert d.get("_source_text"), "draft must carry the full read text"
    assert "Diesel 100 L" in d["_source_text"]


def test_folder_pairs_original_and_text():
    _clean()
    d = capture_folder.save(SHA, [("inv.pdf", b"%PDF fake-bytes")],
                            "===== inv.pdf =====\nEUR 150.00", source_name="inv.pdf")
    assert d and os.path.isdir(d)
    names = [f["name"] for f in capture_folder.listing(SHA)]
    assert "inv.pdf" in names                 # the ORIGINAL document
    assert "captured.txt" in names            # the captured text — together in one folder
    assert capture_folder.file_bytes(SHA, "inv.pdf") == b"%PDF fake-bytes"
    assert "EUR 150.00" in capture_folder.read_text_of(SHA)
    _clean()


def test_folder_blocks_path_traversal():
    _clean()
    capture_folder.save(SHA, [("inv.pdf", b"x")], "txt", source_name="inv.pdf")
    assert capture_folder.file_bytes(SHA, "../../etc/passwd") is None
    assert capture_folder.file_bytes(SHA, "..%2f..%2fsecret") is None
    _clean()


def test_short_sha_rejected():
    assert capture_folder.save("short", [("a.pdf", b"x")], "t") is None
    assert capture_folder.folder("short") is None


def test_routes_serve_folder(client):
    _clean()
    capture_folder.save(SHA, [("inv.pdf", b"%PDF hi")], "read text here", source_name="inv.pdf")
    # listing page
    r = client.get(f"/extract/folder/{SHA}")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "inv.pdf" in body and "captured.txt" in body
    # download the captured text
    r = client.get(f"/extract/folder/{SHA}/captured.txt")
    assert r.status_code == 200
    assert b"read text here" in r.data
    # download the original
    r = client.get(f"/extract/folder/{SHA}/inv.pdf")
    assert r.status_code == 200
    assert r.data == b"%PDF hi"
    _clean()
