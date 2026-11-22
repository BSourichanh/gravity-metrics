#!/usr/bin/env python3
"""Modules d'assemblage UI pour le widget MCP App Gravity Metrics (100 % stdlib)."""

from .app_js import APP_JS
from .charts import CHARTS_JS
from .markdown_view import MARKDOWN_VIEW_JS
from .styles import WIDGET_CSS

__all__ = [
    "WIDGET_CSS",
    "CHARTS_JS",
    "MARKDOWN_VIEW_JS",
    "APP_JS",
]
