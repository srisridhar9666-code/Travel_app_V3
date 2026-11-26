"""
In-app Bug Reports Router.

Lets any signed-in user submit an observation/issue with an auto-captured
screenshot from anywhere in the app, and lets administrators inspect and
resolve reported issues.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from app.core.deps import CurrentUser, DbSession
from app.core.enums import ADMIN_ROLES, AuditAction, Role
from app.core.security import decode_access_token
from app.models.bug_report import BugReport
from app.models.user import User
from app.schemas.bug_report import BugReportRead, BugReportUpdate
from app.services import audit, notifications, storage

router = APIRouter(tags=["bug-reports"])
_optional_bearer = HTTPBearer(auto_error=False)


def _to_read(row: BugReport) -> BugReportRead:
    return BugReportRead(
        id=row.id,
        reporter_id=row.reporter_id,
        reporter_name=row.reporter.full_name if row.reporter else "User",
        reporter_email=row.reporter.email if row.reporter else None,
        user_role=row.user_role,
        page_url=row.page_url,
        page_title=row.page_title,
        browser_info=row.browser_info,
        comment=row.comment,
        has_screenshot=bool(row.screenshot_path),
        status=row.status,
        resolution_note=row.resolution_note,
        resolved_by_id=row.resolved_by_id,
        resolved_by_name=row.resolved_by.full_name if row.resolved_by else None,
        resolved_at=row.resolved_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.post("/bug-reports", response_model=BugReportRead, status_code=status.HTTP_201_CREATED)
async def create_bug_report(
    db: DbSession,
    actor: CurrentUser,
    comment: Annotated[str, Form()],
    page_url: Annotated[str | None, Form()] = None,
    page_title: Annotated[str | None, Form()] = None,
    browser_info: Annotated[str | None, Form()] = None,
    screenshot: Annotated[UploadFile | None, File()] = None,
) -> BugReportRead:
    """Create a bug report with mandatory observation comment and optional screenshot."""
    cleaned = " ".join((comment or "").split())
    if len(cleaned) < 3:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Please describe the bug or observation (at least 3 characters).",
        )

    screenshot_path: str | None = None
    if screenshot is not None and screenshot.filename:
        data = await screenshot.read()
        if len(data) > 10 * 1024 * 1024:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="Screenshot cannot exceed 10 MB.",
            )
        if data:
            ext = storage.validate(screenshot, data)
            screenshot_path = storage.save_in("bug_reports", actor.id, data, ext)

    report = BugReport(
        tenant_id=actor.tenant_id,
        reporter_id=actor.id,
        user_role=str(actor.role),
        page_url=(page_url or "")[:500] or None,
        page_title=(page_title or "")[:200] or None,
        browser_info=(browser_info or "")[:500] or None,
        comment=cleaned,
        screenshot_path=screenshot_path,
        status="OPEN",
    )
    db.add(report)
    db.commit()
    db.refresh(report)

    audit.record(
        db,
        tenant_id=actor.tenant_id,
        actor=actor,
        action=AuditAction.CREATE,
        entity_type="bug_report",
        entity_id=report.id,
        summary=f"{actor.full_name} raised bug report #{report.id}",
        reason=cleaned[:300],
    )

    admins = (
        db.execute(
            select(User).where(
                User.tenant_id == actor.tenant_id,
                User.role.in_(ADMIN_ROLES),
                User.is_active.is_(True),
                User.id != actor.id,
            )
        )
        .scalars()
        .all()
    )
    for admin in admins:
        notifications.notify(
            db,
            tenant_id=actor.tenant_id,
            user=admin,
            kind="BUG_REPORTED",
            title=f"Bug #{report.id} raised by {actor.full_name}",
            body=f"Page: {report.page_url or 'App'} — {cleaned[:180]}",
            send_email=False,
        )
    db.commit()
    db.refresh(report)
    return _to_read(report)


@router.get("/bug-reports", response_model=list[BugReportRead])
def list_bug_reports(
    db: DbSession,
    actor: CurrentUser,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
) -> list[BugReportRead]:
    """Admins see all bug reports; other users see their own submissions."""
    stmt = select(BugReport).where(BugReport.tenant_id == actor.tenant_id)
    if not actor.is_admin:
        stmt = stmt.where(BugReport.reporter_id == actor.id)
    if status_filter:
        stmt = stmt.where(BugReport.status == status_filter)
    stmt = stmt.order_by(BugReport.id.desc())
    rows = db.execute(stmt).scalars().all()
    return [_to_read(r) for r in rows]


@router.get("/bug-reports/{bug_id}/screenshot")
def get_bug_screenshot(
    bug_id: int,
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_optional_bearer)] = None,
    token: Annotated[str | None, Query(description="Optional query token for direct image rendering")] = None,
) -> Response:
    """Serve the captured bug report screenshot to admins or the reporter."""
    raw_token = credentials.credentials if credentials and credentials.credentials else token
    if not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated.")
    payload = decode_access_token(raw_token)
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication token.")
    try:
        user_id = int(payload.get("sub", ""))
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token.")

    user = db.get(User, user_id)
    if not user or not user.is_active or user.tenant_id != payload.get("tenant"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")

    report = db.get(BugReport, bug_id)
    if not report or report.tenant_id != user.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bug report not found.")
    if not user.is_admin and report.reporter_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")
    if not report.screenshot_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No screenshot attached.")

    data = storage.read(report.screenshot_path)
    ext = Path(report.screenshot_path).suffix.lower()
    content_type = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(ext, "application/octet-stream")
    return Response(
        content=data,
        media_type=content_type,
        headers={
            "Content-Disposition": f'inline; filename="bug-{report.id}{ext or ".png"}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=86400",
        },
    )


@router.patch("/bug-reports/{bug_id}", response_model=BugReportRead)
def update_bug_report(
    bug_id: int,
    payload: BugReportUpdate,
    db: DbSession,
    actor: CurrentUser,
) -> BugReportRead:
    """Admins can triage or mark a bug report resolved."""
    if not actor.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only administrators can update bug report status.",
        )
    report = db.get(BugReport, bug_id)
    if not report or report.tenant_id != actor.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bug report not found.")

    report.status = payload.status
    if payload.resolution_note is not None:
        report.resolution_note = payload.resolution_note.strip() or None
    if payload.status == "RESOLVED":
        report.resolved_by_id = actor.id
        report.resolved_at = datetime.now(timezone.utc)
    else:
        report.resolved_by_id = None
        report.resolved_at = None

    db.commit()
    db.refresh(report)
    audit.record(
        db,
        tenant_id=actor.tenant_id,
        actor=actor,
        action=AuditAction.UPDATE,
        entity_type="bug_report",
        entity_id=report.id,
        summary=f"{actor.full_name} marked bug report #{report.id} as {report.status}",
        reason=report.resolution_note,
    )
    return _to_read(report)
