from fastapi import Request
import httpx


class F5Client:
    """Reusable client that wraps iControl REST calls."""

    def __init__(self, request: Request):
        self.tm = request.app.state.token_manager
        self.base = request.app.state.token_manager.host

    async def get(self, path: str, params: dict = None) -> dict:
        headers = await self.tm.get_headers()
        async with httpx.AsyncClient(verify=self.tm.verify_ssl, timeout=30.0) as client:
            resp = await client.get(f"{self.base}{path}", headers=headers, params=params)
            resp.raise_for_status()
            return resp.json()

    async def post(self, path: str, payload: dict) -> dict:
        headers = await self.tm.get_headers()
        async with httpx.AsyncClient(verify=self.tm.verify_ssl, timeout=30.0) as client:
            resp = await client.post(f"{self.base}{path}", headers=headers, json=payload)
            resp.raise_for_status()
            return resp.json()

    async def patch(self, path: str, payload: dict) -> dict:
        headers = await self.tm.get_headers()
        async with httpx.AsyncClient(verify=self.tm.verify_ssl, timeout=30.0) as client:
            resp = await client.patch(f"{self.base}{path}", headers=headers, json=payload)
            resp.raise_for_status()
            return resp.json()

    async def delete(self, path: str) -> None:
        headers = await self.tm.get_headers()
        async with httpx.AsyncClient(verify=self.tm.verify_ssl, timeout=30.0) as client:
            resp = await client.delete(f"{self.base}{path}", headers=headers)
            resp.raise_for_status()
