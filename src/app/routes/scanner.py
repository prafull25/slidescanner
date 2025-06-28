"""
REST API endpoints for scanner operations - Updated for multi-user support.
"""

from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text

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
from app.routes.websocket import get_scanner_manager, _validate_user_id, _get_scanner_manager_internal

logger = get_logger(__name__)
router = APIRouter(prefix="/api/v1", tags=["scanner"])


@router.get("/", summary="API Root")
async def root():
    """Root endpoint for the scanner API."""
    return {
        "message": "Morphle Scanner API v1.0 - Multi-User",
        "status": "running",
        "timestamp": datetime.utcnow().isoformat(),
        "features": ["multi-user", "websocket", "real-time"]
    }


@router.get("/users/{user_id}/state", response_model=ScannerStateResponse, summary="Get Scanner State for User")
async def get_scanner_state(
    user_id: str = Path(..., description="User ID (4-6 alphanumeric characters)"),
    db: AsyncSession = Depends(get_db)
):
    """
    Get the current state of the scanner for a specific user.
    
    Args:
        user_id: User identifier (4-6 characters, alphanumeric)
        
    Returns:
        ScannerStateResponse: Current scanner state including position, 
                             pending movements, and operation status.
    """
    if not _validate_user_id(user_id):
        raise HTTPException(status_code=400, detail="Invalid user_id format")
    
    try:
        scanner_manager = await _get_scanner_manager_internal(db, user_id)
        state_dict = scanner_manager.get_state_dict()
        
        return ScannerStateResponse(
            current_position=Position(**state_dict["current_position"]),
            horizontal_movement_pending=state_dict["horizontal_movement_pending"],
            vertical_movement_pending=state_dict["vertical_movement_pending"],
            operation_status=state_dict["operation_status"],
            operation_start_time=datetime.fromtimestamp(state_dict["operation_start_time"]) if state_dict["operation_start_time"] else None,
            current_movement_duration=state_dict["current_movement_duration"],
            captured_positions=state_dict["captured_positions"],
            last_updated=datetime.fromtimestamp(state_dict["last_updated"]),
            user_id=user_id,
            is_processing=state_dict.get("is_processing", False)
        )
    except Exception as e:
        logger.error("Failed to get scanner state", error=str(e), user_id=user_id)
        raise HTTPException(status_code=500, detail="Failed to retrieve scanner state")


@router.get("/users/{user_id}/health", response_model=HealthResponse, summary="Health Check for User Scanner")
async def health_check(
    user_id: str = Path(..., description="User ID (4-6 alphanumeric characters)"),
    db: AsyncSession = Depends(get_db)
):
    """
    Health check endpoint for monitoring scanner service for a specific user.
    
    Args:
        user_id: User identifier (4-6 characters, alphanumeric)
        
    Returns:
        HealthResponse: Service health status and basic metrics.
    """
    if not _validate_user_id(user_id):
        raise HTTPException(status_code=400, detail="Invalid user_id format")
    
    try:
        scanner_manager = await _get_scanner_manager_internal(db, user_id)
        state_dict = scanner_manager.get_state_dict()
        
        return HealthResponse(
            status="healthy",
            timestamp=datetime.utcnow(),
            connected_clients=len(scanner_manager.connected_clients),
            current_position=Position(**state_dict["current_position"]),
            operation_status=state_dict["operation_status"],
            user_id=user_id,
            is_processing=state_dict.get("is_processing", False)
        )
    except Exception as e:
        logger.error("Health check failed", error=str(e), user_id=user_id)
        raise HTTPException(status_code=503, detail="Service unhealthy")


@router.post("/users/{user_id}/reset", summary="Reset Scanner for User")
async def reset_scanner(
    user_id: str = Path(..., description="User ID (4-6 alphanumeric characters)"),
    db: AsyncSession = Depends(get_db)
):
    """
    Reset the scanner to its initial state for a specific user.
    
    This will:
    - Cancel any ongoing operations
    - Reset position to default
    - Clear all pending movements
    - Clear captured positions
    - Reset operation status to READY
    
    Args:
        user_id: User identifier (4-6 characters, alphanumeric)
    
    Returns:
        dict: Success message
    """
    if not _validate_user_id(user_id):
        raise HTTPException(status_code=400, detail="Invalid user_id format")
    
    try:
        scanner_manager = await _get_scanner_manager_internal(db, user_id)
        await scanner_manager.reset_scanner()
        logger.info("Scanner reset successfully", user_id=user_id)
        return {
            "status": "success", 
            "message": f"Scanner reset successfully for user {user_id}",
            "user_id": user_id,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error("Failed to reset scanner", error=str(e), user_id=user_id)
        raise HTTPException(status_code=500, detail="Failed to reset scanner")


@router.get("/users/{user_id}/operations", response_model=List[ScannerOperationResponse], summary="Get Operation History for User")
async def get_operations(
    user_id: str = Path(..., description="User ID (4-6 alphanumeric characters)"),
    limit: int = Query(50, description="Maximum number of operations to return"),
    session_id: Optional[str] = Query(None, description="Filter by specific session ID"),
    operation_type: Optional[str] = Query(None, description="Filter by operation type"),
    db: AsyncSession = Depends(get_db)
):
    """
    Get scanner operation history for a specific user.
    
    Args:
        user_id: User identifier (4-6 characters, alphanumeric)
        limit: Maximum number of operations to return (default: 50)
        session_id: Filter by specific session ID (optional)
        operation_type: Filter by operation type (optional)
        
    Returns:
        List[ScannerOperationResponse]: List of scanner operations
    """
    if not _validate_user_id(user_id):
        raise HTTPException(status_code=400, detail="Invalid user_id format")
    
    try:
        query = select(ScannerOperation).where(
            ScannerOperation.user_id == user_id
        ).order_by(ScannerOperation.timestamp.desc()).limit(limit)
        
        if session_id:
            query = query.where(ScannerOperation.session_id == session_id)
        
        if operation_type:
            query = query.where(ScannerOperation.operation_type == operation_type)
        
        result = await db.execute(query)
        operations = result.scalars().all()
        
        return [
            ScannerOperationResponse(
                id=op.id,
                session_id=op.session_id,
                user_id=op.user_id,
                timestamp=op.timestamp,
                operation_type=op.operation_type,
                position=Position(x=op.position_x, y=op.position_y),
                duration=op.duration,
                details=op.details
            )
            for op in operations
        ]
    except Exception as e:
        logger.error("Failed to get operations", error=str(e), user_id=user_id)
        raise HTTPException(status_code=500, detail="Failed to retrieve operations")


@router.get("/users/{user_id}/captured-positions", response_model=List[CapturedPositionResponse], summary="Get Captured Positions for User")
async def get_captured_positions(
    user_id: str = Path(..., description="User ID (4-6 alphanumeric characters)"),
    session_id: Optional[str] = Query(None, description="Filter by specific session ID"),
    limit: int = Query(100, description="Maximum number of positions to return"),
    db: AsyncSession = Depends(get_db)
):
    """
    Get all captured positions for a specific user.
    
    Args:
        user_id: User identifier (4-6 characters, alphanumeric)
        session_id: Filter by specific session ID (optional)
        limit: Maximum number of positions to return (default: 100)
        
    Returns:
        List[CapturedPositionResponse]: List of captured positions
    """
    if not _validate_user_id(user_id):
        raise HTTPException(status_code=400, detail="Invalid user_id format")
    
    try:
        query = select(CapturedPosition).where(
            CapturedPosition.user_id == user_id
        ).order_by(CapturedPosition.captured_at.desc()).limit(limit)
        
        if session_id:
            query = query.where(CapturedPosition.session_id == session_id)
        
        result = await db.execute(query)
        positions = result.scalars().all()
        
        return [
            CapturedPositionResponse(
                id=pos.id,
                session_id=pos.session_id,
                user_id=pos.user_id,
                position=Position(x=pos.position_x, y=pos.position_y),
                captured_at=pos.captured_at
            )
            for pos in positions
        ]
    except Exception as e:
        logger.error("Failed to get captured positions", error=str(e), user_id=user_id)
        raise HTTPException(status_code=500, detail="Failed to retrieve captured positions")


@router.get("/users", summary="Get Active Users")
async def get_active_users(db: AsyncSession = Depends(get_db)):
    """
    Get list of users who have active scanner sessions or recent activity.
    
    Returns:
        dict: List of active users with their status
    """
    try:
        # Get users with active sessions
        result = await db.execute(
            text("""
                SELECT DISTINCT user_id, 
                       COUNT(*) as session_count,
                       MAX(last_activity) as last_activity
                FROM scanner_sessions 
                WHERE user_id IS NOT NULL 
                  AND last_activity > NOW() - INTERVAL '1 hour'
                GROUP BY user_id
                ORDER BY last_activity DESC
            """)
        )
        
        active_users = []
        for row in result.fetchall():
            active_users.append({
                "user_id": row.user_id,
                "session_count": row.session_count,
                "last_activity": row.last_activity.isoformat() if row.last_activity else None
            })
        
        # Get users with recent captured positions
        result = await db.execute(
            text("""
                SELECT DISTINCT user_id,
                       COUNT(*) as captured_count,
                       MAX(captured_at) as last_capture
                FROM captured_positions
                WHERE user_id IS NOT NULL
                  AND captured_at > NOW() - INTERVAL '24 hours'
                GROUP BY user_id
                ORDER BY last_capture DESC
            """)
        )
        
        recent_captures = {}
        for row in result.fetchall():
            recent_captures[row.user_id] = {
                "captured_count": row.captured_count,
                "last_capture": row.last_capture.isoformat() if row.last_capture else None
            }
        
        # Combine data
        for user in active_users:
            if user["user_id"] in recent_captures:
                user.update(recent_captures[user["user_id"]])
        
        return {
            "active_users": active_users,
            "total_active_users": len(active_users),
            "timestamp": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logger.error("Failed to get active users", error=str(e))
        raise HTTPException(status_code=500, detail="Failed to retrieve active users")


@router.get("/users/{user_id}/stats", summary="Get User Statistics")
async def get_user_stats(
    user_id: str = Path(..., description="User ID (4-6 alphanumeric characters)"),
    db: AsyncSession = Depends(get_db)
):
    """
    Get statistics for a specific user.
    
    Args:
        user_id: User identifier (4-6 characters, alphanumeric)
        
    Returns:
        dict: User statistics including operation counts, positions captured, etc.
    """
    if not _validate_user_id(user_id):
        raise HTTPException(status_code=400, detail="Invalid user_id format")
    
    try:
        # Get operation statistics
        result = await db.execute(
            text("""
                SELECT operation_type, COUNT(*) as count
                FROM scanner_operations
                WHERE user_id = :user_id
                GROUP BY operation_type
            """),
            {"user_id": user_id}
        )
        
        operation_stats = {row.operation_type: row.count for row in result.fetchall()}
        
        # Get captured positions count
        result = await db.execute(
            text("SELECT COUNT(*) as count FROM captured_positions WHERE user_id = :user_id"),
            {"user_id": user_id}
        )
        captured_count = result.fetchone().count
        
        # Get session statistics
        result = await db.execute(
            text("""
                SELECT COUNT(*) as total_sessions,
                       COUNT(CASE WHEN is_active = true THEN 1 END) as active_sessions,
                       MIN(created_at) as first_session,
                       MAX(last_activity) as last_activity
                FROM scanner_sessions
                WHERE user_id = :user_id
            """),
            {"user_id": user_id}
        )
        
        session_row = result.fetchone()
        
        return {
            "user_id": user_id,
            "operation_statistics": operation_stats,
            "total_operations": sum(operation_stats.values()),
            "captured_positions": captured_count,
            "session_statistics": {
                "total_sessions": session_row.total_sessions,
                "active_sessions": session_row.active_sessions,
                "first_session": session_row.first_session.isoformat() if session_row.first_session else None,
                "last_activity": session_row.last_activity.isoformat() if session_row.last_activity else None
            },
            "timestamp": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logger.error("Failed to get user stats", error=str(e), user_id=user_id)
        raise HTTPException(status_code=500, detail="Failed to retrieve user statistics")


# Legacy endpoints for backward compatibility (without user_id)
@router.get("/state", response_model=ScannerStateResponse, summary="Get Scanner State (Legacy)")
async def get_scanner_state_legacy(
    scanner_manager: ScannerManager = Depends(get_scanner_manager)
):
    """Legacy endpoint - use /users/{user_id}/state instead."""
    return await get_scanner_state(user_id=scanner_manager.user_id, db=scanner_manager.db)


@router.get("/health", response_model=HealthResponse, summary="Health Check (Legacy)")
async def health_check_legacy(
    scanner_manager: ScannerManager = Depends(get_scanner_manager)
):
    """Legacy endpoint - use /users/{user_id}/health instead."""
    return await health_check(user_id=scanner_manager.user_id, db=scanner_manager.db)


@router.post("/reset", summary="Reset Scanner (Legacy)")
async def reset_scanner_legacy(
    scanner_manager: ScannerManager = Depends(get_scanner_manager)
):
    """Legacy endpoint - use /users/{user_id}/reset instead."""
    return await reset_scanner(user_id=scanner_manager.user_id, db=scanner_manager.db)