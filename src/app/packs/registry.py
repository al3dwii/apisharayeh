"""
Backward-compatible registry:

- REGISTRY()          -> {pack_name: register_fn}
- REGISTRY.get(name)  -> register function (callable) expected by existing code
- get_registry()      -> materialized mapping { pack: { agent_name: callable } }

Convenience helpers (new):
- resolve_agent(pack, agent) -> callable (raises KeyError if unknown)
- list_packs()               -> list[str]
- list_agents(pack)          -> list[str]
- invalidate()               -> clear materialized cache (hot-reload support)
"""
from __future__ import annotations
from typing import Callable, Dict, Optional

# --- Register map (lazy imports so missing packs don't crash the app) ---------

def _register_map() -> Dict[str, Callable[[], Dict[str, Dict[str, Callable]]]]:
    from .office import register as register_office
    # If/when doc2deck is ready, uncomment or keep the try/except
    # try:
    #     from .doc2deck import register as register_doc2deck
    # except Exception:
    #     register_doc2deck = None

    out = {
        "office": register_office,  # callable returning {"office": {...agents...}}
    }
    # if register_doc2deck:
    #     out["doc2deck"] = register_doc2deck
    return out

class _RegistryProxy:
    def __init__(self):
        self._map = _register_map()  # { pack: register_fn }

    # allow REGISTRY() usage to get the {pack: register_fn} mapping
    def __call__(self):
        return self._map

    # dict-like surface
    def get(self, *args, **kwargs): return self._map.get(*args, **kwargs)
    def __getitem__(self, k): return self._map[k]
    def __iter__(self): return iter(self._map)
    def items(self): return self._map.items()
    def keys(self): return self._map.keys()
    def values(self): return self._map.values()
    def __contains__(self, k): return k in self._map
    def __len__(self): return len(self._map)
    def __repr__(self): return f"REGISTRY(registers={list(self._map.keys())})"

REGISTRY = _RegistryProxy()

# --- Materialization cache ----------------------------------------------------

_materialized: Optional[Dict[str, Dict[str, Callable]]] = None

def get_registry() -> Dict[str, Dict[str, Callable]]:
    """
    Build (or return cached) { pack: { agent_name: callable } } by invoking each register().
    """
    global _materialized
    if _materialized is None:
        out: Dict[str, Dict[str, Callable]] = {}
        for pack, reg_fn in REGISTRY.items():
            pack_map = reg_fn()  # expected to be { pack: {agent: callable} }
            # Be defensive if a register returns agents under a different key
            agents = pack_map.get(pack) or next(iter(pack_map.values()))
            out[pack] = agents or {}
        _materialized = out
    return _materialized

def invalidate() -> None:
    """Clear the materialized cache (useful for hot-reload in dev)."""
    global _materialized
    _materialized = None

# --- Convenience helpers ------------------------------------------------------

def resolve_agent(pack: str, agent: str) -> Callable:
    """
    Return the agent callable or raise KeyError if not found.
    """
    reg = get_registry()
    if pack not in reg:
        raise KeyError(f"Unknown pack '{pack}'")
    if agent not in reg[pack]:
        raise KeyError(f"Unknown agent '{pack}.{agent}'")
    return reg[pack][agent]

def list_packs() -> list[str]:
    return list(get_registry().keys())

def list_agents(pack: str) -> list[str]:
    reg = get_registry()
    return list(reg.get(pack, {}).keys())
