import importlib

def snake_to_pascal(s: str) -> str:
    return "".join(part.capitalize() for part in s.split("_"))

def load_agent(pack: str, agent: str):
    mod = importlib.import_module(f"app.packs.{pack}.agents")
    cls_name = snake_to_pascal(agent)
    if not hasattr(mod, cls_name):
        raise RuntimeError(f"Class {cls_name} not found in pack {pack}")
    return getattr(mod, cls_name)
