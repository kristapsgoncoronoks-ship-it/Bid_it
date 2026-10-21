"""All-day feed dates preserve the source timezone through DST changes."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.models.project_assignment import ProjectAssignment
from app.services.ics import render


@pytest.mark.parametrize("month,day,next_day,hours", [(3, 29, 30, 23), (10, 25, 26, 25)])
def test_all_day_feed_uses_exclusive_local_dates_across_dst(month, day, next_day, hours):
    from datetime import UTC

    zone = ZoneInfo("Europe/Riga")
    start = datetime(2026, month, day, tzinfo=zone).astimezone(UTC)
    end = datetime(2026, month, next_day, tzinfo=zone).astimezone(UTC)
    assert (end - start).total_seconds() == hours * 3600
    row = ProjectAssignment(
        id="synthetic-assignment",
        org_id="synthetic-org",
        project_id="synthetic-project",
        assignee_user_id="synthetic-user",
        starts_at=start,
        ends_at=end,
        all_day=True,
        time_zone="Europe/Riga",
        status="planned",
    )
    text = render([row], {"synthetic-project": "Site visit"})
    assert f"DTSTART;VALUE=DATE:2026{month:02}{day:02}\r\n" in text
    assert f"DTEND;VALUE=DATE:2026{month:02}{next_day:02}\r\n" in text
    assert "DTSTART:2026" not in text
