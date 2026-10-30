"""Entry point the C1 API imports when STUBS_INLINE=1 (app.main.stubs_mount):

    from stubs.app import c2_router, c3_router, demo_generator

The routers carry no prefix; C1 mounts them under /c2/api/v1 and /c3/api/v1.
They find their databases, secrets and the EventBus through the C1 container
on request.app.state (see stubs.common.get_ctx).
"""
from __future__ import annotations

from .c2 import c2_router
from .c3 import c3_router
from .generator import demo_generator

__all__ = ["c2_router", "c3_router", "demo_generator"]
