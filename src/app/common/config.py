"""
Configuration management for the Morphle Scanner application.
"""

from typing import List, Literal
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    # Environment
    environment: str = Field(default="development", env="ENVIRONMENT")
    debug: bool = Field(default=False, env="DEBUG")

    # Database
    database_url: str = Field(default="sqlite+aiosqlite:///./morphle_scanner.db", env="DATABASE_URL")

    # Server
    host: str = Field(default="0.0.0.0", env="HOST")
    port: int = Field(default=8000, env="PORT")
    reload: bool = Field(default=False, env="RELOAD")

    # CORS
    cors_origins: List[str] = Field(default=["*"], env="CORS_ORIGINS")

    # Logging
    log_level: str = Field(default="INFO", env="LOG_LEVEL")
    log_format: Literal["json", "text"] = Field(default="json", env="LOG_FORMAT")

    # Scanner
    grid_size: int = Field(default=11, env="GRID_SIZE")
    default_position_x: int = Field(default=5, env="DEFAULT_POSITION_X")
    default_position_y: int = Field(default=5, env="DEFAULT_POSITION_Y")
    movement_speed_multiplier: float = Field(default=3.0, env="MOVEMENT_SPEED_MULTIPLIER")
    focus_duration: float = Field(default=2.0, env="FOCUS_DURATION")

    def validate_scanner_defaults(self) -> None:
        """Ensure default scanner position is within grid bounds."""
        if self.default_position_x >= self.grid_size:
            raise ValueError(f"default_position_x ({self.default_position_x}) must be less than grid_size ({self.grid_size})")
        if self.default_position_y >= self.grid_size:
            raise ValueError(f"default_position_y ({self.default_position_y}) must be less than grid_size ({self.grid_size})")

    @property
    def database(self):
        from pydantic import BaseModel

        class DatabaseConfig(BaseModel):
            url: str

        return DatabaseConfig(url=self.database_url)

    @property
    def server(self):
        from pydantic import BaseModel

        class ServerConfig(BaseModel):
            host: str
            port: int
            reload: bool

        return ServerConfig(host=self.host, port=self.port, reload=self.reload)

    @property
    def cors(self):
        from pydantic import BaseModel

        class CORSConfig(BaseModel):
            origins: List[str]
            allow_credentials: bool = True
            allow_methods: List[str] = ["*"]
            allow_headers: List[str] = ["*"]

        return CORSConfig(origins=self.cors_origins)

    @property
    def logging(self):
        from pydantic import BaseModel

        class LoggingConfig(BaseModel):
            level: str
            format: Literal["json", "text"]

        return LoggingConfig(level=self.log_level, format=self.log_format)

    @property
    def scanner(self):
        from pydantic import BaseModel

        class ScannerConfig(BaseModel):
            grid_size: int
            default_position_x: int
            default_position_y: int
            movement_speed_multiplier: float
            focus_duration: float

        return ScannerConfig(
            grid_size=self.grid_size,
            default_position_x=self.default_position_x,
            default_position_y=self.default_position_y,
            movement_speed_multiplier=self.movement_speed_multiplier,
            focus_duration=self.focus_duration
        )


# Global settings instance
settings = Settings()
settings.validate_scanner_defaults()
