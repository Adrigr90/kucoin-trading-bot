"""The state of the uploads, ``/srv/kbot/subidas/estado.json`` (02 §13.1, point 4, E4c-2,
points 3.7 and 4).

Only kbot_upload writes it, always durably (``durable.write_json``); kbot-backup reads it
to decide which sealed days the hot copy may leave out. It holds:

* ``espacio``: the namespace of Object Storage, asked once;
* ``listado``: the last attempt to list the bucket, written before the request goes out,
  with its identifier, when it started, and whether it finished well (E4C2-02);
* ``verificadas``: each copy read again from the bucket with the SHA-256 of its two
  objects, when that happened, and the identifier of the last listing that saw them;
* ``ocupado``, ``peticiones`` and ``ultima_subida_completa`` (the RPO against the loss
  of the server), and the errors of the last cycle.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from kbot.upload import durable

UPLOAD_DIR = Path("/srv/kbot/subidas")
STATE = "estado.json"
COUNTER = "peticiones.json"
DEPENDENCIES = "dependencias.json"
FORMAT = "kbot-subidas-1"
FRESH = timedelta(hours=1)  # E4C2-02: a listing older than this authorises nothing
# The identity of a copy in the bucket, as a hot copy that depends on it fixes it.
IDENTITY = ("objeto", "sha256", "objeto_manifiesto", "sha256_manifiesto", "verificada")


def empty() -> dict[str, Any]:
    return {
        "formato": FORMAT,
        "espacio": None,
        "listado": None,
        "verificadas": {},
        "ocupado": None,
        "peticiones": None,
        "ultima_subida_completa": None,
        "errores": [],
    }


def load(path: Path) -> dict[str, Any] | None:
    """The state, or None when it is missing or cannot be read."""
    try:
        value = durable.read_json(path)
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict) or value.get("formato") != FORMAT:
        return None
    return value


def usable_listing(state: dict[str, Any] | None, now: datetime) -> dict[str, Any] | None:
    """The listing that may authorise omissions, or None (E4C2-02): it must be the last
    attempt and have finished well, and it must have started less than an hour ago. That
    it was not used by an earlier hot copy is checked by kbot-backup, which knows the
    listing its previous hot copy used."""
    if state is None:
        return None
    listing = state.get("listado")
    if not isinstance(listing, dict) or listing.get("ok") is not True:
        return None
    try:
        started = datetime.fromisoformat(str(listing["empezado"]))
        identifier = str(listing["id"])
    except (KeyError, ValueError):
        return None
    if not identifier or started > now or now - started >= FRESH:
        return None
    return listing


def verified_in(state: dict[str, Any], listing_id: str, name: str) -> dict[str, Any] | None:
    """The verification of a copy, if that copy was seen in the given listing."""
    entry = state.get("verificadas", {}).get(name)
    if not isinstance(entry, dict) or entry.get("visto") != listing_id:
        return None
    return entry


def partitions_digest(partitions: list[dict[str, Any]]) -> str:
    """SHA-256 of the partitions of a sealed-day copy (name, rows and fingerprint): equal
    digests mean the same data, whatever the encryption of each copy."""
    rows = sorted([str(p["particion"]), int(p["filas"]), str(p["huella"])] for p in partitions)
    return hashlib.sha256(json.dumps(rows).encode("utf-8")).hexdigest()
