"""Wire schemas for the cost-allocation masters (WO-14 / F1.1).

Departments, cost centers and projects share the list/create/update shape; the
cost center adds its optional department link and the project its date window.
`version` on the update is the optimistic-concurrency token — the service
refuses a stale write with 409, it never last-writer-wins.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MasterStatus = Literal["active", "closed", "archived"]


class DepartmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    code: str
    name: str
    status: str
    version: int


class CostCenterOut(DepartmentOut):
    department_id: str | None = None


class ProjectOut(DepartmentOut):
    start_date: date | None = None
    end_date: date | None = None
    customer_id: str | None = None


class MasterCreate(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=200)


class CostCenterCreate(MasterCreate):
    department_id: str | None = None


class ProjectCreate(MasterCreate):
    start_date: date | None = None
    end_date: date | None = None


class MasterUpdate(BaseModel):
    """Rename and/or transition status — both optional, version-checked.

    The status vocabulary is validated here (shape); WHICH transitions are legal
    for which entity is the service's business rule (`costing._TRANSITIONS`).
    """

    name: str | None = Field(default=None, min_length=1, max_length=200)
    status: MasterStatus | None = None
    version: int
