#!/usr/bin/env python3
"""Générateur du document HTML5 autonome SEP-1865 pour le widget MCP App Gravity Metrics (100 % stdlib)."""

from .ui import APP_JS, CHARTS_JS, MARKDOWN_VIEW_JS, WIDGET_CSS


def render_mcp_widget_html() -> str:
    """Retourne le document HTML5 autonome SEP-1865 assemblé depuis `mcp/ui/`."""
    script_body = "\n".join(
        [
            "(function () {",
            '  "use strict";',
            CHARTS_JS.strip(),
            MARKDOWN_VIEW_JS.strip(),
            APP_JS.strip(),
            "})();",
        ]
    )
    return (
        "<!DOCTYPE html>\n"
        '<html lang="fr" data-theme="dark">\n'
        "<head>\n"
        '<meta charset="utf-8" />\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1" />\n'
        "<title>Gravity Metrics</title>\n"
        "<style>\n"
        f"{WIDGET_CSS.strip()}\n"
        "</style>\n"
        "</head>\n"
        "<body>\n"
        '<div id="root" class="app">\n'
        '  <div class="empty">Connexion à Gravity Metrics...</div>\n'
        "</div>\n"
        "<script>\n"
        f"{script_body}\n"
        "</script>\n"
        "</body>\n"
        "</html>\n"
    )
