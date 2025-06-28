"""
Database models for scanner operations - Updated for multi-user support.
"""

from typing import Optional
from datetime import datetime
from sqlalchemy import String, Integer, Float, DateTime, Text, Boolean, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.common.database import Base
from app.utils.enums import OperationStatus


class ScannerSession(Base):
    """Scanner session model for tracking user sessions."""
    
    __tablename__ = "scanner_sessions"
    
    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(10), nullable=False)  # Added
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_activity: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    
    # Relationships
    operations = relationship("ScannerOperation", back_populates="session", cascade="all, delete-orphan")
    positions = relationship("CapturedPosition", back_populates="session", cascade="all, delete-orphan")


class ScannerState(Base):
    """Current state of the scanner."""
    
    __tablename__ = "scanner_state"
    
    user_id: Mapped[str] = mapped_column(String(10), primary_key=True)  # Changed to primary key
    current_position_x: Mapped[int] = mapped_column(Integer, default=5)
    current_position_y: Mapped[int] = mapped_column(Integer, default=5)
    horizontal_movement_pending: Mapped[int] = mapped_column(Integer, default=0)
    vertical_movement_pending: Mapped[int] = mapped_column(Integer, default=0)
    operation_status: Mapped[str] = mapped_column(String(20), default=OperationStatus.READY.value)
    operation_start_time: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    current_movement_duration: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ScannerOperation(Base):
    """Log of scanner operations."""
    
    __tablename__ = "scanner_operations"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(50), ForeignKey("scanner_sessions.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(String(10), nullable=False)  # Added
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    operation_type: Mapped[str] = mapped_column(String(20))  # move, focus, capture
    position_x: Mapped[int] = mapped_column(Integer)
    position_y: Mapped[int] = mapped_column(Integer)
    duration: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Relationships
    session = relationship("ScannerSession", back_populates="operations")


class CapturedPosition(Base):
    """Positions where images were captured."""
    
    __tablename__ = "captured_positions"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(50), ForeignKey("scanner_sessions.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(String(10), nullable=False)  # Added
    position_x: Mapped[int] = mapped_column(Integer)
    position_y: Mapped[int] = mapped_column(Integer)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    
    # Relationships
    session = relationship("ScannerSession", back_populates="positions")