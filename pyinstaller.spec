# -*- mode: python ; coding: utf-8 -*-
"""
Copyright (C) 2022 Sebastian Thomschke and contributors
SPDX-License-Identifier: AGPL-3.0-or-later

PyInstaller config file, see https://pyinstaller.readthedocs.io/en/stable/spec-files.html
"""
from PyInstaller.utils.hooks import copy_metadata, collect_data_files

datas = [
    * copy_metadata('kleinanzeigen_bot'),  # required to get version info
    * collect_data_files("kleinanzeigen_bot"),  # embeds *.yaml files
    * collect_data_files("selenium_stealth"),  # embeds *.js files
]

block_cipher = None

analysis = Analysis(
        ['kleinanzeigen_bot/__main__.py'],
        pathex = [],
        binaries = [],
        datas = datas,
        hiddenimports = ['pkg_resources'],
        hookspath = [],
        hooksconfig = {},
        runtime_hooks = [],
        excludes = [
            "_aix_support",
            "_osx_support",
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
        ],
        win_no_prefer_redirects = False,
        win_private_assemblies = False,
        cipher = block_cipher,
        noarchive = False
    )

pyz = PYZ(analysis.pure, analysis.zipped_data, cipher = block_cipher)

import shutil

exe = EXE(pyz,
        analysis.scripts,
        analysis.binaries,
        analysis.zipfiles,
        analysis.datas,
        [],
        name = 'kleinanzeigen-bot',
        debug = False,
        bootloader_ignore_signals = False,
        strip = shutil.which("strip") is not None,
        upx = shutil.which("upx") is not None,
        upx_exclude = [],
        runtime_tmpdir = None,
        console = True,
        disable_windowed_traceback = False,
        target_arch = None,
        codesign_identity = None,
        entitlements_file = None
    )
