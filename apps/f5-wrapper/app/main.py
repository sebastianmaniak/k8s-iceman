from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.responses import JSONResponse
from contextlib import asynccontextmanager

from app.config import settings
from app.auth import F5TokenManager
from app.mcp_server import mcp, set_token_manager

# Import FastAPI app for REST routes
from fastapi import FastAPI
from app.routers import pools, virtual_servers, nodes, monitors, irules, certificates, system


# Build the FastAPI sub-app (REST API only)
rest_app = FastAPI(
    title="F5 BIG-IP Automation API",
    description="Wrapper API for F5 BIG-IP iControl REST",
    version="1.0.0",
)
rest_app.include_router(pools.router, prefix="/api/v1/pools", tags=["Pools"])
rest_app.include_router(virtual_servers.router, prefix="/api/v1/virtual-servers", tags=["Virtual Servers"])
rest_app.include_router(nodes.router, prefix="/api/v1/nodes", tags=["Nodes"])
rest_app.include_router(monitors.router, prefix="/api/v1/monitors", tags=["Monitors"])
rest_app.include_router(irules.router, prefix="/api/v1/irules", tags=["iRules"])
rest_app.include_router(certificates.router, prefix="/api/v1/certificates", tags=["Certificates"])
rest_app.include_router(system.router, prefix="/api/v1/system", tags=["System"])


async def health(request):
    return JSONResponse({"status": "ok"})


# Build the MCP streamable-HTTP app (has its own lifespan for task group)
mcp_app = mcp.streamable_http_app()


@asynccontextmanager
async def lifespan(app):
    tm = F5TokenManager(
        host=settings.F5_HOST,
        username=settings.F5_USERNAME,
        password=settings.F5_PASSWORD,
        verify_ssl=settings.F5_VERIFY_SSL,
    )
    await tm.login()
    set_token_manager(tm)
    # Also set on REST app for router compatibility
    rest_app.state.token_manager = tm
    # Trigger MCP sub-app lifespan to initialize its task group
    async with mcp_app.router.lifespan_context(mcp_app):
        yield
    await tm.logout()


# Main Starlette app — routes health + REST at /api, MCP at /mcp
app = Starlette(
    lifespan=lifespan,
    routes=[
        Route("/health", health),
        Mount("/mcp", app=mcp_app),
        Mount("/", app=rest_app),
    ],
)
