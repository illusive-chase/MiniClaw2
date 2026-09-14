"""持久化格式准入、迁移及恢复。"""

from .catalog import CURRENT_VERSION, MINIMUM_VERSION
from .errors import MigrationError

__all__ = ["CURRENT_VERSION", "MINIMUM_VERSION", "MigrationError"]
