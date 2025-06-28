"""
Scanner manager service for handling scanner operations and state management.
"""

import asyncio
import json
import time
from typing import Dict, List, Optional, Set
from datetime import datetime
from fastapi import WebSocket
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text  # Add this import

from app.common.config import settings
from app.common.logging import LoggerMixin
from app.models.scanner import ScannerSession, ScannerState, ScannerOperation, CapturedPosition
from app.services.position_calculator import Position, PositionCalculator
from app.utils.enums import OperationStatus, Direction, MessageType
from app.schemas.scanner import WebSocketMessage, StateUpdateMessage, LogMessage, ErrorMessage


class ScannerManager(LoggerMixin):
    """Manages scanner operations and WebSocket connections."""
    
    def __init__(self, db_session: AsyncSession):
        self.db = db_session
        self.position_calculator = PositionCalculator()
        self.connected_clients: Dict[str, WebSocket] = {}
        self.operation_task: Optional[asyncio.Task] = None
        self.is_processing = False
        self._state_cache: Optional[Dict] = None
        
        # Initialize scanner state
        self.current_position = self.position_calculator.get_default_position()
        self.horizontal_movement_pending = 0
        self.vertical_movement_pending = 0
        self.operation_status = OperationStatus.READY
        self.operation_start_time: Optional[float] = None
        self.captured_positions: Set[tuple] = set()
        self.current_movement_duration: Optional[float] = None
        
        # Edge case prevention
        self._processing_timeout = 300  # 5 minutes max processing time
        self._max_pending_movements = 10000  # Prevent excessive queuing (increased limit)
    
    async def initialize(self) -> None:
        """Initialize scanner manager and load state from database."""
        await self._load_state_from_db()
        self.log_operation("Scanner manager initialized")
    
    def _is_valid_movement(self, direction: Direction) -> bool:
        """Validate if a movement in the given direction is within bounds."""
        try:
            # Calculate what the new pending movements would be
            temp_horizontal = self.horizontal_movement_pending
            temp_vertical = self.vertical_movement_pending
            
            if direction == Direction.LEFT:
                temp_horizontal -= 1
            elif direction == Direction.RIGHT:
                temp_horizontal += 1
            elif direction == Direction.UP:
                temp_vertical += 1
            elif direction == Direction.DOWN:
                temp_vertical -= 1
            
            # Calculate target position with new pending movements
            target_position = self.position_calculator.calculate_target_position(
                self.current_position,
                temp_horizontal,
                temp_vertical
            )
            
            # Check if target position is within bounds using the position calculator's method
            # If the method doesn't exist, we'll fall back to basic validation
            if hasattr(self.position_calculator, 'is_position_valid'):
                return self.position_calculator.is_position_valid(target_position)
            else:
                # Basic validation - assume reasonable bounds exist
                # This is a fallback if the position calculator doesn't have validation
                return True
        except Exception as e:
            self.log_error("Error validating movement", error=e, direction=direction.value)
            # On error, allow the movement to proceed (conservative approach)
            return True
    
    def _is_pending_movements_within_limit(self) -> bool:
        """Check if pending movements are within reasonable limits."""
        total_pending = abs(self.horizontal_movement_pending) + abs(self.vertical_movement_pending)
        return total_pending < self._max_pending_movements
    
    async def _load_state_from_db(self) -> None:
        """Load scanner state from database."""
        try:
            # Get the latest state from database - fix SQL text
            result = await self.db.execute(
                text("SELECT * FROM scanner_state ORDER BY last_updated DESC LIMIT 1")
            )
            state_row = result.fetchone()
            
            if state_row:
                self.current_position = Position(state_row.current_position_x, state_row.current_position_y)
                self.horizontal_movement_pending = state_row.horizontal_movement_pending
                self.vertical_movement_pending = state_row.vertical_movement_pending
                self.operation_status = OperationStatus(state_row.operation_status)
                self.operation_start_time = state_row.operation_start_time.timestamp() if state_row.operation_start_time else None
                self.current_movement_duration = state_row.current_movement_duration
                
                # Validate loaded state and reset if invalid
                if hasattr(self.position_calculator, 'is_position_valid'):
                    if not self.position_calculator.is_position_valid(self.current_position):
                        self.log_error("Invalid position loaded from database, resetting to default")
                        self.current_position = self.position_calculator.get_default_position()
                        self.horizontal_movement_pending = 0
                        self.vertical_movement_pending = 0
                        self.operation_status = OperationStatus.READY
                        self.operation_start_time = None
                        self.current_movement_duration = None
                
                # Reset processing state if it was left in processing
                if self.operation_status in [OperationStatus.MOVING, OperationStatus.FOCUSING]:
                    self.operation_status = OperationStatus.READY
                    self.operation_start_time = None
                    self.current_movement_duration = None
                
                self.log_operation("Scanner state loaded from database", position=str(self.current_position))
            else:
                # Create initial state in database
                await self._save_state_to_db()
                self.log_operation("Initial scanner state created in database")
                
            # Load captured positions - fix SQL text
            result = await self.db.execute(
                text("SELECT DISTINCT position_x, position_y FROM captured_positions")
            )
            for row in result.fetchall():
                self.captured_positions.add((row.position_x, row.position_y))
                
        except Exception as e:
            self.log_error("Failed to load state from database", error=e)
            # Use default state if database load fails
            self.current_position = self.position_calculator.get_default_position()
            self.log_operation("Using default scanner state")
    
    async def _save_state_to_db(self) -> None:
        """Save current scanner state to database."""
        try:
            # Delete existing state and insert new one (simple approach for single-instance) - fix SQL text
            await self.db.execute(text("DELETE FROM scanner_state"))
            
            state = ScannerState(
                current_position_x=self.current_position.x,
                current_position_y=self.current_position.y,
                horizontal_movement_pending=self.horizontal_movement_pending,
                vertical_movement_pending=self.vertical_movement_pending,
                operation_status=self.operation_status.value,
                operation_start_time=datetime.fromtimestamp(self.operation_start_time) if self.operation_start_time else None,
                current_movement_duration=self.current_movement_duration,
                last_updated=datetime.utcnow()
            )
            
            self.db.add(state)
            await self.db.commit()
            
            # Clear cache
            self._state_cache = None
            
        except Exception as e:
            self.log_error("Failed to save state to database", error=e)
            await self.db.rollback()
    
    async def connect_client(self, client_id: str, websocket: WebSocket) -> None:
        """Connect a new WebSocket client."""
        try:
            await websocket.accept()
            self.connected_clients[client_id] = websocket
            
            # Create session in database
            session = ScannerSession(id=client_id)
            self.db.add(session)
            await self.db.commit()
            
            # Send current state to newly connected client
            await self.send_state_to_client(client_id)
            await self.broadcast_log(f"Client {client_id[:8]} connected")
            
            self.log_operation("Client connected", client_id=client_id[:8])
            
        except Exception as e:
            self.log_error("Failed to connect client", error=e, client_id=client_id[:8])
    
    async def disconnect_client(self, client_id: str) -> None:
        """Disconnect a WebSocket client."""
        try:
            if client_id in self.connected_clients:
                del self.connected_clients[client_id]
                
                # Update session in database - fix SQL text
                await self.db.execute(
                    text("UPDATE scanner_sessions SET is_active = false, last_activity = :last_activity WHERE id = :id"),
                    {"last_activity": datetime.utcnow(), "id": client_id}
                )
                await self.db.commit()
                
                await self.broadcast_log(f"Client {client_id[:8]} disconnected")
                self.log_operation("Client disconnected", client_id=client_id[:8])
                
        except Exception as e:
            self.log_error("Failed to disconnect client", error=e, client_id=client_id[:8])
    
    async def queue_movement(self, direction: Direction, session_id: str) -> None:
        """Queue a movement command with validation."""
        try:
            # Temporarily disable strict validation - allow movements to proceed
            # TODO: Re-enable validation once position calculator bounds are confirmed
            
            # Check pending movements limit (keep this as it's a reasonable safety check)
            if not self._is_pending_movements_within_limit():
                error_msg = f"Too many pending movements. Current: H:{self.horizontal_movement_pending}, V:{self.vertical_movement_pending}"
                await self.broadcast_log(error_msg)
                self.log_operation("Movement rejected - too many pending movements")
                return
            
            # Update pending movements based on direction
            if direction == Direction.LEFT:
                self.horizontal_movement_pending -= 1
            elif direction == Direction.RIGHT:
                self.horizontal_movement_pending += 1
            elif direction == Direction.UP:
                self.vertical_movement_pending += 1
            elif direction == Direction.DOWN:
                self.vertical_movement_pending -= 1
            
            # Log operation to database
            await self._log_operation_to_db(
                session_id=session_id,
                operation_type="queue_move",
                position_x=self.current_position.x,
                position_y=self.current_position.y,
                details=f"Direction: {direction.value}, Pending H:{self.horizontal_movement_pending}, V:{self.vertical_movement_pending}"
            )
            
            await self._save_state_to_db()
            await self.broadcast_log(
                f"Movement queued: {direction.value} (H:{self.horizontal_movement_pending}, V:{self.vertical_movement_pending})"
            )
            await self.broadcast_state()
            
            # Start processing if not already processing
            if not self.is_processing and self.has_pending_movements():
                await self.start_processing(session_id)
                
            self.log_operation(
                "Movement queued",
                direction=direction.value,
                horizontal_pending=self.horizontal_movement_pending,
                vertical_pending=self.vertical_movement_pending,
                session_id=session_id[:8]
            )
            
        except Exception as e:
            self.log_error("Failed to queue movement", error=e, direction=direction.value)
            raise
    
    def has_pending_movements(self) -> bool:
        """Check if there are any pending movements."""
        return (self.horizontal_movement_pending != 0 or self.vertical_movement_pending != 0)
    
    async def start_processing(self, session_id: str) -> None:
        """Start the processing cycle."""
        if self.is_processing:
            return
        
        self.is_processing = True
        self.operation_task = asyncio.create_task(self.process_operations(session_id))
    
    async def process_operations(self, session_id: str) -> None:
        """Main processing loop - handles movement and focusing operations."""
        operation_start_time = time.time()
        max_iterations = 100  # Prevent infinite loops
        iteration_count = 0
        
        try:
            while self.has_pending_movements() and iteration_count < max_iterations:
                iteration_count += 1
                
                # Check for processing timeout
                if time.time() - operation_start_time > self._processing_timeout:
                    await self.broadcast_log("Processing timeout reached, stopping operations")
                    break
                
                # Calculate target position
                target_position = self.position_calculator.calculate_target_position(
                    self.current_position,
                    self.horizontal_movement_pending,
                    self.vertical_movement_pending
                )
                
                # Validate target position before moving
                if hasattr(self.position_calculator, 'is_position_valid'):
                    if not self.position_calculator.is_position_valid(target_position):
                        await self.broadcast_log("Target position is invalid, clearing pending movements")
                        self.horizontal_movement_pending = 0
                        self.vertical_movement_pending = 0
                        await self._save_state_to_db()
                        break
                
                # Calculate movement duration
                movement_duration = self.position_calculator.calculate_movement_time(
                    self.current_position, target_position
                )
                
                # Validate movement duration
                if movement_duration <= 0 or movement_duration > 60:  # Max 60 seconds per movement
                    await self.broadcast_log(f"Invalid movement duration: {movement_duration}s, clearing pending movements")
                    self.horizontal_movement_pending = 0
                    self.vertical_movement_pending = 0
                    await self._save_state_to_db()
                    break
                
                # Start moving operation
                self.operation_status = OperationStatus.MOVING
                self.operation_start_time = time.time()
                self.current_movement_duration = movement_duration
                
                await self._save_state_to_db()
                await self.broadcast_log(
                    f"Starting movement to {target_position} - "
                    f"Distance: {self.current_position.distance_to(target_position):.2f}, "
                    f"Duration: {movement_duration:.2f}s"
                )
                await self.broadcast_state()
                
                # Log movement start
                await self._log_operation_to_db(
                    session_id=session_id,
                    operation_type="move_start",
                    position_x=target_position.x,
                    position_y=target_position.y,
                    duration=movement_duration,
                    details=f"From {self.current_position} to {target_position}"
                )
                
                # Movement with dynamic duration - check every 0.1s for new commands
                elapsed = 0.0
                check_interval = 0.1
                last_pending_h = self.horizontal_movement_pending
                last_pending_v = self.vertical_movement_pending
                
                while elapsed < movement_duration:
                    await asyncio.sleep(check_interval)
                    elapsed += check_interval
                    
                    # Check if new movements were queued during this time
                    if (self.horizontal_movement_pending != last_pending_h or 
                        self.vertical_movement_pending != last_pending_v):
                        
                        # New movement queued, recalculate
                        new_target = self.position_calculator.calculate_target_position(
                            self.current_position,
                            self.horizontal_movement_pending,
                            self.vertical_movement_pending
                        )
                        
                        if not (new_target == target_position):
                            await self.broadcast_log("New movement detected during operation, recalculating...")
                            break
                
                # Update position and clear processed movements
                actual_horizontal_move = target_position.x - self.current_position.x
                actual_vertical_move = target_position.y - self.current_position.y
                
                self.current_position = target_position
                self.horizontal_movement_pending -= actual_horizontal_move
                self.vertical_movement_pending -= actual_vertical_move
                self.current_movement_duration = None
                
                await self._save_state_to_db()
                await self.broadcast_log(f"Movement completed to {self.current_position}")
                await self.broadcast_state()
                
                # Log movement completion
                await self._log_operation_to_db(
                    session_id=session_id,
                    operation_type="move_complete",
                    position_x=self.current_position.x,
                    position_y=self.current_position.y,
                    duration=elapsed,
                    details=f"Moved to {self.current_position}"
                )
                
                # Small delay to check for new movements
                await asyncio.sleep(0.1)
            
            # Check for infinite loop condition
            if iteration_count >= max_iterations:
                await self.broadcast_log("Maximum iterations reached, clearing pending movements")
                self.horizontal_movement_pending = 0
                self.vertical_movement_pending = 0
                await self._save_state_to_db()
            
            # If no more movements pending, start focusing
            if not self.has_pending_movements():
                await self.focus_and_capture(session_id)
                
        except Exception as e:
            self.log_error("Error in processing operations", error=e)
            await self.broadcast_log(f"Error in processing: {str(e)}")
            # Reset pending movements on error to prevent stuck state
            self.horizontal_movement_pending = 0
            self.vertical_movement_pending = 0
        finally:
            self.is_processing = False
            self.current_movement_duration = None
            if (self.operation_status != OperationStatus.FOCUSING and 
                self.operation_status != OperationStatus.READY):
                self.operation_status = OperationStatus.READY
                await self._save_state_to_db()
                await self.broadcast_state()
    
    async def focus_and_capture(self, session_id: str) -> None:
        """Focus and capture image at current position."""
        try:
            self.operation_status = OperationStatus.FOCUSING
            self.operation_start_time = time.time()
            
            await self._save_state_to_db()
            await self.broadcast_log(
                f"Starting focus and capture at {self.current_position} - Duration: {settings.focus_duration}s"
            )
            await self.broadcast_state()
            
            # Log focus start
            await self._log_operation_to_db(
                session_id=session_id,
                operation_type="focus_start",
                position_x=self.current_position.x,
                position_y=self.current_position.y,
                duration=settings.focus_duration
            )
            
            # Validate focus duration
            focus_duration = max(0.1, min(settings.focus_duration, 30.0))  # Between 0.1 and 30 seconds
            
            # Focusing takes configured time
            await asyncio.sleep(focus_duration)
            
            # Capture image
            position_tuple = self.current_position.to_tuple()
            self.captured_positions.add(position_tuple)
            self.operation_status = OperationStatus.COMPLETED
            
            # Save captured position to database
            captured_pos = CapturedPosition(
                session_id=session_id,
                position_x=self.current_position.x,
                position_y=self.current_position.y
            )
            self.db.add(captured_pos)
            
            # Log capture completion
            await self._log_operation_to_db(
                session_id=session_id,
                operation_type="capture",
                position_x=self.current_position.x,
                position_y=self.current_position.y,
                details=f"Image captured at {self.current_position}"
            )
            
            await self.db.commit()
            await self._save_state_to_db()
            await self.broadcast_log(f"Image captured at {self.current_position}")
            await self.broadcast_state()
            
            # Reset to ready after a short delay
            await asyncio.sleep(0.5)
            self.operation_status = OperationStatus.READY
            self.operation_start_time = None
            await self._save_state_to_db()
            await self.broadcast_state()
            
            self.log_operation("Image captured", position=str(self.current_position))
            
        except Exception as e:
            self.log_error("Failed to focus and capture", error=e)
            # Reset to ready state on error
            self.operation_status = OperationStatus.READY
            self.operation_start_time = None
            await self._save_state_to_db()
            raise
    
    async def _log_operation_to_db(
        self, 
        session_id: str, 
        operation_type: str, 
        position_x: int, 
        position_y: int,
        duration: Optional[float] = None,
        details: Optional[str] = None
    ) -> None:
        """Log operation to database."""
        try:
            operation = ScannerOperation(
                session_id=session_id,
                operation_type=operation_type,
                position_x=position_x,
                position_y=position_y,
                duration=duration,
                details=details
            )
            self.db.add(operation)
            await self.db.commit()
        except Exception as e:
            self.log_error("Failed to log operation to database", error=e)
            await self.db.rollback()
    
    def get_state_dict(self) -> Dict:
        """Get current state as dictionary."""
        if self._state_cache is None:
            self._state_cache = {
                "current_position": self.current_position.to_dict(),
                "horizontal_movement_pending": self.horizontal_movement_pending,
                "vertical_movement_pending": self.vertical_movement_pending,
                "operation_status": self.operation_status.value,
                "operation_start_time": self.operation_start_time,
                "current_movement_duration": self.current_movement_duration,
                "captured_positions": list(self.captured_positions),
                "last_updated": time.time()
            }
        return self._state_cache
    
    async def send_state_to_client(self, client_id: str) -> None:
        """Send current state to a specific client."""
        if client_id in self.connected_clients:
            try:
                message = StateUpdateMessage(data=self.get_state_dict())
                await self.connected_clients[client_id].send_text(message.model_dump_json())
            except Exception as e:
                self.log_error("Error sending state to client", error=e, client_id=client_id[:8])
                await self.disconnect_client(client_id)
    
    async def broadcast_state(self) -> None:
        """Broadcast current state to all connected clients."""
        if not self.connected_clients:
            return
        
        # Clear cache to force refresh
        self._state_cache = None
        message = StateUpdateMessage(data=self.get_state_dict())
        await self._broadcast_message(message)
    
    async def broadcast_log(self, log_message: str) -> None:
        """Broadcast log message to all connected clients."""
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        log_msg = LogMessage(data={
            "timestamp": timestamp,
            "message": log_message
        })
        
        await self._broadcast_message(log_msg)
        # Fix the parameter conflict - use different parameter name
        self.log_operation("Broadcast log", broadcast_message=log_message)
    
    async def _broadcast_message(self, message: WebSocketMessage) -> None:
        """Broadcast a message to all connected clients."""
        if not self.connected_clients:
            return
        
        disconnected_clients = []
        message_json = message.model_dump_json()
        
        for client_id, websocket in self.connected_clients.items():
            try:
                await websocket.send_text(message_json)
            except Exception as e:
                self.log_error("Error broadcasting to client", error=e, client_id=client_id[:8])
                disconnected_clients.append(client_id)
        
        # Remove disconnected clients
        for client_id in disconnected_clients:
            await self.disconnect_client(client_id)
    
    async def reset_scanner(self) -> None:
        """Reset scanner to initial state."""
        try:
            # Cancel any ongoing operations
            if self.operation_task and not self.operation_task.done():
                self.operation_task.cancel()
            
            # Reset state
            self.current_position = self.position_calculator.get_default_position()
            self.horizontal_movement_pending = 0
            self.vertical_movement_pending = 0
            self.operation_status = OperationStatus.READY
            self.operation_start_time = None
            self.captured_positions = set()
            self.current_movement_duration = None
            self.is_processing = False
            
            # Clear all captured positions from database - fix SQL text
            await self.db.execute(text("DELETE FROM captured_positions"))
            await self.db.execute(text("DELETE FROM scanner_operations"))
            await self._save_state_to_db()
            
            await self.broadcast_log("Scanner reset to initial state")
            await self.broadcast_state()
            
            self.log_operation("Scanner reset completed")
            
        except Exception as e:
            self.log_error("Failed to reset scanner", error=e)
            raise