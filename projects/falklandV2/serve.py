from __future__ import annotations

import os
import logging

from projects.falklandV2 import webdash


def main() -> None:
    app = webdash.app
    port = int(os.environ.get("PORT", "5055"))
    host = os.environ.get("HOST") or os.environ.get("FLASK_RUN_HOST") or "0.0.0.0"
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.info("Starting Falkland V2 dashboard on http://%s:%s", host, port)
    app.run(host=host, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
