"""
Position calculation utilities for the scanner.
"""

import math
from typing import Tuple
from app.common.config import settings
from app.common.logging import LoggerMixin


class Position:
    """Position class with utility methods."""
    
    def __init__(self, x: int, y: int):
        self.x = x
        self.y = y
    
    def __add__(self, other: 'Position') -> 'Position':
        """Add two positions."""
        return Position(self.x + other.x, self.y + other.y)
    
    def __eq__(self, other: 'Position') -> bool:
        """Check if two positions are equal."""
        return self.x == other.x and self.y == other.y
    
    def __str__(self) -> str:
        """String representation of position."""
        return f"({self.x}, {self.y})"
    
    def __repr__(self) -> str:
        """String representation of position."""
        return f"Position(x={self.x}, y={self.y})"
    
    def distance_to(self, other: 'Position') -> float:
        """Calculate Euclidean distance to another position."""
        return math.sqrt((self.x - other.x)**2 + (self.y - other.y)**2)
    
    def to_dict(self) -> dict:
        """Convert position to dictionary."""
        return {"x": self.x, "y": self.y}
    
    def to_tuple(self) -> Tuple[int, int]:
        """Convert position to tuple."""
        return (self.x, self.y)
    
    def is_within_bounds(self, grid_size: int = None) -> bool:
        """Check if position is within grid bounds."""
        if grid_size is None:
            grid_size = settings.grid_size
        return 0 <= self.x < grid_size and 0 <= self.y < grid_size
    
    def clamp_to_bounds(self, grid_size: int = None) -> 'Position':
        """Clamp position to grid bounds."""
        if grid_size is None:
            grid_size = settings.grid_size
        return Position(
            max(0, min(grid_size - 1, self.x)),
            max(0, min(grid_size - 1, self.y))
        )


class PositionCalculator(LoggerMixin):
    """Calculator for position-related operations."""
    
    def __init__(self):
        self.grid_size = settings.grid_size
        self.movement_speed_multiplier = settings.movement_speed_multiplier
    
    def calculate_movement_time(self, start_pos: Position, end_pos: Position) -> float:
        """
        Calculate movement time based on distance.
        
        Args:
            start_pos: Starting position
            end_pos: Ending position
            
        Returns:
            float: Movement time in seconds
        """
        distance = start_pos.distance_to(end_pos)
        if distance == 0:
            return 0.0
        
        movement_time = self.movement_speed_multiplier * math.sqrt(distance)
        
        self.log_debug(
            "Movement time calculated",
            start_position=str(start_pos),
            end_position=str(end_pos),
            distance=round(distance, 2),
            movement_time=round(movement_time, 2)
        )
        
        return movement_time
    
    def calculate_target_position(
        self, 
        current_pos: Position, 
        horizontal_movement: int, 
        vertical_movement: int
    ) -> Position:
        """
        Calculate target position based on current position and pending movements.
        
        Args:
            current_pos: Current position
            horizontal_movement: Pending horizontal movement (negative for left, positive for right)
            vertical_movement: Pending vertical movement (negative for down, positive for up)
            
        Returns:
            Position: Target position clamped to grid bounds
        """
        target_x = current_pos.x + horizontal_movement
        target_y = current_pos.y + vertical_movement
        
        # Clamp to grid bounds
        target_position = Position(target_x, target_y).clamp_to_bounds(self.grid_size)
        
        self.log_debug(
            "Target position calculated",
            current_position=str(current_pos),
            horizontal_movement=horizontal_movement,
            vertical_movement=vertical_movement,
            target_position=str(target_position)
        )
        
        return target_position
    
    def get_default_position(self) -> Position:
        """Get the default starting position."""
        return Position(settings.default_position_x, settings.default_position_y)
    
    def is_valid_position(self, position: Position) -> bool:
        """Check if a position is valid within the grid."""
        return position.is_within_bounds(self.grid_size)