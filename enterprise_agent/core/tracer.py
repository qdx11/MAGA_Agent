import logging
import time
import uuid
from contextlib import contextmanager
from typing import Dict, Iterator


logger = logging.getLogger("enterprise_agent")


class Tracer:
    def __init__(self, session_id: Optional[str] = None) -> None:  # type: ignore[name-defined]
        self.session_id = session_id or str(uuid.uuid4())
        self.entries: list[dict] = []

    @contextmanager
    def span(self, node_name: str, input_summary: str = "") -> Iterator[Dict]:
        trace_id = str(uuid.uuid4())[:8]
        start = time.time()
        logger.info("[%s] START %s (trace=%s)", self.session_id, node_name, trace_id)
        entry: Dict = {
            "trace_id": trace_id,
            "node": node_name,
            "timestamp": start,
            "input_summary": input_summary,
        }
        try:
            yield entry
            entry["error"] = None
        except Exception as exc:  # pragma: no cover
            entry["error"] = str(exc)
            raise
        finally:
            elapsed = (time.time() - start) * 1000
            entry["duration_ms"] = elapsed
            self.entries.append(entry)
            status = "OK" if not entry.get("error") else f"ERR: {entry['error']}"
            logger.info("[%s] END %s (%.0fms) %s", self.session_id, node_name, elapsed, status)

    def summary(self) -> str:
        lines = [f"Session: {self.session_id}"]
        total = 0.0
        for e in self.entries:
            ms = float(e.get("duration_ms", 0))
            total += ms
            status = "OK" if not e.get("error") else "FAIL"
            lines.append(f"  {e['node']:15s} {ms:8.0f}ms  {status}")
        lines.append(f"  {'TOTAL':15s} {total:8.0f}ms")
        return "\n".join(lines)
