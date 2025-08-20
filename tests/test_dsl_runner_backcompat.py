import pytest

from app.kernel.dsl_runner import DSLRunner

class FakeCtx:
    def __init__(self):
        self.events = []
    def url_for(self, x): return x
    def emit(self, typ, payload): self.events.append((typ, payload))

class FakeRouter:
    def call(self, op, ctx, **kw):
        if op == "echo":
            # echo back inputs as outputs for easy assertions
            return {**kw}
        if op == "concat":
            return {"text": f"{kw.get('a','')}{kw.get('b','')}"}
        raise RuntimeError(f"unknown op {op}")

def run(flow, inputs=None):
    dsl = DSLRunner(FakeRouter(), FakeCtx())
    return dsl.run(flow, inputs or {})

def test_legacy_steps_run_executes_and_writes_out():
    flow = {
        "vars": {"greeting": "{{ 'hi' }}"},
        "steps": [
            {
                "id": "mktext",
                "run": "concat",
                "in": {"a": "{{ vars.greeting }}", "b": " there"},
                "out": {"text": "@t1"},
            }
        ],
        "outputs": {"text": "@t1"},
    }
    out = run(flow)
    assert out["text"] == "hi there"

def test_nodes_op_executes():
    flow = {
        "nodes": [
            {"id": "n1", "op": "echo", "in": {"x": 42}, "out": {"x": "@x"}}
        ],
        "outputs": {"x": "@x"},
    }
    out = run(flow)
    assert out["x"] == 42

def test_switch_with_ref_and_true_fallback():
    flow = {
        "steps": [
            {
                "id": "branch",
                "run": "switch",
                "when": {
                    "{{ not @outline }}": {
                        "run": "echo",
                        "in": {"outline": ["a", "b"]},
                        "out": {"outline": "@outline"},
                    },
                    "true": {
                        "run": "echo",
                        "in": {"noop": 1},
                    },
                },
            }
        ],
        "outputs": {"outline": "@outline"},
    }
    out = run(flow)
    assert out["outline"] == ["a", "b"]

def test_mustache_default_and_at_ref_in_outputs():
    flow = {
        "steps": [
            {"id": "e1", "run": "echo", "in": {"v": None}, "out": {"v": "@v"}},
            {"id": "e2", "run": "echo", "in": {"x": "{{ vars.missing | default('fallback') }}"},
             "out": {"x": "@x"}}
        ],
        "outputs": {"v": "@v", "x": "@x"},
    }
    out = run(flow)
    assert out["v"] is None
    assert out["x"] == "fallback"
