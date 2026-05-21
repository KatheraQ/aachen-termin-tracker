import uvicorn

from app.config import load_config

if __name__ == "__main__":
    cfg = load_config()
    uvicorn.run(
        "app.server:app",
        host=cfg.app.host,
        port=cfg.app.port,
        reload=False,
        log_level="info",
    )
