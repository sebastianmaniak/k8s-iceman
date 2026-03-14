import httpx
import time


class F5TokenManager:
    def __init__(self, host: str, username: str, password: str, verify_ssl: bool = False):
        self.host = host.rstrip("/")
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self.token = None
        self.token_expiry = 0
        self.client = httpx.AsyncClient(verify=verify_ssl, timeout=30.0)

    async def login(self):
        resp = await self.client.post(
            f"{self.host}/mgmt/shared/authn/login",
            json={
                "username": self.username,
                "password": self.password,
                "loginProviderName": "tmos",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        self.token = data["token"]["token"]
        # Tokens last 1200s by default; refresh at 80%
        self.token_expiry = time.time() + 960

    async def get_token(self) -> str:
        if time.time() >= self.token_expiry:
            await self.login()
        return self.token

    async def get_headers(self) -> dict:
        token = await self.get_token()
        return {"X-F5-Auth-Token": token, "Content-Type": "application/json"}

    async def logout(self):
        if self.token:
            try:
                headers = {"X-F5-Auth-Token": self.token}
                await self.client.delete(
                    f"{self.host}/mgmt/shared/authz/tokens/{self.token}",
                    headers=headers,
                )
            except Exception:
                pass
        await self.client.aclose()
