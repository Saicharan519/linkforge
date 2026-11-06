"""The metrics HTTP server: a SEPARATE port, never the public one (ARCHITECTURE §1.2).

Prometheus scrapes `/metrics` here (API :9101, worker :9100). It runs in its own thread so a
heavy scrape cannot slow requests, and it listens on its own port so that no Ingress, and no
public host, can ever route to it: the app itself never serves `/metrics`.

Port `0` picks a free port (tests); a negative port turns the server off.
"""

import logging
import threading
from dataclasses import dataclass
from wsgiref.simple_server import WSGIServer

from prometheus_client import start_http_server

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MetricsServer:
    server: WSGIServer
    thread: threading.Thread
    port: int

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def start_metrics_server(port: int) -> MetricsServer | None:
    if port < 0:
        return None
    server, thread = start_http_server(port)
    actual = server.server_port
    logger.info("metrics server listening on port %d", actual)
    return MetricsServer(server=server, thread=thread, port=actual)
