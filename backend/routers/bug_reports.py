"""Bug report router for user feedback submission."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db
from models import User, BugReport
from models.user import UserRole
from schemas.bug_report import BugReportCreate, BugReportResponse, BugReportListResponse
from utils.security import get_current_user

router = APIRouter()


@router.post("", response_model=BugReportResponse, status_code=status.HTTP_201_CREATED)
async def create_bug_report(
    data: BugReportCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Create a new bug report."""
    bug_report = BugReport(
        user_id=current_user.id,
        description=data.description,
        category=data.category,
        browser_info=data.browser_info,
        page_url=data.page_url,
        screen_resolution=data.screen_resolution,
        user_settings_json=data.user_settings_json,
    )

    db.add(bug_report)
    await db.commit()
    await db.refresh(bug_report)

    return BugReportResponse.from_report_and_user(bug_report, current_user)


@router.get("", response_model=BugReportListResponse)
async def get_my_bug_reports(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get bug reports submitted by the current user."""
    result = await db.execute(
        select(BugReport)
        .where(BugReport.user_id == current_user.id)
        .order_by(BugReport.created_at.desc())
    )
    reports = result.scalars().all()

    return BugReportListResponse(
        reports=[BugReportResponse.from_report_and_user(r, current_user) for r in reports],
        total=len(reports),
    )


@router.get("/all", response_model=BugReportListResponse)
async def get_all_bug_reports(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get all bug reports (admin only)."""
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can view all bug reports"
        )

    result = await db.execute(
        select(BugReport)
        .options(selectinload(BugReport.user))
        .order_by(BugReport.created_at.desc())
    )
    reports = result.scalars().all()

    return BugReportListResponse(
        reports=[BugReportResponse.from_report_and_user(r, r.user) for r in reports],
        total=len(reports),
    )
