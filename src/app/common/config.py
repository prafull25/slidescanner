"""
Configuration management for the Morphle Scanner application.
"""

from typing import List, Literal
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseConfig(BaseModel):
    """Database configuration."""
    url: str = Field(default="sqlite+aiosqlite:///./morphle_scanner.db")


class ServerConfig(BaseModel):
    """Server configuration."""
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)
    reload: bool = Field(default=False)


class CORSConfig(BaseModel):
    """CORS configuration."""
    origins: List[str] = Field(default=["*"])
    allow_credentials: bool = Field(default=True)
    allow_methods: List[str] = Field(default=["*"])
    allow_headers: List[str] = Field(default=["*"])


class LoggingConfig(BaseModel):
    """Logging configuration."""
    level: str = Field(default="INFO")
    format: Literal["json", "text"] = Field(default="json")


class ScannerConfig(BaseModel):
    """Scanner-specific configuration."""
    grid_size: int = Field(default=11, ge=1)
    default_position_x: int = Field(default=5, ge=0)
    default_position_y: int = Field(default=5, ge=0)
    movement_speed_multiplier: float = Field(default=3.0, gt=0)
    focus_duration: float = Field(default=2.0, gt=0)


class Settings(BaseSettings):
    """Application settings."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )
    
    # Environment
    environment: str = Field(default="development")
    debug: bool = Field(default=False)
    
    # Database
    database_url: str = Field(default="sqlite+aiosqlite:///./morphle_scanner.db")
    
    # Server
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)
    reload: bool = Field(default=False)
    
    # CORS
    cors_origins: List[str] = Field(default=["*"])
    
    # Logging
    log_level: str = Field(default="INFO")
    log_format: Literal["json", "text"] = Field(default="json")
    
    # Scanner
    grid_size: int = Field(default=11, ge=1)
    default_position_x: int = Field(default=5, ge=0)
    default_position_y: int = Field(default=5, ge=0)
    movement_speed_multiplier: float = Field(default=3.0, gt=0)
    focus_duration: float = Field(default=2.0, gt=0)
    
    @property
    def database(self) -> DatabaseConfig:
        """Get database configuration."""
        return DatabaseConfig(url=self.database_url)
    
    @property
    def server(self) -> ServerConfig:
        """Get server configuration."""
        return ServerConfig(
            host=self.host,
            port=self.port,
            reload=self.reload
        )
    
    @property
    def cors(self) -> CORSConfig:
        """Get CORS configuration."""
        return CORSConfig(origins=self.cors_origins)
    
    @property
    def logging(self) -> LoggingConfig:
        """Get logging configuration."""
        return LoggingConfig(
            level=self.log_level,
            format=self.log_format
        )
    
    @property
    def scanner(self) -> ScannerConfig:
        """Get scanner configuration."""
        return ScannerConfig(
            grid_size=self.grid_size,
            default_position_x=self.default_position_x,
            default_position_y=self.default_position_y,
            movement_speed_multiplier=self.movement_speed_multiplier,
            focus_duration=self.focus_duration
        )
    
    def validate_scanner_defaults(self) -> None:
        """Validate that default position is within grid bounds."""
        if self.default_position_x >= self.grid_size:
            raise ValueError(f"default_position_x ({self.default_position_x}) must be less than grid_size ({self.grid_size})")
        if self.default_position_y >= self.grid_size:
            raise ValueError(f"default_position_y ({self.default_position_y}) must be less than grid_size ({self.grid_size})")


# Global settings instance
settings = Settings()
settings.validate_scanner_defaults()