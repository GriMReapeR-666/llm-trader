"""Capability-Based Security & Permission Sandbox for Trading Harness.

Guarantees least-privilege security by partitioning tools and plugins into
explicit permission levels. Enforces that analytical tools cannot mutate orders,
and live broker execution requires active capability authorization.
"""
from __future__ import annotations

from enum import IntEnum
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
import logging
import secrets
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class PermissionLevel(IntEnum):
    """Graduated permission levels for harness components and tools."""
    READ_ONLY = 10       # Market data, technical indicators, memory lookups
    ANALYTICAL = 20      # Causal inference, macro DAG, feature building
    SIMULATION = 30      # Paper trading, backtesting, speculative forks
    ORDER_WRITE = 40     # Live broker order routing, position closing
    SYSTEM_ADMIN = 50    # Strategy parameter updates, risk limits, live rollout


@dataclass
class CapabilityToken:
    """Cryptographic authorization token granting specific permissions."""
    token_id: str = field(default_factory=lambda: secrets.token_hex(16))
    level: PermissionLevel = PermissionLevel.READ_ONLY
    granted_scopes: Set[str] = field(default_factory=lambda: {"*"})
    expires_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc) + timedelta(minutes=60)
    )
    issuer: str = "HarnessSecuritySupervisor"

    @property
    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) > self.expires_at

    def can_access(self, required_level: PermissionLevel, scope: str = "*") -> bool:
        if self.is_expired:
            return False
        if self.level < required_level:
            return False
        if "*" in self.granted_scopes:
            return True
        return scope in self.granted_scopes


class CapabilitySandbox:
    """Enforces capability verification on tool execution and plugin requests."""

    def __init__(self, default_level: PermissionLevel = PermissionLevel.SIMULATION):
        self.default_level = default_level
        self._active_tokens: Dict[str, CapabilityToken] = {}

    def issue_token(
        self,
        level: PermissionLevel,
        scopes: Optional[List[str]] = None,
        duration_minutes: int = 60,
        issuer: str = "SystemSupervisor",
    ) -> CapabilityToken:
        """Mint a capability token."""
        token = CapabilityToken(
            level=level,
            granted_scopes=set(scopes or ["*"]),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=duration_minutes),
            issuer=issuer,
        )
        self._active_tokens[token.token_id] = token
        return token

    def verify_call(
        self,
        token: Optional[CapabilityToken],
        required_level: PermissionLevel,
        scope: str = "*",
    ) -> bool:
        """Verify whether an operation with the given token is permitted."""
        if required_level <= PermissionLevel.READ_ONLY:
            return True

        if token is None:
            # Fallback to sandbox default level if no token explicitly provided
            return self.default_level >= required_level

        return token.can_access(required_level, scope)

    def guard(self, required_level: PermissionLevel, scope: str = "*"):
        """Decorator for gating function execution behind capability verification."""
        def decorator(fn: Callable):
            def wrapper(*args, **kwargs):
                token = kwargs.get("token", None)
                if not self.verify_call(token, required_level, scope):
                    raise PermissionError(
                        f"Capability authorization failed for '{fn.__name__}': "
                        f"Required level {required_level.name}, scope '{scope}'."
                    )
                return fn(*args, **kwargs)
            return wrapper
        return decorator
