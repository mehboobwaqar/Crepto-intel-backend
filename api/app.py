"""
FastAPI Application
====================
REST API server — Flutter app aur koi bhi HTTP client isse connect kar ke data le sakta hai.
CORS enabled hai taake mobile app bina kisi issue ke connect ho sake.
"""

import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import router
import config


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Server start hone par Binance live streamer background mein chalu karta hai."""
    stream_task = None
    try:
        from ingestion.websocket_streamer import _stream_loop
        print("  ✓ Starting background Binance WebSocket streamer...")
        stream_task = asyncio.create_task(_stream_loop())
    except Exception as e:
        print(f"  [WARN] Could not launch background streamer: {e}")

    yield

    if stream_task and not stream_task.done():
        stream_task.cancel()
        try:
            await stream_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="Crypto Market Intelligence API",
    description="Zero-Cost Crypto Trading Intelligence System",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware — Flutter app ko connect karne dega
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],            # Development mein sab allow
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes register
app.include_router(router)


@app.get("/")
def root():
    return {
        "name": "Crypto Market Intelligence Agent",
        "version": "1.0.0",
        "docs": f"http://{config.API_HOST}:{config.API_PORT}/docs",
    }


def start_server():
    """API server start karta hai (supports cloud PORT env var)."""
    import uvicorn
    import os
    port = int(os.environ.get("PORT", config.API_PORT))
    print(f"\n  Starting API server on http://{config.API_HOST}:{port}")
    print(f"  Docs: http://localhost:{port}/docs\n")
    uvicorn.run(app, host=config.API_HOST, port=port)
