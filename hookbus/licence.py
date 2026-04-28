"""HookBus tier + licence gate.

Distinguishes Light (Apache 2.0 open source) from Enterprise (paid, commercial).
Read at Bus.__init__; flags consulted by feature-gated code paths.

v0.1.0 spec:
  HOOKBUS_LICENSE absent OR "community" -> Light (default, free)
  HOOKBUS_LICENSE = "ent-<customer-id>-<YYYY-MM-DD>"  -> Enterprise (paid)

Light limits (enforced elsewhere by reading these flags):
  - hot_reload            False
  - advanced_consolidation False (basic deny-wins only)
  - failover_groups       False
  - observability_export  False
  - webhook_integrations  False
  - persistent_event_store False
  - high_availability     False

Light positioning:
  Light is Apache 2.0 open source and suitable for evaluation,
  development, and internal pilots. Enterprise adds advanced subscribers,
  compliance workflows, policy management, audit exports, RBAC, and support.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date
from typing import Dict

logger = logging.getLogger(__name__)

LIGHT_FEATURES: Dict[str, bool] = {
    "hot_reload": False,
    "advanced_consolidation": False,
    "failover_groups": False,
    "observability_export": False,
    "webhook_integrations": False,
    "persistent_event_store": False,
    "high_availability": False,
}

ENTERPRISE_FEATURES: Dict[str, bool] = {k: True for k in LIGHT_FEATURES}

_BAR = "=" * 70


@dataclass
class Licence:
    tier: str = "community"
    customer_id: str = ""
    expires_at: str = ""
    features: Dict[str, bool] = field(default_factory=lambda: dict(LIGHT_FEATURES))

    def is_enterprise(self) -> bool:
        return self.tier == "enterprise"

    def has(self, feature: str) -> bool:
        return self.features.get(feature, False)


def _parse_enterprise_key(key: str) -> Licence:
    """Parse 'ent-<customer-id>-<YYYY-MM-DD>'. v0.1.0: no signature; heartbeat detects fraud."""
    parts = key.strip().split("-")
    if len(parts) < 5 or parts[0] != "ent":
        raise ValueError(f"Malformed Enterprise key (expected ent-<id>-<YYYY-MM-DD>): {key[:32]}...")
    expires = "-".join(parts[-3:])
    customer_id = "-".join(parts[1:-3])
    try:
        exp = date.fromisoformat(expires)
    except ValueError:
        raise ValueError(f"Malformed expiry in Enterprise key: {expires}")
    if exp < date.today():
        raise ValueError(f"Enterprise key expired on {expires}")
    return Licence(
        tier="enterprise",
        customer_id=customer_id or "unknown",
        expires_at=expires,
        features=dict(ENTERPRISE_FEATURES),
    )


def load_licence() -> Licence:
    raw = (os.environ.get("HOOKBUS_LICENSE") or "").strip()
    if not raw or raw.lower() == "community":
        return Licence()
    try:
        return _parse_enterprise_key(raw)
    except Exception as exc:
        logger.error(
            "Invalid HOOKBUS_LICENSE: %s. Falling back to community tier.", exc
        )
        return Licence()


def banner(licence: Licence, version: str) -> str:
    if licence.is_enterprise():
        return (
            f"\n{_BAR}\n"
            f"  HookBus Enterprise v{version}\n"
            f"  Licence: customer={licence.customer_id} expires={licence.expires_at}\n"
            f"  UK Patent GB2608069.7 (pending)\n"
            f"{_BAR}\n"
        )
    return (
        f"\n{_BAR}\n"
        f"  HookBus Light v{version}\n"
        f"  UK Patent GB2608069.7 (pending)\n\n"
        f"  LICENCE: Apache 2.0 open source.\n"
        f"  Suitable for evaluation, development, and internal pilots.\n"
        f"  Enterprise adds advanced subscribers, compliance workflows,\n"
        f"  policy management, audit exports, RBAC, and support.\n\n"
        f"  Disabled in this tier: hot-reload, advanced consolidation,\n"
        f"  failover groups, persistent store, HA, observability hooks,\n"
        f"  webhook integrations.\n"
        f"{_BAR}\n"
    )
