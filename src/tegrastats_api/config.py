"""
Configuration module for Tegrastats API.
"""

import os
import math
import re
from decimal import Decimal, InvalidOperation
from typing import Optional


def normalize_tegrastats_interval(value) -> int:
    """Keep the existing numeric meaning: positive whole milliseconds."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError("tegrastats interval must be positive whole milliseconds")
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("tegrastats interval must be positive whole milliseconds")
    if not number.is_finite() or number <= 0 or number != number.to_integral_value():
        raise ValueError("tegrastats interval must be positive whole milliseconds")
    return int(number)


def freshness_timeout_ms(interval):
    return max(5000, 3 * interval)


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
        values = dict(host=host, port=port, debug=debug, update_interval=update_interval,
                      max_connections=max_connections, tegrastats_interval=tegrastats_interval,
                      cors_origins=cors_origins, log_level=log_level, log_file=log_file,
                      allow_unsafe_werkzeug=allow_unsafe_werkzeug)
        for name, value in values.items():
            try:
                setattr(self, name, _normalize(name, value))
            except (ValueError, TypeError, OverflowError) as error:
                raise ValueError("invalid configuration: %s; %s" % (name, error))
        self.sources = {name: "explicit Config/default" for name in values}

    @classmethod
    def resolve(cls, overrides=None, environ=None) -> "Config":
        """Select each source before validating, shared by CLI and from_env."""
        env = os.environ if environ is None else environ
        overrides = overrides or {}
        values = cls().to_dict()
        sources = {}
        for name, (primary, legacy) in ENV_FIELDS.items():
            if name in overrides:
                value, source = overrides[name], "CLI --" + name.replace("_", "-")
                if name == "debug" and value is False:
                    source = "CLI --no-debug"
            elif primary in env:
                value, source = env[primary], primary
            elif legacy in env:
                value, source = env[legacy], legacy
            else:
                value, source = values[name], "内置默认"
            try:
                values[name] = _normalize(name, value)
            except (ValueError, TypeError, OverflowError) as error:
                # Report only field/source, never echo arbitrary environment content.
                raise ValueError("invalid configuration: %s (source: %s); %s" % (name, source, error))
            sources[name] = source
        config = cls(**values)
        config.sources = sources
        return config

    @classmethod
    def from_env(cls) -> "Config":
        return cls.resolve()

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

    @property
    def freshness_timeout_ms(self):
        return freshness_timeout_ms(self.tegrastats_interval)
    
    def __repr__(self) -> str:
        return f"Config(host='{self.host}', port={self.port}, debug={self.debug})"


ENV_FIELDS = {
    "host": ("TEGRASTATS_API_HOST", "TEGRASTATS_HOST"),
    "port": ("TEGRASTATS_API_PORT", "TEGRASTATS_PORT"),
    "debug": ("TEGRASTATS_API_DEBUG", "TEGRASTATS_DEBUG"),
    "update_interval": ("TEGRASTATS_API_UPDATE_INTERVAL", "TEGRASTATS_UPDATE_INTERVAL"),
    "max_connections": ("TEGRASTATS_API_MAX_CONNECTIONS", "TEGRASTATS_MAX_CONNECTIONS"),
    "tegrastats_interval": ("TEGRASTATS_API_TEGRASTATS_INTERVAL", "TEGRASTATS_INTERVAL"),
    "cors_origins": ("TEGRASTATS_API_CORS_ORIGINS", "TEGRASTATS_CORS_ORIGINS"),
    "log_level": ("TEGRASTATS_API_LOG_LEVEL", "TEGRASTATS_LOG_LEVEL"),
    "log_file": ("TEGRASTATS_API_LOG_FILE", "TEGRASTATS_LOG_FILE"),
    "allow_unsafe_werkzeug": ("TEGRASTATS_API_ALLOW_UNSAFE_WERKZEUG", "TEGRASTATS_ALLOW_UNSAFE_WERKZEUG"),
}


def _normalize(name, value):
    if name == "tegrastats_interval":
        return normalize_tegrastats_interval(value)
    if name in ("debug", "allow_unsafe_werkzeug"):
        if isinstance(value, bool):
            return value
        token = str(value).strip().lower()
        if token in ("true", "1", "yes", "on"):
            return True
        if token in ("false", "0", "no", "off"):
            return False
        raise ValueError("expected boolean")
    if name in ("port", "max_connections"):
        if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value).strip()):
            raise ValueError("expected positive integer")
        try:
            number = int(value)
        except ValueError:
            raise ValueError("expected positive integer")
        if number <= 0 or (name == "port" and number > 65535):
            raise ValueError("integer out of range")
        return number
    if name == "update_interval":
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ValueError("expected seconds")
        try:
            number = float(value)
        except ValueError:
            raise ValueError("expected finite positive seconds")
        if not math.isfinite(number) or number <= 0:
            raise ValueError("expected finite positive seconds")
        return number
    if name == "log_file" and value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("expected nonempty text")
    text = value.strip()
    if name == "host" and (any(c.isspace() for c in text) or not re.fullmatch(r"[A-Za-z0-9_.:%-]+", text)):
        raise ValueError("expected host name or address")
    if name == "log_level":
        text = text.upper()
        if text not in ("DEBUG", "INFO", "WARNING", "ERROR"):
            raise ValueError("unsupported log level")
    return text
