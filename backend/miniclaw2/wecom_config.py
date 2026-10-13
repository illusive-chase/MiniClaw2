"""Host-local WeCom (企业微信) settings.

One bot per host: the credentials bind this machine to its own intelligent
bot, and they are as powerful as the bot itself (the same pair can be traded
for a token on WeCom's office-API gateway), so they live in a 0600 file that
the inventory excludes and the store ``.gitignore`` lists. No API response
ever carries the secret.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

WECOM_CONFIG_FILENAME = "wecom.local.json"


class WecomConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    #: Only gates sending cards; a bound host stays connected regardless.
    enabled: bool = True
    bot_id: str = ""
    secret: str = ""
    #: The WeCom userid that claimed the bot with the pairing code.
    bound_userid: str | None = None
    binding_source: Literal["created", "manual"] | None = None
    bound_at: float | None = None
    delay_seconds: int = Field(default=60, ge=0, le=24 * 3600)

    @property
    def has_credentials(self) -> bool:
        return bool(self.bot_id and self.secret)


def wecom_config_path(root: Path) -> Path:
    return root / WECOM_CONFIG_FILENAME


def load_wecom_config(root: Path) -> WecomConfig:
    path = wecom_config_path(root)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return WecomConfig()
    try:
        return WecomConfig.model_validate(payload)
    except ValidationError:
        return WecomConfig()


def save_wecom_config(root: Path, config: WecomConfig) -> None:
    path = wecom_config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(json.dumps(config.model_dump(), ensure_ascii=False, indent=2) + "\n")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def delete_wecom_config(root: Path) -> None:
    try:
        wecom_config_path(root).unlink()
    except FileNotFoundError:
        pass
