"""
VIGIL-AI Cameroun — Authentication Tests
"""
import pytest
from httpx import AsyncClient

from app.models.user import User


@pytest.mark.asyncio
class TestLogin:
    async def test_login_success(self, client: AsyncClient, analyst_user: User):
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "analyst@test.cm", "password": "AnalystTest123!"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"
        assert data["user"]["email"] == "analyst@test.cm"

    async def test_login_wrong_password(self, client: AsyncClient, analyst_user: User):
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "analyst@test.cm", "password": "WrongPassword!"},
        )
        assert response.status_code == 401
        assert response.json()["code"] == "INVALID_CREDENTIALS"

    async def test_login_unknown_email(self, client: AsyncClient):
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@test.cm", "password": "Whatever123!"},
        )
        assert response.status_code == 401

    async def test_account_locks_after_failed_attempts(
        self, client: AsyncClient, analyst_user: User
    ):
        # 5 failed attempts should lock the account
        for _ in range(5):
            await client.post(
                "/api/v1/auth/login",
                json={"email": "analyst@test.cm", "password": "WrongPassword!"},
            )
        # 6th attempt — should be locked even with correct password
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "analyst@test.cm", "password": "AnalystTest123!"},
        )
        assert response.status_code == 401
        assert response.json()["code"] in ("ACCOUNT_LOCKED", "INVALID_CREDENTIALS")


@pytest.mark.asyncio
class TestMe:
    async def test_get_me_authenticated(self, client: AsyncClient, analyst_headers: dict):
        response = await client.get("/api/v1/auth/me", headers=analyst_headers)
        assert response.status_code == 200
        assert response.json()["email"] == "analyst@test.cm"

    async def test_get_me_no_token(self, client: AsyncClient):
        response = await client.get("/api/v1/auth/me")
        assert response.status_code == 401

    async def test_get_me_invalid_token(self, client: AsyncClient):
        response = await client.get(
            "/api/v1/auth/me", headers={"Authorization": "Bearer invalid.token.here"}
        )
        assert response.status_code == 401


@pytest.mark.asyncio
class TestPasswordReset:
    async def test_request_reset_always_200(self, client: AsyncClient):
        """Should return 200 even for unknown email (security: don't leak which emails exist)."""
        response = await client.post(
            "/api/v1/auth/password-reset/request",
            json={"email": "doesnotexist@test.cm"},
        )
        assert response.status_code == 200

    async def test_request_reset_known_email(self, client: AsyncClient, analyst_user: User):
        response = await client.post(
            "/api/v1/auth/password-reset/request",
            json={"email": "analyst@test.cm"},
        )
        assert response.status_code == 200
