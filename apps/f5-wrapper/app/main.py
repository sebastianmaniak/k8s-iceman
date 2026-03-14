from fastapi import FastAPI
from contextlib import asynccontextmanager

from app.config import settings
from app.auth import F5TokenManager
from app.mcp_server import mcp, set_token_manager
from app.routers import pools, virtual_servers, nodes, monitors, irules, certificates, system


@asynccontextmanager
async def lifespan(app: FastAPI):
    tm = F5TokenManager(
        host=settings.F5_HOST,
        username=settings.F5_USERNAME,
        password=settings.F5_PASSWORD,
        verify_ssl=settings.F5_VERIFY_SSL,
    )
    await tm.login()
    app.state.token_manager = tm
    set_token_manager(tm)
    yield
    await tm.logout()


app = FastAPI(
    title="F5 BIG-IP Automation API",
    description="Wrapper API for F5 BIG-IP iControl REST — designed for AI agent consumption via kagent",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(pools.router, prefix="/api/v1/pools", tags=["Pools"])
app.include_router(virtual_servers.router, prefix="/api/v1/virtual-servers", tags=["Virtual Servers"])
app.include_router(nodes.router, prefix="/api/v1/nodes", tags=["Nodes"])
app.include_router(monitors.router, prefix="/api/v1/monitors", tags=["Monitors"])
app.include_router(irules.router, prefix="/api/v1/irules", tags=["iRules"])
app.include_router(certificates.router, prefix="/api/v1/certificates", tags=["Certificates"])
app.include_router(system.router, prefix="/api/v1/system", tags=["System"])

# Mount MCP streamable-HTTP endpoint for kagent tool discovery
mcp_app = mcp.streamable_http_app()
app.mount("/mcp", mcp_app)


@app.get("/health")
async def health():
    return {"status": "ok"}
