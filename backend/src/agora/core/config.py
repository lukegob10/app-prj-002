"""Shared backend environment settings."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv


Environment = Literal["DEV", "PROD"]


def environment() -> Environment:
    """Load the project environment and return the selected runtime mode."""
    load_dotenv(Path.cwd() / ".env")
    selected = os.getenv("ENV", "DEV").strip().upper()
    if selected not in ("DEV", "PROD"):
        raise ValueError("ENV must be DEV or PROD")
    return selected  # type: ignore[return-value]
