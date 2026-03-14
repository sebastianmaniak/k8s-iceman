from fastapi import APIRouter, Request

from app.utils.f5_client import F5Client

router = APIRouter()


@router.get("/", summary="List all monitors")
async def list_monitors(request: Request, partition: str = "Common"):
    """List all LTM health monitors in the specified partition."""
    client = F5Client(request)
    return await client.get(f"/mgmt/tm/ltm/monitor?$filter=partition eq {partition}")


@router.get("/http", summary="List HTTP monitors")
async def list_http_monitors(request: Request, partition: str = "Common"):
    """List all HTTP health monitors."""
    client = F5Client(request)
    return await client.get(f"/mgmt/tm/ltm/monitor/http?$filter=partition eq {partition}")


@router.get("/https", summary="List HTTPS monitors")
async def list_https_monitors(request: Request, partition: str = "Common"):
    """List all HTTPS health monitors."""
    client = F5Client(request)
    return await client.get(f"/mgmt/tm/ltm/monitor/https?$filter=partition eq {partition}")


@router.get("/tcp", summary="List TCP monitors")
async def list_tcp_monitors(request: Request, partition: str = "Common"):
    """List all TCP health monitors."""
    client = F5Client(request)
    return await client.get(f"/mgmt/tm/ltm/monitor/tcp?$filter=partition eq {partition}")
