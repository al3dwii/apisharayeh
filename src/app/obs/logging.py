from __future__ import annotations
import logging, os

def boot_logging() -> None:
    # Inject default structured fields so 3rd-party loggers don’t crash our format.
    old_factory = logging.getLogRecordFactory()
    def record_factory(*args, **kwargs):
        rec = old_factory(*args, **kwargs)
        # Provide BOTH variants (tenant & tenant_id) to cover format strings
        for k in ("tenant", "tenant_id", "run", "plan", "node"):
            if not hasattr(rec, k):
                setattr(rec, k, None)
        return rec
    logging.setLogRecordFactory(record_factory)

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s tenant=%(tenant)s run=%(run)s plan=%(plan)s node=%(node)s msg=%(message)s",
    )
