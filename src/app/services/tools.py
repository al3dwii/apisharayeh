from app.services.models import ModelRouter
from app.ops import doc_ops, ppt_ops, io_ops

class ToolRouter:
    def __init__(self, models: ModelRouter):
        self.models = models

    def resolve(self, op: str):
        ns, name = op.split(".", 1)
        if ns == "mt":   return lambda **kw: self.models.translate(**kw)
        if ns == "llm":  return lambda **kw: self.models.chat(**kw)
        if ns == "doc":  return getattr(doc_ops, name)
        if ns == "ppt":  return getattr(ppt_ops, name)
        if ns == "io":   return getattr(io_ops, name)
        raise KeyError(op)
