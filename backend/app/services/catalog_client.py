"""HTTP client for `restaurant-manager`'s `/api/v2`. Network only -- it
knows how to *fetch* the catalog and nothing about what this app does with
it, the same way upstream keeps `gmail.py` (network) apart from
`gmail_match.py` (logic).

Kept separate from `catalog_sync.py` so the sync's decision-making --
what to upsert, what to skip, how the watermark moves -- is testable
against a fake client with no network and no `httpx` in the way.

Every failure mode collapses into one exception type, `CatalogUnavailable`,
because the caller's response to all of them is identical: record the
failed run, leave the mirror as it is, and let costing carry on against
slightly older data (PLAN.md §5.3). A sync that cannot reach upstream is
not an error state for this app -- it is a mirror that stopped advancing,
and the UI says so by showing its as-of date.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import httpx


class CatalogUnavailable(RuntimeError):
    """Upstream could not be reached, or answered with something unusable.

    Deliberately one exception for connection refused, a timeout, a 500 and
    a malformed body alike: the caller does the same thing for all four, and
    a finer taxonomy would only invite a handler that treats one of them as
    recoverable when it is not.
    """


class ContractMismatch(RuntimeError):
    """Upstream reports a major contract version this app was not written
    against.

    **Fails the sync loudly instead of parsing hopefully.** The two apps are
    installed per restaurant and upgraded independently (PLAN.md D22), so
    this is a real situation, not a theoretical one -- and a payload whose
    *meaning* changed is far more dangerous than one that fails to parse,
    because it produces plausible numbers. Refusing is the safe half.
    """


# The major version this app is written against. Upstream may add fields
# freely -- those bump its minor revision and are ignored here (only the
# known keys are read) -- but a change in meaning gets a new major, and
# that is what this refuses.
#
# **Moved 1 -> 2 on 2026-09-08**, when upstream published `/api/v2`. The
# only difference is that `/proveedores` no longer carries `es_servicios`,
# a boolean replaced upstream by `role` (producto | servicio |
# gasto_fijo). This app never read that field -- it calls `/version`,
# `/categorias`, `/productos` and `/productos/{id}/compras`, and
# `/proveedores` not at all -- so nothing here parses differently. The
# bump declares which contract we speak; it is not a payload change we
# had to absorb.
#
# **Upgrade order matters, in one direction only.** This refuses a server
# whose major it does not recognise, so restaurant-manager must already be
# serving v2 before this app is pointed at it. The reverse is safe: an
# older menu-builder keeps working against a newer server, because v1 is
# still served and unchanged. That asymmetry is the whole reason upstream
# added a path instead of editing v1.
SUPPORTED_MAJOR = 2


class CatalogClient:
    """Thin wrapper over the six upstream endpoints.

    Holds one `httpx.Client` for the whole sync rather than opening a
    connection per request: a full catalog pull is one call per product for
    its purchase history (~1 400 on the live data), and per-call clients
    would spend most of the sync in TCP setup.
    """

    def __init__(self, base_url: str, timeout_seconds: int = 15) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout_seconds)

    def __enter__(self) -> CatalogClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        url = f"{self.base_url}{path}"
        try:
            resp = self._client.get(url, params=params or {})
        except httpx.HTTPError as exc:
            raise CatalogUnavailable(f"No se pudo conectar con {url}: {exc}") from exc
        if resp.status_code != 200:
            raise CatalogUnavailable(f"{url} respondió {resp.status_code}")
        try:
            payload = resp.json()
        except ValueError as exc:
            raise CatalogUnavailable(f"{url} no devolvió JSON válido: {exc}") from exc
        if not isinstance(payload, dict):
            raise CatalogUnavailable(f"{url} devolvió {type(payload).__name__}, se esperaba un objeto")
        return payload

    def version(self) -> dict:
        """Upstream's contract revision. Raises `ContractMismatch` when its
        major does not match `SUPPORTED_MAJOR` -- checked once per sync,
        before a single row is written.
        """
        payload = self._get("/version")
        revision = str(payload.get("api", ""))
        major = revision.split(".")[0]
        if major != str(SUPPORTED_MAJOR):
            raise ContractMismatch(
                f"El catálogo habla la versión {revision or '(desconocida)'} y este app "
                f"entiende la {SUPPORTED_MAJOR}.x. Actualizá restaurant-manager o este app "
                f"antes de sincronizar."
            )
        return payload

    def categorias(self) -> dict:
        """Upstream's category tree. Small, unpaginated, and refetched whole
        on every sync -- it is a few dozen rows and the names change so
        rarely that a watermark would cost more than it saves."""
        return self._get("/categorias")

    def productos(
        self,
        desde: int = 0,
        limite: int = 200,
        modificados_desde: dt.datetime | None = None,
    ) -> dict:
        params: dict[str, Any] = {"desde": desde, "limite": limite}
        if modificados_desde is not None:
            params["modificados_desde"] = modificados_desde.isoformat()
        return self._get("/productos", params)

    def compras(self, product_id: int, cursor: int = 0, limite: int = 200) -> dict:
        return self._get(
            f"/productos/{product_id}/compras", {"cursor": cursor, "limite": limite}
        )
