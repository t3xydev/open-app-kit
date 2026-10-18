from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.notify import notify_publish_result

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(ROOT_DIR))).resolve()
SCRIPTS_DIR = ROOT_DIR / "scripts"
STATUS_FILE = DATA_DIR / "status.json"
WEBHOOK_TOKEN = os.environ.get("WEBHOOK_TOKEN", "")
# Secret from `eas webhook:create --secret`. Falls back to WEBHOOK_TOKEN.
EAS_WEBHOOK_SECRET = os.environ.get("EAS_WEBHOOK_SECRET") or WEBHOOK_TOKEN

# EAS buildProfile → APK channel suffix (project_channel.apk)
EAS_PROFILE_CHANNELS = {
    "production": "production",
    "preview": "preview",
    "development": "dev",
    "dev": "dev",
}

S3_REQUIRED = (
    "S3_REMOTE_NAME",
    "S3_PROVIDER",
    "S3_BUCKET",
    "S3_ACCESS_KEY_ID",
    "S3_SECRET_ACCESS_KEY",
    "S3_ENDPOINT",
)

_publish_lock = asyncio.Lock()


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def s3_configured() -> bool:
    return all(os.environ.get(name) for name in S3_REQUIRED)


def is_self_host() -> bool:
    return _truthy(os.environ.get("SELF_HOST")) or not s3_configured()


def resolve_mode() -> str:
    return "self_host" if is_self_host() else "s3"


def ensure_data_dirs() -> None:
    for name in ("apks", "repo", "metadata", "tmp", "cache", "logs", "srclibs", "assets"):
        (DATA_DIR / name).mkdir(parents=True, exist_ok=True)


def read_status() -> dict[str, Any]:
    if not STATUS_FILE.exists():
        return {
            "status": "idle",
            "mode": resolve_mode(),
            "message": "No publish jobs yet",
        }
    try:
        return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {
            "status": "unknown",
            "mode": resolve_mode(),
            "message": "Could not read status.json",
        }


def write_status(payload: dict[str, Any]) -> None:
    ensure_data_dirs()
    payload = {
        **payload,
        "mode": resolve_mode(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    STATUS_FILE.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def require_token(
    authorization: str | None = Header(default=None),
    x_webhook_token: str | None = Header(default=None, alias="X-Webhook-Token"),
) -> None:
    if not WEBHOOK_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="WEBHOOK_TOKEN is not configured on the server",
        )
    token = None
    if x_webhook_token:
        token = x_webhook_token.strip()
    elif authorization:
        parts = authorization.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1].strip()
        else:
            token = authorization.strip()
    if not token or token != WEBHOOK_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing webhook token")


def safe_apk_name(filename: str | None) -> str:
    name = Path(filename or "upload.apk").name
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    if not name.lower().endswith(".apk"):
        name = f"{name}.apk"
    return name or "upload.apk"


def eas_channel_from_profile(build_profile: str | None) -> str | None:
    if not build_profile:
        return None
    return EAS_PROFILE_CHANNELS.get(build_profile.strip().lower())


def eas_bundle_name(display_name: str, channel: str) -> str:
    """APK filename: {displayName}_{production|preview|dev}.apk"""
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", (display_name or "app").strip()) or "app"
    return safe_apk_name(f"{name}_{channel}.apk")


def verify_eas_signature(body: bytes, expo_signature: str | None) -> None:
    if not EAS_WEBHOOK_SECRET:
        raise HTTPException(
            status_code=503,
            detail="EAS_WEBHOOK_SECRET (or WEBHOOK_TOKEN) is not configured",
        )
    if not expo_signature:
        raise HTTPException(status_code=401, detail="Missing expo-signature header")
    digest = hmac.new(
        EAS_WEBHOOK_SECRET.encode("utf-8"),
        body,
        hashlib.sha1,
    ).hexdigest()
    expected = f"sha1={digest}"
    if not secrets.compare_digest(expo_signature, expected):
        raise HTTPException(status_code=401, detail="Invalid expo-signature")


async def download_url_to_file(url: str, dest: Path) -> None:
    def _download() -> None:
        req = urllib.request.Request(url, headers={"User-Agent": "fdroid-server/1.0"})
        with urllib.request.urlopen(req, timeout=300) as resp, dest.open("wb") as out:
            shutil.copyfileobj(resp, out)

    try:
        await asyncio.to_thread(_download)
    except urllib.error.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to download EAS artifact: HTTP {exc.code}",
        ) from exc
    except urllib.error.URLError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to download EAS artifact: {exc.reason}",
        ) from exc


async def run_script(script_name: str) -> dict[str, Any]:
    script = SCRIPTS_DIR / script_name
    if not script.is_file():
        raise RuntimeError(f"Missing script: {script}")

    env = os.environ.copy()
    env["DATA_DIR"] = str(DATA_DIR)

    proc = await asyncio.create_subprocess_exec(
        str(script),
        cwd=str(DATA_DIR),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout_b, _ = await proc.communicate()
    log = stdout_b.decode("utf-8", errors="replace")
    return {
        "exit_code": proc.returncode or 0,
        "log": log,
        "ok": (proc.returncode or 0) == 0,
    }


async def run_publish_job(trigger: str) -> dict[str, Any]:
    if _publish_lock.locked():
        raise HTTPException(status_code=409, detail="A publish job is already running")

    async with _publish_lock:
        started = datetime.now(timezone.utc).isoformat()
        write_status(
            {
                "status": "running",
                "trigger": trigger,
                "started_at": started,
                "message": f"Running update.sh ({trigger})",
            }
        )
        try:
            result = await run_script("update.sh")
        except Exception as exc:  # noqa: BLE001
            write_status(
                {
                    "status": "failed",
                    "trigger": trigger,
                    "started_at": started,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "message": str(exc),
                    "log": "",
                }
            )
            await notify_publish_result(
                status="failed",
                trigger=trigger,
                message=str(exc),
            )
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        finished = datetime.now(timezone.utc).isoformat()
        if result["ok"]:
            write_status(
                {
                    "status": "success",
                    "trigger": trigger,
                    "started_at": started,
                    "finished_at": finished,
                    "message": "Publish succeeded",
                    "exit_code": result["exit_code"],
                    "log": result["log"][-20000:],
                }
            )
            await notify_publish_result(
                status="success",
                trigger=trigger,
                message="Publish succeeded",
                exit_code=result["exit_code"],
            )
        else:
            write_status(
                {
                    "status": "failed",
                    "trigger": trigger,
                    "started_at": started,
                    "finished_at": finished,
                    "message": "Publish failed",
                    "exit_code": result["exit_code"],
                    "log": result["log"][-20000:],
                }
            )
            await notify_publish_result(
                status="failed",
                trigger=trigger,
                message="Publish failed",
                exit_code=result["exit_code"],
                log_tail=result["log"][-500:],
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "message": "Publish failed",
                    "exit_code": result["exit_code"],
                    "log_tail": result["log"][-4000:],
                },
            )
        return read_status()


app = FastAPI(title="F-Droid Repo Server", version="1.0.0")


@app.on_event("startup")
def on_startup() -> None:
    ensure_data_dirs()
    # Seed shipped icon into data volume if missing
    default_icon = ROOT_DIR / "assets" / "icon.png"
    data_icon = DATA_DIR / "assets" / "icon.png"
    if default_icon.is_file() and not data_icon.is_file():
        shutil.copy2(default_icon, data_icon)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/status")
def api_status() -> dict[str, Any]:
    status = read_status()
    status["mode"] = resolve_mode()
    status["self_host"] = is_self_host()
    status["s3_configured"] = s3_configured()
    status["data_dir"] = str(DATA_DIR)
    status["repo_url"] = os.environ.get("REPO_URL", "")
    return status


@app.get("/api/apps")
def api_apps() -> dict[str, Any]:
    """Get list of apps from the F-Droid index."""
    index_file = repo_dir / "index-v1.json"
    
    if not index_file.exists():
        return {"apps": []}
    
    try:
        index_data = json.loads(index_file.read_text(encoding="utf-8"))
        apps = []
        
        for app_id, app_data in index_data.get("apps", {}).items():
            metadata = app_data.get("localized", {}).get("en-US", {})
            name = metadata.get("name", app_id)
            latest_version = metadata.get("summary", "")
            
            # Get the latest APK version
            versions = app_data.get("versions", {})
            if versions:
                version_code = max(versions.keys())
                version_info = versions[version_code]
                version_name = version_info.get("versionName", latest_version)
                apk_name = version_info.get("apkName", f"{app_id}.apk")
                
                # Determine download URL based on mode
                if is_self_host():
                    download_url = f"/fdroid/repo/{apk_name}"
                else:
                    repo_url = os.environ.get("REPO_URL", "").rstrip("/")
                    download_url = f"{repo_url}/{apk_name}" if repo_url else None
                
                if download_url:
                    apps.append({
                        "id": app_id,
                        "name": name,
                        "version": version_name,
                        "download_url": download_url,
                    })
        
        return {"apps": apps}
    except (json.JSONDecodeError, OSError, KeyError):
        return {"apps": []}


@app.post("/hooks/publish")
async def hooks_publish(_: None = Depends(require_token)) -> dict[str, Any]:
    return await run_publish_job("publish")


@app.post("/hooks/apk")
async def hooks_apk(
    file: UploadFile = File(...),
    _: None = Depends(require_token),
) -> dict[str, Any]:
    ensure_data_dirs()
    filename = safe_apk_name(file.filename)
    dest = DATA_DIR / "apks" / filename
    with dest.open("wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
    status = await run_publish_job(f"apk:{filename}")
    status["apk"] = filename
    return status


@app.post("/hooks/eas")
async def hooks_eas(request: Request) -> dict[str, Any]:
    """Ingest finished Android APK builds from an EAS Build webhook.

    Saves as ``{displayName}_{production|preview|dev}.apk`` then publishes.
    Display name comes from EAS ``metadata.appName`` (app.json ``name``).
    Configure with: ``eas webhook:create --event BUILD --url ... --secret ...``
    """
    body = await request.body()
    verify_eas_signature(body, request.headers.get("expo-signature"))

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body") from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Expected a JSON object")

    status = str(payload.get("status") or "")
    platform = str(payload.get("platform") or "").lower()
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    artifacts = payload.get("artifacts") if isinstance(payload.get("artifacts"), dict) else {}
    build_profile = str(metadata.get("buildProfile") or "")
    # Prefer display name (app.json `name`); fall back to Expo project slug.
    display_name = str(metadata.get("appName") or payload.get("projectName") or "app")
    build_url = str(artifacts.get("buildUrl") or "")
    channel = eas_channel_from_profile(build_profile)

    # Ack non-ingest events so EAS does not retry.
    if status != "finished":
        return {
            "ok": True,
            "ingested": False,
            "reason": f"ignored status={status or 'unknown'}",
        }
    if platform != "android":
        return {
            "ok": True,
            "ingested": False,
            "reason": f"ignored platform={platform or 'unknown'}",
        }
    if not channel:
        return {
            "ok": True,
            "ingested": False,
            "reason": (
                f"ignored buildProfile={build_profile or 'unknown'}; "
                "expected production, preview, or development/dev"
            ),
        }
    if not build_url:
        return {
            "ok": True,
            "ingested": False,
            "reason": "finished build has no artifacts.buildUrl",
        }
    if not build_url.lower().endswith(".apk") and ".apk?" not in build_url.lower():
        return {
            "ok": True,
            "ingested": False,
            "reason": "artifact is not an APK (set android.buildType=apk on the EAS profile)",
        }

    ensure_data_dirs()
    filename = eas_bundle_name(display_name, channel)
    dest = DATA_DIR / "apks" / filename
    await download_url_to_file(build_url, dest)

    result = await run_publish_job(f"eas:{filename}")
    result["apk"] = filename
    result["ingested"] = True
    result["eas"] = {
        "build_id": payload.get("id"),
        "display_name": display_name,
        "project_name": payload.get("projectName"),
        "build_profile": build_profile,
        "channel": channel,
        "app_version": metadata.get("appVersion"),
        "app_build_version": metadata.get("appBuildVersion"),
    }
    return result


# Mount repo static files when self-host is active (and always for debug browse).
# Clients should use REPO_URL pointing here when mode is self_host.
repo_dir = DATA_DIR / "repo"
repo_dir.mkdir(parents=True, exist_ok=True)
app.mount("/fdroid/repo", StaticFiles(directory=str(repo_dir), html=True), name="repo")


@app.get("/api/info")
def api_info() -> JSONResponse:
    return JSONResponse(
        {
            "service": "fdroid-server",
            "mode": resolve_mode(),
            "health": "/health",
            "status": "/api/status",
            "repo": "/fdroid/repo/" if is_self_host() else None,
            "hooks": {
                "apk": "POST /hooks/apk",
                "eas": "POST /hooks/eas",
                "publish": "POST /hooks/publish",
            },
        }
    )


@app.get("/")
def root() -> HTMLResponse:
    repo_url = os.environ.get("REPO_URL", "")
    repo_name = os.environ.get("REPO_NAME", "F-Droid Repository")
    is_sh = is_self_host()
    
    return HTMLResponse(content=f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{repo_name}</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #f5f5f5;
            color: #333;
            line-height: 1.6;
        }}
        .container {{
            max-width: 800px;
            margin: 0 auto;
            padding: 2rem;
        }}
        header {{
            text-align: center;
            margin-bottom: 2rem;
        }}
        h1 {{
            font-size: 2rem;
            margin-bottom: 0.5rem;
        }}
        .repo-btn {{
            display: inline-block;
            background: #3b82f6;
            color: white;
            text-decoration: none;
            padding: 0.75rem 1.5rem;
            border-radius: 8px;
            font-weight: 500;
            margin-top: 1rem;
        }}
        .repo-btn:hover {{
            background: #2563eb;
        }}
        .apps-section {{
            background: white;
            border-radius: 12px;
            padding: 1.5rem;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }}
        .apps-section h2 {{
            font-size: 1.25rem;
            margin-bottom: 1rem;
        }}
        .app-list {{
            list-style: none;
        }}
        .app-item {{
            padding: 1rem;
            border-bottom: 1px solid #e5e5e5;
        }}
        .app-item:last-child {{
            border-bottom: none;
        }}
        .app-name {{
            font-weight: 600;
            font-size: 1.1rem;
        }}
        .app-version {{
            color: #666;
            font-size: 0.9rem;
        }}
        .download-link {{
            display: inline-block;
            margin-top: 0.5rem;
            color: #3b82f6;
            text-decoration: none;
            font-size: 0.9rem;
        }}
        .download-link:hover {{
            text-decoration: underline;
        }}
        .loading {{
            text-align: center;
            color: #666;
            padding: 2rem;
        }}
        .error {{
            color: #dc2626;
            text-align: center;
            padding: 2rem;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>{repo_name}</h1>
            <a href="{repo_url if repo_url else ('/fdroid/repo/' if is_sh else '#')}" class="repo-btn">Add to F-Droid</a>
        </header>
        
        <div class="apps-section">
            <h2>Available Apps</h2>
            <div id="app-list" class="loading">Loading apps...</div>
        </div>
    </div>

    <script>
        async function loadApps() {{
            const container = document.getElementById('app-list');
            try {{
                const response = await fetch('/api/apps');
                if (!response.ok) {{
                    container.innerHTML = '<div class="error">Failed to load apps</div>';
                    return;
                }}
                const data = await response.json();
                
                if (!data.apps || data.apps.length === 0) {{
                    container.innerHTML = '<div class="loading">No apps available yet</div>';
                    return;
                }}
                
                const html = data.apps.map(app => `
                    <li class="app-item">
                        <div class="app-name">${{app.name}}</div>
                        <div class="app-version">${{app.version}}</div>
                        <a href="${{app.download_url}}" class="download-link">Download APK</a>
                    </li>
                `).join('');
                
                container.innerHTML = `<ul class="app-list">${{html}}</ul>`;
            }} catch (error) {{
                container.innerHTML = '<div class="error">Failed to load apps</div>';
            }}
        }}
        
        loadApps();
    </script>
</body>
</html>""")
