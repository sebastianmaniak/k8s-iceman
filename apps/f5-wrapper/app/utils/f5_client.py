from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from fastapi import Request
    from app.auth import F5TokenManager


class F5Client:
    """Reusable client that wraps iControl REST calls."""

    def __init__(self, source: Request | F5TokenManager):
        from app.auth import F5TokenManager as _TM

        if isinstance(source, _TM):
            self.tm = source
        else:
            self.tm = source.app.state.token_manager
        self.base = self.tm.host

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
