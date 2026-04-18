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
from quasarr.providers.imdb_metadata import get_imdb_id_from_title
from quasarr.providers.log import debug, error, info
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
    Config("WebUI").save(
        "downloads_path", (payload.get("downloads_path") or "").strip()
    )


def _normalize_media_type(value):
    media_type = str(value or "").strip().lower()
    if media_type in ("movie", "movies"):
        return "movie"
    return "tv"


def _search_releases(shared_state, query, imdb_id, category):
    results = get_search_results(
        shared_state,
        request_from="Quasarr-WebUI",
        search_category=int(category),
        search_phrase=query,
        imdb_id=imdb_id,
        offset=0,
        limit=500,
    )
    cleaned = []
    for result_item in results:
        details = result_item.get("details", {})
        if details and details.get("link"):
            cleaned.append(
                {
                    "title": details.get("title", "Unknown"),
                    "size": details.get("size", "Unknown"),
                    "link": details.get("link", ""),
                    "source": details.get("source", "Unknown"),
                    "date": details.get("date", ""),
                    "password": details.get("password", ""),
                    "imdb_id": details.get("imdb_id", ""),
                    "source_key": details.get("source_key", ""),
                    "size_mb": details.get("size_mb", 0),
                }
            )
    return cleaned


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

                history_item = _get_history_entry_by_package_id(
                    shared_state, package_id
                )
                if not history_item:
                    continue

                is_archive = bool(history_item.get("is_archive"))
                extraction_ok = bool(history_item.get("extraction_ok"))
                status = str(history_item.get("status", "")).lower()
                is_finished = status == "completed" and (
                    (not is_archive) or extraction_ok
                )
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
  <title>Quasarr Media Browser</title>
  <style>
    :root {{ --bg: #1e1e2e; --surface: #313244; --text: #cdd6f4; --primary: #89b4fa; --error: #f38ba8; }}
    body {{ font-family: system-ui, sans-serif; background: var(--bg); color: var(--text); padding: 2rem; max-width: 1000px; margin: 0 auto; }}
    .search-container {{ display: flex; gap: 1rem; margin-bottom: 2rem; flex-wrap: wrap; }}
    input, select, button {{ padding: 0.75rem; border-radius: 0.5rem; border: 1px solid var(--surface); background: var(--surface); color: var(--text); font-size: 1rem; }}
    button {{ background: var(--primary); color: var(--bg); font-weight: bold; cursor: pointer; border: none; transition: opacity 0.2s; min-width: 120px; }}
    button:disabled {{ opacity: 0.5; cursor: not-allowed; }}
    .error {{ color: var(--error); padding: 1rem; background: rgba(243, 139, 168, 0.1); border-radius: 0.5rem; margin-bottom: 1rem; display: none; }}
    .results {{ display: grid; gap: 1rem; }}
    .result-card {{ background: var(--surface); padding: 1rem; border-radius: 0.5rem; display: flex; justify-content: space-between; align-items: center; word-break: break-all; }}
    .loader {{ display: none; margin-left: 1rem; align-self: center; }}
  </style>
</head>
<body>
  <h1>Quasarr Media Browser</h1>
  <div class="search-container">
    <select id="category">
      <option value="2000">Movies (2000)</option>
      <option value="5000">TV Shows (5000)</option>
    </select>
    <input type="text" id="query" placeholder="Search title or tt1234567..." style="flex-grow: 1;">
    <button id="searchBtn">Search</button>
    <span class="loader" id="loader">Searching... (This may take up to 2 minutes)</span>
  </div>
  <div id="error" class="error"></div>
  <div id="results" class="results"></div>

  <script>
    const API_KEY = {json.dumps(api_key)};
    let currentController = null;

    function escapeHtml(text) {{
      return String(text || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
    }}

    document.getElementById("searchBtn").addEventListener("click", async () => {{
      const queryInput = document.getElementById("query").value.trim();
      const category = document.getElementById("category").value;
      const errorEl = document.getElementById("error");
      const resultsEl = document.getElementById("results");
      const loader = document.getElementById("loader");
      const searchBtn = document.getElementById("searchBtn");

      if (!queryInput) return;

      if (currentController) currentController.abort();
      currentController = new AbortController();

      const isImdb = queryInput.startsWith("tt");
      const queryParam = isImdb ? `imdbid=${{encodeURIComponent(queryInput)}}` : `q=${{encodeURIComponent(queryInput)}}`;

      errorEl.style.display = "none";
      resultsEl.innerHTML = "";
      loader.style.display = "inline";
      searchBtn.disabled = true;

      try {{
        const timeoutId = setTimeout(() => currentController.abort(), 120000);

        const response = await fetch(`/api/webui/search?cat=${{encodeURIComponent(category)}}&${{queryParam}}`, {{
          signal: currentController.signal,
          headers: {{ "X-API-Key": API_KEY }}
        }});

        clearTimeout(timeoutId);

        if (!response.ok) throw new Error(`HTTP Error: ${{response.status}}`);

        const data = await response.json();
        if (!data.status) throw new Error(data.error || "Unknown backend error");

        if (data.results.length === 0) {{
          resultsEl.innerHTML = '<div class="result-card">No results found.</div>';
        }} else {{
          data.results.forEach((res) => {{
            const payload = encodeURIComponent(JSON.stringify(res));
            const card = document.createElement("div");
            card.className = "result-card";
            card.innerHTML = `
              <div style="flex-grow: 1; padding-right: 1rem;">
                <strong>${{escapeHtml(res.title)}}</strong><br>
                <small>${{escapeHtml(res.size)}} | Source: ${{escapeHtml(res.source)}}</small>
              </div>
              <button data-payload="${{payload}}">Download</button>
            `;
            const downloadBtn = card.querySelector("button");
            downloadBtn.addEventListener("click", async () => {{
              const release = JSON.parse(decodeURIComponent(downloadBtn.dataset.payload));
              const mediaType = category === "2000" ? "movie" : "tv";
              const dlResp = await fetch("/api/webui/release/download", {{
                method: "POST",
                headers: {{ "Content-Type": "application/json", "X-API-Key": API_KEY }},
                body: JSON.stringify({{ release: release, media_type: mediaType }})
              }});
              const dlData = await dlResp.json();
              if (dlData.captcha_required && dlData.captcha_url) {{
                window.location.href = dlData.captcha_url;
              }}
            }});
            resultsEl.appendChild(card);
          }});
        }}
      }} catch (err) {{
        if (err.name === "AbortError") {{
          errorEl.innerText = "Search timed out. Indexers took too long to respond.";
        }} else {{
          errorEl.innerText = err.message;
        }}
        errorEl.style.display = "block";
      }} finally {{
        loader.style.display = "none";
        searchBtn.disabled = false;
      }}
    }});
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
        imdb_id = (request.query.get("imdbid") or "").strip()
        category = (request.query.get("cat") or "2000").strip()
        if not query and not imdb_id:
            response.status = 400
            return {
                "status": False,
                "error": "Search query or IMDb ID required.",
            }

        try:
            results = _search_releases(shared_state, query, imdb_id, category)
        except Exception as exc:
            response.status = 500
            return {
                "status": False,
                "error": str(exc),
                "traceback": traceback.format_exc(),
            }

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
            shared_state.get_db("protected").retrieve(package_id)
            if package_id
            else None
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
