import logging
import os

import uvicorn

if __name__ == "__main__":
    # 로깅 설정
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    
    port = int(os.getenv("PORT", "8088"))
    reload_enabled = os.getenv("RELOAD", "false").lower() in {"1", "true", "yes"}
    uvicorn.run("src.mcp_server:app", host="0.0.0.0", port=port, reload=reload_enabled)
