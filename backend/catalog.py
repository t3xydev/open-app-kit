from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlparse


def _localized_text(value: Any, fallback: str = "") -> str:
    """Read a human-facing value from either v1 or v2 localized metadata."""
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return fallback
    for locale in ("en-US", "en", "C"):
        localized = value.get(locale)
        if isinstance(localized, str) and localized.strip():
            return localized
    return next(
        (item for item in value.values() if isinstance(item, str) and item.strip()),
        fallback,
    )


def _public_repo_file(repo_url: str, filename: str) -> str:
    return f"{repo_url.rstrip('/')}/{quote(filename.lstrip('/'), safe='/')}"


def _app_page_path(bundle_id: str) -> str:
    return f"/apps/{quote(bundle_id, safe='.')}"


def _icon_url(repo_url: str, icon: Any, *, v2: bool = False) -> str | None:
    if isinstance(icon, dict):
        icon = icon.get("en-US") or icon.get("en") or next(iter(icon.values()), None)
    if isinstance(icon, dict):
        icon = icon.get("name")
    if not isinstance(icon, str) or not icon.strip():
        return None
    icon_path = icon.strip().lstrip("/")
    if not v2 and "/" not in icon_path:
        icon_path = f"icons-640/{icon_path}"
    return _public_repo_file(repo_url, icon_path)


def _latest_version(versions: Any, *, v2: bool) -> dict[str, Any] | None:
    if isinstance(versions, dict):
        candidates = list(versions.values())
    elif isinstance(versions, list):
        candidates = versions
    else:
        return None

    def version_code(version: Any) -> int:
        if not isinstance(version, dict):
            return -1
        manifest = version.get("manifest", {}) if v2 else version
        if not isinstance(manifest, dict):
            return -1
        try:
            return int(manifest.get("versionCode", -1))
        except (TypeError, ValueError):
            return -1

    valid = [version for version in candidates if isinstance(version, dict)]
    return max(valid, key=version_code, default=None)


def _metadata_url(metadata: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            url = value.strip()
            if urlparse(url).scheme in {"http", "https"}:
                return url
    return None


def parse_index_v1(index_data: dict[str, Any], repo_url: str) -> list[dict[str, Any]]:
    """Convert the standard F-Droid index-v1 shape into homepage app cards."""
    raw_apps = index_data.get("apps", [])
    packages = index_data.get("packages", {})
    if not isinstance(raw_apps, list) or not isinstance(packages, dict):
        return []

    apps: list[dict[str, Any]] = []
    for metadata in raw_apps:
        if not isinstance(metadata, dict):
            continue
        app_id = str(metadata.get("packageName") or "").strip()
        if not app_id:
            continue
        latest = _latest_version(packages.get(app_id, []), v2=False)
        if not latest:
            continue
        apk_name = latest.get("apkName")
        if not isinstance(apk_name, str) or not apk_name:
            continue
        apps.append(
            {
                "id": app_id,
                "page_url": _app_page_path(app_id),
                "name": _localized_text(metadata.get("name"), app_id),
                "summary": _localized_text(metadata.get("summary")),
                "version": str(latest.get("versionName") or latest.get("versionCode") or "Latest"),
                "version_code": latest.get("versionCode"),
                "download_url": _public_repo_file(repo_url, apk_name),
                "icon_url": _icon_url(repo_url, metadata.get("icon")),
                "source_url": _metadata_url(metadata, "sourceCode", "webSite"),
            }
        )
    return sorted(apps, key=lambda app: app["name"].casefold())


def parse_index_v2(index_data: dict[str, Any], repo_url: str) -> list[dict[str, Any]]:
    """Convert index-v2 packages into homepage app cards."""
    packages = index_data.get("packages", {})
    if not isinstance(packages, dict):
        return []

    apps: list[dict[str, Any]] = []
    for app_id, package in packages.items():
        if not isinstance(package, dict):
            continue
        metadata = package.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        latest = _latest_version(package.get("versions", {}), v2=True)
        if not latest:
            continue
        manifest = latest.get("manifest", {})
        file_info = latest.get("file", {})
        if not isinstance(manifest, dict) or not isinstance(file_info, dict):
            continue
        apk_name = file_info.get("name")
        if not isinstance(apk_name, str) or not apk_name:
            continue
        apps.append(
            {
                "id": str(app_id),
                "page_url": _app_page_path(str(app_id)),
                "name": _localized_text(metadata.get("name"), str(app_id)),
                "summary": _localized_text(metadata.get("summary")),
                "version": str(manifest.get("versionName") or manifest.get("versionCode") or "Latest"),
                "version_code": manifest.get("versionCode"),
                "download_url": _public_repo_file(repo_url, apk_name),
                "icon_url": _icon_url(repo_url, metadata.get("icon"), v2=True),
                "source_url": _metadata_url(metadata, "sourceCode", "webSite"),
            }
        )
    return sorted(apps, key=lambda app: app["name"].casefold())


def fdroid_deep_link(repo_url: str, fingerprint: str = "") -> str:
    target = f"https://fdroid.link/#repo={quote(repo_url, safe=':/')}"
    if fingerprint:
        target += f"&fingerprint={quote(fingerprint, safe='')}"
    return target
