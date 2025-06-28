"""
Enums and constants for the Morphle Scanner application.
"""

from enum import Enum


class OperationStatus(Enum):
    """Scanner operation status."""
    READY = "ready"
    MOVING = "moving"
    FOCUSING = "focusing"
    COMPLETED = "completed"


class Direction(Enum):
    """Movement directions."""
    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"


class MessageType(Enum):
    """WebSocket message types."""
    MOVE = "move"
    STATE_UPDATE = "state_update"
    LOG = "log"
    ERROR = "error"
    PING = "ping"
    PONG = "pong"
    GET_STATE = "get_state"