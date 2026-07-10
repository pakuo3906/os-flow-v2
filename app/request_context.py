from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any


_customer_scope_context: ContextVar[dict[str, Any] | None] = ContextVar("customer_scope", default=None)


def set_customer_scope(scope: dict[str, Any] | None) -> Token[dict[str, Any] | None]:
    return _customer_scope_context.set(scope)


def reset_customer_scope(token: Token[dict[str, Any] | None]) -> None:
    _customer_scope_context.reset(token)


def get_customer_scope() -> dict[str, Any] | None:
    return _customer_scope_context.get()


def build_customer_scope_metadata(scope: dict[str, Any] | None) -> dict[str, Any] | None:
    if not scope or not scope.get("ready"):
        return None
    return {
        "source": scope.get("source"),
        "effective_slug": scope.get("effective_slug"),
        "effective_name": scope.get("effective_name"),
        "default_slug": scope.get("default_slug"),
        "default_name": scope.get("default_name"),
    }


