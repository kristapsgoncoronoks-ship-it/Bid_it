"""Explicit current-revision requests for ordinary automation API scenarios.

Stale/missing revision behavior is tested separately without this helper.
"""


async def reviewed_request(client, method, path, **kwargs):
    if "/automation/rules/" in path and not path.endswith("/dry-run"):
        rule_id = path.split("/automation/rules/")[1].split("/")[0]
        response = await client.get("/api/v1/automation/rules")
        assert response.status_code == 200
        row = next((r for r in response.json() if r["id"] == rule_id), None)
        if row:
            kwargs["headers"] = {**kwargs.get("headers", {}), "If-Match": row["revision"]}
    return await getattr(client, method)(path, **kwargs)
