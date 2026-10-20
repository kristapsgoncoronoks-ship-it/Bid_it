"""CAP-RESOURCE: cap+1 reads precede processing and release upload handles.

Direct route probes stub only pre-read authorization/load collaborators. They do
not prove ingress limits: Starlette can spool multipart before invoking routes.
Existing HTTP workflow tests exercise accepted files through parsing/storage.
"""

import ast
import asyncio
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from app.api.routes import costing, expenses, invoice_review, issuer, reconciliation
from app.api.routes.transport import customers, estimate, statements
from app.services import documents, filesec


class ProcessingReached(Exception):
    pass


class Upload:
    filename = "synthetic.csv"
    content_type = "text/csv"
    size = None  # Unknown length must never authorize a whole-file read.

    def __init__(self, data, fault=None):
        self.file = io.BytesIO(data)
        self.reads = []
        self.consumed = 0
        self.closed = False
        self.fault = fault

    async def read(self, size=-1):
        self.reads.append(size)
        if self.fault:
            raise self.fault
        data = self.file.read(size)
        self.consumed += len(data)
        return data

    async def close(self):
        self.closed = True
        self.file.close()


CASES = [
    (reconciliation, "import_statement", {}, None),
    (issuer, "upload_logo", {}, "logo"),
    (invoice_review, "add_attachment", {"invoice_id": "synthetic"}, None),
    (costing, "attach_project_document", {"entity_id": "synthetic"}, None),
    (expenses, "import_bank_statement", {}, None),
    (expenses, "receipt_scan", {}, "receipt"),
    (expenses, "upload_receipt", {"report_id": "synthetic", "item_id": "item"}, "receipt"),
    (
        statements,
        "upload_statement",
        {"entity_id": "synthetic", "coversheet_total": None, "period": "2026-10"},
        None,
    ),
    (estimate, "estimate_refund", {"period": "2026-Q4"}, None),
    (
        customers,
        "upload_claimant_document",
        {"entity_id": "synthetic", "kind": "contract", "valid_from": None, "valid_until": None},
        None,
    ),
]


@pytest.fixture
def gates(monkeypatch):
    monkeypatch.setattr(
        filesec, "max_bytes", lambda purpose=None: {None: 8, "logo": 2, "receipt": 5}[purpose]
    )
    check = AsyncMock(side_effect=ProcessingReached)
    active = Mock(side_effect=ProcessingReached)
    store = AsyncMock(side_effect=AssertionError("Refused files must not reach storage"))
    monkeypatch.setattr(filesec, "check_async", check)
    monkeypatch.setattr(filesec, "reject_active_content", active)
    monkeypatch.setattr(costing.project_profit, "attach_document", check)
    monkeypatch.setattr(documents, "store", store)
    for module in (reconciliation, expenses):
        monkeypatch.setattr(module, "_guard", AsyncMock())
    monkeypatch.setattr(invoice_review, "_load", AsyncMock())
    monkeypatch.setattr(
        expenses,
        "_load",
        AsyncMock(return_value=SimpleNamespace(items=[SimpleNamespace(id="item")])),
    )
    monkeypatch.setattr(expenses, "_require_owner_editable", Mock())
    monkeypatch.setattr(statements.issuer, "get_by_id", AsyncMock(return_value=object()))
    return check, active, store


@pytest.mark.asyncio
@pytest.mark.parametrize("module,name,kwargs,purpose", CASES, ids=[c[1] for c in CASES])
async def test_routes_refuse_at_cap_plus_one_before_processing(
    gates, module, name, kwargs, purpose
):
    limit = filesec.max_bytes(purpose)
    upload = Upload(b"x" * (limit + 100))
    with pytest.raises(HTTPException) as caught:
        await getattr(module, name)(
            current=SimpleNamespace(org_id="synthetic", email="operator@synthetic.test"),
            db=None,
            file=upload,
            **kwargs,
        )
    assert caught.value.status_code == 413
    assert caught.value.detail == filesec.too_large_message(purpose)
    assert upload.consumed == limit + 1
    assert all(0 < n <= 65536 for n in upload.reads)
    assert upload.closed
    for gate in gates:
        gate.assert_not_called()


def _unbounded_reads(source):
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Await):
            continue
        call = node.value
        if not (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "read"
        ):
            continue
        if (
            not (call.args or call.keywords)
            or any(ast.unparse(arg) == "-1" for arg in call.args)
            or any(ast.unparse(kw.value) == "-1" for kw in call.keywords)
        ):
            found.append(node.lineno)
    return found


def test_unbounded_api_read_guard_detects_seeded_violations():
    assert _unbounded_reads("async def f():\n await upload.read()")
    assert _unbounded_reads("async def f():\n await file.read(-1)")
    assert _unbounded_reads("async def f():\n await file.read(size=-1)")
    assert not _unbounded_reads("async def f():\n await file.read(64)")


def test_api_routes_do_not_reintroduce_whole_upload_reads():
    root = Path(__file__).resolve().parents[1] / "app" / "api"
    offenders = {
        str(p.relative_to(root)): lines
        for p in root.rglob("*.py")
        if (lines := _unbounded_reads(p.read_text()))
    }
    assert not offenders, offenders


@pytest.mark.asyncio
@pytest.mark.parametrize("module,name,kwargs,purpose", CASES, ids=[c[1] for c in CASES])
async def test_routes_deliver_exact_cap_unchanged_and_release_handle(
    gates, module, name, kwargs, purpose
):
    data = b"x" * filesec.max_bytes(purpose)
    upload = Upload(data)
    with pytest.raises(ProcessingReached):
        await getattr(module, name)(
            current=SimpleNamespace(org_id="synthetic", email="operator@synthetic.test"),
            db=None,
            file=upload,
            **kwargs,
        )
    assert upload.closed
    assert all(0 < n <= 65536 for n in upload.reads)
    # The original bytes, name and type remain available to downstream gates.
    call = gates[1].call_args if module is invoice_review else gates[0].call_args
    if module is costing:
        assert call.kwargs["data"] == data
    else:
        assert data in call.args


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fault",
    [OSError("synthetic read failure"), asyncio.CancelledError()],
    ids=["io-error", "cancelled"],
)
@pytest.mark.parametrize("module,name,kwargs,purpose", CASES, ids=[c[1] for c in CASES])
async def test_routes_close_failed_or_cancelled_reads(gates, module, name, kwargs, purpose, fault):
    upload = Upload(b"synthetic", fault=fault)
    with pytest.raises(type(fault)):
        await getattr(module, name)(
            current=SimpleNamespace(org_id="synthetic", email="operator@synthetic.test"),
            db=None,
            file=upload,
            **kwargs,
        )
    assert upload.closed
    for gate in gates:
        gate.assert_not_called()
