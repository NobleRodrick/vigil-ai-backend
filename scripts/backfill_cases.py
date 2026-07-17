"""
VIGIL-AI Cameroun — Backfill investigation cases

Creates a Case for every submission that has a completed analysis but no
case yet. Needed after lowering AUTO_CREATE_CASE_MIN_SCORE (historically
only score >= 30 opened a case, so safe submissions — and any submission
analyzed while case-creation was broken — have no case record).

Usage:  python scripts/backfill_cases.py
Idempotent: submissions that already have a case are skipped.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import AsyncSessionLocal, engine
from app.models.case import Case, CasePriority
from app.models.submission import Submission, SubmissionStatus


def priority_for(score: int | None) -> str:
    if score is None:
        return CasePriority.LOW.value
    if score >= 70:
        return CasePriority.HIGH.value
    if score >= 50:
        return CasePriority.MEDIUM.value
    return CasePriority.LOW.value


async def main():
    created = 0
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Submission)
            .options(selectinload(Submission.analysis), selectinload(Submission.case))
            .where(Submission.status == SubmissionStatus.COMPLETE.value)
            .order_by(Submission.created_at.asc())
        )
        submissions = result.scalars().all()

        for sub in submissions:
            if sub.case is not None:
                continue
            if sub.analysis is None or sub.analysis.risk_score is None:
                continue
            case = Case(
                submission_id=sub.id,
                created_by=sub.submitted_by,
                assigned_to=sub.submitted_by,
                priority=priority_for(sub.analysis.risk_score),
                status="open",
            )
            db.add(case)
            created += 1
            print(
                f"  + case for {sub.case_number} "
                f"(score={sub.analysis.risk_score}, {sub.analysis.classification})"
            )

        await db.commit()

    print(f"Done — {created} case(s) created, {len(submissions) - created} already covered.")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
