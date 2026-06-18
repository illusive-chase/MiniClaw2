"""Bundled virtual-node templates."""

from .launcher import launch_template
from .loader import Template, TemplateError, list_templates, load_template

__all__ = [
    "Template",
    "TemplateError",
    "launch_template",
    "list_templates",
    "load_template",
]
