"""
WebSocket API handlers for real-time scanner communication.
"""

import json
import time
import uuid
from typing import Dict, Any
from fastapi import WebSocket, WebSocketDisconnect, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.database import async_session_factory, get_db
from app.common.logging import get_logger
from app.services.scanner_manager import ScannerManager
from app.utils.enums import Direction, MessageType
from app.schemas.scanner import ErrorMessage, PongMessage

logger = get_logger(__name__)

# Global registry of scanner managers per database session
_scanner_managers: Dict[str, ScannerManager] = {}


async def get_scanner_manager(db: AsyncSession = Depends(get_db)) -> Any:
    """
    Get or create scanner manager for the database session.
    
    Note: Return type is Any to avoid FastAPI Pydantic validation issues.
    """
    session_id = str(id(db))
    
    if session_id not in _scanner_managers:
        manager = ScannerManager(db)
        await manager.initialize()
        _scanner_managers[session_id] = manager
    
    return _scanner_managers[session_id]


async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for real-time scanner communication.
    
    Handles:
    - Client connection/disconnection
    - Movement commands
    - State requests
    - Ping/pong for connection health
    """
    client_id = str(uuid.uuid4())
    
    # Create database session manually for WebSocket
    async with async_session_factory() as db:
        try:
            # Get scanner manager directly without dependency injection
            scanner_manager = await _get_scanner_manager_internal(db)
            
            await scanner_manager.connect_client(client_id, websocket)
            logger.info("WebSocket client connected", client_id=client_id[:8])
            
            while True:
                try:
                    # Receive message from client
                    data = await websocket.receive_text()
                    message = json.loads(data)
                    
                    message_type = message.get("type", "").lower()
                    
                    if message_type == MessageType.MOVE.value:
                        await _handle_move_message(scanner_manager, message, client_id)
                    
                    elif message_type == MessageType.GET_STATE.value:
                        await scanner_manager.send_state_to_client(client_id)
                    
                    elif message_type == MessageType.PING.value:
                        await _handle_ping_message(websocket)
                    
                    else:
                        await _send_error_message(websocket, f"Unknown message type: {message_type}")
                        
                except WebSocketDisconnect:
                    break
                except json.JSONDecodeError:
                    await _send_error_message(websocket, "Invalid JSON format")
                except Exception as e:
                    logger.error("WebSocket message handling error", error=str(e), client_id=client_id[:8])
                    await _send_error_message(websocket, str(e))
                    
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error("WebSocket connection error", error=str(e), client_id=client_id[:8])
        finally:
            try:
                await scanner_manager.disconnect_client(client_id)
                logger.info("WebSocket client disconnected", client_id=client_id[:8])
            except:
                pass


async def _get_scanner_manager_internal(db: AsyncSession) -> ScannerManager:
    """Internal helper to get scanner manager without FastAPI dependency injection."""
    session_id = str(id(db))
    
    if session_id not in _scanner_managers:
        manager = ScannerManager(db)
        await manager.initialize()
        _scanner_managers[session_id] = manager
    
    return _scanner_managers[session_id]


async def _handle_move_message(scanner_manager: ScannerManager, message: dict, client_id: str):
    """Handle movement command message."""
    try:
        direction_str = message.get("direction", "").lower()
        direction = Direction(direction_str)
        await scanner_manager.queue_movement(direction, client_id)
    except ValueError:
        logger.warning("Invalid direction received", direction=direction_str, client_id=client_id[:8])
        raise ValueError("Invalid direction")


async def _handle_ping_message(websocket: WebSocket):
    """Handle ping message with pong response."""
    pong_message = PongMessage(data={"timestamp": time.time()})
    await websocket.send_text(pong_message.model_dump_json())


async def _send_error_message(websocket: WebSocket, error_message: str):
    """Send error message to client."""
    error_msg = ErrorMessage(data={"message": error_message})
    await websocket.send_text(error_msg.model_dump_json())