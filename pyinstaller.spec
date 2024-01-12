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
    * collect_data_files("selenium_stealth"),  # embeds *.js files

    # required to get version info via 'importlib.metadata.version(__package__)'
    # but we use https://backend.pdm-project.org/metadata/#writing-dynamic-version-to-file
    # * copy_metadata('kleinanzeigen_bot'),
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

# https://github.com/pyinstaller/pyinstaller/blob/f563dce1e83fd5ec72a20dffd2ac24be3e647150/PyInstaller/building/build_main.py#L320
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
        # cipher = None, # Deprecated
        # win_no_prefer_redirets = False, # Deprecated
        # win_private_assemblies = False, # Deprecated
        # noarchive = False,
        # module_collection_mode = None
    )

# https://github.com/pyinstaller/pyinstaller/blob/f563dce1e83fd5ec72a20dffd2ac24be3e647150/PyInstaller/building/api.py#L51
pyz = PYZ(
        analysis.pure,  # tocs
        analysis.zipped_data,
        # name = None
    )

import shutil

# https://github.com/pyinstaller/pyinstaller/blob/f563dce1e83fd5ec72a20dffd2ac24be3e647150/PyInstaller/building/api.py#L338
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
        strip = shutil.which("strip") is not None,
        upx = shutil.which("upx") is not None,
        upx_exclude = [],
        runtime_tmpdir = None,
    )
