"""TestClient acting as the page served on this machine (devialet_web/security.py).

Host is localhost, as for the browser on Windows, and every request carries the
X-Devialet-Request header the frontend sends; tests of the guard itself build
their own requests without it."""
from fastapi.testclient import TestClient

from devialet_web.security import CSRF_HEADER

LOCAL_URL = "http://localhost"


class LocalClient(TestClient):
    def websocket_connect(self, url: str, *args, **kw):
        # Starlette joins WebSocket paths to ws://testserver whatever base_url is
        if url.startswith("/"):
            url = "ws://localhost" + url
        return super().websocket_connect(url, *args, **kw)


def local_client(app, **kw) -> LocalClient:
    headers = {CSRF_HEADER: "1", **kw.pop("headers", {})}
    return LocalClient(app, base_url=kw.pop("base_url", LOCAL_URL), headers=headers, **kw)
