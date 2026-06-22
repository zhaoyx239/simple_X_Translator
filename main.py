from __future__ import annotations

import uvicorn

from app import CONFIG, app


def main() -> None:
    server = CONFIG.get("server", {})
    uvicorn.run(
        app,
        host=server.get("host", "0.0.0.0"),
        port=int(server.get("port", 7654)),
    )


if __name__ == "__main__":
    main()
