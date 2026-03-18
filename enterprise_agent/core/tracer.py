from __future__ import annotations
import logging
import time
import uuid
from contextlib import contextmanager
from typing import Generator, List, Optional

logger = logging.getLogger("maga_agent")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)


class Tracer:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.entries: List[dict] = []

    @contextmanager
    def span(self, node_name: str, input_summary: str = "") -> Generator[dict, None, None]:
        trace_id = str(uuid.uuid4())[:8]
        start = time.time()
        logger.info(f"[{self.session_id}] ▶ {node_name} (trace={trace_id})")

        entry: dict = {
            "trace_id": trace_id,
            "node": node_name,
            "timestamp": start,
            "input_summary": input_summary,
            "output_summary": "",
            "duration_ms": 0.0,
            "error": None,
        }

        try:
            yield entry
        except Exception as e:
            entry["error"] = str(e)
            logger.error(f"[{self.session_id}] ✗ {node_name} ERROR: {e}")
            raise
        finally:
            elapsed = (time.time() - start) * 1000
            entry["duration_ms"] = elapsed
            self.entries.append(entry)
            status = "✓" if not entry.get("error") else "✗"
            logger.info(
                f"[{self.session_id}] {status} {node_name} "
                f"({elapsed:.0f}ms)"
                + (f" | {entry['output_summary']}" if entry.get("output_summary") else "")
            )

    def summary(self) -> str:
        lines = [f"\n{'='*50}", f"Session: {self.session_id}", f"{'='*50}"]
        total = 0.0
        for e in self.entries:
            ms = e.get("duration_ms", 0)
            total += ms
            status = "OK  " if not e.get("error") else "FAIL"
            lines.append(f"  {e['node']:20s} {ms:8.1f}ms  [{status}]")
        lines.append(f"  {'─'*38}")
        lines.append(f"  {'TOTAL':20s} {total:8.1f}ms")
        lines.append(f"{'='*50}")
        return "\n".join(lines)
