"""R1 — the upload gate must exclude TERMINAL failed/held jobs.

One un-actioned failed/held document must NOT freeze fleet-wide intake; only
GENUINELY in-flight work (queued/waiting/processing) gates new uploads.
"""
import pytest
import waiting_room as IQ


@pytest.fixture
def iq(tmp_path, monkeypatch):
    monkeypatch.setattr(IQ, "DB", str(tmp_path / "intake.db"))
    monkeypatch.setattr(IQ, "INBOX", str(tmp_path / "inbox"))
    IQ._SCHEMA_READY.clear()
    return IQ


def _seed(iq, status):
    """Insert one job directly in the given state (no extractor needed)."""
    con = iq.connect()
    con.execute("INSERT INTO intake_jobs (filename, status) VALUES (?,?)",
                ("x.pdf", status))
    con.commit()
    con.close()


def test_terminal_states_do_not_gate_uploads(iq):
    """A failed AND a held job present, with NO in-flight work -> gate is OPEN."""
    _seed(iq, "failed")
    _seed(iq, "held")
    # the upload gate uses BLOCKING_STATES, which excludes failed/held
    assert iq.pending_count(iq.BLOCKING_STATES) == 0
    # PENDING_STATES still counts them as "not done" backlog (unchanged meaning)
    assert iq.pending_count() == 2


@pytest.mark.parametrize("state", ["queued", "processing", "waiting"])
def test_in_flight_states_gate_uploads(iq, state):
    """A genuinely in-flight job still blocks new uploads, as before."""
    _seed(iq, state)
    assert iq.pending_count(iq.BLOCKING_STATES) == 1


def test_terminal_plus_in_flight(iq):
    """Mixed: only the in-flight job counts toward the gate."""
    _seed(iq, "failed")
    _seed(iq, "held")
    _seed(iq, "queued")
    assert iq.pending_count(iq.BLOCKING_STATES) == 1
    assert iq.pending_count() == 3
