"""Templates: bundled starter subgraphs plus user-authored templates.

Two entry points live here. ``launch_template`` (bundled) creates a fresh
temporary project and stamps its lane. ``apply_user_template`` stamps a
saved user template into an existing project's active planspace. Both
share the ``Template``/``TemplateNodeSpec`` schema in ``loader.py``.
"""

from .launcher import (
    apply_user_template,
    embedded_session_slug,
    embedded_session_template_id,
    launch_template,
    materialize_embedded_session,
)
from .loader import (
    SCHEMA_VERSION,
    Template,
    TemplateArgument,
    TemplateError,
    TemplateInput,
    list_templates,
    list_user_templates,
    load_template,
    load_user_template,
    user_templates_root,
)
from .serializer import (
    SerializerError,
    delete_user_template,
    rewrite_user_template,
    serialize_embedded_session,
    serialize_selection,
)

__all__ = [
    "SCHEMA_VERSION",
    "SerializerError",
    "Template",
    "TemplateArgument",
    "TemplateError",
    "TemplateInput",
    "apply_user_template",
    "delete_user_template",
    "embedded_session_slug",
    "embedded_session_template_id",
    "launch_template",
    "list_templates",
    "list_user_templates",
    "load_template",
    "load_user_template",
    "materialize_embedded_session",
    "rewrite_user_template",
    "serialize_embedded_session",
    "serialize_selection",
    "user_templates_root",
]
