"""PyInstaller GUI entry point for the frozen Windows application."""

from __future__ import annotations

from codex_quota.__main__ import main


if __name__ == "__main__":
    raise SystemExit(main())
