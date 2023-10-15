# -*- mode: python ; coding: utf-8 -*-
"""
SPDX-FileCopyrightText: © Sebastian Thomschke and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
SPDX-ArtifactOfProjectHomePage: https://github.com/Second-Hand-Friends/kleinanzeigen-bot/

PyInstaller config file, see https://pyinstaller.readthedocs.io/en/stable/spec-files.html
"""
from PyInstaller.utils.hooks import copy_metadata, collect_data_files

datas = [
    * copy_metadata('kleinanzeigen_bot'),  # required to get version info
    * collect_data_files("kleinanzeigen_bot"),  # embeds *.yaml files
    * collect_data_files("selenium_stealth"),  # embeds *.js files
]

excluded_modules = [
    "_aix_support",
    "argparse",
    "backports",
    "bz2",
    "cryptography.hazmat",
    "distutils",
    "doctest",
    "ftplib",
    "lzma",
    "pep517",
    "pdb",
    "pip",
    "pydoc",
    "pydoc_data",
    "optparse",
    "setuptools",
    "six",
    "statistics",
    "test",
    "unittest",
    "xml.sax"
]

from sys import platform
if platform != "darwin":
    excluded_modules.append("_osx_support")

# https://github.com/pyinstaller/pyinstaller/blob/e7c252573f424ad9b79169ab01229d27599004b1/PyInstaller/building/build_main.py#L318
analysis = Analysis(
        ['kleinanzeigen_bot/__main__.py'],
        # pathex = [],
        # binaries = [],
        datas = datas,
        hiddenimports = ['pkg_resources'],
        # hookspath = [],
        # hooksconfig = {},
        excludes = excluded_modules,
        # runtime_hooks = [],
        # noarchive = False,
        # module_collection_mode = None
    )

# https://github.com/pyinstaller/pyinstaller/blob/e7c252573f424ad9b79169ab01229d27599004b1/PyInstaller/building/api.py#L51
pyz = PYZ(
        analysis.pure,  # tocs
        analysis.zipped_data,
        # name = None
    )

import shutil

# https://github.com/pyinstaller/pyinstaller/blob/e7c252573f424ad9b79169ab01229d27599004b1/PyInstaller/building/api.py#L338
exe = EXE(pyz,
        analysis.scripts,
        analysis.binaries,
        analysis.datas,
        # bootloader_ignore_signals = False,
        # console = True,
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
        strip = shutil.which("strip") is not None,
        upx = shutil.which("upx") is not None,
        upx_exclude = [],
        runtime_tmpdir = None,
    )
