"""
Configuration module for Tegrastats API.
"""

import os
from typing import Optional


class Config:
    """Configuration class for Tegrastats API server."""
    
    def __init__(
        self,
        host: str = "10.10.99.98",
        port: int = 58090,
        debug: bool = False,
        update_interval: float = 1.0,
        max_connections: int = 10,
        tegrastats_interval: int = 1000,
        cors_origins: str = "*",
        log_level: str = "INFO",
        log_file: Optional[str] = "app.log",
        allow_unsafe_werkzeug: bool = True
    ):
        """
        Initialize configuration.
        
        Args:
            host: Server host address
            port: Server port number
            debug: Enable debug mode
            update_interval: Data update interval in seconds
            max_connections: Maximum concurrent connections
            tegrastats_interval: Tegrastats sampling interval in milliseconds
            cors_origins: CORS allowed origins
            log_level: Logging level
            log_file: Log file path (None to disable file logging)
            allow_unsafe_werkzeug: Allow unsafe Werkzeug for production
        """
        self.host = host
        self.port = port
        self.debug = debug
        self.update_interval = update_interval
        self.max_connections = max_connections
        self.tegrastats_interval = tegrastats_interval
        self.cors_origins = cors_origins
        self.log_level = log_level
        self.log_file = log_file
        self.allow_unsafe_werkzeug = allow_unsafe_werkzeug
    
    @classmethod
    def from_env(cls) -> "Config":
        """Create configuration from environment variables."""
        return cls(
            host=os.getenv("TEGRASTATS_API_HOST", os.getenv("TEGRASTATS_HOST", "10.10.99.98")),
            port=int(os.getenv("TEGRASTATS_API_PORT", os.getenv("TEGRASTATS_PORT", "58090"))),
            debug=os.getenv("TEGRASTATS_API_DEBUG", os.getenv("TEGRASTATS_DEBUG", "false")).lower() == "true",
            update_interval=float(os.getenv("TEGRASTATS_API_UPDATE_INTERVAL", os.getenv("TEGRASTATS_UPDATE_INTERVAL", "1.0"))),
            max_connections=int(os.getenv("TEGRASTATS_API_MAX_CONNECTIONS", os.getenv("TEGRASTATS_MAX_CONNECTIONS", "10"))),
            tegrastats_interval=int(os.getenv("TEGRASTATS_API_TEGRASTATS_INTERVAL", os.getenv("TEGRASTATS_INTERVAL", "1000"))),
            cors_origins=os.getenv("TEGRASTATS_API_CORS_ORIGINS", os.getenv("TEGRASTATS_CORS_ORIGINS", "*")),
            log_level=os.getenv("TEGRASTATS_API_LOG_LEVEL", os.getenv("TEGRASTATS_LOG_LEVEL", "INFO")),
            log_file=os.getenv("TEGRASTATS_API_LOG_FILE", os.getenv("TEGRASTATS_LOG_FILE", "app.log")),
            allow_unsafe_werkzeug=os.getenv("TEGRASTATS_API_ALLOW_UNSAFE_WERKZEUG", os.getenv("TEGRASTATS_ALLOW_UNSAFE_WERKZEUG", "true")).lower() == "true"
        )
    
    def to_dict(self) -> dict:
        """Convert configuration to dictionary."""
        return {
            "host": self.host,
            "port": self.port,
            "debug": self.debug,
            "update_interval": self.update_interval,
            "max_connections": self.max_connections,
            "tegrastats_interval": self.tegrastats_interval,
            "cors_origins": self.cors_origins,
            "log_level": self.log_level,
            "log_file": self.log_file,
            "allow_unsafe_werkzeug": self.allow_unsafe_werkzeug
        }
    
    def __repr__(self) -> str:
        return f"Config(host='{self.host}', port={self.port}, debug={self.debug})"