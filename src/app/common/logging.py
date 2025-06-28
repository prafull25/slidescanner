"""
Logging configuration for the Morphle Scanner application.
"""

import logging
import sys
from typing import Any, Dict
import structlog
from structlog.types import Processor
from app.common.config import settings


def setup_logging() -> None:
    """Configure structured logging."""
    
    # Configure processors based on format
    processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    
    if settings.log_format == "json":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=True))
    
    # Configure structlog
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level.upper())
        ),
        logger_factory=structlog.WriteLoggerFactory(file=sys.stdout),
        context_class=dict,
        cache_logger_on_first_use=True,
    )
    
    # Configure standard library logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, settings.log_level.upper()),
    )
    
    # Configure uvicorn logging
    uvicorn_logger = logging.getLogger("uvicorn")
    uvicorn_logger.setLevel(getattr(logging, settings.log_level.upper()))


def get_logger(name: str = __name__) -> structlog.BoundLogger:
    """Get a logger instance."""
    return structlog.get_logger(name)


class LoggerMixin:
    """Mixin to provide logging capabilities to classes."""
    
    @property
    def logger(self) -> structlog.BoundLogger:
        """Get logger for this class."""
        return get_logger(self.__class__.__name__)
    
    def log_operation(self, event: str, **kwargs: Any) -> None:
        """Log an operation with structured data."""
        self.logger.info(event, **kwargs)
    
    def log_error(self, event: str, error: Exception = None, **kwargs: Any) -> None:
        """Log an error with structured data."""
        log_data = kwargs.copy()
        if error:
            log_data["error"] = str(error)
            log_data["error_type"] = type(error).__name__
        self.logger.error(event, **log_data)
    
    def log_warning(self, event: str, **kwargs: Any) -> None:
        """Log a warning with structured data."""
        self.logger.warning(event, **kwargs)
    
    def log_debug(self, event: str, **kwargs: Any) -> None:
        """Log debug information with structured data."""
        self.logger.debug(event, **kwargs)