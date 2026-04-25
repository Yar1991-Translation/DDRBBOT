"""QQ adapter implementations."""

# Lazy imports to avoid circular dependency:
#   delivery → qq.napcat → qq.__init__ → {qq.ws_client, qq.commands} → delivery
# All submodule names are resolved via __getattr__ at access time.

__all__ = [
    "BotAdapter",
    "NapCatAdapter",
    "NapCatWSClient",
    "QQCommandAuthorizer",
    "QQCommandDispatchResult",
    "QQCommandRouter",
    "QQOperationsService",
    "get_test_card_fixtures",
    "handle_inbound_event",
    "normalize_inbound_event",
]


def __getattr__(name: str):
    if name in {"QQCommandRouter", "QQCommandDispatchResult", "QQCommandAuthorizer"}:
        from . import commands as _mod
        return getattr(_mod, name)
    if name in {"QQOperationsService", "get_test_card_fixtures"}:
        from . import operations as _mod
        return getattr(_mod, name)
    if name in {"NapCatWSClient", "handle_inbound_event"}:
        from . import ws_client as _mod
        return getattr(_mod, name)
    if name in {"BotAdapter", "NapCatAdapter", "normalize_inbound_event"}:
        from . import napcat as _mod
        return getattr(_mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
