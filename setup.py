#!/usr/bin/env python3
"""
Copyright (C) 2021 Sebastian Thomschke and contributors
SPDX-License-Identifier: AGPL-3.0-or-later
"""
import sys, warnings
import setuptools

warnings.filterwarnings("ignore", message = "setup_requires is deprecated", category = setuptools.SetuptoolsDeprecationWarning)

setup_args = {}

if "py2exe" in sys.argv:
    import importlib.resources, glob, os, py2exe, zipfile

    # py2exe config https://www.py2exe.org/index.cgi/ListOfOptions
    setup_args["options"] = {
        "py2exe": {
            "bundle_files": 1,  # 1 = include the python runtime
            "compressed": True,
            "optimize": 2,
            "includes": [
                "kleinanzeigen_bot"
            ],
            "excludes": [
                "_aix_support",
                "_osx_support",
                "argparse",
                "backports",
                "bz2",
                "doctest",
                "ftplib",
                "lzma",
                "pip",
                "pydoc",
                "pydoc_data",
                "optparse",
                "pyexpat",
                "six",
                "statistics",
                "test",
                "unittest",
                "xml.sax"
            ]
        }
    }
    setup_args["console"] = [{
        "script": "kleinanzeigen_bot/__main__.py",
        "dest_base": "kleinanzeigen-bot",
    }]
    setup_args["zipfile"] = None

    #
    # embedding required DLLs directly into the exe
    #
    # http://www.py2exe.org/index.cgi/OverridingCriteraForIncludingDlls
    bundle_dlls = ("libcrypto", "libffi", "libssl")
    orig_determine_dll_type = py2exe.dllfinder.DllFinder.determine_dll_type

    def determine_dll_type(self, dll_filepath):
        basename = os.path.basename(dll_filepath)
        if basename.startswith(bundle_dlls):
            return "EXT"
        return orig_determine_dll_type(self, dll_filepath)

    py2exe.dllfinder.DllFinder.determine_dll_type = determine_dll_type

    #
    # embedding required resource files directly into the exe
    #
    files_to_embed = [
        ("kleinanzeigen_bot/resources", "kleinanzeigen_bot/resources/*.yaml"),
        ("certifi", importlib.resources.path("certifi", "cacert.pem")),
        ("selenium_stealth", os.path.dirname(importlib.resources.path("selenium_stealth.js", "util.js")))
    ]

    orig_copy_files = py2exe.runtime.Runtime.copy_files

    def embed_files(self, destdir):
        orig_copy_files(self, destdir)

        libpath = os.path.join(destdir, "kleinanzeigen-bot.exe")
        with zipfile.ZipFile(libpath, "a", zipfile.ZIP_DEFLATED if self.options.compress else zipfile.ZIP_STORED) as arc:
            for target, source in files_to_embed:
                print(source)
                if os.path.isdir(source):
                    for file in os.listdir(source):
                        if self.options.verbose:
                            print(f"Embedding file {source}\\{file} in {libpath}")
                        arc.write(os.path.join(source, file), target + "/" + file)
                elif isinstance(source, str):
                    for file in glob.glob(source, root_dir = os.getcwd(), recursive = True):
                        if self.options.verbose:
                            print(f"Embedding file {file} in {libpath}")
                        arc.write(file, target + "/" + os.path.basename(file))
                else:
                    if self.options.verbose:
                        print(f"Embedding file {source} in {libpath}")
                    arc.write(source, target + "/" + os.path.basename(source))
        os.remove(os.path.join(destdir, "cacert.pem"))  # file was embedded

    py2exe.runtime.Runtime.copy_files = embed_files

    #
    # use best zip compression level 9
    #
    from zipfile import ZipFile

    class ZipFileExt(ZipFile):

        def __init__(self, file, mode = "r", compression = zipfile.ZIP_STORED):
            super().__init__(file, mode, compression, compresslevel = 9)

    py2exe.runtime.zipfile.ZipFile = ZipFileExt

setuptools.setup(
    name = "kleinanzeigen-bot",
    use_scm_version = {
        'write_to': 'kleinanzeigen_bot/version.py',
    },
    packages = setuptools.find_packages(""),
    package_data = {'kleinanzeigen_bot': ['*.yaml']},

    # https://docs.python.org/3/distutils/setupscript.html#additional-meta-data
    author = "The kleinanzeigen-bot authors",
    url = "https://github.com/kleinanzeigen-bot/kleinanzeigen-bot",
    description = "Command line tool to publish ads on ebay-kleinanzeigen.de",
    license = 'GNU AGPL 3.0+',
    classifiers = [  # https://pypi.org/classifiers/
        'Development Status :: 4 - Beta',
        'Environment :: Console',
        'Operating System :: OS Independent',

        'Intended Audience :: End Users/Desktop',
        'Topic :: Office/Business',

        'License :: OSI Approved :: GNU Affero General Public License v3 or later (AGPLv3+)',
        'Programming Language :: Python :: 3.10',
    ],

    python_requires = '>=3.10',
    install_requires = open("requirements.txt", encoding = "utf-8").readlines(),  # pylint: disable=consider-using-with
    extras_require = {
        "dev": [
            "py2exe; sys_platform == 'win32'"
        ]
    },
    setup_requires = [
        'setuptools_scm'
    ],

    ** setup_args
)
