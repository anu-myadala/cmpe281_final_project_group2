"""page (1-based) and size query parameters: default 25, maximum 100.
A size over 100 fails the schema and becomes a VALIDATION / 400 answer."""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Query


@dataclass
class Page:
    page: int
    size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.size


def page_params(page: int = Query(1, ge=1), size: int = Query(25, ge=1, le=100)) -> Page:
    return Page(page=page, size=size)


def page_body(items: list, total: int, page: Page) -> dict:
    return {"items": items, "page": page.page, "size": page.size, "total": total}
