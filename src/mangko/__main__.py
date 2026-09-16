"""Entry point: python -m mangko"""

import logging

from mangko.bot import create_app, register_handlers
from mangko.config import get_settings
from mangko.downloader import ensure_dependencies

logging.basicConfig(
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("mangko")


def main() -> None:
    # Auto-update from upstream before starting
    try:
        from mangko.updater import main as upstream_update

        upstream_update()
    except Exception as e:
        logger.warning("upstream update skipped: %s", e)

    settings = get_settings()

    logger.info("Checking dependencies…")
    ensure_dependencies()

    logger.info("Starting Mangko with Kurigram (MTProto)…")
    logger.info("UPLOAD_CHANNEL=%s  DUMP_CHANNEL=%s", settings.upload_chat, settings.dump_chat)

    app = create_app(settings)
    register_handlers(app, settings)
    app.run()


if __name__ == "__main__":
    main()
