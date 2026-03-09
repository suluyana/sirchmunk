# -*- coding: utf-8 -*-
"""
Configuration module for Sirchmunk web application.
Handles environment-based settings and feature flags.
"""

import os
from typing import Any, Dict, Optional
from dataclasses import dataclass, field


@dataclass
class DatabaseConfig:
    """Database configuration."""
    host: str = "localhost"
    port: int = 5432
    name: str = "sirchmunk"
    user: str = "sirchmunk"
    password: Optional[str] = None
    pool_size: int = 10
    ssl_enabled: bool = False

    @property
    def url(self) -> str:
        """Build database URL."""
        protocol = "postgresql+ssl" if self.ssl_enabled else "postgresql"
        pwd = f":{self.password}" if self.password else ""
        return f"{protocol}://{self.user}{pwd}@{self.host}:{self.port}/{self.name}"


@dataclass
class CacheConfig:
    """Cache configuration."""
    enabled: bool = True
    backend: str = "redis"  # redis, memory, disk
    ttl_seconds: int = 3600
    max_size_mb: int = 500

    # Redis settings
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0

    # Memory cache settings
    memory_max_items: int = 10000


@dataclass
class SearchConfig:
    """Search configuration."""
    default_mode: str = "FAST"
    max_depth: int = 5
    top_k_results: int = 10
    grep_timeout_seconds: int = 30
    max_file_size_mb: int = 50
    supported_extensions: tuple = field(default_factory=lambda: (
        ".txt", ".md", ".py", ".js", ".ts", ".java", ".go", ".rs",
        ".json", ".yaml", ".yml", ".xml", ".html", ".css",
        ".pdf", ".docx", ".pptx", ".xlsx"
    ))


@dataclass
class LLMConfig:
    """LLM configuration."""
    provider: str = "openai"  # openai, anthropic, local
    model: str = "gpt-4-turbo-preview"
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    timeout_seconds: int = 60
    max_tokens: int = 4000
    temperature: float = 0.7


@dataclass
class AppConfig:
    """Main application configuration."""
    # Environment
    environment: str = "development"  # development, staging, production
    debug: bool = False

    # Server
    host: str = "0.0.0.0"
    port: int = 8584
    workers: int = 4

    # Security
    secret_key: Optional[str] = None
    cors_origins: tuple = field(default_factory=lambda: ("*",))
    rate_limit_per_minute: int = 60

    # Components
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)

    @classmethod
    def from_env(cls) -> "AppConfig":
        """Load configuration from environment variables."""
        config = cls()

        config.environment = os.getenv("APP_ENV", "development")
        config.debug = os.getenv("DEBUG", "false").lower() == "true"
        config.host = os.getenv("HOST", "0.0.0.0")
        config.port = int(os.getenv("PORT", "8584"))
        config.workers = int(os.getenv("WORKERS", "4"))

        config.secret_key = os.getenv("SECRET_KEY")
        config.cors_origins = tuple(
            os.getenv("CORS_ORIGINS", "*").split(",")
        )
        config.rate_limit_per_minute = int(
            os.getenv("RATE_LIMIT", "60")
        )

        # Database
        config.database.host = os.getenv("DB_HOST", "localhost")
        config.database.port = int(os.getenv("DB_PORT", "5432"))
        config.database.name = os.getenv("DB_NAME", "sirchmunk")
        config.database.user = os.getenv("DB_USER", "sirchmunk")
        config.database.password = os.getenv("DB_PASSWORD")
        config.database.ssl_enabled = (
            os.getenv("DB_SSL", "false").lower() == "true"
        )

        # Cache
        config.cache.backend = os.getenv("CACHE_BACKEND", "redis")
        config.cache.redis_host = os.getenv("REDIS_HOST", "localhost")
        config.cache.redis_port = int(os.getenv("REDIS_PORT", "6379"))

        # LLM
        config.llm.provider = os.getenv("LLM_PROVIDER", "openai")
        config.llm.model = os.getenv("LLM_MODEL", "gpt-4-turbo-preview")
        config.llm.base_url = os.getenv("LLM_BASE_URL")
        config.llm.api_key = os.getenv("LLM_API_KEY")

        return config

    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary."""
        return {
            "environment": self.environment,
            "debug": self.debug,
            "host": self.host,
            "port": self.port,
            "workers": self.workers,
            "cors_origins": self.cors_origins,
            "rate_limit_per_minute": self.rate_limit_per_minute,
            "database": {
                "host": self.database.host,
                "port": self.database.port,
                "name": self.database.name,
            },
            "cache": {
                "enabled": self.cache.enabled,
                "backend": self.cache.backend,
            },
            "llm": {
                "provider": self.llm.provider,
                "model": self.llm.model,
            },
        }


# Global configuration instance
_config: Optional[AppConfig] = None


def get_config() -> AppConfig:
    """Get or create global configuration instance."""
    global _config
    if _config is None:
        _config = AppConfig.from_env()
    return _config


def reload_config() -> AppConfig:
    """Reload configuration from environment."""
    global _config
    _config = AppConfig.from_env()
    return _config
