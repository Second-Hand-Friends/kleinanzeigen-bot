# SPDX-FileCopyrightText: © Jens Bergmann and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

# This file makes the tests/ directory a Python package.
# It is required so that direct imports like 'from tests.conftest import ...' work correctly,
# and to avoid mypy errors about duplicate module names when using such imports.
# Pytest does not require this for fixture discovery, but Python and mypy do for package-style imports.
