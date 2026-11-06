"""A test client that behaves like our frontend, shared by the authentication tests."""

from argon2 import PasswordHasher
from httpx import AsyncClient, Response

PASSWORD = "correct horse battery"
PASSWORD_HASH = PasswordHasher().hash(PASSWORD)  # hashed once: argon2 is deliberately slow
ORIGIN = "https://app.example.com"  # the one allowed origin in these tests


class Browser:
    """Cookies come from the client's jar; state-changing requests carry the CSRF token and the
    Origin, exactly as the real frontend sends them."""

    def __init__(self, client: AsyncClient) -> None:
        self.http = client

    async def login(self, email: str, password: str = PASSWORD) -> "Browser":
        resp = await self.http.post(
            "/api/v1/auth/login", json={"email": email, "password": password}
        )
        assert resp.status_code == 200, resp.text
        return self

    def headers(self) -> dict[str, str]:
        return {"X-CSRF-Token": self.http.cookies["lf_csrf"], "Origin": ORIGIN}

    async def get(self, url: str) -> Response:
        return await self.http.get(url)

    async def post(self, url: str, **kw: object) -> Response:
        return await self.http.post(url, headers=self.headers(), **kw)  # type: ignore[arg-type]

    async def patch(self, url: str, **kw: object) -> Response:
        return await self.http.patch(url, headers=self.headers(), **kw)  # type: ignore[arg-type]

    async def delete(self, url: str) -> Response:
        return await self.http.delete(url, headers=self.headers())
