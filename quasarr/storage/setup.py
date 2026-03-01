# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

import os
import re
import sys
from urllib.parse import urlparse

import requests
from bottle import Bottle, request, response

import quasarr
import quasarr.providers.html_images as images
import quasarr.providers.sessions.al
import quasarr.providers.sessions.dd
import quasarr.providers.sessions.dl
import quasarr.providers.sessions.nx
from quasarr.constants import FALLBACK_USER_AGENT
from quasarr.providers.auth import add_auth_hook, add_auth_routes
from quasarr.providers.hostname_issues import get_all_hostname_issues
from quasarr.providers.html_templates import (
    render_button,
    render_centered_html,
    render_fail,
    render_form,
    render_success,
)
from quasarr.providers.log import info
from quasarr.providers.notifications.notification_types import (
    NotificationType,
    get_user_configurable_notification_types,
)
from quasarr.providers.shared_state import extract_valid_hostname
from quasarr.providers.utils import (
    check_flaresolverr,
    extract_allowed_keys,
    extract_kv_pairs,
)
from quasarr.providers.web_server import Server
from quasarr.search.sources.helpers import get_login_required_hostnames
from quasarr.storage.config import Config
from quasarr.storage.sqlite_database import DataBase

NOTIFICATION_PROVIDERS = ("discord", "telegram")
NOTIFICATION_TYPES = get_user_configurable_notification_types()


def _notification_toggle_key(provider, notification_type):
    return f"{provider}_{notification_type.value}"


def _notification_silent_key(provider, notification_type):
    return f"{provider}_{notification_type.value}_silent"


def _ensure_notification_defaults(notification_config):
    existing_keys = {
        key for key, _key_type, _value in getattr(notification_config, "__config__", [])
    }
    changed = False

    for provider in NOTIFICATION_PROVIDERS:
        for notification_type in NOTIFICATION_TYPES:
            for config_key, default_value in (
                (_notification_toggle_key(provider, notification_type), "true"),
                (_notification_silent_key(provider, notification_type), "true"),
            ):
                if config_key in existing_keys:
                    continue
                notification_config.save(config_key, default_value)
                existing_keys.add(config_key)
                changed = True

    return changed


def _coerce_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def _read_notification_settings(backfill_defaults=False):
    notification_config = Config("Notifications")
    if backfill_defaults:
        _ensure_notification_defaults(notification_config)

    settings = {
        "discord_webhook": notification_config.get("discord_webhook") or "",
        "telegram_bot_token": notification_config.get("telegram_bot_token") or "",
        "telegram_chat_id": notification_config.get("telegram_chat_id") or "",
        "toggles": {"discord": {}, "telegram": {}},
        "silent": {"discord": {}, "telegram": {}},
    }

    for provider in NOTIFICATION_PROVIDERS:
        for notification_type in NOTIFICATION_TYPES:
            case_key = notification_type.value
            settings["toggles"][provider][case_key] = bool(
                notification_config.get(
                    _notification_toggle_key(provider, notification_type)
                )
            )
            settings["silent"][provider][case_key] = bool(
                notification_config.get(
                    _notification_silent_key(provider, notification_type)
                )
            )

    return settings


def _validate_notification_provider_credentials(
    discord_webhook,
    telegram_bot_token,
    telegram_chat_id,
):
    discord_webhook_pattern = r"^https://discord\.com/api/webhooks/\d+/[\w-]+$"
    telegram_token_pattern = r"^\d+:[A-Za-z0-9_-]{35,}$"

    if discord_webhook and not re.match(discord_webhook_pattern, discord_webhook):
        return "Invalid Discord Webhook URL"

    if telegram_bot_token or telegram_chat_id:
        if not telegram_bot_token or not telegram_chat_id:
            return "Telegram setup requires both bot token and chat ID"
        if not re.match(telegram_token_pattern, telegram_bot_token):
            return "Invalid Telegram bot token format"

    return None


def refresh_notification_settings(shared_state, backfill_defaults=False):
    settings = _read_notification_settings(backfill_defaults=backfill_defaults)

    # Keep legacy shared_state keys for compatibility with existing callers.
    shared_state.update("webhook", settings["discord_webhook"])
    shared_state.update("discord", settings["discord_webhook"])
    shared_state.update("telegram_bot_token", settings["telegram_bot_token"])
    shared_state.update("telegram_chat_id", settings["telegram_chat_id"])

    shared_state.update("notification_toggles", settings["toggles"])
    shared_state.update("notification_silent", settings["silent"])
    shared_state.update("notification_settings", settings)

    return settings


def initialize_notification_settings(shared_state):
    return refresh_notification_settings(shared_state, backfill_defaults=True)


def get_notification_settings_data(shared_state):
    response.content_type = "application/json"
    settings = refresh_notification_settings(shared_state)
    return {"success": True, "settings": settings}


def save_notification_settings(shared_state):
    response.content_type = "application/json"

    data = request.json
    if not isinstance(data, dict):
        return {"success": False, "message": "Invalid JSON payload"}

    discord_webhook = str(data.get("discord_webhook", "")).strip()
    telegram_bot_token = str(data.get("telegram_bot_token", "")).strip()
    telegram_chat_id = str(data.get("telegram_chat_id", "")).strip()
    toggles = data.get("toggles") if isinstance(data.get("toggles"), dict) else {}
    silent = data.get("silent") if isinstance(data.get("silent"), dict) else {}

    validation_error = _validate_notification_provider_credentials(
        discord_webhook,
        telegram_bot_token,
        telegram_chat_id,
    )
    if validation_error:
        return {"success": False, "message": validation_error}

    notification_config = Config("Notifications")
    notification_config.save("discord_webhook", discord_webhook)
    notification_config.save("telegram_bot_token", telegram_bot_token)
    notification_config.save("telegram_chat_id", telegram_chat_id)

    for provider in NOTIFICATION_PROVIDERS:
        provider_toggles = toggles.get(provider)
        provider_silent = silent.get(provider)
        for notification_type in NOTIFICATION_TYPES:
            enabled_key = _notification_toggle_key(provider, notification_type)
            current_enabled = bool(notification_config.get(enabled_key))
            if (
                isinstance(provider_toggles, dict)
                and notification_type.value in provider_toggles
            ):
                next_enabled = _coerce_bool(
                    provider_toggles.get(notification_type.value),
                    current_enabled,
                )
            else:
                next_enabled = current_enabled
            notification_config.save(enabled_key, "true" if next_enabled else "false")

            silent_key = _notification_silent_key(provider, notification_type)
            current_silent = bool(notification_config.get(silent_key))
            if (
                isinstance(provider_silent, dict)
                and notification_type.value in provider_silent
            ):
                next_silent = _coerce_bool(
                    provider_silent.get(notification_type.value),
                    current_silent,
                )
            else:
                next_silent = current_silent
            notification_config.save(silent_key, "true" if next_silent else "false")

    settings = refresh_notification_settings(shared_state)

    return {
        "success": True,
        "message": "Notification settings saved successfully",
        "settings": settings,
    }


def send_notification_test(shared_state):
    response.content_type = "application/json"

    data = request.json
    if not isinstance(data, dict):
        return {"success": False, "message": "Invalid JSON payload"}

    provider = str(data.get("provider", "")).strip().lower()
    if provider not in NOTIFICATION_PROVIDERS:
        return {"success": False, "message": "Unknown provider"}

    settings = refresh_notification_settings(shared_state)
    title = "Quasarr Notification Test"

    if provider == "discord":
        if not settings["discord_webhook"]:
            return {
                "success": False,
                "message": "Discord webhook is not configured",
            }
        from quasarr.providers.notifications import discord

        try:
            sent = discord.send(
                shared_state,
                title=title,
                case=NotificationType.TEST,
                details={"provider": "Discord"},
                silent=False,
            )
        except Exception as e:
            info(f"Discord test notification error: {e}")
            return {
                "success": False,
                "message": f"Failed to send Discord test message: {e}",
            }
        if sent:
            return {"success": True, "message": "Discord test message sent"}
        return {
            "success": False,
            "message": "Failed to send Discord test message",
        }

    if not settings["telegram_bot_token"] or not settings["telegram_chat_id"]:
        return {
            "success": False,
            "message": "Telegram bot token and chat ID are required",
        }

    from quasarr.providers.notifications import telegram

    try:
        sent = telegram.send(
            shared_state,
            title=title,
            case=NotificationType.TEST,
            details={"provider": "Telegram"},
            silent=False,
        )
    except Exception as e:
        info(f"Telegram test notification error: {e}")
        return {
            "success": False,
            "message": f"Failed to send Telegram test message: {e}",
        }
    if sent:
        return {"success": True, "message": "Telegram test message sent"}
    return {"success": False, "message": "Failed to send Telegram test message"}


def render_reconnect_success(message, countdown_seconds=3):
    """Render a success page that waits, then polls until the server is back online."""
    button_html = render_button(
        f"Continuing in {countdown_seconds}...",
        "secondary",
        {"id": "reconnectBtn", "disabled": "true"},
    )

    script = f"""
        <script>
            var remaining = {countdown_seconds};
            var btn = document.getElementById('reconnectBtn');

            var interval = setInterval(function() {{
                remaining--;
                btn.innerText = 'Continuing in ' + remaining + '...';
                if (remaining <= 0) {{
                    clearInterval(interval);
                    btn.innerText = 'Reconnecting...';
                    tryReconnect();
                }}
            }}, 1000);

            function tryReconnect() {{
                var attempts = 0;
                function attempt() {{
                    attempts++;
                    fetch('/', {{ method: 'HEAD', cache: 'no-store' }})
                    .then(function(response) {{
                        if (response.ok) {{
                            btn.innerText = 'Connected! Reloading...';
                            btn.className = 'btn-primary';
                            setTimeout(function() {{ window.location.href = '/'; }}, 500);
                        }} else {{
                            scheduleRetry();
                        }}
                    }})
                    .catch(function() {{
                        scheduleRetry();
                    }});
                }}
                function scheduleRetry() {{
                    btn.innerText = 'Reconnecting... (attempt ' + attempts + ')';
                    setTimeout(attempt, 1000);
                }}
                attempt();
            }}
        </script>
    """

    content = f'''<h1><img src="{images.logo}" type="image/webp" alt="Quasarr logo" class="logo"/>Quasarr</h1>
    <h2>✅ Success</h2>
    <p>{message}</p>
    {button_html}
    {script}
    '''
    return render_centered_html(content)


def add_no_cache_headers(app):
    """Add hooks to prevent browser caching of setup pages."""

    @app.hook("after_request")
    def set_no_cache():
        response.set_header("Cache-Control", "no-cache, no-store, must-revalidate")
        response.set_header("Pragma", "no-cache")
        response.set_header("Expires", "0")


def setup_auth(app):
    """Add authentication to setup app if enabled."""
    add_auth_routes(app)
    add_auth_hook(app)


def path_config(shared_state):
    app = Bottle()
    add_no_cache_headers(app)
    setup_auth(app)

    current_path = os.path.dirname(os.path.abspath(sys.argv[0]))

    @app.get("/")
    def config_form():
        config_form_html = f'''
            <form action="/api/config" method="post" onsubmit="return handleSubmit(this)">
                <label for="config_path">Path</label>
                <input type="text" id="config_path" name="config_path" placeholder="{current_path}"><br>
                {render_button("Save", "primary", {"type": "submit", "id": "submitBtn"})}
            </form>
            <script>
            var formSubmitted = false;
            function handleSubmit(form) {{
                if (formSubmitted) return false;
                formSubmitted = true;
                var btn = document.getElementById('submitBtn');
                if (btn) {{ btn.disabled = true; btn.textContent = 'Saving...'; }}
                return true;
            }}
            </script>
            '''
        return render_form(
            "Press 'Save' to set desired path for configuration", config_form_html
        )

    def set_config_path(config_path):
        config_path_file = "Quasarr.conf"

        if not config_path:
            config_path = current_path

        config_path = config_path.replace("\\", "/")
        config_path = config_path[:-1] if config_path.endswith("/") else config_path

        if not os.path.exists(config_path):
            os.makedirs(config_path)

        with open(config_path_file, "w") as f:
            f.write(config_path)

        return config_path

    @app.post("/api/config")
    def set_config():
        config_path = request.forms.get("config_path")
        config_path = set_config_path(config_path)
        quasarr.providers.web_server.temp_server_success = True
        return render_reconnect_success(f'Config path set to: "{config_path}"')

    info(
        f'Starting web server for config at: "{shared_state.values["external_address"]}".'
    )
    info("Please set desired config path there!")
    quasarr.providers.web_server.temp_server_success = False
    return Server(
        app, listen="0.0.0.0", port=shared_state.values["port"]
    ).serve_temporarily()


def _escape_js_for_html_attr(s):
    """Escape a string for use inside a JS string literal within an HTML attribute."""
    if s is None:
        return ""
    return (
        str(s)
        .replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace('"', "&quot;")
        .replace("\n", "\\n")
        .replace("\r", "")
    )


def hostname_form_html(shared_state, message, show_skip_management=False):
    hostname_fields = """
    <div class="hostname-row">
        <button type="button" class="{btn_class}" onclick="showStatusDetail(\'{id}\', \'{label}\', \'{status}\', \'{error_details_for_modal}\', \'{timestamp}\', \'{operation}\', \'{url}\', \'{user}\', \'{password}\', {supports_login})" title="{status_title}">
            <span class="status-indicator" id="status-{id}" data-status="{status}">{status_emoji}</span>
            {label}
        </button>
        <input type="text" id="{id}" name="{id}" placeholder="example.com" autocorrect="off" autocomplete="off" value="{value}">
    </div>
    """

    skip_indicator = """
    <div class="skip-indicator" id="skip-indicator-{id}" style="margin-top:-0.5rem; margin-bottom:0.75rem; padding:0.5rem; background:var(--code-bg, #f8f9fa); border-radius:0.25rem; font-size:0.875rem;">
        <span style="color:#dc3545;">⚠️ Login skipped. Please click header to enable!</span>
    </div>
    """

    field_html = []
    hostnames = Config("Hostnames")  # Load once outside the loop
    skip_login_db = DataBase("skip_login")
    hostname_issues = get_all_hostname_issues()

    for label in shared_state.values["sites"]:
        field_id = label.lower()

        # Get the current value (if any and non-empty)
        current_value = hostnames.get(field_id)
        if not current_value:
            current_value = ""  # Ensure it's empty if None or ""

        # Determine traffic light status
        is_login_skipped = (
            field_id in get_login_required_hostnames()
            and skip_login_db.retrieve(field_id)
        )
        issue = hostname_issues.get(field_id)
        timestamp = ""
        operation = ""

        if not current_value:
            status = "unset"
            status_emoji = "⚫️"
            status_title = "Hostname not configured"
            error_details_for_modal = "This hostname is not configured."
        elif is_login_skipped:
            status = "skipped"
            status_emoji = "🟡"
            status_title = "Login was skipped"
            error_details_for_modal = "Login was skipped for this site."
        elif issue:
            status = "error"
            status_emoji = "🔴"
            operation = issue.get("operation", "unknown")
            error_details_for_modal = issue.get(
                "error", "Unknown error"
            )  # Get the full error message
            timestamp = issue.get("timestamp", "")
            status_title = f"Error in {operation}"
        else:
            status = "ok"
            status_emoji = "🟢"
            status_title = "Working normally"
            error_details_for_modal = "Configured and working normally."

        # Get credentials
        user = ""
        password = ""
        supports_login = "false"
        if field_id in get_login_required_hostnames():
            supports_login = "true"
            section = "JUNKIES" if field_id in ["dj", "sj"] else field_id.upper()
            site_config = Config(section)
            user = site_config.get("user") or ""
            password = site_config.get("password") or ""

        btn_class = "btn-secondary" if status == "unset" else "btn-primary"

        field_html.append(
            hostname_fields.format(
                id=field_id,
                label=_escape_js_for_html_attr(label),
                value=current_value,
                status=status,
                status_emoji=status_emoji,
                status_title=status_title,
                error_details_for_modal=_escape_js_for_html_attr(
                    error_details_for_modal
                ),
                timestamp=timestamp,
                operation=_escape_js_for_html_attr(operation),
                url=_escape_js_for_html_attr(current_value),
                user=_escape_js_for_html_attr(user),
                password=_escape_js_for_html_attr(password),
                supports_login=supports_login,
                btn_class=btn_class,
            )
        )

        # Add skip indicator for login-required sites if skip management is enabled
        if show_skip_management and field_id in get_login_required_hostnames():
            if current_value and skip_login_db.retrieve(field_id):
                field_html.append(skip_indicator.format(id=field_id))

    hostname_form_content = "".join(field_html)
    button_html = render_button(
        "Save", "primary", {"type": "submit", "id": "submitBtn"}
    )

    # Get stored hostnames URL if available
    stored_url = Config("Settings").get("hostnames_url") or ""

    # Check if FlareSolverr is skipped
    skip_flaresolverr_db = DataBase("skip_flaresolverr")
    is_flaresolverr_skipped = bool(skip_flaresolverr_db.retrieve("skipped"))

    template = """
<style>
    .url-import-section {{
        border: 1px solid var(--divider-color, #dee2e6);
        border-radius: 0.5rem;
        padding: 1rem;
        margin-bottom: 1.5rem;
        background: var(--code-bg, #f8f9fa);
    }}
    .url-import-section h3 {{
        margin: 0 0 0.75rem 0;
        font-size: 1rem;
        font-weight: 600;
    }}
    .url-import-row {{
        display: flex;
        gap: 0.5rem;
        align-items: stretch;
    }}
    .url-import-row input {{
        flex: 1;
        margin-bottom: 0;
    }}
    .url-import-row button {{
        margin-top: 0;
        white-space: nowrap;
    }}
    .import-status {{
        margin-top: 0.5rem;
        font-size: 0.875rem;
    }}
    .import-status.empty {{
        display: none;
    }}
    .import-status.success {{ color: #198754; }}
    .import-status.error {{ color: #dc3545; }}
    .import-status.loading {{ color: var(--secondary, #6c757d); }}
    .status-indicator {{
        transition: transform 0.1s ease;
    }}
    .status-indicator:hover {{
        transform: scale(1.2);
    }}
    .btn-subtle {{
        background: transparent;
        color: var(--fg-color, #212529);
        border: 1px solid var(--btn-subtle-border, #ced4da);
        padding: 0.25rem 0.5rem;
        border-radius: 0.25rem;
        cursor: pointer;
        font-size: 0.875rem;
    }}
    .btn-subtle:hover {{
        background: var(--btn-subtle-bg, #e9ecef);
    }}
    .hostname-row {{
        display: flex;
        gap: 0.5rem;
        margin-bottom: 0.75rem;
        align-items: stretch;
    }}
    .hostname-row button {{
        margin-top: 0;
        margin-bottom: 0;
        white-space: nowrap;
        min-width: 6rem;
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 0.4rem;
    }}
    .hostname-row input {{
        flex: 1;
        margin-bottom: 0;
    }}
</style>

<div id="message" style="margin-bottom:0.5em;">{message}</div>
<div id="error-msg" style="color:red; margin-bottom:1em;"></div>

<div class="url-import-section">
    <h3>📥 Import from URL</h3>
    <div class="url-import-row">
        <input type="url" id="hostnamesUrl" placeholder="https://quasarr-host.name/ini?token=123..." value="{stored_url}">
        <button type="button" class="btn-secondary" id="importBtn" onclick="importHostnames()">Import</button>
    </div>
    <div id="importStatus" class="import-status"></div>
    <p style="font-size:0.75rem; color:var(--secondary, #6c757d); margin:0.5rem 0 0 0;">
        Paste a URL containing hostname definitions (one valid Hostname per line "ab = xyz")
    </p>
</div>

<form action="/api/hostnames" method="post" onsubmit="return validateHostnames(this)">
    <input type="hidden" id="hostnamesUrlHidden" name="hostnames_url" value="{stored_url}">
    {hostname_form_content}
    {button}
</form>

<script>
  var formSubmitted = false;
  var isFlaresolverrSkipped = {is_flaresolverr_skipped};

  function validateHostnames(form) {{
    if (formSubmitted) return false;

    var errorDiv = document.getElementById('error-msg');
    errorDiv.textContent = '';

    var inputs = form.querySelectorAll('input[type="text"]:not(#hostnamesUrl)');
    for (var i = 0; i < inputs.length; i++) {{
      if (inputs[i].value.trim() !== '') {{
        formSubmitted = true;
        var btn = document.getElementById('submitBtn');
        if (btn) {{ btn.disabled = true; btn.textContent = 'Saving...'; }}
        // Sync the URL field to hidden input
        document.getElementById('hostnamesUrlHidden').value = document.getElementById('hostnamesUrl').value.trim();
        return true;
      }}
    }}

    errorDiv.textContent = 'Please fill in at least one hostname!';
    return false;
  }}

  function importHostnames() {{
    var urlInput = document.getElementById('hostnamesUrl');
    var url = urlInput.value.trim();
    var statusDiv = document.getElementById('importStatus');
    var importBtn = document.getElementById('importBtn');

    if (!url) {{
      statusDiv.className = 'import-status error';
      statusDiv.textContent = 'Please enter a URL';
      return;
    }}

    statusDiv.className = 'import-status loading';
    statusDiv.textContent = 'Importing...';
    importBtn.disabled = true;
    importBtn.textContent = 'Importing...';

    fetch('/api/hostnames/import-url', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ url: url }})
    }})
    .then(response => response.json())
    .then(data => {{
      importBtn.disabled = false;
      importBtn.textContent = 'Import';

      if (data.success) {{
        var count = 0;
        for (var key in data.hostnames) {{
          var input = document.getElementById(key);
          if (input) {{
            input.value = data.hostnames[key];
            count++;
          }}
        }}
        statusDiv.className = 'import-status success';
        var msg = 'Imported ' + count + ' hostname(s)';
        if (data.errors && Object.keys(data.errors).length > 0) {{
          msg += ' (' + Object.keys(data.errors).length + ' invalid)';
        }}
        statusDiv.textContent = msg + '. Review and click Save.';
      }} else {{
        statusDiv.className = 'import-status error';
        statusDiv.textContent = data.error || 'Import failed';
      }}
    }})
    .catch(error => {{
      importBtn.disabled = false;
      importBtn.textContent = 'Import';
      statusDiv.className = 'import-status error';
      statusDiv.textContent = 'Network error: ' + error.message;
    }});
  }}

  function confirmRestart() {{
    showModal('Restart Quasarr?', 'Are you sure you want to restart Quasarr now? Any unsaved changes will be lost.', 
        `<button class="btn-secondary" onclick="closeModal()">Cancel</button>
         <button class="btn-primary" onclick="performRestart()">Restart</button>`
    );
  }}

  function performRestart() {{
    closeModal();
    fetch('/api/restart', {{ method: 'POST' }})
    .then(response => response.json())
    .then(data => {{
      if (data.success) {{
        showRestartOverlay();
      }}
    }})
    .catch(error => {{
      // Expected - connection will be lost during restart
      showRestartOverlay();
    }});
  }}

  function showRestartOverlay() {{
    document.body.innerHTML = `
      <div style="text-align:center; padding:2rem; font-family:system-ui,-apple-system,sans-serif;">
        <h2>Restarting Quasarr...</h2>
        <p id="restartStatus">Waiting <span id="countdown">10</span> seconds...</p>
        <div id="spinner" style="display:none; margin-top:1rem;">
          <div style="display:inline-block; width:24px; height:24px; border:3px solid #ccc; border-top-color:#333; border-radius:50%; animation:spin 1s linear infinite;"></div>
          <style>@keyframes spin {{ to {{ transform: rotate(360deg); }} }}</style>
        </div>
      </div>
    `;
    startCountdown(10);
  }}

  function startCountdown(seconds) {{
    var countdownEl = document.getElementById('countdown');
    var statusEl = document.getElementById('restartStatus');
    var spinnerEl = document.getElementById('spinner');

    var remaining = seconds;
    var interval = setInterval(function() {{
      remaining--;
      if (countdownEl) countdownEl.textContent = remaining;

      if (remaining <= 0) {{
        clearInterval(interval);
        statusEl.textContent = 'Reconnecting...';
        spinnerEl.style.display = 'block';
        tryReconnect();
      }}
    }}, 1000);
  }}

  function tryReconnect() {{
    var statusEl = document.getElementById('restartStatus');
    var attempts = 0;

    function attempt() {{
      attempts++;
      fetch('/', {{ method: 'HEAD', cache: 'no-store' }})
      .then(response => {{
        if (response.ok) {{
          statusEl.textContent = 'Connected! Reloading...';
          setTimeout(function() {{ window.location.href = '/'; }}, 500);
        }} else {{
          scheduleRetry();
        }}
      }})
      .catch(function() {{
        scheduleRetry();
      }});
    }}

    function scheduleRetry() {{
      statusEl.textContent = 'Reconnecting... (attempt ' + attempts + ')';
      setTimeout(attempt, 1000);
    }}

    attempt();
  }}
</script>
<script>
    function showStatusDetail(id, label, status, error_details, timestamp, operation, url, user, password, supports_login) {{
        var statusTextMap = {{
            ok: 'Operational',
            error: 'Error',
            unset: 'Not configured',
            skipped: 'Login skipped',
            info: 'Information'
        }};

        var emojiMap = {{
            ok: '🟢',
            error: '🔴',
            unset: '⚫️',
            skipped: '🟡',
            info: 'ℹ️'
        }};

        var content_html = '';
        if (status === 'error') {{
            content_html += '<p>' + (error_details || 'No details available.') + '</p>';
        }} else {{
            content_html += '<p>' + (error_details || 'No additional details available.') + '</p>';
        }}

        var timestamp_html = '';
        if (timestamp) {{
            var d = new Date(timestamp);
            var day = ("0" + d.getDate()).slice(-2);
            var month = ("0" + (d.getMonth() + 1)).slice(-2);
            var year = d.getFullYear();
            var hours = ("0" + d.getHours()).slice(-2);
            var minutes = ("0" + d.getMinutes()).slice(-2);
            var seconds = ("0" + d.getSeconds()).slice(-2);
            var formattedTimestamp = day + "." + month + "." + year + " " + hours + ":" + minutes + ":" + seconds;

            if (operation) {{
                timestamp_html = '<p><small>Occurred in ' + operation + ' at ' + formattedTimestamp + '</small></p>';
            }} else {{
                timestamp_html = '<p><small>Occurred at: ' + formattedTimestamp + '</small></p>';
            }}
        }}

        var credentials_html = '';
        if (url && supports_login) {{
             var flaresolverrWarning = '';
             if (id === 'al' && isFlaresolverrSkipped) {{
                flaresolverrWarning = `
                    <div style="margin-bottom: 1rem; padding: 0.75rem; background: #fff3cd; border: 1px solid #ffeeba; border-radius: 0.25rem; color: #856404; font-size: 0.875rem;">
                        <strong>⚠️ FlareSolverr Required</strong><br>
                        This site requires FlareSolverr, but it was skipped. You must configure it first.
                        <div style="margin-top: 0.5rem;">
                            <button class="btn-secondary" style="font-size: 0.75rem; padding: 0.25rem 0.5rem;" onclick="window.location.href='/flaresolverr'">Configure FlareSolverr</button>
                        </div>
                    </div>
                `;
             }}

             credentials_html = `
                <div style="margin-top: 1rem; border-top: 1px solid var(--divider-color, #dee2e6); padding-top: 1rem;">
                    <h4 style="margin-top:0; font-size:1rem;">Credentials</h4>
                    ${{flaresolverrWarning}}
                    <div style="margin-bottom: 0.5rem;">
                        <label style="display:block; font-size: 0.875rem;">Login</label>
                        <input type="text" id="cred-user-${{id}}" value="${{user}}" style="width: 100%; padding: 0.375rem 0.75rem; border: 1px solid #ced4da; border-radius: 0.25rem;">
                    </div>
                    <div style="margin-bottom: 0.5rem;">
                        <label style="display:block; font-size: 0.875rem;">Password</label>
                        <input type="password" id="cred-pass-${{id}}" value="${{password}}" style="width: 100%; padding: 0.375rem 0.75rem; border: 1px solid #ced4da; border-radius: 0.25rem;">
                    </div>
                    <div id="cred-status-${{id}}" style="margin-bottom: 0.5rem; font-size: 0.875rem; min-height: 1.25em;"></div>
                    <button class="btn-primary" onclick="saveAndCheckCredentials('${{id}}')">Check & Save Session</button>
                    <div style="margin-top: 1rem; border-bottom: 1px solid var(--divider-color, #dee2e6);"></div>
                </div>
            `;
        }}

        var content = content_html + timestamp_html + credentials_html;
        var title = '<span>' + (emojiMap[status] || 'ℹ️') + '</span> ' + label + ' - ' + (statusTextMap[status] || status);

        var buttons = '';
        if (url) {{
            var href = url;
            if (!href.startsWith('http://') && !href.startsWith('https://')) {{
                href = 'https://' + href;
            }}
            buttons = `
                <button class="btn-primary" style="margin-right: auto;" onclick="window.open('${{href}}', '_blank')">Open ${{id.toUpperCase()}}</button>
                <button class="btn-secondary" onclick="closeModal()">Close</button>
            `;
        }} else {{
            buttons = '<button class="btn-secondary" onclick="closeModal()">Close</button>';
        }}

        showModal(title, content, buttons);
    }}

    function saveAndCheckCredentials(id) {{
        var user = document.getElementById('cred-user-' + id).value;
        var pass = document.getElementById('cred-pass-' + id).value;
        var statusDiv = document.getElementById('cred-status-' + id);

        statusDiv.innerHTML = 'Checking...';
        statusDiv.style.color = 'var(--secondary, #6c757d)';

        fetch('/api/hostnames/check-credentials/' + id, {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ user: user, password: pass }})
        }})
        .then(response => response.json())
        .then(data => {{
            if (data.success) {{
                statusDiv.innerHTML = '✅ ' + data.message;
                statusDiv.style.color = '#198754';
                // Update the status indicator in the main list
                var indicator = document.getElementById('status-' + id);
                if (indicator) {{
                    indicator.textContent = '🟢';
                    indicator.setAttribute('data-status', 'ok');
                }}
                // Remove skip indicator if present
                var skipIndicator = document.getElementById('skip-indicator-' + id);
                if (skipIndicator) {{
                    skipIndicator.remove();
                }}
            }} else {{
                statusDiv.innerHTML = '❌ ' + data.message;
                statusDiv.style.color = '#dc3545';
            }}
        }})
        .catch(error => {{
            statusDiv.innerHTML = '❌ Error: ' + error.message;
            statusDiv.style.color = '#dc3545';
        }});
    }}
</script>
"""
    return template.format(
        message=message,
        hostname_form_content=hostname_form_content,
        button=button_html,
        stored_url=stored_url,
        is_flaresolverr_skipped="true" if is_flaresolverr_skipped else "false",
    )


def save_hostnames(shared_state, timeout=5, first_run=True):
    hostnames = Config("Hostnames")

    # Collect submitted hostnames, validate, and track errors
    valid_domains = {}
    errors = {}

    for site_key in shared_state.values["sites"]:
        shorthand = site_key.lower()
        raw_value = request.forms.get(shorthand)
        # treat missing or empty string as intentional clear, no validation
        if raw_value is None or raw_value.strip() == "":
            continue

        # non-empty submission: must validate
        result = extract_valid_hostname(raw_value, shorthand)
        domain = result.get("domain")
        message = result.get("message", "Error checking the hostname you provided!")
        if domain:
            valid_domains[site_key] = domain
        else:
            errors[site_key] = message

    # Filter out any accidental empty domains and require at least one valid hostname overall
    valid_domains = {k: d for k, d in valid_domains.items() if d}
    if not valid_domains:
        # report last or generic message
        fail_msg = next(iter(errors.values()), "No valid hostname provided!")
        return render_fail(fail_msg)

    # Save: valid ones, explicit empty for those omitted cleanly, leave untouched if error
    changed_sites = []
    for site_key in shared_state.values["sites"]:
        shorthand = site_key.lower()
        raw_value = request.forms.get(shorthand)
        # determine if change applies
        if site_key in valid_domains:
            new_val = valid_domains[site_key]
            old_val = hostnames.get(shorthand) or ""
            if old_val != new_val:
                hostnames.save(shorthand, new_val)
                changed_sites.append(shorthand)
        elif raw_value is None:
            # no submission: leave untouched
            continue
        elif raw_value.strip() == "":
            old_val = hostnames.get(shorthand) or ""
            if old_val != "":
                hostnames.save(shorthand, "")

    # Handle hostnames URL storage
    hostnames_url = request.forms.get("hostnames_url", "").strip()
    settings_config = Config("Settings")
    settings_config.save("hostnames_url", hostnames_url)

    quasarr.providers.web_server.temp_server_success = True

    # Build success message, include any per-site errors
    success_msg = "At least one valid hostname set!"
    if errors:
        optional_text = (
            "<br>".join(f"{site}: {msg}" for site, msg in errors.items()) + "<br>"
        )
    else:
        optional_text = "All provided hostnames are valid.<br>"

    full_message = f"{success_msg}<br><small>{optional_text}</small>"
    return render_reconnect_success(full_message)


def check_credentials(shared_state, shorthand):
    response.content_type = "application/json"
    try:
        data = request.json
        user = data.get("user")
        password = data.get("password")

        sh_lower = shorthand.lower()
        section = "JUNKIES" if sh_lower in ["dj", "sj"] else shorthand.upper()
        config = Config(section)

        # Store old credentials to revert if check fails
        old_user = config.get("user")
        old_password = config.get("password")

        # Temporarily save new credentials for the check
        config.save("user", user)
        config.save("password", password)

        success = False

        # Clear skip login if set (temporarily, will be restored if check fails?)
        # Actually, if user is trying to set credentials, they probably intend to stop skipping.
        # But if check fails, we might want to keep the skip status?
        # For now, let's assume if they try to check credentials, they want to use them.

        if sh_lower == "al":
            if quasarr.providers.sessions.al.create_and_persist_session(shared_state):
                success = True
                message = "Session valid!"
            else:
                message = "Session check failed (check logs)"
        elif sh_lower == "dd":
            if quasarr.providers.sessions.dd.create_and_persist_session(shared_state):
                success = True
                message = "Session valid!"
            else:
                message = "Session check failed (check logs)"
        elif sh_lower == "dl":
            if quasarr.providers.sessions.dl.create_and_persist_session(shared_state):
                success = True
                message = "Session valid!"
            else:
                message = "Session check failed (check logs)"
        elif sh_lower == "nx":
            if quasarr.providers.sessions.nx.create_and_persist_session(shared_state):
                success = True
                message = "Session valid!"
            else:
                message = "Session check failed (check logs)"
        elif sh_lower in ["dj", "sj"]:
            # dj and sj don't have session providers just save credentials
            success = True
            message = "Credentials saved"
        else:
            success = True
            message = "Credentials saved"

        if success:
            # If successful, ensure skip login is removed
            DataBase("skip_login").delete(sh_lower)
            # If dj/sj, also clear skip for the sibling
            if sh_lower == "dj":
                DataBase("skip_login").delete("sj")
            elif sh_lower == "sj":
                DataBase("skip_login").delete("dj")
        else:
            # If failed, revert credentials
            config.save("user", old_user)
            config.save("password", old_password)

        return {"success": success, "message": message}

    except Exception as e:
        return {"success": False, "message": str(e)}


def import_hostnames_from_url():
    """Fetch URL and parse hostnames, return JSON for JS to populate fields."""
    response.content_type = "application/json"
    try:
        data = request.json
        url = data.get("url", "").strip()

        if not url:
            return {"success": False, "error": "No URL provided"}

        # Validate URL
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return {"success": False, "error": "Invalid URL format"}

        # Fetch content
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            content = resp.text
        except requests.RequestException as e:
            info(f"Failed to fetch hostnames URL: {e}")
            return {
                "success": False,
                "error": "Failed to fetch URL. Check the console log for details.",
            }

        # Parse hostnames
        allowed_keys = extract_allowed_keys(Config._DEFAULT_CONFIG, "Hostnames")
        results = extract_kv_pairs(content, allowed_keys)

        if not results:
            return {
                "success": False,
                "error": "No hostnames found in the provided URL",
            }

        # Validate each hostname
        valid_hostnames = {}
        invalid_hostnames = {}
        for shorthand, hostname in results.items():
            domain_check = extract_valid_hostname(hostname, shorthand)
            domain = domain_check.get("domain")
            if domain:
                valid_hostnames[shorthand] = domain
            else:
                invalid_hostnames[shorthand] = domain_check.get("message", "Invalid")

        if not valid_hostnames:
            return {
                "success": False,
                "error": "No valid hostnames found in the provided URL",
            }

        return {
            "success": True,
            "hostnames": valid_hostnames,
            "errors": invalid_hostnames,
        }

    except Exception as e:
        return {"success": False, "error": f"Error: {str(e)}"}


def get_skip_login():
    """Return list of hostnames with skipped login."""
    response.content_type = "application/json"
    skip_db = DataBase("skip_login")
    skipped = []
    for site in get_login_required_hostnames():
        if skip_db.retrieve(site):
            skipped.append(site)
    return {"skipped": skipped}


def clear_skip_login(shorthand):
    """Clear skip login preference for a hostname."""
    response.content_type = "application/json"
    shorthand = shorthand.lower()
    if shorthand not in get_login_required_hostnames():
        return {"success": False, "error": f"Invalid shorthand: {shorthand}"}

    skip_db = DataBase("skip_login")
    skip_db.delete(shorthand)
    info(f'Skip login preference cleared for "{shorthand.upper()}"')
    return {"success": True}


def save_flaresolverr_url(shared_state, is_setup=False):
    """Save FlareSolverr URL from web UI."""
    url = request.forms.get("url", "").strip()
    config = Config("FlareSolverr")

    if not url:
        # If URL is empty, treat it as skipping FlareSolverr
        config.save("url", "")
        DataBase("skip_flaresolverr").update_store("skipped", "true")
        # Set fallback user agent
        shared_state.update("user_agent", FALLBACK_USER_AGENT)
        info("FlareSolverr URL cleared and setup skipped")

        if is_setup:
            quasarr.providers.web_server.temp_server_success = True

        return render_reconnect_success("FlareSolverr URL cleared.")

    if not url.startswith("http://") and not url.startswith("https://"):
        url = "http://" + url

    # Validate URL format
    if not re.search(r"/v\d+$", url):
        return render_fail(
            "FlareSolverr URL must end with /v1 (or similar version path)."
        )

    flaresolverr_check = check_flaresolverr(shared_state, url)
    if flaresolverr_check:
        config.save("url", url)
        # Clear skip preference since we now have a working URL
        DataBase("skip_flaresolverr").delete("skipped")

        info(
            f'FlareSolverr connection successful. Using User-Agent: "{shared_state.values["user_agent"]}"'
        )
        info(f'FlareSolverr URL configured: "{url}"')

        if is_setup:
            quasarr.providers.web_server.temp_server_success = True

        return render_reconnect_success("FlareSolverr URL saved successfully!")
    else:
        return render_fail("Could not reach FlareSolverr!")


def get_flaresolverr_status_data(shared_state):
    """Return FlareSolverr configuration status."""
    response.content_type = "application/json"
    skip_db = DataBase("skip_flaresolverr")
    is_skipped = bool(skip_db.retrieve("skipped"))
    current_url = Config("FlareSolverr").get("url") or ""

    # Test connection if URL is set
    is_working = False
    if current_url and not is_skipped:
        is_working = check_flaresolverr(shared_state, current_url)

    return {"skipped": is_skipped, "url": current_url, "working": is_working}


def delete_skip_flaresolverr_preference():
    """Clear skip FlareSolverr preference."""
    response.content_type = "application/json"
    skip_db = DataBase("skip_flaresolverr")
    skip_db.delete("skipped")
    info("Skip FlareSolverr preference cleared")
    return {"success": True}


def flaresolverr_form_html(shared_state, is_setup=False):
    skip_db = DataBase("skip_flaresolverr")
    is_skipped = skip_db.retrieve("skipped")
    current_url = Config("FlareSolverr").get("url") or ""

    skip_indicator = ""
    if is_skipped and not is_setup:
        skip_indicator = """
        <div class="skip-indicator" style="margin-bottom:1rem; padding:0.75rem; background:var(--code-bg, #f8f9fa); border-radius:0.25rem; font-size:0.875rem;">
            <span style="color:#dc3545;">⚠️ FlareSolverr setup was skipped</span>
            <p style="margin:0.5rem 0 0 0; font-size:0.75rem; color:var(--secondary, #6c757d);">
                Some sites (like AL) won't work until FlareSolverr is configured.
            </p>
        </div>
        """

    form_content = f'''
    {skip_indicator}
    <span><a href="https://github.com/FlareSolverr/FlareSolverr?tab=readme-ov-file#installation" target="_blank">FlareSolverr</a>
    must be running and reachable to Quasarr for some sites to work.</span><br><br>
    <label for="url">FlareSolverr URL</label>
    <input type="text" id="url" name="url" placeholder="http://192.168.0.1:8191/v1" value="{current_url}"><br>
    '''

    buttons = render_button("Save", "primary", {"type": "submit", "id": "submitBtn"})

    extra_js = ""

    if is_setup:
        buttons += ' <button type="button" class="btn-warning" id="skipBtn" onclick="skipFlaresolverr()">Skip for now</button>'
        extra_js = """
        function skipFlaresolverr() {
            if (formSubmitted) return;
            formSubmitted = true;
            var skipBtn = document.getElementById('skipBtn');
            var submitBtn = document.getElementById('submitBtn');
            if (skipBtn) { skipBtn.disabled = true; skipBtn.textContent = 'Skipping...'; }
            if (submitBtn) { submitBtn.disabled = true; }

            fetch('/api/flaresolverr/skip', { method: 'POST' })
            .then(response => {
                if (response.ok) {
                    window.location.href = '/skip-success';
                } else {
                    showModal('Error', 'Failed to skip FlareSolverr setup');
                    formSubmitted = false;
                    if (skipBtn) { skipBtn.disabled = false; skipBtn.textContent = 'Skip for now'; }
                    if (submitBtn) { submitBtn.disabled = false; }
                }
            })
            .catch(error => {
                showModal('Error', 'Error: ' + error.message);
                formSubmitted = false;
                if (skipBtn) { skipBtn.disabled = false; skipBtn.textContent = 'Skip for now'; }
                if (submitBtn) { submitBtn.disabled = false; }
            });
        }
        """

    form_html = f"""
    <style>
        .button-row {{
            display: flex;
            gap: 0.75rem;
            justify-content: center;
            flex-wrap: wrap;
            margin-top: 1rem;
        }}
        .btn-warning {{
            background-color: #ffc107;
            color: #212529;
            border: 1.5px solid #d39e00;
            padding: 0.5rem 1rem;
            font-size: 1rem;
            border-radius: 0.5rem;
            font-weight: 500;
            cursor: pointer;
        }}
        .btn-warning:hover {{
            background-color: #e0a800;
            border-color: #c69500;
        }}
    </style>
    <form action="/api/flaresolverr" method="post" onsubmit="return handleSubmit(this)">
        {form_content}
        <div class="button-row">
            {buttons}
        </div>
    </form>
    <script>
    var formSubmitted = false;
    function handleSubmit(form) {{
        if (formSubmitted) return false;
        formSubmitted = true;
        var btn = document.getElementById('submitBtn');
        if (btn) {{ btn.disabled = true; btn.textContent = 'Saving...'; }}
        var skipBtn = document.getElementById('skipBtn');
        if (skipBtn) {{ skipBtn.disabled = true; }}
        return true;
    }}
    {extra_js}
    </script>
    """

    if not is_setup:
        form_html += f"""<p>{render_button("Back", "secondary", {"onclick": "location.href='/'"})}</p>"""

    return form_html


def hostnames_config(shared_state):
    app = Bottle()
    add_no_cache_headers(app)
    setup_auth(app)

    @app.get("/")
    def hostname_form():
        message = """<p>
          If you're having trouble setting this up, take a closer look at 
          <a href="https://github.com/rix1337/Quasarr?tab=readme-ov-file#quasarr" target="_blank" rel="noopener noreferrer">
            the instructions.
          </a>
        </p>"""
        return render_form(
            "Set at least one valid hostname", hostname_form_html(shared_state, message)
        )

    @app.post("/api/hostnames")
    def set_hostnames():
        return save_hostnames(shared_state)

    @app.post("/api/hostnames/import-url")
    def import_hostnames_route():
        return import_hostnames_from_url()

    @app.get("/api/skip-login")
    def get_skip_login_route():
        return get_skip_login()

    @app.delete("/api/skip-login/<shorthand>")
    def clear_skip_login_route(shorthand):
        return clear_skip_login(shorthand)

    @app.post("/api/hostnames/check-credentials/<shorthand>")
    def check_credentials_route(shorthand):
        return check_credentials(shared_state, shorthand)

    info(
        f'Hostnames not set. Starting web server for config at: "{shared_state.values["external_address"]}".'
    )
    info("Please set at least one valid hostname there!")
    quasarr.providers.web_server.temp_server_success = False
    return Server(
        app, listen="0.0.0.0", port=shared_state.values["port"]
    ).serve_temporarily()


def hostname_credentials_config(shared_state, shorthand, domain):
    app = Bottle()
    add_no_cache_headers(app)
    setup_auth(app)

    shorthand = shorthand.upper()

    @app.post("/api/flaresolverr_inline")
    def set_flaresolverr_inline():
        return save_flaresolverr_url(shared_state, is_setup=False)

    @app.get("/")
    def credentials_form():
        flaresolverr_url = Config("FlareSolverr").get("url")

        is_al_missing_flaresolverr = shorthand == "AL" and not flaresolverr_url

        flaresolverr_section = ""

        if is_al_missing_flaresolverr:
            flaresolverr_section = """
             <div style="margin-bottom: 1.5rem; padding: 1rem; background: #fff3cd; border: 1px solid #ffeeba; border-radius: 0.5rem;">
                <h4 style="margin-top:0; font-size:1rem; color:#856404;">⚠️ FlareSolverr Required</h4>
                <p style="font-size:0.875rem; margin-bottom:0.5rem; color:#856404;">
                    This site requires FlareSolverr. Please configure it below before checking credentials.
                </p>
                <form action="/api/flaresolverr_inline" method="post" onsubmit="return handleFlareSolverrSubmit(this)">
                    <div style="display:flex; gap:0.5rem;">
                        <input type="text" name="url" placeholder="http://192.168.0.1:8191/v1" style="flex:1; margin-bottom:0;">
                        <button type="submit" class="btn-secondary" id="fsSubmitBtn" style="margin-top:0;">Save URL</button>
                    </div>
                </form>
             </div>
             <script>
             function handleFlareSolverrSubmit(form) {{
                 var btn = document.getElementById('fsSubmitBtn');
                 if (btn) {{ btn.disabled = true; btn.textContent = 'Saving...'; }}
                 return true;
             }}
             </script>
             """

        disabled_attr = "disabled" if is_al_missing_flaresolverr else ""

        credentials_inputs = f"""
        <span>If required register account at: <a href="https://{domain}">{domain}</a>!</span><br><br>
        <label for="user">Login</label>
        <input type="text" id="user" name="user" placeholder="User" autocorrect="off" {disabled_attr}><br>

        <label for="password">Password</label>
        <input type="password" id="password" name="password" placeholder="Password" {disabled_attr}><br>
        """

        form_html = f"""
        <style>
            .button-row {{
                display: flex;
                gap: 0.75rem;
                justify-content: center;
                flex-wrap: wrap;
                margin-top: 1rem;
            }}
            .btn-warning {{
                background-color: #ffc107;
                color: #212529;
                border: 1.5px solid #d39e00;
                padding: 0.5rem 1rem;
                font-size: 1rem;
                border-radius: 0.5rem;
                font-weight: 500;
                cursor: pointer;
            }}
            .btn-warning:hover {{
                background-color: #e0a800;
                border-color: #c69500;
            }}
        </style>
        {flaresolverr_section}
        <form id="credentialsForm" action="/api/credentials/{shorthand}" method="post" onsubmit="return handleSubmit(this)">
            {credentials_inputs}
            <div class="button-row">
                {render_button("Save", "primary", {"type": "submit", "id": "submitBtn"})}
                <button type="button" class="btn-warning" id="skipBtn" onclick="skipLogin()">Skip for now</button>
            </div>
        </form>
        <p style="font-size:0.875rem; color:var(--secondary, #6c757d); margin-top:1rem;">
            Skipping will allow Quasarr to start, but this site won't work until credentials are provided.
        </p>
        <script>
        var formSubmitted = false;
        var isAlMissingFlaresolverr = {"true" if is_al_missing_flaresolverr else "false"};

        function handleSubmit(form) {{
            if (isAlMissingFlaresolverr) {{
                showModal('FlareSolverr Required', 'You must configure FlareSolverr below or skip login for this site.');
                return false;
            }}

            if (formSubmitted) return false;
            formSubmitted = true;
            var btn = document.getElementById('submitBtn');
            if (btn) {{ btn.disabled = true; btn.textContent = 'Saving...'; }}
            document.getElementById('skipBtn').disabled = true;
            return true;
        }}
        function skipLogin() {{
            if (formSubmitted) return;
            formSubmitted = true;
            var skipBtn = document.getElementById('skipBtn');
            var submitBtn = document.getElementById('submitBtn');
            if (skipBtn) {{ skipBtn.disabled = true; skipBtn.textContent = 'Skipping...'; }}
            if (submitBtn) {{ submitBtn.disabled = true; }}

            fetch('/api/credentials/{shorthand}/skip', {{ method: 'POST' }})
            .then(response => {{
                if (response.ok) {{
                    window.location.href = '/skip-success';
                }} else {{
                    showModal('Error', 'Failed to skip login');
                    formSubmitted = false;
                    if (skipBtn) {{ skipBtn.disabled = false; skipBtn.textContent = 'Skip for now'; }}
                    if (submitBtn) {{ submitBtn.disabled = false; }}
                }}
            }})
            .catch(error => {{
                showModal('Error', 'Error: ' + error.message);
                formSubmitted = false;
                if (skipBtn) {{ skipBtn.disabled = false; skipBtn.textContent = 'Skip for now'; }}
                if (submitBtn) {{ submitBtn.disabled = false; }}
            }});
        }}
        </script>
        """

        return render_form(f"Set User and Password for {shorthand}", form_html)

    @app.get("/skip-success")
    def skip_success():
        return render_reconnect_success(
            f"{shorthand} login skipped. You can configure credentials later in the web UI."
        )

    @app.post("/api/credentials/<sh>/skip")
    def skip_credentials(sh):
        """Skip login for this hostname and continue startup."""
        sh_lower = sh.lower()
        DataBase("skip_login").update_store(sh_lower, "true")
        # If dj/sj, also skip for the sibling
        if sh_lower == "dj":
            DataBase("skip_login").update_store("sj", "true")
        elif sh_lower == "sj":
            DataBase("skip_login").update_store("dj", "true")
        info(f'Login for "{sh}" skipped by user choice')
        quasarr.providers.web_server.temp_server_success = True
        return {"success": True}

    @app.post("/api/credentials/<sh>")
    def set_credentials(sh):
        # Guard against duplicate submissions (e.g., double-click)
        if quasarr.providers.web_server.temp_server_success:
            return render_success(f"{sh} credentials already being processed", 5)

        user = request.forms.get("user")
        password = request.forms.get("password")
        sh_lower = sh.lower()
        section = "JUNKIES" if sh_lower in ["dj", "sj"] else sh.upper()
        config = Config(section)

        error_message = "User and Password wrong or empty!"

        if user and password:
            config.save("user", user)
            config.save("password", password)

            # Clear any skip preference since we now have credentials
            DataBase("skip_login").delete(sh_lower)
            # If dj/sj, also clear skip for the sibling
            if sh_lower == "dj":
                DataBase("skip_login").delete("sj")
            elif sh_lower == "sj":
                DataBase("skip_login").delete("dj")

            if sh_lower == "al":
                error_message = (
                    "User and Password wrong or empty.<br><br>"
                    "Or if you skipped Flaresolverr setup earlier, "
                    "you must chose to skip login for this site, "
                    "set up FlareSolverr in the UI and then restart Quasarr!"
                )
                if quasarr.providers.sessions.al.create_and_persist_session(
                    shared_state
                ):
                    quasarr.providers.web_server.temp_server_success = True
                    return render_reconnect_success(
                        f"{sh} credentials set successfully"
                    )
            elif sh_lower == "dd":
                if quasarr.providers.sessions.dd.create_and_persist_session(
                    shared_state
                ):
                    quasarr.providers.web_server.temp_server_success = True
                    return render_reconnect_success(
                        f"{sh} credentials set successfully"
                    )
            elif sh_lower == "dl":
                if quasarr.providers.sessions.dl.create_and_persist_session(
                    shared_state
                ):
                    quasarr.providers.web_server.temp_server_success = True
                    return render_reconnect_success(
                        f"{sh} credentials set successfully"
                    )
            elif sh_lower == "nx":
                if quasarr.providers.sessions.nx.create_and_persist_session(
                    shared_state
                ):
                    quasarr.providers.web_server.temp_server_success = True
                    return render_reconnect_success(
                        f"{sh} credentials set successfully"
                    )
            elif sh_lower in ["dj", "sj"]:
                # dj and sj don't have session providers yet, just save credentials
                quasarr.providers.web_server.temp_server_success = True
                return render_reconnect_success(f"{sh} credentials set successfully")
            else:
                quasarr.providers.web_server.temp_server_success = False
                return render_fail(f"Unknown site shorthand! ({sh})")

        config.save("user", "")
        config.save("password", "")
        return render_fail(error_message)

    info(
        f'"{shorthand.lower()}" credentials required to access download links. '
        f'Starting web server for config at: "{shared_state.values["external_address"]}".'
    )
    info(f"If needed register here: 'https://{domain}'")
    info("Please set your credentials now, or skip to allow Quasarr to launch!")
    quasarr.providers.web_server.temp_server_success = False
    return Server(
        app, listen="0.0.0.0", port=shared_state.values["port"]
    ).serve_temporarily()


def flaresolverr_config(shared_state):
    app = Bottle()
    add_no_cache_headers(app)
    setup_auth(app)

    @app.get("/")
    def url_form():
        return render_form(
            "Set FlareSolverr URL", flaresolverr_form_html(shared_state, is_setup=True)
        )

    @app.get("/skip-success")
    def skip_success():
        return render_reconnect_success(
            "FlareSolverr setup skipped. Some sites (like AL) won't work. You can configure it later in the web UI."
        )

    @app.post("/api/flaresolverr/skip")
    def skip_flaresolverr():
        """Skip FlareSolverr setup and continue startup."""
        DataBase("skip_flaresolverr").update_store("skipped", "true")
        # Set fallback user agent
        shared_state.update("user_agent", FALLBACK_USER_AGENT)
        info("FlareSolverr setup skipped by user choice")
        quasarr.providers.web_server.temp_server_success = True
        return {"success": True}

    @app.post("/api/flaresolverr")
    def set_flaresolverr_url():
        return save_flaresolverr_url(shared_state, is_setup=True)

    info(
        '"flaresolverr" URL is required for some sites (like AL). '
        f'Starting web server for config at: "{shared_state.values["external_address"]}".'
    )
    info("Please enter your FlareSolverr URL now, or skip to allow Quasarr to launch!")
    quasarr.providers.web_server.temp_server_success = False
    return Server(
        app, listen="0.0.0.0", port=shared_state.values["port"]
    ).serve_temporarily()


def verify_jdownloader_credentials(shared_state):
    """Verify JDownloader credentials and return devices."""
    response.content_type = "application/json"
    try:
        data = request.json
        username = data.get("user")
        password = data.get("pass")

        devices = shared_state.get_devices(username, password)
        device_names = []

        if devices:
            for device in devices:
                device_names.append(device["name"])

        if device_names:
            return {"success": True, "devices": device_names}
        else:
            return {
                "success": False,
                "message": "No devices found or invalid credentials",
            }
    except Exception as e:
        return {"success": False, "message": str(e)}


def save_jdownloader_settings(shared_state, is_setup=False):
    """Save JDownloader settings."""
    # Handle both JSON (from main UI) and Form (from setup UI)
    if request.json:
        username = request.json.get("user")
        password = request.json.get("pass")
        device = request.json.get("device")
    else:
        username = request.forms.get("user")
        password = request.forms.get("pass")
        device = request.forms.get("device")

    if username and password and device:
        # Verify connection works before saving credentials
        if shared_state.set_device(username, password, device):
            config = Config("JDownloader")
            config.save("user", username)
            config.save("password", password)
            config.save("device", device)

            if is_setup:
                quasarr.providers.web_server.temp_server_success = True
                return render_reconnect_success("Credentials set")
            else:
                response.content_type = "application/json"
                return {
                    "success": True,
                    "message": "JDownloader configured successfully",
                }
        else:
            if is_setup:
                return render_fail("Could not connect to selected device!")
            else:
                response.content_type = "application/json"
                return {
                    "success": False,
                    "message": "Could not connect to selected device",
                }

    if is_setup:
        return render_fail("Could not set credentials!")
    else:
        response.content_type = "application/json"
        return {"success": False, "message": "Missing required fields"}


def jdownloader_config(shared_state):
    app = Bottle()
    add_no_cache_headers(app)
    setup_auth(app)

    @app.get("/")
    def jd_form():
        verify_form_html = f"""
        <span>If required register account at: <a href="https://my.jdownloader.org/login.html#register" target="_blank">
        my.jdownloader.org</a>!</span><br>

        <p><strong>JDownloader must be running and connected to My JDownloader!</strong></p><br>

        <form id="verifyForm" action="/api/verify_jdownloader" method="post">
            <label for="user">E-Mail</label>
            <input type="text" id="user" name="user" placeholder="user@example.org" autocorrect="off"><br>
            <label for="pass">Password</label>
            <input type="password" id="pass" name="pass" placeholder="Password"><br>
            {
            render_button(
                "Verify Credentials",
                "secondary",
                {
                    "id": "verifyButton",
                    "type": "button",
                    "onclick": "verifyCredentials()",
                },
            )
        }
        </form>

        <p>Some JDownloader settings will be enforced by Quasarr on startup.</p>

        <form action="/api/store_jdownloader" method="post" id="deviceForm" style="display: none;" onsubmit="return handleStoreSubmit(this)">
            <input type="hidden" id="hiddenUser" name="user">
            <input type="hidden" id="hiddenPass" name="pass">
            <label for="device">JDownloader</label>
            <select id="device" name="device"></select><br>
            {render_button("Save", "primary", {"type": "submit", "id": "storeBtn"})}
        </form>
        <p><strong>Saving may take a while!</strong></p><br>
        """

        verify_script = """
        <script>
        var verifyInProgress = false;
        var storeSubmitted = false;
        function verifyCredentials() {
            if (verifyInProgress) return;
            verifyInProgress = true;
            var btn = document.getElementById('verifyButton');
            if (btn) { btn.disabled = true; btn.textContent = 'Verifying...'; }

            var user = document.getElementById('user').value;
            var pass = document.getElementById('pass').value;
            fetch('/api/verify_jdownloader', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({user: user, pass: pass}),
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    var select = document.getElementById('device');
                    data.devices.forEach(device => {
                        var opt = document.createElement('option');
                        opt.value = device;
                        opt.innerHTML = device;
                        select.appendChild(opt);
                    });
                    document.getElementById('hiddenUser').value = document.getElementById('user').value;
                    document.getElementById('hiddenPass').value = document.getElementById('pass').value;
                    document.getElementById("verifyButton").style.display = "none";
                    document.getElementById('deviceForm').style.display = 'block';
                } else {
                    showModal('Error', 'Error! Please check your Credentials.');
                    verifyInProgress = false;
                    if (btn) { btn.disabled = false; btn.textContent = 'Verify Credentials'; }
                }
            })
            .catch((error) => {
                console.error('Error:', error);
                verifyInProgress = false;
                if (btn) { btn.disabled = false; btn.textContent = 'Verify Credentials'; }
            });
        }
        function handleStoreSubmit(form) {
            if (storeSubmitted) return false;
            storeSubmitted = true;
            var btn = document.getElementById('storeBtn');
            if (btn) { btn.disabled = true; btn.textContent = 'Saving...'; }
            return true;
        }
        </script>
        """
        return render_form(
            "Set your credentials for My JDownloader", verify_form_html, verify_script
        )

    @app.post("/api/verify_jdownloader")
    def verify_jdownloader():
        return verify_jdownloader_credentials(shared_state)

    @app.post("/api/store_jdownloader")
    def store_jdownloader():
        return save_jdownloader_settings(shared_state, is_setup=True)

    info(
        f"My-JDownloader-Credentials not set. "
        f'Starting web server for config at: "{shared_state.values["external_address"]}".'
    )
    info("If needed register here: 'https://my.jdownloader.org/login.html#register'")
    info("Please set your credentials now, to allow Quasarr to launch!")
    quasarr.providers.web_server.temp_server_success = False
    return Server(
        app, listen="0.0.0.0", port=shared_state.values["port"]
    ).serve_temporarily()
