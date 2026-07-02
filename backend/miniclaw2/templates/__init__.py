"""Templates: bundled starter subgraphs plus user-authored templates.

Two entry points live here. ``launch_template`` (bundled) creates a fresh
temporary project and stamps its lane. ``apply_user_template`` stamps a
saved user template into an existing project's active planspace. Both
share the ``Template``/``TemplateNodeSpec`` schema in ``loader.py``.
"""

from .launcher import apply_user_template, launch_template
from .loader import (
    Template,
    TemplateError,
    list_templates,
    list_user_templates,
    load_template,
    load_user_template,
    user_templates_root,
)
from .serializer import SerializerError, delete_user_template, serialize_selection

__all__ = [
    "SerializerError",
    "Template",
    "TemplateError",
    "apply_user_template",
    "delete_user_template",
    "launch_template",
    "list_templates",
    "list_user_templates",
    "load_template",
    "load_user_template",
    "serialize_selection",
    "user_templates_root",
]
