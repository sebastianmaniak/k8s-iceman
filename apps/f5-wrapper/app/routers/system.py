from fastapi import APIRouter, Request

from app.utils.f5_client import F5Client

router = APIRouter()


@router.get("/info", summary="Get BIG-IP system info")
async def system_info(request: Request):
    """Get BIG-IP version, hostname, and platform info."""
    client = F5Client(request)
    return await client.get("/mgmt/tm/sys/version")


@router.get("/failover-status", summary="Get failover status")
async def failover_status(request: Request):
    """Check HA failover status -- active or standby."""
    client = F5Client(request)
    return await client.get("/mgmt/tm/sys/failover")


@router.get("/performance", summary="Get system performance stats")
async def performance(request: Request):
    """Get throughput, connections, CPU, and memory stats."""
    client = F5Client(request)
    return await client.get("/mgmt/tm/sys/performance/all-stats")


@router.get("/config-sync-status", summary="Get config sync status")
async def config_sync(request: Request):
    """Check config sync status across HA peers."""
    client = F5Client(request)
    return await client.get("/mgmt/tm/cm/sync-status")
