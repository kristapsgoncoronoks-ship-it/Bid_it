"""Real adapter serialization/error contracts against a bounded fake HTTP peer."""

import json
import uuid
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.services import calendar_providers as provider

STABLE = "f918424b-576d-5eab-b623-980ef4733477"
MARKER = "d66af0fc-85a5-4aa2-9375-c63e3f922750"
PAYLOAD = {
    "summary": "SYN-001 · Synthetic work",
    "description": "Synthetic note",
    "start": {"dateTime": "2026-10-25T08:00:00+02:00", "timeZone": "Europe/Riga"},
    "end": {"dateTime": "2026-10-25T09:00:00+02:00", "timeZone": "Europe/Riga"},
    "status": "tentative",
}


@pytest.fixture(autouse=True)
def credentials(monkeypatch):
    monkeypatch.setattr(
        provider,
        "settings",
        SimpleNamespace(
            google_calendar_client_id="synthetic-google-client",
            google_calendar_client_secret="google-secret",
            outlook_calendar_client_id="synthetic-outlook-client",
            outlook_calendar_client_secret="outlook-secret",
        ),
    )


class ResponseStream(httpx.AsyncByteStream):
    def __init__(self, body: bytes):
        self.body = body

    async def __aiter__(self):
        for index in range(0, len(self.body), 32768):
            yield self.body[index : index + 32768]


def peer(monkeypatch, fn):
    seen = []

    def handle(request):
        seen.append(request)
        response = fn(request)
        return httpx.Response(
            response.status_code, headers=response.headers, stream=ResponseStream(response.content)
        )

    monkeypatch.setattr(
        provider,
        "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handle), follow_redirects=False),
    )
    return seen


@pytest.mark.parametrize(
    "kind,host,scope",
    [
        ("google", "accounts.google.com", provider.GOOGLE_SCOPE),
        ("outlook", "login.microsoftonline.com", provider.OUTLOOK_SCOPE),
    ],
)
def test_authorization_uses_fixed_provider_pkce_and_calendar_scopes(kind, host, scope):
    url = provider.authorization_url(
        kind, "one-use-state", "proof-challenge", "https://app.example/api/calendar/callback"
    )
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    assert parsed.hostname == host
    assert query["state"] == ["one-use-state"]
    assert query["code_challenge"] == ["proof-challenge"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["scope"] == [scope]
    assert "secret" not in url
    if kind == "google":
        assert query["access_type"] == ["offline"]


def test_missing_configuration_does_not_offer_authorization(monkeypatch):
    monkeypatch.setattr(provider, "settings", SimpleNamespace())
    with pytest.raises(provider.ProviderError, match="provider_not_configured"):
        provider.authorization_url("google", "state", "proof", "https://app.example/callback")


@pytest.mark.parametrize(
    "url",
    [
        "https://user:secret@app.example/callback",
        "http://example.com/callback",
        "https://app.example/callback#fragment",
    ],
)
def test_unsafe_callback_urls_are_refused(url):
    with pytest.raises(provider.ProviderError, match="invalid_callback_url"):
        provider.authorization_url("google", "state", "proof", url)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,token_host",
    [("google", "oauth2.googleapis.com"), ("outlook", "login.microsoftonline.com")],
)
async def test_exchange_posts_secret_and_verifier_only_to_fixed_token_endpoint(
    monkeypatch, kind, token_host
):
    seen = peer(
        monkeypatch,
        lambda req: httpx.Response(
            200,
            json={
                "access_token": "access-secret",
                "refresh_token": "refresh-secret",
                "expires_in": 3600,
                "token_type": "Bearer",
                "id_token": "unneeded-identity-token",
            },
        ),
    )
    result = await provider.exchange_code(
        kind, "auth-code", "pkce-verifier", "https://app.example/callback"
    )
    req = seen[0]
    form = parse_qs(req.content.decode())
    assert req.url.host == token_host
    assert form["code"] == ["auth-code"] and form["code_verifier"] == ["pkce-verifier"]
    assert form["client_secret"] == [kind + "-secret"]
    assert result == {
        "access_token": "access-secret",
        "refresh_token": "refresh-secret",
        "expires_in": 3600,
    }


@pytest.mark.asyncio
async def test_refresh_preserves_refresh_token_when_google_does_not_rotate(monkeypatch):
    peer(
        monkeypatch,
        lambda req: httpx.Response(200, json={"access_token": "new-access", "expires_in": 3600}),
    )
    result = await provider.refresh_tokens("google", "existing-refresh")
    assert result["refresh_token"] == "existing-refresh"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        {"access_token": "", "expires_in": 3600},
        {"access_token": "a", "expires_in": True},
        {"access_token": "a", "expires_in": 99999999},
        {"access_token": "a", "expires_in": 3600, "token_type": []},
        {"access_token": "a", "expires_in": 3600, "refresh_token": "x" * 16385},
    ],
)
async def test_token_response_bounds_do_not_accept_arbitrary_credentials(monkeypatch, response):
    peer(monkeypatch, lambda req: httpx.Response(200, json=response))
    with pytest.raises(provider.ProviderError, match="invalid_token_response"):
        await provider.exchange_code("google", "code", "verifier", "https://app.example/callback")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["google", "outlook"])
async def test_calendar_creation_uses_friendly_name_and_application_marker(monkeypatch, kind):
    seen = peer(monkeypatch, lambda req: httpx.Response(200, json={"id": "provider-calendar"}))
    assert (
        await provider.create_calendar(
            kind, "secret-access", name="Synthetic workspace work schedule", marker=MARKER
        )
        == "provider-calendar"
    )
    body = json.loads(seen[0].content)
    assert body["summary" if kind == "google" else "name"] == "Synthetic workspace work schedule"
    if kind == "google":
        assert body["description"] == "Bid_it managed calendar: " + MARKER
    else:
        assert body["singleValueExtendedProperties"] == [
            {"id": provider.CALENDAR_PROPERTY, "value": MARKER}
        ]
    assert "attendees" not in body


@pytest.mark.asyncio
async def test_calendar_recovery_matches_marker_not_unrelated_same_title(monkeypatch):
    peer(
        monkeypatch,
        lambda req: httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "other",
                        "summary": "Same name",
                        "description": "Someone else's calendar",
                    },
                    {
                        "id": "ours",
                        "summary": "Renamed by owner",
                        "description": "Bid_it managed calendar: " + MARKER,
                    },
                ]
            },
        ),
    )
    assert await provider.find_calendar("google", "access", "Same name", marker=MARKER) == "ours"


@pytest.mark.asyncio
async def test_graph_pagination_never_follows_untrusted_origin(monkeypatch):
    seen = peer(
        monkeypatch,
        lambda req: httpx.Response(
            200, json={"value": [], "@odata.nextLink": "https://attacker.invalid/collect"}
        ),
    )
    with pytest.raises(provider.ProviderError, match="invalid_provider_pagination"):
        await provider.find_calendar("outlook", "secret-access", "Name", marker=MARKER)
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_calendar_inventory_has_finite_page_budget(monkeypatch):
    seen = peer(
        monkeypatch, lambda req: httpx.Response(200, json={"items": [], "nextPageToken": "more"})
    )
    with pytest.raises(provider.ProviderError, match="calendar_inventory_too_large"):
        await provider.find_calendar("google", "access", "Name", marker=MARKER)
    assert len(seen) == provider.MAX_CALENDAR_PAGES


@pytest.mark.asyncio
async def test_google_stable_create_is_private_without_provider_reminders(monkeypatch):
    def serve(req):
        return (
            httpx.Response(404)
            if req.method == "GET"
            else httpx.Response(200, json={"id": uuid.UUID(STABLE).hex})
        )

    seen = peer(monkeypatch, serve)
    result = await provider.put_event(
        "google", "secret-access", "calendar@example.com", None, STABLE, PAYLOAD
    )
    assert result == uuid.UUID(STABLE).hex
    posts = [req for req in seen if req.method == "POST"]
    assert len(posts) == 1
    body = json.loads(posts[0].content)
    assert body["id"] == uuid.UUID(STABLE).hex
    assert body["visibility"] == "private" and body["reminders"] == {
        "useDefault": False,
        "overrides": [],
    }
    assert "attendees" not in body
    assert parse_qs(posts[0].url.query.decode())["sendUpdates"] == ["none"]
    assert all(req.headers["authorization"] == "Bearer secret-access" for req in seen)


@pytest.mark.asyncio
async def test_graph_stable_create_uses_transaction_identity_and_extension(monkeypatch):
    def serve(req):
        return (
            httpx.Response(200, json={"value": []})
            if req.method == "GET"
            else httpx.Response(201, json={"id": "graph-event"})
        )

    seen = peer(monkeypatch, serve)
    assert (
        await provider.put_event("outlook", "access", "calendar", None, STABLE, PAYLOAD)
        == "graph-event"
    )
    body = json.loads(seen[-1].content)
    assert body["transactionId"] == STABLE
    assert body["singleValueExtendedProperties"] == [
        {"id": provider.EVENT_PROPERTY, "value": STABLE}
    ]
    assert body["start"] == {"dateTime": "2026-10-25T06:00:00", "timeZone": "UTC"}
    assert body["isReminderOn"] is False and body["sensitivity"] == "private"
    assert "attendees" not in body


@pytest.mark.asyncio
async def test_graph_all_day_dates_remain_local_midnights(monkeypatch):
    calls = []

    def serve(req):
        calls.append(req)
        return (
            httpx.Response(200, json={"value": []})
            if req.method == "GET"
            else httpx.Response(201, json={"id": "all-day"})
        )

    peer(monkeypatch, serve)
    payload = {
        **PAYLOAD,
        "start": {"date": "2026-10-25", "timeZone": "Europe/Riga"},
        "end": {"date": "2026-10-26", "timeZone": "Europe/Riga"},
    }
    await provider.put_event("outlook", "access", "calendar", None, STABLE, payload)
    body = json.loads(calls[-1].content)
    assert body["isAllDay"] is True
    assert body["start"] == {"dateTime": "2026-10-25T00:00:00", "timeZone": "Europe/Riga"}
    assert body["end"] == {"dateTime": "2026-10-26T00:00:00", "timeZone": "Europe/Riga"}


@pytest.mark.asyncio
async def test_unknown_graph_create_recovery_cannot_blindly_post(monkeypatch):
    seen = peer(monkeypatch, lambda req: httpx.Response(200, json={"value": []}))
    with pytest.raises(provider.ProviderError) as caught:
        await provider.put_event(
            "outlook", "access", "calendar", None, STABLE, PAYLOAD, allow_create=False
        )
    assert caught.value.ambiguous and caught.value.code == "event_creation_unconfirmed"
    assert [req.method for req in seen] == ["GET"]


@pytest.mark.asyncio
async def test_google_conflict_recovers_existing_event_without_another_create(monkeypatch):
    lookups = 0

    def serve(req):
        nonlocal lookups
        if req.method == "POST":
            return httpx.Response(409, json={"error": "SECRET provider text"})
        if req.method == "PATCH":
            return httpx.Response(200, json={"id": uuid.UUID(STABLE).hex})
        lookups += 1
        if lookups == 1:
            return httpx.Response(404)
        return httpx.Response(
            200,
            json={
                "id": uuid.UUID(STABLE).hex,
                "etag": '"read-revision"',
                "extendedProperties": {"private": {"bidit_assignment": STABLE}},
            },
        )

    seen = peer(monkeypatch, serve)
    assert (
        await provider.put_event("google", "access", "calendar", None, STABLE, PAYLOAD)
        == uuid.UUID(STABLE).hex
    )
    assert sum(req.method == "POST" for req in seen) == 1
    assert sum(req.method == "PATCH" for req in seen) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["google", "outlook"])
@pytest.mark.parametrize("operation", ["update", "delete"])
async def test_external_attendees_require_manual_handling_without_invites(
    monkeypatch, kind, operation
):
    seen = peer(
        monkeypatch,
        lambda req: httpx.Response(
            200, json={"id": "event", "attendees": [{"email": "private@example.com"}]}
        ),
    )
    with pytest.raises(provider.ProviderError, match="remote_event_has_attendees"):
        if operation == "update":
            await provider.put_event(kind, "access", "calendar", "event", STABLE, PAYLOAD)
        else:
            await provider.delete_event(kind, "access", "calendar", "event")
    assert [req.method for req in seen] == ["GET"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["google", "outlook"])
async def test_delete_is_idempotent_when_remote_event_missing(monkeypatch, kind):
    seen = peer(monkeypatch, lambda req: httpx.Response(404))
    await provider.delete_event(kind, "access", "calendar", "event")
    assert len(seen) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,code",
    [
        (401, "provider_authorization_required"),
        (403, "provider_authorization_required"),
        (400, "provider_rejected_request"),
        (302, "provider_rejected_request"),
    ],
)
async def test_errors_hide_provider_body_and_token(monkeypatch, status, code):
    peer(
        monkeypatch,
        lambda req: httpx.Response(
            status,
            json={"error": "PRIVATE provider diagnostic secret-access"},
            headers={"Location": "https://attacker.invalid"},
        ),
    )
    with pytest.raises(provider.ProviderError) as caught:
        await provider.account_key("outlook", "secret-access")
    assert str(caught.value) == code
    assert "PRIVATE" not in str(caught.value) and "secret-access" not in str(caught.value)
    assert caught.value.auth_required == (status in (401, 403))


@pytest.mark.asyncio
async def test_rate_limit_retry_hint_is_bounded(monkeypatch):
    peer(
        monkeypatch,
        lambda req: httpx.Response(
            429, json={"error": "private"}, headers={"Retry-After": "99999999"}
        ),
    )
    with pytest.raises(provider.ProviderError) as caught:
        await provider.account_key("google", "access")
    assert caught.value.retry_after == 86400


@pytest.mark.asyncio
async def test_ambiguous_create_timeout_cannot_report_success(monkeypatch):
    def serve(req):
        if req.method == "POST":
            raise httpx.ReadTimeout("PRIVATE timeout", request=req)
        return httpx.Response(200, json={"value": []})

    peer(monkeypatch, serve)
    with pytest.raises(provider.ProviderError) as caught:
        await provider.put_event("outlook", "access", "calendar", None, STABLE, PAYLOAD)
    assert caught.value.ambiguous and str(caught.value) == "provider_transport_error"


@pytest.mark.asyncio
async def test_large_provider_response_stops_at_budget(monkeypatch):
    peer(
        monkeypatch,
        lambda req: httpx.Response(200, content=b"x" * (provider.MAX_RESPONSE_BYTES + 1)),
    )
    with pytest.raises(provider.ProviderError, match="provider_response_too_large"):
        await provider.account_key("google", "access")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["google", "outlook"])
async def test_existing_event_update_is_patch_without_another_create(monkeypatch, kind):
    seen = peer(
        monkeypatch,
        lambda req: httpx.Response(
            200,
            json={
                "id": "existing",
                "attendees": [],
                "etag": '"read-revision"',
                "@odata.etag": 'W/"read-revision"',
            },
        ),
    )
    assert (
        await provider.put_event(kind, "access", "calendar", "existing", STABLE, PAYLOAD)
        == "existing"
    )
    assert [req.method for req in seen] == ["GET", "PATCH"]
    assert seen[-1].headers["If-Match"] == (
        '"read-revision"' if kind == "google" else 'W/"read-revision"'
    )
    body = json.loads(seen[-1].content)
    assert body["summary" if kind == "google" else "subject"] == PAYLOAD["summary"]
    assert "attendees" not in body


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["google", "outlook"])
async def test_existing_private_event_delete_uses_provider_acknowledgement(monkeypatch, kind):
    seen = peer(
        monkeypatch,
        lambda req: (
            httpx.Response(204)
            if req.method == "DELETE"
            else httpx.Response(
                200,
                json={
                    "id": "existing",
                    "attendees": [],
                    "etag": '"read-revision"',
                    "@odata.etag": 'W/"read-revision"',
                },
            )
        ),
    )
    await provider.delete_event(kind, "access", "calendar", "existing")
    assert [req.method for req in seen] == ["GET", "DELETE"]
    assert seen[-1].headers["If-Match"] == (
        '"read-revision"' if kind == "google" else 'W/"read-revision"'
    )
    if kind == "google":
        assert parse_qs(seen[-1].url.query.decode())["sendUpdates"] == ["none"]


@pytest.mark.asyncio
async def test_ambiguous_graph_duplicate_identity_is_not_arbitrarily_overwritten(monkeypatch):
    seen = peer(
        monkeypatch,
        lambda req: httpx.Response(200, json={"value": [{"id": "first"}, {"id": "second"}]}),
    )
    with pytest.raises(provider.ProviderError, match="duplicate_remote_events"):
        await provider.put_event("outlook", "access", "calendar", None, STABLE, PAYLOAD)
    assert [req.method for req in seen] == ["GET"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "point",
    [
        {"dateTime": 3},
        {"dateTime": "2026-10-25T08:00:00"},
        {"dateTime": "2026-10-25T08:00:00Z", "timeZone": "Not/AZone"},
    ],
)
async def test_bad_source_window_is_refused_before_provider_io(monkeypatch, point):
    seen = peer(monkeypatch, lambda req: httpx.Response(200, json={}))
    with pytest.raises(provider.ProviderError, match="invalid_event_payload"):
        await provider.put_event(
            "outlook", "access", "calendar", None, STABLE, {**PAYLOAD, "start": point}
        )
    assert not seen


@pytest.mark.asyncio
async def test_graph_calendar_marker_recovers_renamed_application_calendar(monkeypatch):
    peer(
        monkeypatch,
        lambda req: httpx.Response(
            200,
            json={
                "value": [
                    {"id": "unrelated", "name": "Work schedule"},
                    {
                        "id": "ours",
                        "name": "Renamed",
                        "singleValueExtendedProperties": [
                            {"id": provider.CALENDAR_PROPERTY, "value": MARKER}
                        ],
                    },
                ]
            },
        ),
    )
    assert (
        await provider.find_calendar("outlook", "access", "Work schedule", marker=MARKER) == "ours"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [{"value": [42]}, {"value": None}])
async def test_invalid_graph_result_shape_is_a_sanitized_failure(monkeypatch, response):
    peer(monkeypatch, lambda req: httpx.Response(200, json=response))
    with pytest.raises(provider.ProviderError, match="invalid_provider_response"):
        await provider.find_event("outlook", "access", "calendar", STABLE)


@pytest.mark.asyncio
async def test_google_event_identity_needs_the_managed_marker(monkeypatch):
    peer(
        monkeypatch,
        lambda req: httpx.Response(
            200, json={"id": uuid.UUID(STABLE).hex, "extendedProperties": None}
        ),
    )
    with pytest.raises(provider.ProviderError, match="unmanaged_remote_event"):
        await provider.find_event("google", "access", "calendar", STABLE)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,path",
    [("google", "/calendar/v3/users/me/calendarList/primary"), ("outlook", "/v1.0/me/calendar")],
)
async def test_account_binding_uses_primary_calendar_without_profile_scope(monkeypatch, kind, path):
    seen = peer(
        monkeypatch, lambda req: httpx.Response(200, json={"id": "opaque-primary-calendar"})
    )
    assert await provider.account_key(kind, "access") == "opaque-primary-calendar"
    assert seen[0].url.path == path


@pytest.mark.asyncio
async def test_malformed_success_after_create_remains_unknown(monkeypatch):
    peer(
        monkeypatch,
        lambda req: (
            httpx.Response(200, json={"value": []})
            if req.method == "GET"
            else httpx.Response(200, content=b"{PRIVATE invalid response")
        ),
    )
    with pytest.raises(provider.ProviderError) as caught:
        await provider.put_event("outlook", "access", "calendar", None, STABLE, PAYLOAD)
    assert caught.value.ambiguous and caught.value.code == "invalid_provider_response"
    assert "PRIVATE" not in str(caught.value)


@pytest.mark.asyncio
async def test_calendar_create_without_receipt_cannot_be_called_confirmed(monkeypatch):
    peer(monkeypatch, lambda req: httpx.Response(201, json={"name": "Created without id"}))
    with pytest.raises(provider.ProviderError) as caught:
        await provider.create_calendar("outlook", "access", name="Synthetic work", marker=MARKER)
    assert caught.value.ambiguous


@pytest.mark.asyncio
async def test_graph_missing_attendee_evidence_cannot_trigger_automatic_meeting_mail(monkeypatch):
    seen = peer(monkeypatch, lambda req: httpx.Response(200, json={"id": "event"}))
    with pytest.raises(provider.ProviderError, match="remote_event_attendees_unavailable"):
        await provider.delete_event("outlook", "access", "calendar", "event")
    assert [req.method for req in seen] == ["GET"]
    assert seen[0].url.params["$select"] == "id,attendees"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["google", "outlook"])
@pytest.mark.parametrize("operation", ["update", "delete"])
async def test_private_event_mutation_requires_a_specific_read_revision(
    monkeypatch, kind, operation
):
    seen = peer(monkeypatch, lambda req: httpx.Response(200, json={"id": "event", "attendees": []}))
    with pytest.raises(provider.ProviderError) as caught:
        if operation == "update":
            await provider.put_event(kind, "access", "calendar", "event", STABLE, PAYLOAD)
        else:
            await provider.delete_event(kind, "access", "calendar", "event")
    assert caught.value.code == "remote_event_revision_unavailable"
    assert caught.value.permanent and not caught.value.ambiguous
    assert [request.method for request in seen] == ["GET"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["google", "outlook"])
@pytest.mark.parametrize("etag", ["*", "", 'W/"revision"\r\nInjected: value', "not-quoted"])
async def test_wildcard_or_invalid_revision_cannot_disable_conditional_delete(
    monkeypatch, kind, etag
):
    seen = peer(
        monkeypatch,
        lambda req: httpx.Response(
            200,
            json={
                "id": "event",
                "attendees": [],
                "etag" if kind == "google" else "@odata.etag": etag,
            },
        ),
    )
    with pytest.raises(provider.ProviderError, match="remote_event_revision_unavailable"):
        await provider.delete_event(kind, "access", "calendar", "event")
    assert [request.method for request in seen] == ["GET"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["google", "outlook"])
@pytest.mark.parametrize("operation", ["update", "delete"])
async def test_attendee_change_after_read_returns_conflict_without_blind_write_retry(
    monkeypatch, kind, operation
):
    read_revision = '"private-revision"' if kind == "google" else 'W/"private-revision"'
    event = {"attendees": [], "revision": read_revision}
    unsafe_writes = []

    def serve(request):
        if request.method == "GET":
            response = httpx.Response(
                200,
                json={
                    "id": "event",
                    "attendees": list(event["attendees"]),
                    "etag" if kind == "google" else "@odata.etag": event["revision"],
                },
            )
            # A user changes this same event immediately after the safe read.
            event["attendees"] = [{"email": "new-person@example.com"}]
            event["revision"] = '"meeting-revision"'
            return response
        if request.headers.get("If-Match") != event["revision"]:
            return httpx.Response(412, json={"error": "PRIVATE changed meeting details"})
        unsafe_writes.append(request)
        return httpx.Response(204 if request.method == "DELETE" else 200, json={"id": "event"})

    seen = peer(monkeypatch, serve)

    async def mutate():
        if operation == "update":
            return await provider.put_event(kind, "access", "calendar", "event", STABLE, PAYLOAD)
        return await provider.delete_event(kind, "access", "calendar", "event")

    with pytest.raises(provider.ProviderError) as caught:
        await mutate()
    assert caught.value.code == "provider_event_changed"
    assert not caught.value.permanent and not caught.value.auth_required
    assert not caught.value.ambiguous and "PRIVATE" not in str(caught.value)
    assert [request.method for request in seen] == [
        "GET",
        "PATCH" if operation == "update" else "DELETE",
    ]
    assert seen[-1].headers["If-Match"] == read_revision
    assert not unsafe_writes
    # The durable worker may retry later: its fresh attendee check then blocks
    # mutation, rather than turning the conditional conflict into a blind write.
    with pytest.raises(provider.ProviderError, match="remote_event_has_attendees"):
        await mutate()
    assert seen[-1].method == "GET" and not unsafe_writes
