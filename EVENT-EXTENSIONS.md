# Opaque event extensions

HookBus forwards publisher-defined top-level event fields without interpreting
their policy meaning. HookEvent retains unknown JSON fields through from_dict,
from_json, to_dict and to_json, including nested objects, false, null and empty
values. Missing fields remain absent.

Known transport fields retain their existing defaults and semantics. Extension
storage cannot replace event identity or other known fields. Extension values
are copied at parsing and serialization boundaries; no extra internal wrapper
is added to the wire representation. Legacy create() output is unchanged.

This carries AgentHook canonical governance fields and future extensions without
adding an AgentProtect-specific vocabulary or decisions to the bus. Subscribers
remain responsible for validating operation claims and applying policy.

Regression: tests/test_event_extensions.py verifies round trips, collisions,
copy isolation and authenticated HTTP-to-Unix-subscriber forwarding. The same
tests reproduce field loss against the unmodified baseline. No schema migration
or dependency change is required.
