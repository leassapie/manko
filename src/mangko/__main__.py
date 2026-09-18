"""Entry point: python -m mangko"""

from mangko.bot import create_app, register_handlers
from mangko.config import get_settings
from mangko.database import init_db
from mangko.downloader import ensure_dependencies
from mangko.logging import get_logger, setup_logging


def main() -> None:
    setup_logging()
    logger = get_logger("mangko")

    # Auto-update from upstream before starting
    try:
        from mangko.updater import main as upstream_update

        upstream_update()
    except Exception as e:
        logger.warning("upstream update skipped", error=str(e))

    settings = get_settings()

    logger.info("Checking dependencies…")
    ensure_dependencies()

    # Initialize database
    init_db()
    logger.info("Database initialized")

    logger.info("Starting Mangko with Kurigram (MTProto)…")
    logger.info(
        "Configuration loaded",
        upload_channel=settings.upload_chat,
        dump_channel=settings.dump_chat,
    )

    app = create_app(settings)
    register_handlers(app, settings)
    app.run()


if __name__ == "__main__":
    main()
