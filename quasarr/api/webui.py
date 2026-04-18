# -*- coding: utf-8 -*-

import json
import os
import time
from datetime import datetime, timezone

import requests
from bottle import request, response

from quasarr.downloads import download
from quasarr.downloads.packages import get_packages
from quasarr.providers.auth import require_api_key
from quasarr.providers.log import debug, error, info
from quasarr.providers.imdb_metadata import get_imdb_id_from_title
from quasarr.search import get_search_results
from quasarr.storage.config import Config
from quasarr.storage.sqlite_database import DataBase

ARR_MONITOR_TABLE = "arr_monitor"


def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def _safe_json_loads(payload, default=None):
    if default is None:
        default = {}
    if not payload:
        return default
    try:
        return json.loads(payload)
    except Exception:
        return default


def _apply_truenas_permissions(target_path):
    puid_raw = os.environ.get("PUID", "").strip()
    pgid_raw = os.environ.get("PGID", "").strip()
    if not puid_raw or not pgid_raw:
        return
    try:
        puid = int(puid_raw)
        pgid = int(pgid_raw)
    except ValueError:
        return
    try:
        os.chown(target_path, puid, pgid)
    except Exception:
        # Non-fatal on systems where chown is not permitted.
        return


def _get_arr_config():
    return {
        "sonarr_url": Config("Sonarr").get("url") or "",
        "sonarr_api_key": Config("Sonarr").get("api_key") or "",
        "radarr_url": Config("Radarr").get("url") or "",
        "radarr_api_key": Config("Radarr").get("api_key") or "",
        "jdownloader_user": Config("JDownloader").get("user") or "",
        "jdownloader_device": Config("JDownloader").get("device") or "",
        "downloads_path": Config("WebUI").get("downloads_path") or "",
        "puid": os.environ.get("PUID", ""),
        "pgid": os.environ.get("PGID", ""),
    }


def _save_arr_config(payload):
    Config("Sonarr").save("url", (payload.get("sonarr_url") or "").strip())
    Config("Sonarr").save("api_key", (payload.get("sonarr_api_key") or "").strip())
    Config("Radarr").save("url", (payload.get("radarr_url") or "").strip())
    Config("Radarr").save("api_key", (payload.get("radarr_api_key") or "").strip())
    Config("WebUI").save("downloads_path", (payload.get("downloads_path") or "").strip())


def _normalize_media_type(value):
    media_type = str(value or "").strip().lower()
    if media_type in ("movie", "movies"):
        return "movie"
    return "tv"


def _search_category_for_media_type(media_type):
    return 2000 if media_type == "movie" else 5000


def _search_releases(shared_state, query, media_type):
    imdb_id = get_imdb_id_from_title(shared_state, query, language="en", media_type=media_type)
    if not imdb_id:
        debug(f"WebUI search could not resolve IMDb ID for query: {query}")
        return []

    releases = get_search_results(
        shared_state=shared_state,
        request_from="Quasarr WebUI",
        search_category=_search_category_for_media_type(media_type),
        imdb_id=imdb_id,
        offset=0,
        limit=100,
    )
    data = []
    for item in releases:
        details = item.get("details", {})
        data.append(
            {
                "title": details.get("title", ""),
                "size": details.get("size", 0),
                "size_mb": details.get("size_mb", 0),
                "link": details.get("link", ""),
                "source": details.get("source", ""),
                "source_key": details.get("source_key", ""),
                "password": details.get("password", ""),
                "imdb_id": details.get("imdb_id", ""),
                "hostname": details.get("hostname", ""),
                "date": details.get("date", ""),
            }
        )
    return data


def _register_arr_monitor(package_id, media_type, downloads_path):
    monitor_db = DataBase(ARR_MONITOR_TABLE)
    value = {
        "package_id": package_id,
        "media_type": media_type,
        "downloads_path": downloads_path,
        "created_at": _utc_now_iso(),
        "status": "queued",
    }
    monitor_db.update_store(package_id, json.dumps(value))


def _build_arr_command(media_type, downloads_path):
    if media_type == "movie":
        return "DownloadedMoviesScan", {"path": downloads_path}
    return "DownloadedEpisodesScan", {"path": downloads_path}


def _trigger_arr_scan(media_type, downloads_path):
    if media_type == "movie":
        base_url = (Config("Radarr").get("url") or "").rstrip("/")
        api_key = Config("Radarr").get("api_key") or ""
    else:
        base_url = (Config("Sonarr").get("url") or "").rstrip("/")
        api_key = Config("Sonarr").get("api_key") or ""

    if not base_url or not api_key:
        return False, "ARR endpoint or API key is not configured"

    command_name, payload = _build_arr_command(media_type, downloads_path)
    body = {"name": command_name, **payload}
    try:
        result = requests.post(
            f"{base_url}/api/v3/command",
            headers={"X-Api-Key": api_key, "Content-Type": "application/json"},
            json=body,
            timeout=20,
        )
        if result.status_code >= 400:
            return False, f"ARR command failed with status {result.status_code}"
        return True, "ARR import triggered"
    except Exception as exc:
        return False, str(exc)


def _get_history_entry_by_package_id(shared_state, package_id):
    downloads = get_packages(shared_state)
    for history_item in downloads.get("history", []):
        if str(history_item.get("nzo_id", "")) == str(package_id):
            return history_item
    return None


def webui_arr_monitor_loop(shared_state_dict, shared_state_lock):
    from quasarr.providers import shared_state

    shared_state.set_state(shared_state_dict, shared_state_lock)

    while True:
        try:
            rows = DataBase(ARR_MONITOR_TABLE).retrieve_all_titles() or []
            for key, raw_value in rows:
                package_id = key
                record = _safe_json_loads(raw_value, {})
                media_type = _normalize_media_type(record.get("media_type"))
                downloads_path = record.get("downloads_path") or Config("WebUI").get(
                    "downloads_path"
                )

                if not downloads_path:
                    continue

                history_item = _get_history_entry_by_package_id(shared_state, package_id)
                if not history_item:
                    continue

                is_archive = bool(history_item.get("is_archive"))
                extraction_ok = bool(history_item.get("extraction_ok"))
                status = str(history_item.get("status", "")).lower()
                is_finished = status == "completed" and ((not is_archive) or extraction_ok)
                if not is_finished:
                    continue

                ok, message = _trigger_arr_scan(media_type, downloads_path)
                if ok:
                    DataBase(ARR_MONITOR_TABLE).delete(package_id)
                    info(
                        f"ARR monitor triggered import for {package_id} ({media_type}): {message}"
                    )
                else:
                    record["status"] = "error"
                    record["last_error"] = message
                    record["last_attempt_at"] = _utc_now_iso()
                    DataBase(ARR_MONITOR_TABLE).update_store(
                        package_id, json.dumps(record)
                    )
                    error(
                        f"ARR monitor failed import trigger for {package_id} ({media_type}): {message}"
                    )
        except Exception as exc:
            error(f"ARR monitor loop error: {exc}")

        time.sleep(30)


def setup_webui_routes(app, shared_state):
    @app.get("/webui")
    def webui_page():
        api_key = Config("API").get("key")
        html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Quasarr Web UI</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 0; background: #0f1115; color: #e5e7eb; }}
    .container {{ max-width: 1080px; margin: 0 auto; padding: 24px; }}
    .card {{ background: #171a21; border: 1px solid #2b303b; border-radius: 10px; padding: 16px; margin-bottom: 18px; }}
    h1,h2 {{ margin: 0 0 12px 0; }}
    .grid {{ display: grid; gap: 10px; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); }}
    label {{ display: block; font-size: 13px; margin-bottom: 6px; color: #9ca3af; }}
    input, select, button {{ width: 100%; box-sizing: border-box; border-radius: 8px; border: 1px solid #374151; background: #111827; color: #e5e7eb; padding: 9px 10px; }}
    button {{ cursor: pointer; background: #2563eb; border-color: #2563eb; font-weight: 600; }}
    button.secondary {{ background: #1f2937; border-color: #374151; }}
    .row {{ display: flex; gap: 10px; }}
    .row > * {{ flex: 1; }}
    .status {{ margin-top: 10px; font-size: 13px; color: #93c5fd; }}
    .results {{ max-height: 420px; overflow: auto; border: 1px solid #2b303b; border-radius: 8px; }}
    .release {{ display: grid; grid-template-columns: 1fr auto; gap: 8px; border-bottom: 1px solid #262b35; padding: 10px; }}
    .release:last-child {{ border-bottom: 0; }}
    .meta {{ font-size: 12px; color: #9ca3af; margin-top: 4px; }}
    a {{ color: #93c5fd; }}
  </style>
</head>
<body>
  <div class="container">
    <h1>Quasarr Central Web UI</h1>
    <div class="card">
      <h2>Config UI</h2>
      <div class="grid">
        <div><label>Sonarr URL</label><input id="sonarr_url" placeholder="http://sonarr:8989" /></div>
        <div><label>Sonarr API Key</label><input id="sonarr_api_key" /></div>
        <div><label>Radarr URL</label><input id="radarr_url" placeholder="http://radarr:7878" /></div>
        <div><label>Radarr API Key</label><input id="radarr_api_key" /></div>
        <div><label>Shared Downloads Path</label><input id="downloads_path" placeholder="/downloads" /></div>
        <div><label>JDownloader Device</label><input id="jdownloader_device" disabled /></div>
      </div>
      <div class="row" style="margin-top:10px;">
        <button id="saveConfigBtn">Save Settings</button>
        <button class="secondary" id="reloadConfigBtn">Reload</button>
      </div>
      <div id="configStatus" class="status"></div>
      <div class="meta">TrueNAS permissions (PUID/PGID): <span id="permMeta"></span></div>
    </div>

    <div class="card">
      <h2>Media Browser & Search</h2>
      <div class="row">
        <input id="searchQuery" placeholder="Search title..." />
        <select id="mediaType"><option value="tv">TV</option><option value="movie">Movie</option></select>
      </div>
      <div class="row" style="margin-top:10px;">
        <button id="searchBtn">Search</button>
        <button class="secondary" id="openCaptchaBtn">Open CAPTCHA Queue</button>
      </div>
      <div id="searchStatus" class="status"></div>
      <div id="results" class="results" style="margin-top:10px;"></div>
    </div>
  </div>

  <script>
    const API_KEY = {json.dumps(api_key)};
    const headers = {{ "Content-Type": "application/json", "X-API-Key": API_KEY }};
    async function api(path, options = {{}}) {{
      const res = await fetch(path, {{
        ...options,
        headers: {{ ...headers, ...(options.headers || {{}}) }}
      }});
      if (!res.ok) {{
        const text = await res.text();
        throw new Error(text || `HTTP ${{res.status}}`);
      }}
      return res.json();
    }}

    function setText(id, value) {{ document.getElementById(id).textContent = value || ""; }}
    function setValue(id, value) {{ document.getElementById(id).value = value || ""; }}

    async function loadConfig() {{
      const cfg = await api("/api/webui/config");
      setValue("sonarr_url", cfg.sonarr_url);
      setValue("sonarr_api_key", cfg.sonarr_api_key);
      setValue("radarr_url", cfg.radarr_url);
      setValue("radarr_api_key", cfg.radarr_api_key);
      setValue("downloads_path", cfg.downloads_path);
      setValue("jdownloader_device", cfg.jdownloader_device);
      setText("permMeta", `PUID=${{cfg.puid || "-"}}, PGID=${{cfg.pgid || "-"}}`);
    }}

    async function saveConfig() {{
      const payload = {{
        sonarr_url: document.getElementById("sonarr_url").value.trim(),
        sonarr_api_key: document.getElementById("sonarr_api_key").value.trim(),
        radarr_url: document.getElementById("radarr_url").value.trim(),
        radarr_api_key: document.getElementById("radarr_api_key").value.trim(),
        downloads_path: document.getElementById("downloads_path").value.trim()
      }};
      await api("/api/webui/config", {{ method: "POST", body: JSON.stringify(payload) }});
      setText("configStatus", "Configuration saved.");
    }}

    function formatSize(v) {{
      const n = Number(v || 0);
      if (!n) return "?";
      const mb = n / (1024 * 1024);
      if (mb < 1024) return `${{mb.toFixed(0)}} MB`;
      return `${{(mb / 1024).toFixed(2)}} GB`;
    }}

    function renderResults(items, mediaType) {{
      const root = document.getElementById("results");
      if (!items.length) {{
        root.innerHTML = '<div class="release"><div>No results</div></div>';
        return;
      }}
      root.innerHTML = items.map((item, index) => {{
        const payload = encodeURIComponent(JSON.stringify(item));
        return `<div class="release">
          <div>
            <div>${{item.title || "Untitled"}}</div>
            <div class="meta">${{item.hostname || item.source || "unknown"}} | ${{formatSize(item.size)}} | ${{item.date || ""}}</div>
          </div>
          <div><button onclick="downloadRelease('${{payload}}', '${{mediaType}}')">Download</button></div>
        </div>`;
      }}).join("");
    }}

    async function runSearch() {{
      const q = document.getElementById("searchQuery").value.trim();
      const mediaType = document.getElementById("mediaType").value;
      if (!q) {{
        setText("searchStatus", "Query is required.");
        return;
      }}
      setText("searchStatus", "Searching...");
      const data = await api(`/api/webui/search?q=${{encodeURIComponent(q)}}&media_type=${{encodeURIComponent(mediaType)}}`);
      renderResults(data.results || [], mediaType);
      setText("searchStatus", `Results: ${{(data.results || []).length}}`);
    }}

    async function downloadRelease(encodedPayload, mediaType) {{
      const release = JSON.parse(decodeURIComponent(encodedPayload));
      setText("searchStatus", "Submitting release...");
      const data = await api("/api/webui/release/download", {{
        method: "POST",
        body: JSON.stringify({{ release, media_type: mediaType }})
      }});
      if (data.captcha_required) {{
        setText("searchStatus", "CAPTCHA required. Opening CAPTCHA flow...");
        window.location.href = data.captcha_url;
        return;
      }}
      setText("searchStatus", data.message || "Submitted.");
    }}

    document.getElementById("saveConfigBtn").addEventListener("click", () => saveConfig().catch(e => setText("configStatus", e.message)));
    document.getElementById("reloadConfigBtn").addEventListener("click", () => loadConfig().catch(e => setText("configStatus", e.message)));
    document.getElementById("searchBtn").addEventListener("click", () => runSearch().catch(e => setText("searchStatus", e.message)));
    document.getElementById("openCaptchaBtn").addEventListener("click", () => window.location.href = "/captcha");
    loadConfig().catch(e => setText("configStatus", e.message));
  </script>
</body>
</html>"""
        response.content_type = "text/html; charset=utf-8"
        return html

    @app.get("/api/webui/config")
    @require_api_key
    def webui_get_config():
        response.content_type = "application/json"
        return _get_arr_config()

    @app.post("/api/webui/config")
    @require_api_key
    def webui_save_config():
        payload = request.json or {}
        _save_arr_config(payload)
        downloads_path = (payload.get("downloads_path") or "").strip()
        if downloads_path:
            _apply_truenas_permissions(downloads_path)
        response.content_type = "application/json"
        return {"success": True}

    @app.get("/api/webui/search")
    @require_api_key
    def webui_search():
        query = (request.query.get("q") or "").strip()
        media_type = _normalize_media_type(request.query.get("media_type"))
        if not query:
            response.status = 400
            return {"success": False, "message": "Missing query"}
        results = _search_releases(shared_state, query, media_type)
        response.content_type = "application/json"
        return {"success": True, "results": results}

    @app.post("/api/webui/release/download")
    @require_api_key
    def webui_release_download():
        payload = request.json or {}
        release = payload.get("release") or {}
        media_type = _normalize_media_type(payload.get("media_type"))
        title = str(release.get("title") or "").strip()
        url = str(release.get("link") or "").strip()
        if not title or not url:
            response.status = 400
            return {"success": False, "message": "Release title and link are required"}

        download_category = "movies" if media_type == "movie" else "tv"
        request_from = "Quasarr WebUI"
        result = download(
            shared_state=shared_state,
            request_from=request_from,
            download_category=download_category,
            title=title,
            url=url,
            size_mb=release.get("size_mb") or 0,
            password=release.get("password") or "",
            imdb_id=release.get("imdb_id") or "",
            source_key=release.get("source_key") or "",
        )

        package_id = result.get("package_id")
        if not result.get("success"):
            response.status = 500
            return {"success": False, "message": "Download submission failed"}

        is_failed = bool(result.get("failed"))
        protected_data = (
            shared_state.get_db("protected").retrieve(package_id) if package_id else None
        )
        captcha_required = bool(protected_data) and not is_failed

        if package_id and not captcha_required and not is_failed:
            downloads_path = Config("WebUI").get("downloads_path") or ""
            if downloads_path:
                _register_arr_monitor(package_id, media_type, downloads_path)

        response.content_type = "application/json"
        if captcha_required:
            return {
                "success": True,
                "captcha_required": True,
                "captcha_url": f"/captcha?package_id={package_id}",
                "package_id": package_id,
            }
        return {
            "success": True,
            "captcha_required": False,
            "package_id": package_id,
            "message": "Release submitted to JDownloader",
        }

    @app.get("/api/webui/monitor/status")
    @require_api_key
    def webui_monitor_status():
        rows = DataBase(ARR_MONITOR_TABLE).retrieve_all_titles() or []
        entries = []
        for key, value in rows:
            item = _safe_json_loads(value, {})
            item["package_id"] = key
            entries.append(item)
        entries.sort(key=lambda i: i.get("created_at", ""))
        return {"success": True, "entries": entries}
