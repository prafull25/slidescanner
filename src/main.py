"""
Main entry point for the Morphle Scanner application.
"""

import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from app.common.config import settings
from app.common.logging import setup_logging, get_logger
from app.common.database import create_tables, close_db
from app.routes.scanner import router as scanner_router
from app.routes.websocket import ws_router
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.
    Handles startup and shutdown events.
    """
    logger = get_logger(__name__)
    
    # Startup
    logger.info("Starting Morphle Scanner application")
    try:
        # Initialize database
        await create_tables()
        logger.info("Database initialized successfully")
        
        yield
        
    except Exception as e:
        logger.error("Application startup failed", error=str(e))
        raise
    finally:
        # Shutdown
        logger.info("Shutting down Morphle Scanner application")
        await close_db()
        logger.info("Application shutdown complete")


def create_app() -> FastAPI:
    """
    Create and configure the FastAPI application.
    
    Returns:
        FastAPI: Configured application instance
    """
    # Setup logging first
    setup_logging()
    logger = get_logger(__name__)
    
    # Create FastAPI app with lifespan
    app = FastAPI(
        title="Morphle Scanner API",
        description="REST API and WebSocket interface for scanner operations",
        version="1.0.0",
        lifespan=lifespan
    )
    
    # Configure CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.ALLOWED_ORIGINS if hasattr(settings, 'ALLOWED_ORIGINS') else ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    # Include REST API routes
    app.include_router(scanner_router)
    app.include_router(ws_router)
        
    logger.info("FastAPI application created and configured")
    return app


# Create the app instance
app = create_app()

app.mount("/static", StaticFiles(directory="static"), name="static")

# Serve your HTML on root path or a specific route
@app.get("/")
def serve_index():
    return FileResponse("static/index.html")

def main():
    """
    Main entry point to run the application server.
    """
    logger = get_logger(__name__)
    
    try:
        # Get server configuration from settings
        host = getattr(settings, 'HOST', '0.0.0.0')
        port = getattr(settings, 'PORT', 8000)
        debug = getattr(settings, 'DEBUG', False)
        
        logger.info(
            "Starting server",
            host=host,
            port=port,
            debug=debug
        )
        
        # Run the server
        uvicorn.run(
            "main:app",
            host=host,
            port=port,
            reload=debug,
            log_level="info" if not debug else "debug",
            access_log=True
        )
        
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
    except Exception as e:
        logger.error("Server failed to start", error=str(e))
        raise


if __name__ == "__main__":
    main()