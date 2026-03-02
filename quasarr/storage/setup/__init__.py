# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

from .common import (
    add_no_cache_headers,
    render_reconnect_success,
    setup_auth,
)
from .flaresolverr import (
    delete_skip_flaresolverr_preference,
    flaresolverr_config,
    flaresolverr_form_html,
    get_flaresolverr_status_data,
    save_flaresolverr_url,
)
from .hostnames import (
    check_credentials,
    clear_skip_login,
    get_skip_login,
    hostname_credentials_config,
    hostname_form_html,
    hostnames_config,
    import_hostnames_from_url,
    save_hostnames,
)
from .jdownloader import (
    jdownloader_config,
    save_jdownloader_settings,
    verify_jdownloader_credentials,
)
from .notifications import (
    get_notification_settings_data,
    initialize_notification_settings,
    refresh_notification_settings,
    save_notification_settings,
    send_notification_test,
)
from .path import path_config

__all__ = [
    "add_no_cache_headers",
    "check_credentials",
    "clear_skip_login",
    "delete_skip_flaresolverr_preference",
    "flaresolverr_config",
    "flaresolverr_form_html",
    "get_flaresolverr_status_data",
    "get_notification_settings_data",
    "get_skip_login",
    "hostname_credentials_config",
    "hostname_form_html",
    "hostnames_config",
    "import_hostnames_from_url",
    "initialize_notification_settings",
    "jdownloader_config",
    "path_config",
    "refresh_notification_settings",
    "render_reconnect_success",
    "save_flaresolverr_url",
    "save_hostnames",
    "save_jdownloader_settings",
    "save_notification_settings",
    "send_notification_test",
    "setup_auth",
    "verify_jdownloader_credentials",
]
