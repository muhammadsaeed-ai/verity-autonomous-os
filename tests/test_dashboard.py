"""Structural and syntax checks for the static command-center dashboard."""

from __future__ import annotations

import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "docs" / "index.html"


class DashboardParser(HTMLParser):
    """Collect IDs, local anchors, and executable inline scripts."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: list[str] = []
        self.anchors: list[str] = []
        self.scripts: list[str] = []
        self._inside_script = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        element_id = attributes.get("id")
        if element_id:
            self.ids.append(element_id)
        href = attributes.get("href")
        if href and href.startswith("#"):
            self.anchors.append(href[1:])
        if tag == "script":
            self._inside_script = True
            self.scripts.append("")

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._inside_script = False

    def handle_data(self, data: str) -> None:
        if self._inside_script and self.scripts:
            self.scripts[-1] += data


def _parse_dashboard() -> tuple[str, DashboardParser]:
    source = DASHBOARD.read_text(encoding="utf-8")
    parser = DashboardParser()
    parser.feed(source)
    parser.close()
    return source, parser


def test_dashboard_has_professional_visual_and_operational_sections() -> None:
    source, _ = _parse_dashboard()
    for marker in (
        "Autonomous operations.",
        "mission-graph",
        "telemetry",
        "progress-chart",
        "replay-flow",
        "artifact-list",
        "prefers-reduced-motion",
        "aria-live",
        "state.json",
        "Mission health",
    ):
        assert marker in source
    assert (ROOT / "docs" / "assets" / "architecture.svg").is_file()


def test_dashboard_ids_are_unique_and_internal_links_resolve() -> None:
    _, parser = _parse_dashboard()
    assert len(parser.ids) == len(set(parser.ids)), "Dashboard contains duplicate HTML IDs"
    known = set(parser.ids)
    missing = sorted(set(parser.anchors) - known)
    assert not missing, "Unresolved dashboard anchors: " + ", ".join(missing)


def test_dashboard_inline_javascript_parses_when_node_is_available() -> None:
    _, parser = _parse_dashboard()
    assert parser.scripts, "Dashboard should include its interactive client logic"
    node = shutil.which("node")
    if not node:
        return
    with TemporaryDirectory(prefix="verity-dashboard-check-") as directory:
        for index, script in enumerate(parser.scripts):
            script_path = Path(directory) / ("dashboard-" + str(index) + ".js")
            script_path.write_text(script, encoding="utf-8")
            result = subprocess.run(
                [node, "--check", str(script_path)],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            assert result.returncode == 0, result.stderr or result.stdout
