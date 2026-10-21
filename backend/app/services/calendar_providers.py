"""Bounded Google Calendar / Microsoft Graph clients for private schedule mirrors.

These clients never create attendees or provider reminders. An application email
is a separate, explicitly selected operation. HTTP error bodies and tokens are
never reflected in errors. All URLs are constructed from fixed provider origins.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from app.core.config import settings

GOOGLE_SCOPE = (
    "https://www.googleapis.com/auth/calendar.app.created "
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly"
)
OUTLOOK_SCOPE = "offline_access https://graph.microsoft.com/Calendars.ReadWrite"
GOOGLE_BASE = "https://www.googleapis.com/calendar/v3"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"
# One fixed namespace; values, not property names, identify a particular mirror.
EVENT_PROPERTY = "String {af913f27-99a5-4b76-b087-bbd1e82e8031} Name BidItAssignment"
CALENDAR_PROPERTY = "String {af913f27-99a5-4b76-b087-bbd1e82e8031} Name BidItCalendar"
MAX_RESPONSE_BYTES = 256 * 1024
MAX_CALENDAR_PAGES = 5


class ProviderError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        retry_after: float | None = None,
        auth_required: bool = False,
        ambiguous: bool = False,
        permanent: bool = False,
    ) -> None:
        self.code = code
        self.retry_after = retry_after
        self.auth_required = auth_required
        self.ambiguous = ambiguous
        self.permanent = permanent
        super().__init__(code)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(15, connect=5), follow_redirects=False, trust_env=False
    )


def _provider(provider: str) -> str:
    if provider not in ("google", "outlook"):
        raise ProviderError("unsupported_provider", permanent=True)
    return provider


def _credentials(provider: str) -> tuple[str, str]:
    _provider(provider)
    prefix = "google" if provider == "google" else "outlook"
    client_id = getattr(settings, f"{prefix}_calendar_client_id", None)
    secret = getattr(settings, f"{prefix}_calendar_client_secret", None)
    if not client_id or not secret:
        raise ProviderError("provider_not_configured", permanent=True)
    return client_id, secret


def _id(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise ProviderError("invalid_provider_response", permanent=True)
    if any(ord(char) < 32 for char in value):
        raise ProviderError("invalid_provider_response", permanent=True)
    return value


def _created_id(result: dict) -> str:
    try:
        return _id(result.get("id"))
    except ProviderError:
        raise ProviderError("invalid_provider_response", ambiguous=True) from None


def _stable(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ProviderError("invalid_mirror_identity", permanent=True) from exc


def _redirect(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.username
        or parsed.password
        or parsed.fragment
        or not parsed.hostname
        or (
            parsed.scheme != "https"
            and not (
                parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1")
            )
        )
    ):
        raise ProviderError("invalid_callback_url", permanent=True)
    return value


def authorization_url(provider: str, state: str, challenge: str, redirect_uri: str) -> str:
    client_id, _ = _credentials(provider)
    params = {
        "client_id": client_id,
        "redirect_uri": _redirect(redirect_uri),
        "response_type": "code",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "scope": GOOGLE_SCOPE if provider == "google" else OUTLOOK_SCOPE,
    }
    if provider == "google":
        base = "https://accounts.google.com/o/oauth2/v2/auth"
        params.update(access_type="offline", prompt="consent", include_granted_scopes="true")
    else:
        base = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
        params.update(response_mode="query")
    return base + "?" + urlencode(params)


def _retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        delay = float(value)
    except (ValueError, TypeError):
        try:
            instant = parsedate_to_datetime(value)
            if instant.tzinfo is None:
                instant = instant.replace(tzinfo=UTC)
            delay = (instant - datetime.now(UTC)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    if not 0 <= delay < float("inf"):
        return None
    return min(delay, 86400.0)


async def _request(
    method: str,
    url: str,
    *,
    token: str | None = None,
    body: dict | None = None,
    data: dict | None = None,
    params: dict | None = None,
    headers: dict | None = None,
    allow_not_found: bool = False,
) -> dict[str, Any] | None:
    opts: dict[str, Any] = {
        "headers": {"Accept": "application/json", "Accept-Encoding": "identity", **(headers or {})}
    }
    if token is not None:
        opts["headers"]["Authorization"] = "Bearer " + token
    if body is not None:
        opts["json"] = body
    if data is not None:
        opts["data"] = data
    if params is not None:
        opts["params"] = params
    try:
        async with asyncio.timeout(20):
            async with _client() as client:
                async with client.stream(method, url, **opts) as response:
                    content = bytearray()
                    if response.headers.get("Content-Encoding", "identity") not in ("identity", ""):
                        raise ProviderError(
                            "unexpected_provider_encoding",
                            permanent=True,
                            ambiguous=method == "POST",
                        )
                    async for chunk in response.aiter_raw(chunk_size=65536):
                        content.extend(chunk)
                        if len(content) > MAX_RESPONSE_BYTES:
                            raise ProviderError(
                                "provider_response_too_large",
                                permanent=True,
                                ambiguous=method == "POST",
                            )
                    status = response.status_code
                    if status == 404 and allow_not_found:
                        return None
                    if status in (401, 403):
                        raise ProviderError(
                            "provider_authorization_required", auth_required=True, permanent=True
                        )
                    if status == 409:
                        raise ProviderError("provider_conflict", permanent=True)
                    if status == 412:
                        raise ProviderError("provider_event_changed")
                    if status == 429 or status >= 500:
                        raise ProviderError(
                            "provider_rate_limited" if status == 429 else "provider_unavailable",
                            retry_after=_retry_after(response.headers.get("Retry-After")),
                            ambiguous=method == "POST" and status >= 500,
                        )
                    if status < 200 or status >= 300:
                        raise ProviderError("provider_rejected_request", permanent=True)
                    if not content and status == 204:
                        return {}
                    try:
                        result = json.loads(content)
                    except (ValueError, RecursionError):
                        raise ProviderError(
                            "invalid_provider_response", permanent=True, ambiguous=method == "POST"
                        ) from None
                    if not isinstance(result, dict):
                        raise ProviderError(
                            "invalid_provider_response", permanent=True, ambiguous=method == "POST"
                        )
                    return result
    except (httpx.HTTPError, TimeoutError):
        raise ProviderError("provider_transport_error", ambiguous=method == "POST") from None


def _token_result(result: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise ProviderError("invalid_token_response", permanent=True)
    access = result.get("access_token")
    refresh = result.get("refresh_token")
    expiry = result.get("expires_in")
    if (
        not isinstance(access, str)
        or not 1 <= len(access) <= 16384
        or any(ord(char) <= 32 or ord(char) >= 127 for char in access)
        or (
            refresh is not None and (not isinstance(refresh, str) or not 1 <= len(refresh) <= 16384)
        )
        or isinstance(expiry, bool)
        or not isinstance(expiry, (int, float))
        or not 1 <= expiry <= 604800
        or not isinstance(result.get("token_type", "Bearer"), str)
        or result.get("token_type", "Bearer").lower() != "bearer"
    ):
        raise ProviderError("invalid_token_response", permanent=True)
    # Preserve only needed credentials; provider diagnostic/identity fields are not retained.
    return {"access_token": access, "refresh_token": refresh, "expires_in": int(expiry)}


async def exchange_code(
    provider: str, code: str, verifier: str, redirect_uri: str
) -> dict[str, Any]:
    client_id, secret = _credentials(provider)
    url = (
        "https://oauth2.googleapis.com/token"
        if provider == "google"
        else "https://login.microsoftonline.com/common/oauth2/v2.0/token"
    )
    data = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": secret,
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": _redirect(redirect_uri),
    }
    if provider == "outlook":
        data["scope"] = OUTLOOK_SCOPE
    return _token_result(await _request("POST", url, data=data))


async def refresh_tokens(provider: str, refresh_token: str) -> dict[str, Any]:
    client_id, secret = _credentials(provider)
    url = (
        "https://oauth2.googleapis.com/token"
        if provider == "google"
        else "https://login.microsoftonline.com/common/oauth2/v2.0/token"
    )
    data = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": secret,
        "refresh_token": refresh_token,
    }
    if provider == "outlook":
        data["scope"] = OUTLOOK_SCOPE
    result = _token_result(await _request("POST", url, data=data))
    result["refresh_token"] = result["refresh_token"] or refresh_token
    return result


async def account_key(provider: str, token: str) -> str:
    """A provider-stable primary calendar identity; caller hashes it at rest."""
    _provider(provider)
    url = (
        GOOGLE_BASE + "/users/me/calendarList/primary"
        if provider == "google"
        else GRAPH_BASE + "/me/calendar"
    )
    result = await _request(
        "GET", url, token=token, params={"$select": "id"} if provider == "outlook" else None
    )
    assert result is not None
    return _id(result.get("id"))


def _marker(marker: str) -> str:
    return "Bid_it managed calendar: " + _stable(marker)


async def find_calendar(
    provider: str, token: str, name: str, *, marker: str | None = None
) -> str | None:
    _provider(provider)
    params = (
        {"maxResults": "100"}
        if provider == "google"
        else {
            "$top": "100",
            "$expand": f"singleValueExtendedProperties($filter=id eq '{CALENDAR_PROPERTY}')",
        }
    )
    url = (
        GOOGLE_BASE + "/users/me/calendarList"
        if provider == "google"
        else GRAPH_BASE + "/me/calendars"
    )
    found: list[str] = []
    for _ in range(MAX_CALENDAR_PAGES):
        result = await _request("GET", url, token=token, params=params)
        assert result is not None
        entries = result.get("items" if provider == "google" else "value")
        if not isinstance(entries, list) or len(entries) > 100:
            raise ProviderError("invalid_provider_response", permanent=True)
        for item in entries:
            if not isinstance(item, dict):
                raise ProviderError("invalid_provider_response", permanent=True)
            if marker:
                properties = item.get("singleValueExtendedProperties", [])
                if provider == "outlook" and (
                    not isinstance(properties, list)
                    or any(not isinstance(prop, dict) for prop in properties)
                ):
                    raise ProviderError("invalid_provider_response", permanent=True)
                matches = (
                    item.get("description") == _marker(marker)
                    if provider == "google"
                    else any(
                        prop.get("id") == CALENDAR_PROPERTY and prop.get("value") == _stable(marker)
                        for prop in properties
                        if isinstance(prop, dict)
                    )
                )
            else:
                matches = item.get("summary" if provider == "google" else "name") == name
            if matches:
                found.append(_id(item.get("id")))
        if provider == "google":
            next_token = result.get("nextPageToken")
            if not next_token:
                break
            params["pageToken"] = _id(next_token)
        else:
            next_link = result.get("@odata.nextLink")
            if not next_link:
                break
            parsed = urlsplit(_id(next_link))
            if (
                parsed.scheme != "https"
                or parsed.netloc != "graph.microsoft.com"
                or parsed.path != "/v1.0/me/calendars"
            ):
                raise ProviderError("invalid_provider_pagination", permanent=True)
            values = parse_qs(parsed.query)
            params = {
                k: v[0]
                for k, v in values.items()
                if k in ("$skiptoken", "$skip", "$top", "$expand") and len(v) == 1
            }
    else:
        raise ProviderError("calendar_inventory_too_large", permanent=True)
    if len(found) > 1:
        raise ProviderError("duplicate_managed_calendars", permanent=True)
    return found[0] if found else None


async def create_calendar(
    provider: str, token: str, *, name: str = "Bid_it work schedule", marker: str | None = None
) -> str:
    _provider(provider)
    if not isinstance(name, str) or not 1 <= len(name) <= 120:
        raise ProviderError("invalid_calendar_name", permanent=True)
    if provider == "google":
        body: dict[str, Any] = {"summary": name, "timeZone": "UTC"}
        if marker:
            body["description"] = _marker(marker)
        result = await _request("POST", GOOGLE_BASE + "/calendars", token=token, body=body)
    else:
        body = {"name": name}
        if marker:
            body["singleValueExtendedProperties"] = [
                {"id": CALENDAR_PROPERTY, "value": _stable(marker)}
            ]
        result = await _request("POST", GRAPH_BASE + "/me/calendars", token=token, body=body)
    assert result is not None
    return _created_id(result)


def _event_path(provider: str, calendar_id: str, event_id: str | None = None) -> str:
    _provider(provider)
    prefix = GOOGLE_BASE + "/calendars/" if provider == "google" else GRAPH_BASE + "/me/calendars/"
    path = prefix + quote(_id(calendar_id), safe="") + "/events"
    return path + ("/" + quote(_id(event_id), safe="") if event_id else "")


async def find_event(provider: str, token: str, calendar_id: str, stable_id: str) -> str | None:
    stable_id = _stable(stable_id)
    if provider == "google":
        event_id = uuid.UUID(stable_id).hex
        result = await _request(
            "GET", _event_path(provider, calendar_id, event_id), token=token, allow_not_found=True
        )
        if result is None:
            return None
        metadata = result.get("extendedProperties")
        private = metadata.get("private") if isinstance(metadata, dict) else None
        if not isinstance(private, dict) or private.get("bidit_assignment") != stable_id:
            raise ProviderError("unmanaged_remote_event", permanent=True)
        actual_id = _id(result.get("id"))
        if actual_id != event_id:
            raise ProviderError("unmanaged_remote_event", permanent=True)
        return actual_id
    query = f"singleValueExtendedProperties/Any(ep: ep/id eq '{EVENT_PROPERTY}' and ep/value eq '{stable_id}')"
    result = await _request(
        "GET",
        _event_path(provider, calendar_id),
        token=token,
        params={"$filter": query, "$top": "2", "$select": "id"},
    )
    assert result is not None
    entries = result.get("value")
    if not isinstance(entries, list) or any(not isinstance(item, dict) for item in entries):
        raise ProviderError("invalid_provider_response", permanent=True)
    if len(entries) > 1 or result.get("@odata.nextLink"):
        raise ProviderError("duplicate_remote_events", permanent=True)
    return _id(entries[0].get("id")) if entries else None


def _event_body(provider: str, stable_id: str, payload: dict) -> dict[str, Any]:
    summary, description = payload.get("summary", "Work assignment"), payload.get("description", "")
    if (
        not isinstance(summary, str)
        or len(summary) > 512
        or not isinstance(description, str)
        or len(description) > 8192
    ):
        raise ProviderError("invalid_event_payload", permanent=True)
    starts, ends = payload.get("start"), payload.get("end")
    if not isinstance(starts, dict) or not isinstance(ends, dict):
        raise ProviderError("invalid_event_payload", permanent=True)
    all_day = "date" in starts and "date" in ends
    converted: list[dict[str, str]] = []
    normalized: list[dict[str, str]] = []
    for point in (starts, ends):
        zone = point.get("timeZone", "UTC")
        try:
            ZoneInfo(zone)
            if all_day:
                local = date.fromisoformat(point["date"])
                normalized.append({"date": local.isoformat(), "timeZone": zone})
                converted.append({"dateTime": local.isoformat() + "T00:00:00", "timeZone": zone})
            else:
                raw_instant = point.get("dateTime")
                if not isinstance(raw_instant, str):
                    raise ValueError("missing timestamp")
                instant = datetime.fromisoformat(raw_instant.replace("Z", "+00:00"))
                if instant.tzinfo is None:
                    raise ValueError("missing offset")
                normalized.append({"dateTime": instant.isoformat(), "timeZone": zone})
                converted.append(
                    {
                        "dateTime": instant.astimezone(UTC).replace(tzinfo=None).isoformat(),
                        "timeZone": "UTC",
                    }
                )
        except (KeyError, ValueError, TypeError, ZoneInfoNotFoundError) as exc:
            raise ProviderError("invalid_event_payload", permanent=True) from exc
    if (all_day and converted[0]["timeZone"] != converted[1]["timeZone"]) or converted[1][
        "dateTime"
    ] <= converted[0]["dateTime"]:
        raise ProviderError("invalid_event_payload", permanent=True)
    if provider == "google":
        return {
            "summary": summary,
            "description": description,
            "start": normalized[0],
            "end": normalized[1],
            "visibility": "private",
            "transparency": "opaque",
            "status": "tentative" if payload.get("status") == "tentative" else "confirmed",
            "reminders": {"useDefault": False, "overrides": []},
            "extendedProperties": {"private": {"bidit_assignment": stable_id}},
        }
    return {
        "subject": summary,
        "body": {"contentType": "text", "content": description},
        "start": converted[0],
        "end": converted[1],
        "isAllDay": all_day,
        "sensitivity": "private",
        "isReminderOn": False,
        "showAs": "tentative" if payload.get("status") == "tentative" else "busy",
        "singleValueExtendedProperties": [{"id": EVENT_PROPERTY, "value": stable_id}],
    }


async def _check_private_event(
    provider: str, token: str, calendar_id: str, event_id: str
) -> dict | None:
    result = await _request(
        "GET",
        _event_path(provider, calendar_id, event_id),
        token=token,
        allow_not_found=True,
        params={"$select": "id,attendees"}
        if provider == "outlook"
        else {"fields": "id,attendees,etag,extendedProperties"},
    )
    if (
        result is not None
        and provider == "outlook"
        and not isinstance(result.get("attendees"), list)
    ):
        raise ProviderError("remote_event_attendees_unavailable", permanent=True)
    if result is not None and result.get("attendees"):
        # Graph can send meeting updates/cancellations automatically. A mirror
        # externally converted into a meeting needs deliberate human handling.
        raise ProviderError("remote_event_has_attendees", permanent=True)
    return result


def _mutation_headers(provider: str, current: dict) -> dict[str, str]:
    """Bind the write to the same private event revision inspected above.

    Both providers return quoted entity tags (Graph commonly uses W/ tags).
    Missing or wildcard tags cannot protect against a user adding attendees
    between our read and write, so automatic changes must stop instead.
    """
    etag = current.get("etag" if provider == "google" else "@odata.etag")
    if not isinstance(etag, str) or not 2 <= len(etag) <= 4096:
        raise ProviderError("remote_event_revision_unavailable", permanent=True)
    quoted = etag[2:] if etag.startswith("W/") else etag
    if (
        not quoted.startswith('"')
        or not quoted.endswith('"')
        or any(ord(char) < 33 or ord(char) > 126 or char == '"' for char in quoted[1:-1])
    ):
        raise ProviderError("remote_event_revision_unavailable", permanent=True)
    return {"If-Match": etag}


async def _patch_event(
    provider: str, token: str, calendar_id: str, event_id: str, body: dict
) -> str | None:
    current = await _check_private_event(provider, token, calendar_id, event_id)
    if current is None:
        return None
    headers = _mutation_headers(provider, current)
    result = await _request(
        "PATCH",
        _event_path(provider, calendar_id, event_id),
        token=token,
        body=body,
        params={"sendUpdates": "none"} if provider == "google" else None,
        headers=headers,
    )
    assert result is not None
    return _id(result.get("id", event_id))


async def put_event(
    provider: str,
    token: str,
    calendar_id: str,
    event_id: str | None,
    stable_id: str,
    event_payload: dict,
    *,
    allow_create: bool = True,
) -> str:
    _provider(provider)
    stable_id = _stable(stable_id)
    body = _event_body(provider, stable_id, event_payload)
    if event_id:
        updated = await _patch_event(provider, token, calendar_id, event_id, body)
        if updated:
            return updated
    found = await find_event(provider, token, calendar_id, stable_id)
    if found:
        updated = await _patch_event(provider, token, calendar_id, found, body)
        if updated:
            return updated
        raise ProviderError("event_creation_unconfirmed", ambiguous=True)
    if not allow_create:
        raise ProviderError("event_creation_unconfirmed", ambiguous=True)
    if provider == "google":
        expected_id = uuid.UUID(stable_id).hex
        body["id"] = expected_id
        try:
            result = await _request(
                "POST",
                _event_path(provider, calendar_id),
                token=token,
                body=body,
                params={"sendUpdates": "none"},
            )
        except ProviderError as exc:
            if exc.code != "provider_conflict":
                raise
            found = await find_event(provider, token, calendar_id, stable_id)
            if found:
                updated = await _patch_event(provider, token, calendar_id, found, body)
                if updated:
                    return updated
            raise ProviderError("event_creation_unconfirmed", ambiguous=True) from exc
    else:
        body["transactionId"] = stable_id
        result = await _request("POST", _event_path(provider, calendar_id), token=token, body=body)
    assert result is not None
    actual = _created_id(result)
    if provider == "google" and actual != uuid.UUID(stable_id).hex:
        raise ProviderError("event_creation_unconfirmed", ambiguous=True)
    return actual


async def delete_event(provider: str, token: str, calendar_id: str, event_id: str) -> None:
    current = await _check_private_event(provider, token, calendar_id, event_id)
    if current is None:
        return
    headers = _mutation_headers(provider, current)
    await _request(
        "DELETE",
        _event_path(provider, calendar_id, event_id),
        token=token,
        params={"sendUpdates": "none"} if provider == "google" else None,
        headers=headers,
        allow_not_found=True,
    )
