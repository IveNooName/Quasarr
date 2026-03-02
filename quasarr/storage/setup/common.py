# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

_COMMON_MODULE_NAME = "quasarr.storage._setup_common"
_COMMON_MODULE_PATH = Path(__file__).resolve().parent.parent / "setup.py"

if _COMMON_MODULE_NAME in sys.modules:
    _setup_common = sys.modules[_COMMON_MODULE_NAME]
else:
    _setup_common_spec = spec_from_file_location(
        _COMMON_MODULE_NAME, _COMMON_MODULE_PATH
    )
    _setup_common = module_from_spec(_setup_common_spec)
    sys.modules[_COMMON_MODULE_NAME] = _setup_common
    _setup_common_spec.loader.exec_module(_setup_common)

add_no_cache_headers = _setup_common.add_no_cache_headers
render_reconnect_success = _setup_common.render_reconnect_success
setup_auth = _setup_common.setup_auth

__all__ = [
    "add_no_cache_headers",
    "render_reconnect_success",
    "setup_auth",
]
