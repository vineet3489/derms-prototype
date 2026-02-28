"""DERMS Prototype Entry Point"""
import uvicorn
from src.config import settings

if __name__ == "__main__":
    uvicorn.run(
        "src.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        log_level="info",
        access_log=False,
    )
