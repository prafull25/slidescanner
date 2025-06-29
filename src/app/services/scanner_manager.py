"""
Scanner manager service for handling scanner operations and state management.
Fixed version with improved database concurrency handling.
"""

import asyncio
import json
import time
from typing import Dict, List, Optional, Set
from datetime import datetime
from fastapi import WebSocket
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from contextlib import asynccontextmanager

from app.common.config import settings
from app.common.logging import LoggerMixin
from app.models.scanner import ScannerSession, ScannerState, ScannerOperation, CapturedPosition
from app.services.position_calculator import Position, PositionCalculator
from app.utils.enums import OperationStatus, Direction, MessageType
from app.schemas.scanner import WebSocketMessage, StateUpdateMessage, LogMessage, ErrorMessage


class ScannerManager(LoggerMixin):
    """Manages scanner operations and WebSocket connections."""
    
    def __init__(self, db_session: AsyncSession, user_id: str):
        self.db = db_session
        self.user_id = user_id  
        self.position_calculator = PositionCalculator()
        self.connected_clients: Dict[str, WebSocket] = {}
        self.operation_task: Optional[asyncio.Task] = None
        self.is_processing = False
        self._state_cache: Optional[Dict] = None
        
        # Enhanced concurrency control
        self._db_lock = asyncio.Lock()
        self._state_lock = asyncio.Lock()  # Separate lock for state operations
        self._operation_lock = asyncio.Lock()  # Lock for operation processing
        self._db_operation_semaphore = asyncio.Semaphore(1)  # Limit concurrent DB operations
        
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
        self._max_pending_movements = 10000  # Prevent excessive queuing
        
        # Connection health tracking
        self._last_db_operation = time.time()
        self._db_operation_timeout = 30  # 30 seconds timeout for DB operations
    
    @asynccontextmanager
    async def _db_transaction(self, timeout: Optional[float] = None):
        """Enhanced context manager for safe database transactions with timeout."""
        timeout = timeout or self._db_operation_timeout
        
        async with self._db_operation_semaphore:
            async with self._db_lock:
                try:
                    # Check if we need to refresh the connection
                    if time.time() - self._last_db_operation > 300:  # 5 minutes
                        await self._refresh_db_connection()
                    
                    # Use asyncio.wait_for to add timeout protection
                    async with asyncio.timeout(timeout):
                        yield
                        await self.db.commit()
                        self._last_db_operation = time.time()
                        
                except asyncio.TimeoutError:
                    self.log_error("Database operation timed out", timeout=timeout)
                    try:
                        await self.db.rollback()
                    except Exception as rollback_error:
                        self.log_error("Failed to rollback after timeout", error=rollback_error)
                    raise Exception(f"Database operation timed out after {timeout}s")
                    
                except Exception as e:
                    self.log_error("Database transaction error", error=e)
                    try:
                        await self.db.rollback()
                    except Exception as rollback_error:
                        self.log_error("Failed to rollback transaction", error=rollback_error)
                    raise e
    
    async def _refresh_db_connection(self):
        """Refresh database connection if needed."""
        try:
            # Simple query to check connection health
            await self.db.execute(text("SELECT 1"))
            await self.db.commit()
        except Exception as e:
            self.log_error("Database connection refresh failed", error=e)
            # Let the connection pool handle reconnection
    
    async def _safe_db_operation(self, operation_func, *args, **kwargs):
        """Wrapper for safe database operations with retry logic."""
        max_retries = 3
        base_delay = 0.1
        
        for attempt in range(max_retries):
            try:
                return await operation_func(*args, **kwargs)
            except Exception as e:
                if "concurrent operations" in str(e).lower() or "provisioning" in str(e).lower():
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt)  # Exponential backoff
                        self.log_error(f"DB operation failed, retrying in {delay}s", error=e, attempt=attempt + 1)
                        await asyncio.sleep(delay)
                        continue
                raise e
    
    async def initialize(self) -> None:
        """Initialize scanner manager and load state from database."""
        async with self._state_lock:
            await self._safe_db_operation(self._load_state_from_db)
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
            
            # Check if target position is within bounds
            return self.position_calculator.is_valid_position(target_position)
            
        except Exception as e:
            self.log_error("Error validating movement", error=e, direction=direction.value)
            return False
    
    def _is_pending_movements_within_limit(self) -> bool:
        """Check if pending movements are within reasonable limits."""
        total_pending = abs(self.horizontal_movement_pending) + abs(self.vertical_movement_pending)
        return total_pending < self._max_pending_movements
    
    async def _load_state_from_db(self) -> None:
        """Load scanner state from database for specific user."""
        try:
            async with self._db_transaction():
                # Get the latest state from database for this user
                result = await self.db.execute(
                    text("SELECT * FROM scanner_state WHERE user_id = :user_id"),
                    {"user_id": self.user_id}
                )
                state_row = result.fetchone()
                
                if state_row:
                    # Load position and validate it
                    loaded_position = Position(state_row.current_position_x, state_row.current_position_y)
                    
                    # Validate loaded position and reset if invalid
                    if not self.position_calculator.is_valid_position(loaded_position):
                        self.log_error("Invalid position loaded from database, resetting to default", 
                                     loaded_position=str(loaded_position))
                        self.current_position =  loaded_position.clamp_to_bounds()
                        self.horizontal_movement_pending = 0
                        self.vertical_movement_pending = 0
                        self.operation_status = OperationStatus.READY
                        self.operation_start_time = None
                        self.current_movement_duration = None
                    else:
                        self.current_position = loaded_position
                        self.horizontal_movement_pending = state_row.horizontal_movement_pending
                        self.vertical_movement_pending = state_row.vertical_movement_pending
                        self.operation_status = OperationStatus(state_row.operation_status)
                        self.operation_start_time = state_row.operation_start_time.timestamp() if state_row.operation_start_time else None
                        self.current_movement_duration = state_row.current_movement_duration
                        
                        # Validate that pending movements don't take us out of bounds
                        target_position = self.position_calculator.calculate_target_position(
                            self.current_position,
                            self.horizontal_movement_pending,
                            self.vertical_movement_pending
                        )
                        
                        if not self.position_calculator.is_valid_position(target_position):
                            self.log_error("Pending movements would go out of bounds, clearing them",
                                         current_position=str(self.current_position),
                                         target_position=str(target_position))
                            self.horizontal_movement_pending = 0
                            self.vertical_movement_pending = 0
                    
                    # Reset processing state if it was left in processing
                    if self.operation_status in [OperationStatus.MOVING, OperationStatus.FOCUSING]:
                        self.operation_status = OperationStatus.READY
                        self.operation_start_time = None
                        self.current_movement_duration = None
                    
                    self.log_operation("Scanner state loaded from database", position=str(self.current_position), user_id=self.user_id)
                else:
                    # Create initial state in database
                    await self._save_state_to_db_internal()
                    self.log_operation("Initial scanner state created in database", user_id=self.user_id)
                    
                # Load captured positions for this user
                result = await self.db.execute(
                    text("SELECT DISTINCT position_x, position_y FROM captured_positions WHERE user_id = :user_id"),
                    {"user_id": self.user_id}
                )
                for row in result.fetchall():
                    self.captured_positions.add((row.position_x, row.position_y))
                    
        except Exception as e:
            self.log_error("Failed to load state from database", error=e, user_id=self.user_id)
            # Use default state if database load fails
            self.current_position = self.position_calculator.get_default_position()
            self.log_operation("Using default scanner state", user_id=self.user_id)
    
    async def _save_state_to_db_internal(self) -> None:
        """Internal method to save state without transaction wrapper."""
        # Delete existing state for this user and insert new one
        await self.db.execute(
            text("DELETE FROM scanner_state WHERE user_id = :user_id"),
            {"user_id": self.user_id}
        )
        self.current_position.clamp_to_bounds()
        state = ScannerState(
            user_id=self.user_id,
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
        
        # Clear cache
        self._state_cache = None

    async def _save_state_to_db(self) -> None:
        """Save current scanner state to database for specific user."""
        try:
            await self._safe_db_operation(self._save_state_to_db_with_transaction)
        except Exception as e:
            self.log_error("Failed to save state to database", error=e, user_id=self.user_id)
    
    async def _save_state_to_db_with_transaction(self) -> None:
        """Save state with transaction wrapper."""
        async with self._db_transaction():
            await self._save_state_to_db_internal()
    
    async def connect_client(self, client_id: str, websocket: WebSocket) -> None:
        """Connect a new WebSocket client."""
        try:
            await websocket.accept()
            self.connected_clients[client_id] = websocket
            
            # Create session in database with user_id
            await self._safe_db_operation(self._create_client_session, client_id)
            
            # Send current state to newly connected client
            await self.send_state_to_client(client_id)
            await self.broadcast_log(f"Client {client_id[:8]} connected")
            
            self.log_operation("Client connected", client_id=client_id[:8], user_id=self.user_id)
            
        except Exception as e:
            self.log_error("Failed to connect client", error=e, client_id=client_id[:8], user_id=self.user_id)
    
    async def _create_client_session(self, client_id: str):
        """Create client session in database."""
        async with self._db_transaction():
            session = ScannerSession(id=client_id, user_id=self.user_id)
            self.db.add(session)

    async def disconnect_client(self, client_id: str) -> None:
        """Disconnect a WebSocket client."""
        try:
            if client_id in self.connected_clients:
                del self.connected_clients[client_id]
                
                # Update session in database
                await self._safe_db_operation(self._update_client_session, client_id)
                
                await self.broadcast_log(f"Client {client_id[:8]} disconnected")
                self.log_operation("Client disconnected", client_id=client_id[:8], user_id=self.user_id)
                
        except Exception as e:
            self.log_error("Failed to disconnect client", error=e, client_id=client_id[:8], user_id=self.user_id)
    
    async def _update_client_session(self, client_id: str):
        """Update client session in database."""
        async with self._db_transaction():
            await self.db.execute(
                text("UPDATE scanner_sessions SET is_active = false, last_activity = :last_activity WHERE id = :id AND user_id = :user_id"),
                {"last_activity": datetime.utcnow(), "id": client_id, "user_id": self.user_id}
            )
    
    async def queue_movement(self, direction: Direction, session_id: str) -> None:
        """Queue a movement command with validation."""
        async with self._state_lock:  # Prevent concurrent state modifications
            try:
                # Validate movement is within bounds BEFORE queuing
                if not self._is_valid_movement(direction):
                    error_msg = f"Movement {direction.value} would go out of bounds. Current position: {self.current_position}, Pending: H:{self.horizontal_movement_pending}, V:{self.vertical_movement_pending}"
                    await self.broadcast_log(error_msg)
                    self.log_operation("Movement rejected - would go out of bounds", 
                                     direction=direction.value,
                                     current_position=str(self.current_position))
                    return
                
                if not self._is_pending_movements_within_limit():
                    error_msg = f"Too many pending movements. Current: H:{self.horizontal_movement_pending}, V:{self.vertical_movement_pending}"
                    await self.broadcast_log(error_msg)
                    self.log_operation("Movement rejected - too many pending movements")
                    return
                
                # Update pending movements based on direction
                if direction == Direction.LEFT:
                    self.horizontal_movement_pending -=1
                elif direction == Direction.RIGHT:
                    self.horizontal_movement_pending += 1
                elif direction == Direction.UP:
                    self.vertical_movement_pending += 1
                elif direction == Direction.DOWN:
                    self.vertical_movement_pending -=1
                
                # Log operation to database and save state
                await self._safe_db_operation(self._log_and_save_movement, direction, session_id)
                
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
    
    async def _log_and_save_movement(self, direction: Direction, session_id: str):
        """Log operation and save state in a single transaction."""
        async with self._db_transaction():
            # Log operation
            self.current_position.clamp_to_bounds()
            operation = ScannerOperation(
                session_id=session_id,
                user_id=self.user_id,
                operation_type="queue_move",
                position_x=self.current_position.x,
                position_y=self.current_position.y,
                details=f"Direction: {direction.value}, Pending H:{self.horizontal_movement_pending}, V:{self.vertical_movement_pending}"
            )
            self.db.add(operation)
            
            # Save state
            await self._save_state_to_db_internal()
    
    def has_pending_movements(self) -> bool:
        """Check if there are any pending movements."""
        return (self.horizontal_movement_pending != 0 or self.vertical_movement_pending != 0)
    
    async def start_processing(self, session_id: str) -> None:
        """Start the processing cycle."""
        async with self._operation_lock:
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
                
                movement_duration = self.position_calculator.calculate_movement_time(
                    self.current_position, target_position
                )
                
                # Validate movement duration
                if movement_duration <= 0 or movement_duration > 60:  # Max 60 seconds per movement
                    await self.broadcast_log(f"Invalid movement duration: {movement_duration}s, clearing pending movements")
                    async with self._state_lock:
                        self.horizontal_movement_pending = 0
                        self.vertical_movement_pending = 0
                        await self._save_state_to_db()
                    break
                
                # Start moving operation
                async with self._state_lock:
                    self.operation_status = OperationStatus.MOVING
                    self.operation_start_time = time.time()
                    self.current_movement_duration = movement_duration
                
                # Save state and log operation
                await self._safe_db_operation(self._log_movement_start, session_id, target_position, movement_duration)
                
                await self.broadcast_log(
                    f"Starting movement to {target_position} - "
                    f"Distance: {self.current_position.distance_to(target_position):.2f}, "
                    f"Duration: {movement_duration:.2f}s"
                )
                # await self.broadcast_state()
                
                # Movement with dynamic duration - check every 0.1s for new commands
                elapsed = 0.0
                check_interval = 0.1
                
                async with self._state_lock:
                    last_pending_h = self.horizontal_movement_pending
                    last_pending_v = self.vertical_movement_pending
                    self.horizontal_movement_pending = 0
                    self.vertical_movement_pending = 0
                
                last_pending_distance = abs(last_pending_h) + abs(last_pending_v)
                per_slide_time = movement_duration/last_pending_distance if last_pending_distance > 0 else movement_duration

                flag_break = False
                while elapsed < movement_duration:
                    await asyncio.sleep(check_interval)
                    elapsed += check_interval
                    blocks_movable = int(elapsed / per_slide_time) if per_slide_time > 0 else 0

                    # Check if new movements were queued during this time
                    async with self._state_lock:
                        has_new_movements = (self.horizontal_movement_pending != 0 or 
                                           self.vertical_movement_pending != 0)
                    
                    if has_new_movements:
                        if blocks_movable >= 1:
                            if last_pending_h != 0 and blocks_movable >= 1:
                                # Determine horizontal movement direction and amount
                                h_direction = 1 if last_pending_h > 0 else -1
                                h_moves = min(abs(last_pending_h), blocks_movable)
                                
                                # Update current position for horizontal movement
                                async with self._state_lock:
                                    new_x = self.current_position.x + (h_moves * h_direction)
                                    self.current_position = Position(new_x, self.current_position.y).clamp_to_bounds()
                                
                                # Update pending horizontal movement
                                last_pending_h -= (h_moves * h_direction)
                                blocks_movable -= h_moves
                                
                                await self.broadcast_log(f"Moved {h_moves} blocks horizontally, position: {self.current_position}")
                            
                            if last_pending_v != 0 and blocks_movable >= 1:
                                # Determine vertical movement direction and amount
                                v_direction = 1 if last_pending_v > 0 else -1
                                v_moves = min(abs(last_pending_v), blocks_movable)
                                
                                # Update current position for vertical movement
                                async with self._state_lock:
                                    new_y = self.current_position.y + (v_moves * v_direction)
                                    self.current_position = Position(self.current_position.x, new_y).clamp_to_bounds()
                                
                                # Update pending temp vertical movement
                                last_pending_v -= (v_moves * v_direction)
                                
                                await self.broadcast_log(f"Moved {v_moves} blocks vertically, position: {self.current_position}")
                        flag_break = True           
                        break
                
                if not flag_break:
                    async with self._state_lock:
                        self.current_position = target_position.clamp_to_bounds()
                    last_pending_h = 0
                    last_pending_v = 0
                
                async with self._state_lock:
                    self.horizontal_movement_pending += last_pending_h
                    self.vertical_movement_pending += last_pending_v
                    self.current_movement_duration = None
                
                # Save state and log completion
                await self._safe_db_operation(self._log_movement_complete, session_id, elapsed)
                
                await self.broadcast_log(f"Movement completed to {self.current_position}")
                
                # Small delay to check for new movements
                await asyncio.sleep(0.1)
            
            # Check for infinite loop condition
            if iteration_count >= max_iterations:
                await self.broadcast_log("Maximum iterations reached, clearing pending movements")
                async with self._state_lock:
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
            async with self._state_lock:
                self.horizontal_movement_pending = 0
                self.vertical_movement_pending = 0
        finally:
            async with self._state_lock:
                self.is_processing = False
                self.current_movement_duration = None
                if (self.operation_status != OperationStatus.FOCUSING and 
                    self.operation_status != OperationStatus.READY):
                    self.operation_status = OperationStatus.READY
                    await self._save_state_to_db()
    
    async def _log_movement_start(self, session_id: str, target_position: Position, movement_duration: float):
        """Log movement start and save state."""
        async with self._db_transaction():
            await self._save_state_to_db_internal()
            
            # Log movement start
            operation = ScannerOperation(
                session_id=session_id,
                user_id=self.user_id,
                operation_type="move_start",
                position_x=target_position.x,
                position_y=target_position.y,
                duration=movement_duration,
                details=f"From {self.current_position} to {target_position}"
            )
            self.db.add(operation)
    
    async def _log_movement_complete(self, session_id: str, elapsed: float):
        """Log movement completion and save state."""
        async with self._db_transaction():
            await self._save_state_to_db_internal()
            
            # Log movement completion
            operation = ScannerOperation(
                session_id=session_id,
                user_id=self.user_id,
                operation_type="move_complete",
                position_x=self.current_position.x,
                position_y=self.current_position.y,
                duration=elapsed,
                details=f"Moved to {self.current_position}"
            )
            self.db.add(operation)
    
    async def focus_and_capture(self, session_id: str) -> None:
        """Focus and capture image at current position."""
        try:
            async with self._state_lock:
                self.operation_status = OperationStatus.FOCUSING
                self.operation_start_time = time.time()
            
            await self._save_state_to_db()
            await self.broadcast_log(
                f"Starting focus and capture at {self.current_position} - Duration: {settings.focus_duration}s"
            )
            await self.broadcast_state(True)
            
            # Validate focus duration
            focus_duration = max(0.1, min(settings.focus_duration, 30.0))  # Between 0.1 and 30 seconds
            
            # Focusing takes configured time
            await asyncio.sleep(focus_duration)
            
            # Capture image
            async with self._state_lock:
                self.current_position.clamp_to_bounds()
                position_tuple = self.current_position.to_tuple()
                self.captured_positions.add(position_tuple)
                self.operation_status = OperationStatus.COMPLETED
            
            # Save captured position and log operations to database
            await self._safe_db_operation(self._log_capture_complete, session_id, focus_duration)
            
            await self.broadcast_log(f"Image captured at {self.current_position}")
            await self.broadcast_state(True)
            
            # Reset to ready after a short delay
            await asyncio.sleep(0.5)
            async with self._state_lock:
                self.operation_status = OperationStatus.READY
                self.operation_start_time = None
            await self._save_state_to_db()
            await self.broadcast_state()
            
            self.log_operation("Image captured", position=str(self.current_position), user_id=self.user_id)
            
        except Exception as e:
            self.log_error("Failed to focus and capture", error=e, user_id=self.user_id)
            # Reset to ready state on error
            async with self._state_lock:
                self.operation_status = OperationStatus.READY
                self.operation_start_time = None
            await self._save_state_to_db()
            raise
    
    async def _log_capture_complete(self, session_id: str, focus_duration: float):
        """Log capture completion and save to database."""
        async with self._db_transaction():
            # Log focus start
            self.current_position.clamp_to_bounds()
            focus_start_op = ScannerOperation(
                session_id=session_id,
                user_id=self.user_id,
                operation_type="focus_start",
                position_x=self.current_position.x,
                position_y=self.current_position.y,
                duration=focus_duration
            )
            self.db.add(focus_start_op)
            
            # Save captured position
            captured_pos = CapturedPosition(
                session_id=session_id,
                user_id=self.user_id,
                position_x=self.current_position.x,
                position_y=self.current_position.y
            )
            self.db.add(captured_pos)
            
            # Log capture completion
            capture_op = ScannerOperation(
                session_id=session_id,
                user_id=self.user_id,
                operation_type="capture",
                position_x=self.current_position.x,
                position_y=self.current_position.y,
                details=f"Image captured at {self.current_position}"
            )
            self.db.add(capture_op)
            
            # Save state
            await self._save_state_to_db_internal()

    def get_state_dict(self) -> Dict:
        """Get current state as dictionary."""
        if self._state_cache is None:
            self.current_position.clamp_to_bounds()
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
    
    
    async def broadcast_state(self,force: bool = False) -> None:
        """Broadcast current state to all connected clients."""
        if not self.connected_clients:
            return
        if not force and self.is_processing:
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
        """Reset scanner to initial state for specific user."""
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
            
            # Clear all captured positions and operations from database for this user
            async with self._db_transaction():
                await self.db.execute(
                    text("DELETE FROM captured_positions WHERE user_id = :user_id"),
                    {"user_id": self.user_id}
                )
                await self.db.execute(
                    text("DELETE FROM scanner_operations WHERE user_id = :user_id"),
                    {"user_id": self.user_id}
                )
                await self._save_state_to_db_internal()
            
            await self.broadcast_log("Scanner reset to initial state")
            await self.broadcast_state()
            
            self.log_operation("Scanner reset completed", user_id=self.user_id)
            
        except Exception as e:
            self.log_error("Failed to reset scanner", error=e, user_id=self.user_id)
            raise