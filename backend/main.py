from __future__ import annotations

import asyncio
import hashlib
import hmac
import html
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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.catalog import fdroid_deep_link, parse_index_v1, parse_index_v2
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


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    icon = DATA_DIR / "assets" / "icon.png"
    if not icon.is_file():
        icon = ROOT_DIR / "assets" / "icon.png"
    return FileResponse(icon, media_type="image/png")


@app.get("/api/status")
def api_status() -> dict[str, Any]:
    status = read_status()
    status["mode"] = resolve_mode()
    status["self_host"] = is_self_host()
    status["s3_configured"] = s3_configured()
    status["data_dir"] = str(DATA_DIR)
    status["repo_url"] = os.environ.get("REPO_URL", "")
    return status


def public_repo_url(request: Request) -> str:
    configured = os.environ.get("REPO_URL", "").strip()
    if configured:
        return configured.rstrip("/")
    return f"{str(request.base_url).rstrip('/')}/fdroid/repo"


def read_repo_indexes() -> tuple[dict[str, Any], dict[str, Any]]:
    indexes: dict[str, dict[str, Any]] = {}
    for filename in ("index-v1.json", "index-v2.json"):
        path = DATA_DIR / "repo" / filename
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                indexes[filename] = payload
        except (json.JSONDecodeError, OSError):
            continue
    return indexes.get("index-v1.json", {}), indexes.get("index-v2.json", {})


@app.get("/api/apps")
def api_apps(request: Request) -> dict[str, Any]:
    """Return homepage-ready apps from the repository's v2 or v1 index."""
    repo_url = public_repo_url(request)
    index_v1, index_v2 = read_repo_indexes()
    apps = parse_index_v2(index_v2, repo_url) if index_v2 else []
    if not apps and index_v1:
        apps = parse_index_v1(index_v1, repo_url)

    repo_metadata = index_v1.get("repo", {}) if isinstance(index_v1.get("repo"), dict) else {}
    fingerprint = str(repo_metadata.get("fingerprint") or "")
    return {
        "apps": apps,
        "repository": {
            "name": os.environ.get("REPO_NAME", "F-Droid Repository"),
            "url": repo_url,
            "fingerprint": fingerprint,
            "add_url": fdroid_deep_link(repo_url, fingerprint),
            "source_url": os.environ.get(
                "SOURCE_REPO_URL",
                "https://github.com/t3xydev/app-distribution-kit",
            ),
        },
    }


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
def root(request: Request) -> HTMLResponse:
    repo_url = public_repo_url(request)
    repo_name = os.environ.get("REPO_NAME", "F-Droid Repository").strip() or "F-Droid Repository"
    repo_description = (
        os.environ.get("REPO_DESCRIPTION", "Open Android apps, delivered directly.").strip()
        or "Open Android apps, delivered directly."
    )
    source_url = os.environ.get(
        "SOURCE_REPO_URL",
        "https://github.com/t3xydev/app-distribution-kit",
    ).strip()
    index_v1, _ = read_repo_indexes()
    repo_metadata = index_v1.get("repo", {}) if isinstance(index_v1.get("repo"), dict) else {}
    fingerprint = str(repo_metadata.get("fingerprint") or "")
    add_url = fdroid_deep_link(repo_url, fingerprint)

    safe_name = html.escape(repo_name)
    safe_description = html.escape(repo_description)
    safe_repo_url = html.escape(repo_url, quote=True)
    safe_add_url = html.escape(add_url, quote=True)
    safe_source_url = html.escape(source_url, quote=True)

    return HTMLResponse(content=f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="{safe_description}">
  <meta name="theme-color" content="#0a1714">
  <title>{safe_name}</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #07110f;
      --panel: rgba(18, 34, 30, .78);
      --panel-solid: #10211d;
      --line: rgba(177, 232, 210, .14);
      --text: #f5fbf8;
      --muted: #9db2aa;
      --accent: #7ef0bd;
      --accent-strong: #49d89d;
      --ink: #062217;
      --radius: 22px;
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      margin: 0;
      min-height: 100vh;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--text);
      background:
        radial-gradient(circle at 80% -10%, rgba(58, 209, 148, .19), transparent 36rem),
        radial-gradient(circle at -10% 30%, rgba(45, 126, 103, .16), transparent 30rem),
        var(--bg);
      line-height: 1.5;
    }}
    a {{ color: inherit; }}
    button, a {{ -webkit-tap-highlight-color: transparent; }}
    .shell {{ width: min(1120px, calc(100% - 40px)); margin: 0 auto; }}
    .nav {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      min-height: 76px;
      border-bottom: 1px solid var(--line);
    }}
    .brand {{ display: flex; align-items: center; gap: 12px; font-weight: 750; letter-spacing: -.02em; }}
    .brand-mark {{
      display: grid;
      place-items: center;
      width: 38px;
      height: 38px;
      border-radius: 12px;
      color: var(--ink);
      background: var(--accent);
      box-shadow: 0 10px 34px rgba(73, 216, 157, .18);
    }}
    .nav-links {{ display: flex; align-items: center; gap: 10px; }}
    .nav-link {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      min-height: 40px;
      padding: 0 14px;
      color: var(--muted);
      text-decoration: none;
      border-radius: 12px;
    }}
    .nav-link:hover {{ color: var(--text); background: rgba(255,255,255,.05); }}
    .hero {{ display: grid; grid-template-columns: minmax(0, 1.3fr) minmax(310px, .7fr); gap: 56px; padding: 92px 0 78px; align-items: center; }}
    .eyebrow {{ display: flex; align-items: center; gap: 9px; margin-bottom: 18px; color: var(--accent); font-size: .82rem; font-weight: 750; letter-spacing: .12em; text-transform: uppercase; }}
    .eyebrow::before {{ content: ""; width: 28px; height: 1px; background: currentColor; }}
    h1 {{ max-width: 760px; margin: 0; font-size: clamp(3rem, 7vw, 5.7rem); line-height: .98; letter-spacing: -.065em; }}
    .hero-copy {{ max-width: 650px; margin: 24px 0 32px; color: var(--muted); font-size: clamp(1.02rem, 2vw, 1.2rem); }}
    .actions {{ display: flex; flex-wrap: wrap; gap: 12px; }}
    .button {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 10px;
      min-height: 50px;
      padding: 0 19px;
      border: 1px solid var(--line);
      border-radius: 14px;
      color: var(--text);
      background: rgba(255,255,255,.035);
      font: inherit;
      font-weight: 700;
      text-decoration: none;
      cursor: pointer;
      transition: transform .18s ease, background .18s ease, border-color .18s ease;
    }}
    .button:hover {{ transform: translateY(-2px); border-color: rgba(126,240,189,.35); background: rgba(255,255,255,.07); }}
    .button-primary {{ color: var(--ink); background: var(--accent); border-color: var(--accent); box-shadow: 0 15px 40px rgba(73,216,157,.16); }}
    .button-primary:hover {{ background: #95f5ca; border-color: #95f5ca; }}
    .repo-card {{ position: relative; overflow: hidden; padding: 26px; border: 1px solid var(--line); border-radius: 28px; background: linear-gradient(145deg, rgba(26,51,44,.92), rgba(12,27,23,.88)); box-shadow: 0 30px 70px rgba(0,0,0,.22); }}
    .repo-card::after {{ content: ""; position: absolute; width: 190px; height: 190px; right: -80px; top: -90px; border-radius: 50%; background: rgba(126,240,189,.09); filter: blur(4px); }}
    .repo-label {{ color: var(--muted); font-size: .78rem; font-weight: 700; letter-spacing: .1em; text-transform: uppercase; }}
    .repo-title {{ margin: 12px 0 6px; font-size: 1.45rem; font-weight: 750; letter-spacing: -.03em; }}
    .repo-note {{ margin: 0 0 24px; color: var(--muted); font-size: .92rem; }}
    .url-box {{ display: flex; align-items: center; gap: 10px; padding: 12px 13px; border: 1px solid var(--line); border-radius: 14px; background: rgba(2,12,9,.42); }}
    .url-box code {{ min-width: 0; flex: 1; overflow: hidden; color: #cae6dc; font-size: .76rem; text-overflow: ellipsis; white-space: nowrap; }}
    .copy {{ padding: 0; border: 0; color: var(--accent); background: none; font: inherit; font-size: .78rem; font-weight: 750; cursor: pointer; }}
    .repo-card .button {{ width: 100%; margin-top: 13px; }}
    .content {{ padding: 20px 0 92px; }}
    .section-head {{ display: flex; align-items: end; justify-content: space-between; gap: 20px; margin-bottom: 24px; }}
    h2 {{ margin: 0; font-size: clamp(1.8rem, 4vw, 2.6rem); letter-spacing: -.045em; }}
    .section-copy {{ margin: 6px 0 0; color: var(--muted); }}
    .app-count {{ flex: none; padding: 8px 12px; border: 1px solid var(--line); border-radius: 999px; color: var(--muted); font-size: .8rem; }}
    .app-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }}
    .app-card {{ display: flex; flex-direction: column; min-height: 246px; padding: 22px; border: 1px solid var(--line); border-radius: var(--radius); background: var(--panel); backdrop-filter: blur(14px); transition: transform .2s ease, border-color .2s ease; }}
    .app-card:hover {{ transform: translateY(-3px); border-color: rgba(126,240,189,.34); }}
    .app-top {{ display: flex; align-items: center; gap: 15px; }}
    .app-icon {{ width: 58px; height: 58px; flex: none; overflow: hidden; display: grid; place-items: center; border: 1px solid var(--line); border-radius: 16px; color: var(--accent); background: #19342c; font-weight: 800; font-size: 1.15rem; }}
    .app-icon img {{ width: 100%; height: 100%; object-fit: cover; }}
    .app-name {{ margin: 0; font-size: 1.15rem; letter-spacing: -.02em; }}
    .app-id {{ max-width: 260px; overflow: hidden; margin-top: 3px; color: var(--muted); font: .74rem ui-monospace, SFMono-Regular, Menlo, monospace; text-overflow: ellipsis; white-space: nowrap; }}
    .summary {{ flex: 1; margin: 20px 0; color: var(--muted); font-size: .92rem; }}
    .app-footer {{ display: flex; align-items: center; justify-content: space-between; gap: 12px; }}
    .version {{ color: #b8cbc4; font-size: .78rem; }}
    .app-actions {{ display: flex; align-items: center; gap: 14px; }}
    .text-link {{ color: var(--muted); font-size: .82rem; font-weight: 650; text-decoration: none; }}
    .download {{ color: var(--accent); font-size: .82rem; font-weight: 750; text-decoration: none; }}
    .state {{ grid-column: 1 / -1; padding: 64px 24px; border: 1px dashed var(--line); border-radius: var(--radius); color: var(--muted); text-align: center; }}
    .state strong {{ display: block; margin-bottom: 5px; color: var(--text); font-size: 1rem; }}
    footer {{ display: flex; justify-content: space-between; gap: 20px; padding: 28px 0 42px; border-top: 1px solid var(--line); color: var(--muted); font-size: .78rem; }}
    @media (max-width: 820px) {{
      .hero {{ grid-template-columns: 1fr; gap: 36px; padding-top: 64px; }}
      .repo-card {{ max-width: 560px; }}
    }}
    @media (max-width: 620px) {{
      .shell {{ width: min(100% - 26px, 1120px); }}
      .nav {{ min-height: 66px; }}
      .nav-link span {{ display: none; }}
      .hero {{ padding: 54px 0 62px; }}
      h1 {{ font-size: clamp(2.8rem, 16vw, 4.2rem); }}
      .app-grid {{ grid-template-columns: 1fr; }}
      .section-head {{ align-items: start; }}
      .app-card {{ min-height: 230px; }}
      footer {{ flex-direction: column; }}
    }}
    @media (prefers-reduced-motion: reduce) {{ * {{ scroll-behavior: auto; transition: none !important; }} }}
  </style>
</head>
<body>
  <header class="shell nav">
    <div class="brand">
      <span class="brand-mark" aria-hidden="true">
        <svg width="21" height="21" viewBox="0 0 24 24" fill="none"><path d="M7 4v9.5A3.5 3.5 0 0 0 10.5 17H20" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/><path d="m4 10 3 3 3-3M16 14l4 3-4 3" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>
      </span>
      <span>{safe_name}</span>
    </div>
    <nav class="nav-links" aria-label="Repository links">
      <a class="nav-link" href="{safe_repo_url}"><span>Repository</span> ↗</a>
      <a class="nav-link" href="{safe_source_url}" target="_blank" rel="noreferrer"><span>Source</span> ↗</a>
    </nav>
  </header>

  <main class="shell">
    <section class="hero">
      <div>
        <div class="eyebrow">Independent app distribution</div>
        <h1>Your apps.<br>Directly delivered.</h1>
        <p class="hero-copy">{safe_description} Add this repository once, then install and update every available app from F-Droid.</p>
        <div class="actions">
          <a class="button button-primary" href="{safe_add_url}">Add to F-Droid <span aria-hidden="true">↗</span></a>
          <a class="button" href="#apps">Browse apps <span aria-hidden="true">↓</span></a>
        </div>
      </div>
      <aside class="repo-card" aria-label="Repository address">
        <div class="repo-label">Repository endpoint</div>
        <div class="repo-title">Ready for F-Droid</div>
        <p class="repo-note">The install link opens F-Droid with this repository prefilled.</p>
        <div class="url-box">
          <code id="repo-url">{safe_repo_url}</code>
          <button class="copy" id="copy-repo" type="button">Copy</button>
        </div>
        <a class="button" href="{safe_repo_url}">View raw repository <span aria-hidden="true">↗</span></a>
      </aside>
    </section>

    <section class="content" id="apps">
      <div class="section-head">
        <div>
          <h2>Available apps</h2>
          <p class="section-copy">Signed Android packages published in this repository.</p>
        </div>
        <span class="app-count" id="app-count">Loading</span>
      </div>
      <div class="app-grid" id="app-list" aria-live="polite">
        <div class="state"><strong>Loading the catalog…</strong>Reading the latest repository index.</div>
      </div>
    </section>
  </main>

  <footer class="shell">
    <span>Distributed with F-Droid technology.</span>
    <a href="{safe_source_url}" target="_blank" rel="noreferrer">View the distribution kit source ↗</a>
  </footer>

  <script>
    const appList = document.getElementById('app-list');
    const appCount = document.getElementById('app-count');
    const copyButton = document.getElementById('copy-repo');
    const repoUrl = document.getElementById('repo-url').textContent;

    copyButton.addEventListener('click', async () => {{
      try {{
        await navigator.clipboard.writeText(repoUrl);
        copyButton.textContent = 'Copied';
        window.setTimeout(() => {{ copyButton.textContent = 'Copy'; }}, 1600);
      }} catch (_) {{
        copyButton.textContent = 'Select URL';
      }}
    }});

    function element(tag, className, text) {{
      const node = document.createElement(tag);
      if (className) node.className = className;
      if (text !== undefined) node.textContent = text;
      return node;
    }}

    function renderApp(app) {{
      const card = element('article', 'app-card');
      const top = element('div', 'app-top');
      const icon = element('div', 'app-icon', (app.name || app.id).slice(0, 2).toUpperCase());
      if (app.icon_url) {{
        const image = document.createElement('img');
        image.src = app.icon_url;
        image.alt = '';
        image.loading = 'lazy';
        image.addEventListener('error', () => image.remove());
        icon.append(image);
      }}
      const identity = document.createElement('div');
      identity.append(element('h3', 'app-name', app.name || app.id));
      identity.append(element('div', 'app-id', app.id));
      top.append(icon, identity);

      const summary = element('p', 'summary', app.summary || 'Available as a signed Android package.');
      const footer = element('div', 'app-footer');
      footer.append(element('span', 'version', `Version ${{app.version}}`));
      const actions = element('div', 'app-actions');
      if (app.source_url) {{
        const source = element('a', 'text-link', 'Source ↗');
        source.href = app.source_url;
        source.target = '_blank';
        source.rel = 'noreferrer';
        actions.append(source);
      }}
      const download = element('a', 'download', 'Download APK ↓');
      download.href = app.download_url;
      actions.append(download);
      footer.append(actions);
      card.append(top, summary, footer);
      return card;
    }}

    async function loadApps() {{
      try {{
        const response = await fetch('/api/apps', {{ headers: {{ Accept: 'application/json' }} }});
        if (!response.ok) throw new Error(`HTTP ${{response.status}}`);
        const data = await response.json();
        const apps = Array.isArray(data.apps) ? data.apps : [];
        appList.replaceChildren();
        appCount.textContent = `${{apps.length}} ${{apps.length === 1 ? 'app' : 'apps'}}`;
        if (!apps.length) {{
          const empty = element('div', 'state');
          empty.append(element('strong', '', 'No apps published yet'));
          empty.append(document.createTextNode('The catalog will update automatically after the first APK is published.'));
          appList.append(empty);
          return;
        }}
        apps.forEach(app => appList.append(renderApp(app)));
      }} catch (_) {{
        appCount.textContent = 'Unavailable';
        const error = element('div', 'state');
        error.append(element('strong', '', 'Could not load the catalog'));
        error.append(document.createTextNode('The repository is still available from the link above. Try refreshing this page.'));
        appList.replaceChildren(error);
      }}
    }}

    loadApps();
  </script>
</body>
</html>""")
