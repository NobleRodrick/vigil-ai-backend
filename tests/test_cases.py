"""
VIGIL-AI Cameroun — Case Management Tests
"""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.case import Case
from app.models.submission import Analysis, Submission
from app.models.user import User


@pytest.fixture
async def sample_case(db_session: AsyncSession, analyst_user: User) -> Case:
    """Create a sample submission + analysis + case for testing."""
    submission = Submission(
        case_number="VIGIL-2026-00001",
        submitted_by=analyst_user.id,
        content_type="text",
        content_text="Sample suspicious text content for testing purposes.",
        status="complete",
    )
    db_session.add(submission)
    await db_session.flush()

    analysis = Analysis(
        submission_id=submission.id,
        risk_score=75,
        classification="malicious",
        confidence=0.85,
        explanation_en="Test explanation",
        explanation_fr="Explication de test",
        engine_used="test_engine",
    )
    db_session.add(analysis)

    case = Case(
        submission_id=submission.id,
        created_by=analyst_user.id,
        assigned_to=analyst_user.id,
        status="open",
        priority="high",
    )
    db_session.add(case)
    await db_session.commit()
    await db_session.refresh(case)
    return case


@pytest.mark.asyncio
class TestListCases:
    async def test_list_cases(self, client: AsyncClient, analyst_headers: dict, sample_case: Case):
        response = await client.get("/api/v1/cases/", headers=analyst_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["total_count"] >= 1

    async def test_filter_by_status(self, client: AsyncClient, analyst_headers: dict, sample_case: Case):
        response = await client.get(
            "/api/v1/cases/?status=open", headers=analyst_headers
        )
        assert response.status_code == 200
        for item in response.json()["items"]:
            assert item["status"] == "open"


@pytest.mark.asyncio
class TestCaseDetail:
    async def test_get_case_detail(self, client: AsyncClient, analyst_headers: dict, sample_case: Case):
        response = await client.get(f"/api/v1/cases/{sample_case.id}", headers=analyst_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["risk_score"] == 75
        assert data["classification"] == "malicious"

    async def test_get_nonexistent_case(self, client: AsyncClient, analyst_headers: dict):
        fake_id = uuid.uuid4()
        response = await client.get(f"/api/v1/cases/{fake_id}", headers=analyst_headers)
        assert response.status_code == 404


@pytest.mark.asyncio
class TestCaseStatusTransitions:
    async def test_valid_transition_open_to_in_review(
        self, client: AsyncClient, analyst_headers: dict, sample_case: Case
    ):
        response = await client.patch(
            f"/api/v1/cases/{sample_case.id}/status",
            headers=analyst_headers,
            json={"status": "in_review"},
        )
        assert response.status_code == 200

    async def test_invalid_transition_open_to_resolved(
        self, client: AsyncClient, analyst_headers: dict, sample_case: Case
    ):
        # Cannot go directly from open to resolved
        response = await client.patch(
            f"/api/v1/cases/{sample_case.id}/status",
            headers=analyst_headers,
            json={"status": "resolved"},
        )
        assert response.status_code == 422


@pytest.mark.asyncio
class TestCaseNotes:
    async def test_add_note(self, client: AsyncClient, analyst_headers: dict, sample_case: Case):
        response = await client.post(
            f"/api/v1/cases/{sample_case.id}/notes",
            headers=analyst_headers,
            json={"content": "This is an investigation note about the case."},
        )
        assert response.status_code == 200
        assert "investigation note" in response.json()["content"]

    async def test_list_notes(self, client: AsyncClient, analyst_headers: dict, sample_case: Case):
        await client.post(
            f"/api/v1/cases/{sample_case.id}/notes",
            headers=analyst_headers,
            json={"content": "First note on the case for testing."},
        )
        response = await client.get(
            f"/api/v1/cases/{sample_case.id}/notes", headers=analyst_headers
        )
        assert response.status_code == 200
        assert len(response.json()) >= 1


@pytest.mark.asyncio
class TestCaseEscalation:
    async def test_escalate_case(self, client: AsyncClient, analyst_headers: dict, sample_case: Case):
        response = await client.post(
            f"/api/v1/cases/{sample_case.id}/escalate",
            headers=analyst_headers,
            json={"reason": "This case requires immediate administrator attention and review."},
        )
        assert response.status_code == 200

    async def test_cannot_escalate_twice(
        self, client: AsyncClient, analyst_headers: dict, sample_case: Case
    ):
        payload = {"reason": "This case requires immediate administrator attention and review."}
        await client.post(
            f"/api/v1/cases/{sample_case.id}/escalate", headers=analyst_headers, json=payload
        )
        response = await client.post(
            f"/api/v1/cases/{sample_case.id}/escalate", headers=analyst_headers, json=payload
        )
        assert response.status_code == 422


@pytest.mark.asyncio
class TestCaseExport:
    async def test_admin_can_export(self, client: AsyncClient, admin_headers: dict, sample_case: Case):
        response = await client.get("/api/v1/cases/export/csv", headers=admin_headers)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")

    async def test_analyst_cannot_export(
        self, client: AsyncClient, analyst_headers: dict, sample_case: Case
    ):
        response = await client.get("/api/v1/cases/export/csv", headers=analyst_headers)
        assert response.status_code == 403
