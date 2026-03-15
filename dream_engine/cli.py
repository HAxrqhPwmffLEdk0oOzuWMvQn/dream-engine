"""CLI entry point for Dream Engine."""

import sys

import uvicorn

from .config import settings


def main():
    # Check if configured
    if not settings.is_configured():
        from .setup import run_setup
        run_setup()
        # Reload settings after setup writes .env
        from importlib import reload
        import dream_engine.config
        reload(dream_engine.config)

    print(f"  Dream Engine starting on http://{settings.host}:{settings.port}")
    print(f"  Scanning: {settings.scan_paths}")
    print()

    uvicorn.run(
        "dream_engine.main:app",
        host=settings.host,
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
