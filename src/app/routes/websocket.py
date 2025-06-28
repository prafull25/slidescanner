"""
WebSocket API handlers for real-time scanner communication - Updated for multi-user support.
"""

import json
import time
import uuid
from typing import Dict, Any
from fastapi import WebSocket, WebSocketDisconnect, Depends, HTTPException,APIRouter
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.database import async_session_factory, get_db
from app.common.logging import get_logger
from app.services.scanner_manager import ScannerManager
from app.utils.enums import Direction, MessageType
from app.schemas.scanner import ErrorMessage, PongMessage

logger = get_logger(__name__)

# Global registry of scanner managers per user
_scanner_managers: Dict[str, ScannerManager] = {}

ws_router = APIRouter()

def _validate_user_id(user_id: str) -> bool:
    """Validate user_id format (4-6 characters, alphanumeric)."""
    if not user_id or not isinstance(user_id, str):
        return False
    if len(user_id) < 4 or len(user_id) > 6:
        return False
    return user_id.isalnum()


async def get_scanner_manager(user_id: str, db: AsyncSession = Depends(get_db)) -> Any:
    """
    Get or create scanner manager for the user.
    
    Note: Return type is Any to avoid FastAPI Pydantic validation issues.
    """
    if not _validate_user_id(user_id):
        raise HTTPException(status_code=400, detail="Invalid user_id format")
    
    manager_key = f"{user_id}_{str(id(db))}"
    
    if manager_key not in _scanner_managers:
        manager = ScannerManager(db, user_id)
        await manager.initialize()
        _scanner_managers[manager_key] = manager
    
    return _scanner_managers[manager_key]

@ws_router.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str):
    """
    WebSocket endpoint for real-time scanner communication.
    
    Args:
        user_id: User identifier (4-6 characters, alphanumeric)
    
    Handles:
    - Client connection/disconnection
    - Movement commands
    - State requests
    - Ping/pong for connection health
    """
    if not _validate_user_id(user_id):
        await websocket.close(code=4000, reason="Invalid user_id format")
        return
    
    client_id = str(uuid.uuid4())
    
    # Create database session manually for WebSocket
    async with async_session_factory() as db:
        try:
            # Get scanner manager directly without dependency injection
            scanner_manager = await _get_scanner_manager_internal(db, user_id)
            
            await scanner_manager.connect_client(client_id, websocket)
            logger.info("WebSocket client connected", client_id=client_id[:8], user_id=user_id)
            
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
                    logger.error("WebSocket message handling error", error=str(e), client_id=client_id[:8], user_id=user_id)
                    await _send_error_message(websocket, str(e))
                    
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error("WebSocket connection error", error=str(e), client_id=client_id[:8], user_id=user_id)
        finally:
            try:
                await scanner_manager.disconnect_client(client_id)
                logger.info("WebSocket client disconnected", client_id=client_id[:8], user_id=user_id)
            except:
                pass


async def _get_scanner_manager_internal(db: AsyncSession, user_id: str) -> ScannerManager:
    """Internal helper to get scanner manager without FastAPI dependency injection."""
    manager_key = f"{user_id}_{str(id(db))}"
    
    if manager_key not in _scanner_managers:
        manager = ScannerManager(db, user_id)
        await manager.initialize()
        _scanner_managers[manager_key] = manager
    
    return _scanner_managers[manager_key]


async def _handle_move_message(scanner_manager: ScannerManager, message: dict, client_id: str):
    """Handle movement command message."""
    try:
        direction_str = message.get("direction", "").lower()
        direction = Direction(direction_str)
        await scanner_manager.queue_movement(direction, client_id)
    except ValueError:
        logger.warning("Invalid direction received", direction=direction_str, client_id=client_id[:8], user_id=scanner_manager.user_id)
        raise ValueError("Invalid direction")


async def _handle_ping_message(websocket: WebSocket):
    """Handle ping message with pong response."""
    pong_message = PongMessage(data={"timestamp": time.time()})
    await websocket.send_text(pong_message.model_dump_json())


async def _send_error_message(websocket: WebSocket, error_message: str):
    """Send error message to client."""
    error_msg = ErrorMessage(data={"message": error_message})
    await websocket.send_text(error_msg.model_dump_json())