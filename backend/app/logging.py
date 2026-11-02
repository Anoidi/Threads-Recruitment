import logging
import re
import structlog

SENSITIVE = re.compile(r"(access[_-]?token|api[_-]?key|password|app[_-]?secret)", re.I)


def redact(_, __, event_dict):
    for key in list(event_dict):
        if SENSITIVE.search(str(key)):
            event_dict[key] = "[REDACTED]"
    return event_dict


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # HTTPX INFO logs include complete URLs. OAuth GET URLs contain secrets.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    structlog.configure(
        processors=[redact, structlog.processors.add_log_level, structlog.processors.TimeStamper(fmt="iso"), structlog.processors.JSONRenderer()],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    )


def get_logger(category: str):
    return structlog.get_logger(category=category)

