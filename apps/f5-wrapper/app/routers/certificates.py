from fastapi import APIRouter, Request

from app.utils.f5_client import F5Client

router = APIRouter()


@router.get("/", summary="List all SSL certificates")
async def list_certificates(request: Request, partition: str = "Common"):
    """List all SSL certificates and their expiration dates."""
    client = F5Client(request)
    return await client.get(f"/mgmt/tm/sys/file/ssl-cert?$filter=partition eq {partition}")


@router.get("/{cert_name}", summary="Get certificate details")
async def get_certificate(request: Request, cert_name: str, partition: str = "Common"):
    """Get detailed information about a specific SSL certificate."""
    client = F5Client(request)
    return await client.get(f"/mgmt/tm/sys/file/ssl-cert/~{partition}~{cert_name}")
