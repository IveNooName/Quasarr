# -*- coding: utf-8 -*-
# Quasarr
# Project by https://github.com/rix1337

from bottle import response

import quasarr.providers.html_images as images
from quasarr.providers.auth import add_auth_hook, add_auth_routes
from quasarr.providers.html_templates import (
    render_button,
    render_centered_html,
)


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


__all__ = [
    "add_no_cache_headers",
    "render_reconnect_success",
    "setup_auth",
]
