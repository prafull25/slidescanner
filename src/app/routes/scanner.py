"""
REST API endpoints for scanner operations.
"""

from typing import List
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.common.database import get_db
from app.common.logging import get_logger
from app.models.scanner import ScannerOperation, CapturedPosition
from app.schemas.scanner import (
    ScannerStateResponse, 
    ScannerOperationResponse, 
    CapturedPositionResponse,
    HealthResponse,
    Position
)
from app.services.scanner_manager import ScannerManager
from app.routes.websocket import get_scanner_manager

logger = get_logger(__name__)
router = APIRouter(prefix="/api/v1", tags=["scanner"])


@router.get("/", summary="API Root")
async def root():
    """Root endpoint for the scanner API."""
    return {
        "message": "Morphle Scanner API v1.0",
        "status": "running",
        "timestamp": datetime.utcnow().isoformat()
    }


@router.get("/state", response_model=ScannerStateResponse, summary="Get Scanner State")
async def get_scanner_state(
    scanner_manager: ScannerManager = Depends(get_scanner_manager)
):
    """
    Get the current state of the scanner.
    
    Returns:
        ScannerStateResponse: Current scanner state including position, 
                             pending movements, and operation status.
    """
    try:
        state_dict = scanner_manager.get_state_dict()
        
        return ScannerStateResponse(
            current_position=Position(**state_dict["current_position"]),
            horizontal_movement_pending=state_dict["horizontal_movement_pending"],
            vertical_movement_pending=state_dict["vertical_movement_pending"],
            operation_status=state_dict["operation_status"],
            operation_start_time=datetime.fromtimestamp(state_dict["operation_start_time"]) if state_dict["operation_start_time"] else None,
            current_movement_duration=state_dict["current_movement_duration"],
            captured_positions=state_dict["captured_positions"],
            last_updated=datetime.fromtimestamp(state_dict["last_updated"])
        )
    except Exception as e:
        logger.error("Failed to get scanner state", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to retrieve scanner state")


@router.get("/health", response_model=HealthResponse, summary="Health Check")
async def health_check(
    scanner_manager: ScannerManager = Depends(get_scanner_manager)
):
    """
    Health check endpoint for monitoring scanner service.
    
    Returns:
        HealthResponse: Service health status and basic metrics.
    """
    try:
        state_dict = scanner_manager.get_state_dict()
        
        return HealthResponse(
            status="healthy",
            timestamp=datetime.utcnow(),
            connected_clients=len(scanner_manager.connected_clients),
            current_position=Position(**state_dict["current_position"]),
            operation_status=state_dict["operation_status"]
        )
    except Exception as e:
        logger.error("Health check failed", error=str(e))
        raise HTTPException(status_code=503, detail="Service unhealthy")


@router.post("/reset", summary="Reset Scanner")
async def reset_scanner(
    scanner_manager: ScannerManager = Depends(get_scanner_manager)
):
    """
    Reset the scanner to its initial state.
    
    This will:
    - Cancel any ongoing operations
    - Reset position to default
    - Clear all pending movements
    - Clear captured positions
    - Reset operation status to READY
    
    Returns:
        dict: Success message
    """
    try:
        await scanner_manager.reset_scanner()
        logger.info("Scanner reset successfully")
        return {"status": "success", "message": "Scanner reset successfully"}
    except Exception as e:
        logger.error("Failed to reset scanner", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to reset scanner")


@router.get("/operations", response_model=List[ScannerOperationResponse], summary="Get Operation History")
async def get_operations(
    limit: int = 50,
    session_id: str = None,
    db: AsyncSession = Depends(get_db)
):
    """
    Get scanner operation history.
    
    Args:
        limit: Maximum number of operations to return (default: 50)
        session_id: Filter by specific session ID (optional)
        
    Returns:
        List[ScannerOperationResponse]: List of scanner operations
    """
    try:
        query = select(ScannerOperation).order_by(ScannerOperation.timestamp.desc()).limit(limit)
        
        if session_id:
            query = query.where(ScannerOperation.session_id == session_id)
        
        result = await db.execute(query)
        operations = result.scalars().all()
        
        return [
            ScannerOperationResponse(
                id=op.id,
                session_id=op.session_id,
                timestamp=op.timestamp,
                operation_type=op.operation_type,
                position=Position(x=op.position_x, y=op.position_y),
                duration=op.duration,
                details=op.details
            )
            for op in operations
        ]
    except Exception as e:
        logger.error("Failed to get operations", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to retrieve operations")


@router.get("/captured-positions", response_model=List[CapturedPositionResponse], summary="Get Captured Positions")
async def get_captured_positions(
    session_id: str = None,
    db: AsyncSession = Depends(get_db)
):
    """
    Get all captured positions.
    
    Args:
        session_id: Filter by specific session ID (optional)
        
    Returns:
        List[CapturedPositionResponse]: List of captured positions
    """
    try:
        query = select(CapturedPosition).order_by(CapturedPosition.captured_at.desc())
        
        if session_id:
            query = query.where(CapturedPosition.session_id == session_id)
        
        result = await db.execute(query)
        positions = result.scalars().all()
        
        return [
            CapturedPositionResponse(
                id=pos.id,
                session_id=pos.session_id,
                position=Position(x=pos.position_x, y=pos.position_y),
                captured_at=pos.captured_at
            )
            for pos in positions
        ]
    except Exception as e:
        logger.error("Failed to get captured positions", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to retrieve captured positions")