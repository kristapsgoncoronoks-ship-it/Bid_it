"""FS16-02: reject malformed executable definitions before persistence."""

import copy

import pytest

from app.services import automation
from tests.test_automation import STALE_RULE


@pytest.mark.parametrize(
    "condition",
    [
        {"==": [1]},
        {"!": []},
        {"in": [1, 2]},
        {"and": []},
        {"var": ["days_quiet"]},
        {"var": ""},
        {">": [True, 1]},
        {">": ["seven", 7]},
        {"==": [float("inf"), 1]},
        {"==": ["x" * 2049, "x"]},
        {"in": [1, "12"]},
    ],
)
def test_malformed_conditions_refuse_predictably(condition):
    with pytest.raises(automation.AutomationError):
        automation.validate_condition(condition)


def test_condition_tree_budgets():
    deep = True
    for _ in range(18):
        deep = {"!": deep}
    for condition in (deep, {"and": [True] * 300}):
        with pytest.raises(automation.AutomationError):
            automation.validate_condition(condition)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "patch",
    [
        {"condition": {"==": [1]}},
        {"condition": {">": [{"var": "unknown_field"}, 7]}},
        {"condition": {">": [{"var": "offer_number"}, 7]}},
        {"actions": [{"kind": "notify_owner_email", "subject": 12, "body": "x"}]},
        {"actions": [{"kind": "emit_webhook", "url": "https://example.com"}]},
        {"actions": [{"kind": "notify_owner_email", "subject": "x", "body": "{{unknown_field}}"}]},
        {"actions": [{"kind": "emit_webhook"}] * 11},
    ],
)
async def test_invalid_definition_cannot_be_saved(auth_client, patch):
    rule = copy.deepcopy(STALE_RULE)
    rule.update(patch)
    response = await auth_client.post("/api/v1/automation/rules", json=rule)
    assert response.status_code == 400
    assert (await auth_client.get("/api/v1/automation/rules")).json() == []


def test_ordered_decimal_comparison_does_not_round_through_float():
    assert automation.eval_condition({">": [9007199254740993, 9007199254740992]}, {}) is True
    assert automation.eval_condition({">": [{"var": "missing"}, 0]}, {}) is False
