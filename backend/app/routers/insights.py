"""
Travel logs across every employee, and the filterable admin dashboard.

Admin-only, like the cost reports: both carry spend, and a log of where every
colleague has been is not something ground staff need. Each person can still
read their own timeline through `/users/{id}/travel-history`.
"""
from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.deps import AdminUser, CurrentUser, DbSession, FinanceOrAdmin
from app.core.enums import RequestType, TravellerStatus
from app.services import insights

router = APIRouter(tags=["insights"])


def report_filters(
    since: Annotated[date | None, Query()] = None,
    until: Annotated[date | None, Query()] = None,
    user_id: Annotated[int | None, Query()] = None,
    project_id: Annotated[int | None, Query()] = None,
    department_id: Annotated[int | None, Query(ge=0)] = None,
    request_type: Annotated[RequestType | None, Query()] = None,
    state: Annotated[str | None, Query(max_length=80)] = None,
    city: Annotated[str | None, Query(max_length=120)] = None,
) -> insights.Filters:
    """The slice every report page offers: dates, person, campaign, department
    (0 for people with none), type and destination. Shared so the dashboard, the log and the cost page cannot
    drift into three meanings of "Maharashtra" or "this month".

    A missing date is open on that side; reversed dates are swapped.
    """
    return insights.Filters(
        since=since,
        until=until,
        user_id=user_id,
        project_id=project_id,
        department_id=department_id,
        request_type=request_type,
        state=(state or "").strip() or None,
        city=(city or "").strip() or None,
    )


ReportFilters = Annotated[insights.Filters, Depends(report_filters)]


@router.get("/travel-logs")
def travel_logs(
    actor: FinanceOrAdmin,
    db: DbSession,
    filters: ReportFilters,
    status: Annotated[list[TravellerStatus] | None, Query()] = None,
    search: Annotated[str | None, Query(max_length=120)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=insights.MAX_LOG_ROWS)] = 50,
) -> dict:
    """Every movement in a window, for one employee or all of them.

    Without a status filter every status is listed, rejected and cancelled
    trips included. The summary still counts only pending, approved and booked
    movements, and spend only booked ones.

    Paged; the summary counts every match. An export asks for one page of up
    to MAX_LOG_ROWS.
    """
    filters.statuses = tuple(status) if status else None
    filters.search = (search or "").strip() or None
    return insights.travel_log(db, actor.tenant_id, filters, page=page, page_size=page_size)


@router.get("/analytics/insights")
def dashboard(actor: FinanceOrAdmin, db: DbSession, filters: ReportFilters) -> dict:
    """The dashboard for a window and slice. No dates means all time; a
    missing side is open."""
    return insights.dashboard(db, actor.tenant_id, filters)


@router.get("/analytics/filter-options")
def filter_options(actor: CurrentUser, db: DbSession) -> dict:
    """Campaigns, departments, people, destination states and cities, for the
    report filters."""
    return insights.filter_options(db, actor.tenant_id)
