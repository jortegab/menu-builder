"""The page-number strip, ported from `restaurant-manager`'s
`services/pagination.py` so the two apps paginate identically.

Only `pagination_window` came across. Upstream's `paginate` slices an
in-memory list, which is right for its tables and wrong here: the mapping
queue pages one product at a time out of a few hundred, and materialising
every pending row to hand back one of them is a cost paid on a screen that
already spends most of its render waiting on the network. This app pages in
SQL (`pendientes`' own `offset`/`limit`) and only needs the strip.

Pure -- no DB, no request -- for the same reason `units.py` and `costing.py`
are: the off-by-one arithmetic is the part worth testing directly, and it is
testable without any infrastructure at all.
"""

from __future__ import annotations

__all__ = ["pagination_window"]


def pagination_window(page: int, total_pages: int, radius: int = 1) -> list[int | None]:
    """Page numbers to render, Google-results style: page 1, the last page,
    and `page` +/- `radius` always shown; every other gap collapses to a
    single `None` (rendered as "…"), so the strip stays a fixed width
    instead of listing every page as the data grows.

    Byte-for-byte upstream's, deliberately: two apps whose page strips
    disagree about when an ellipsis appears look like two different
    products, and this is the kind of arithmetic that only diverges once
    somebody "improves" one copy.
    """
    if total_pages <= 1:
        return [1]
    keep = sorted({1, total_pages, page, *(p for p in range(page - radius, page + radius + 1))})
    keep = [p for p in keep if 1 <= p <= total_pages]
    windowed: list[int | None] = []
    previous: int | None = None
    for p in keep:
        if previous is not None and p - previous > 1:
            windowed.append(None)
        windowed.append(p)
        previous = p
    return windowed
