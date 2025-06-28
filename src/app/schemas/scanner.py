from typing import Dict, List, Optional
from datetime import datetime
from pydantic import BaseModel, Field
from app.utils.enums import OperationStatus, Direction, MessageType


class Position(BaseModel):
    """Position model."""
    x: int = Field(..., ge=0, description="X coordinate")
    y: int = Field(..., ge=0, description="Y coordinate")


class MovementRequest(BaseModel):
    """Request model for movement operations."""
    direction: Direction = Field(..., description="Movement direction")


class ScannerStateResponse(BaseModel):
    """Response model for scanner state."""
    current_position: Position
    horizontal_movement_pending: int
    vertical_movement_pending: int
    operation_status: OperationStatus
    operation_start_time: Optional[datetime] = None
    current_movement_duration: Optional[float] = None
    captured_positions: List[List[int]]
    last_updated: datetime


class ScannerOperationResponse(BaseModel):
    """Response model for scanner operations."""
    id: int
    session_id: str
    timestamp: datetime
    operation_type: str
    position: Position
    duration: Optional[float] = None
    details: Optional[str] = None


class CapturedPositionResponse(BaseModel):
    """Response model for captured positions."""
    id: int
    session_id: str
    position: Position
    captured_at: datetime


class WebSocketMessage(BaseModel):
    """Base WebSocket message model."""
    type: MessageType
    data: Dict = Field(default_factory=dict)


class StateUpdateMessage(WebSocketMessage):
    """State update WebSocket message."""
    type: MessageType = MessageType.STATE_UPDATE
    data: Dict


class LogMessage(WebSocketMessage):
    """Log WebSocket message."""
    type: MessageType = MessageType.LOG
    data: Dict[str, str]  # timestamp, message


class ErrorMessage(WebSocketMessage):
    """Error WebSocket message."""
    type: MessageType = MessageType.ERROR
    data: Dict[str, str]  # message


class PingMessage(WebSocketMessage):
    """Ping WebSocket message."""
    type: MessageType = MessageType.PING


class PongMessage(WebSocketMessage):
    """Pong WebSocket message."""
    type: MessageType = MessageType.PONG
    data: Dict[str, float]  # timestamp


class MoveMessage(WebSocketMessage):
    """Move command WebSocket message."""
    type: MessageType = MessageType.MOVE
    direction: Direction


class HealthResponse(BaseModel):
    """Health check response model."""
    status: str
    timestamp: datetime
    connected_clients: int
    current_position: Position
    operation_status: OperationStatus