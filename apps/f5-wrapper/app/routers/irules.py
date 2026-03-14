from fastapi import APIRouter, Request

from app.utils.f5_client import F5Client

router = APIRouter()


@router.get("/", summary="List all iRules")
async def list_irules(request: Request, partition: str = "Common"):
    """List all iRules in the specified partition."""
    client = F5Client(request)
    return await client.get(f"/mgmt/tm/ltm/rule?$filter=partition eq {partition}")


@router.get("/{irule_name}", summary="Get iRule details")
async def get_irule(request: Request, irule_name: str, partition: str = "Common"):
    """Get the full iRule definition including its TCL code."""
    client = F5Client(request)
    return await client.get(f"/mgmt/tm/ltm/rule/~{partition}~{irule_name}")
