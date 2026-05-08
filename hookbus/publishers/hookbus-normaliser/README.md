# HookBus Normaliser

Shared AgentHook normalisation and control-flow layer for HookBus publishers.

The normaliser keeps vendor publishers thin: adapters map native runtime payloads into a neutral event request, the normaliser handles AgentHook envelope creation, HookBus submission, allow/deny/ask semantics, AgentFlow approval wait/recheck, and returns a neutral result for the adapter to format back to the runtime.

HookBus itself remains transport and routing only.
