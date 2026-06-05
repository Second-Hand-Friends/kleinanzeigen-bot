# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal

if TYPE_CHECKING:
    from .model.ad_model import Ad

__all__ = [
    "RESET_FIELDS",
    "apply_after_delete_policy",
    "relative_ad_path",
]

RESET_FIELDS:Final[frozenset[str]] = frozenset({
    "id",
    "created_on",
    "updated_on",
    "content_hash",
    "repost_count",
    "price_reduction_count",
})


def relative_ad_path(ad_file:str | Path, config_file_path:str | Path) -> str:
    """Return `ad_file` relative to the config directory when possible."""
    try:
        return str(Path(ad_file).relative_to(Path(config_file_path).parent))
    except ValueError:
        return str(ad_file)


def apply_after_delete_policy(
    ad_cfg:Ad,
    ad_cfg_orig:dict[str, Any],
    *,
    mode:Literal["NONE", "RESET", "DISABLE"],
) -> bool:
    """Apply post-delete cleanup to the in-memory model and raw dict state."""
    if mode == "NONE":
        return False

    if mode == "RESET":
        for key in RESET_FIELDS:
            ad_cfg_orig.pop(key, None)
        ad_cfg.id = None
        ad_cfg.created_on = None
        ad_cfg.updated_on = None
        ad_cfg.content_hash = None
        ad_cfg.repost_count = 0
        ad_cfg.price_reduction_count = 0
        return True

    if mode == "DISABLE":
        ad_cfg.active = False
        ad_cfg_orig["active"] = False
        return True

    return False
