import glob, importlib, os, yaml
from app.kernel.plugins.spec import ServiceManifest

def _manifests():
    return glob.glob("plugins/**/manifest.yaml", recursive=True)

def test_manifests_parse_and_entrypoints_importable():
    assert _manifests(), "No plugin manifests found under plugins/**/manifest.yaml"
    for mpath in _manifests():
        spec = yaml.safe_load(open(mpath, "r", encoding="utf-8"))
        mf = ServiceManifest.model_validate(spec)

        # basic shape
        assert mf.id and mf.version and mf.runtime in {"dsl","inproc","http","grpc"}

        # dsl plugins must have a flow.yaml next to manifest
        if mf.runtime == "dsl":
            flow_path = os.path.join(os.path.dirname(mpath), "flow.yaml")
            assert os.path.exists(flow_path), f"{mf.id}: missing flow.yaml"

        # inproc plugins must have importable entrypoint
        if mf.runtime == "inproc":
            assert mf.entrypoint, f"{mf.id}: missing entrypoint"
            module, _, func = mf.entrypoint.partition(":")
            mod = importlib.import_module(module)
            assert hasattr(mod, func), f"{mf.id}: {mf.entrypoint} not found"

def test_registry_lists_services():
    from app.kernel.plugins.loader import registry
    registry.refresh()
    ids = {s.id for s in registry.list()}
    # sanity: should include the built-ins you have
    assert any(x in ids for x in {"office.word_to_pptx",
                              "slides.generate",
                             "slides.author",
                             "slides.edit"})
