# -*- mode: python ; coding: utf-8 -*-
"""
SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

PyInstaller config file, see https://pyinstaller.readthedocs.io/en/stable/spec-files.html
"""
from PyInstaller.utils.hooks import collect_data_files

datas = [
    * collect_data_files("kleinanzeigen_bot"),  # embeds *.yaml files

    # required to get version info via 'importlib.metadata.version(__package__)'
    # but we use https://backend.pdm-project.org/metadata/#writing-dynamic-version-to-file
    # * copy_metadata('kleinanzeigen_bot'),
]

excluded_modules = [
    "_aix_support",
    "argparse",
    "bz2",
    "ftplib",
    "lzma",
    "mypy",  # wrongly included dev-dep
    "rich",  # wrongly included dev-dep (transitive dep of pip-audit)
    "setuptools",
    "smtplib",
    "statistics",
    "toml",  # wrongly included dev-dep (transitive dep of pip-audit)
    "tomllib",
    "tracemalloc",
    "xml.sax",
    "xmlrpc"
]

from sys import platform
if platform != "darwin":
    excluded_modules.append("_osx_support")

# https://github.com/pyinstaller/pyinstaller/blob/adceeab4c2901fba853b29f9ae2db7bb67667030/PyInstaller/building/build_main.py#L399
analysis = Analysis(
        ['src/kleinanzeigen_bot/__main__.py'],
        # pathex = [],
        # binaries = [],
        datas = datas,
        hiddenimports = ['pkg_resources'],
        # hookspath = [],
        # hooksconfig = {},
        excludes = excluded_modules,
        # runtime_hooks = [],
        # cipher = None, # Deprecated
        # win_no_prefer_redirets = False, # Deprecated
        # win_private_assemblies = False, # Deprecated
        # noarchive = False,
        # module_collection_mode = None,
        # optimize = -1
    )

# https://github.com/pyinstaller/pyinstaller/blob/adceeab4c2901fba853b29f9ae2db7bb67667030/PyInstaller/building/api.py#L52
pyz = PYZ(
        analysis.pure,  # tocs
        analysis.zipped_data,
        # name = None
    )

import os, shutil

# https://github.com/pyinstaller/pyinstaller/blob/adceeab4c2901fba853b29f9ae2db7bb67667030/PyInstaller/building/api.py#L363
exe = EXE(pyz,
        analysis.scripts,
        analysis.binaries,
        analysis.datas,
        # bootloader_ignore_signals = False,
        # console = True,
        # hide_console = None,
        # disable_windowed_traceback = False,
        # debug = False,
        name = 'kleinanzeigen-bot',
        # exclude_binaries = False,
        # icon = None,
        # version = None,
        # uac_admin = False,
        # uac_uiaccess = False,
        # argv_emulation = None,
        # target_arch = None,
        # codesign_identity = None,
        # entitlements_file = None,
        # contents_directory = "_internal",
        # using strip on windows results in "ImportError: Can't connect to HTTPS URL because the SSL module is not available."
        strip = not platform.startswith("win") and shutil.which("strip") is not None,
        upx = shutil.which("upx") is not None and not os.getenv("NO_UPX"),
        upx_exclude = [],
        runtime_tmpdir = None,
    )
