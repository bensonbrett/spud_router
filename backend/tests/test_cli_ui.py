"""Tests for the spud-cli status bar's pending-changes indicator."""
from unittest.mock import patch

import cli.ui as ui


def _strip(text: str) -> str:
    import re
    return re.sub(r'\033\[[0-9;]*m', '', text)


class TestPendingChangesSegment:
    def test_shows_indicator_when_pending(self):
        with patch("cli.api.GET", return_value={"pending": True}):
            segment = ui._pending_changes_segment()
        assert "Unapplied changes" in _strip(segment)

    def test_empty_when_not_pending(self):
        with patch("cli.api.GET", return_value={"pending": False}):
            segment = ui._pending_changes_segment()
        assert segment == ""

    def test_empty_on_backend_error(self):
        with patch("cli.api.GET", side_effect=RuntimeError("unreachable")):
            segment = ui._pending_changes_segment()
        assert segment == ""

    def test_status_bar_includes_segment_when_pending(self, capsys):
        with patch("cli.api.GET", return_value={"pending": True}):
            ui.print_status_bar({"router": {}, "vlans": [], "tailscale": {}})
        out = capsys.readouterr().out
        assert "Unapplied changes" in _strip(out)
