"""
VIGIL-AI Cameroun — Submission Endpoint Tests
"""
import pytest
from httpx import AsyncClient
from unittest.mock import patch

from app.models.user import User


@pytest.mark.asyncio
class TestTextSubmission:
    async def test_submit_text_success(self, client: AsyncClient, analyst_headers: dict):
        with patch("app.api.v1.submissions._enqueue_analysis"):
            response = await client.post(
                "/api/v1/submissions/text",
                headers=analyst_headers,
                json={
                    "content_text": "This is a sample text long enough to pass validation rules.",
                    "language": "en",
                },
            )
        assert response.status_code == 202
        data = response.json()
        assert data["case_number"].startswith("VIGIL-")
        assert data["status"] == "queued"

    async def test_submit_text_too_short(self, client: AsyncClient, analyst_headers: dict):
        response = await client.post(
            "/api/v1/submissions/text",
            headers=analyst_headers,
            json={"content_text": "short"},
        )
        assert response.status_code == 422

    async def test_submit_text_requires_auth(self, client: AsyncClient):
        response = await client.post(
            "/api/v1/submissions/text",
            json={"content_text": "This is a sample text long enough to pass validation."},
        )
        assert response.status_code == 401

    async def test_viewer_cannot_submit(self, client: AsyncClient, viewer_headers: dict):
        response = await client.post(
            "/api/v1/submissions/text",
            headers=viewer_headers,
            json={"content_text": "This is a sample text long enough to pass validation."},
        )
        assert response.status_code == 403


@pytest.mark.asyncio
class TestVideoSubmission:
    async def test_submit_video_valid_url(self, client: AsyncClient, analyst_headers: dict):
        with patch("app.api.v1.submissions._enqueue_analysis"):
            response = await client.post(
                "/api/v1/submissions/video",
                headers=analyst_headers,
                json={"content_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
            )
        assert response.status_code == 202

    async def test_submit_video_direct_link_accepted(self, client: AsyncClient, analyst_headers: dict):
        # Direct media links are now valid — they're fetched by streaming download
        with patch("app.api.v1.submissions._enqueue_analysis"):
            response = await client.post(
                "/api/v1/submissions/video",
                headers=analyst_headers,
                json={"content_url": "https://example.org/videos/clip.mp4"},
            )
        assert response.status_code == 202

    async def test_submit_video_invalid_scheme_rejected(self, client: AsyncClient, analyst_headers: dict):
        response = await client.post(
            "/api/v1/submissions/video",
            headers=analyst_headers,
            json={"content_url": "ftp://example.org/video.mp4"},
        )
        assert response.status_code == 422


@pytest.mark.asyncio
class TestListSubmissions:
    async def test_list_submissions_empty(self, client: AsyncClient, analyst_headers: dict):
        response = await client.get("/api/v1/submissions/", headers=analyst_headers)
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total_count" in data

    async def test_viewer_can_list(self, client: AsyncClient, viewer_headers: dict):
        response = await client.get("/api/v1/submissions/", headers=viewer_headers)
        assert response.status_code == 200
