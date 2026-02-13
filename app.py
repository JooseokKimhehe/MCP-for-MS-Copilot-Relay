import os

import uvicorn

from src.mcp_server import configure_gateway_logging

if __name__ == "__main__":
    configure_gateway_logging()
    
    port = int(os.getenv("PORT", "8098"))
    reload_enabled = os.getenv("RELOAD", "false").lower() in {"1", "true", "yes"}
    uvicorn.run("src.mcp_server:app", host="0.0.0.0", port=port, reload=reload_enabled)
