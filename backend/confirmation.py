# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Brett Benson (https://github.com/bensonbrett)
"""Shared bounds for the connectivity-confirmation watchdog."""

DEFAULT_CONFIRM_WINDOW_SECONDS = 120
MIN_CONFIRM_WINDOW_SECONDS = 10
MAX_CONFIRM_WINDOW_SECONDS = 3600


def validate_confirm_window(value: int) -> int:
    """Return a safe watchdog window or raise a clear validation error."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("confirm_window_seconds must be a whole number of seconds")
    if not MIN_CONFIRM_WINDOW_SECONDS <= value <= MAX_CONFIRM_WINDOW_SECONDS:
        raise ValueError(
            "confirm_window_seconds must be between "
            f"{MIN_CONFIRM_WINDOW_SECONDS} and {MAX_CONFIRM_WINDOW_SECONDS} seconds"
        )
    return value
