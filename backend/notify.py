from __future__ import annotations

import asyncio
import os
import sys
from typing import Literal

import apprise

APPRISE_CONFIG_URL = os.environ.get("APPRISE_CONFIG_URL", "").strip()
REPO_NAME = os.environ.get("REPO_NAME", "F-Droid Repository").strip() or "F-Droid Repository"

_apprise_instance: apprise.Apprise | None = None
_apprise_init_failed = False


def _build_apprise() -> apprise.Apprise | None:
    global _apprise_instance, _apprise_init_failed

    if not APPRISE_CONFIG_URL:
        return None
    if _apprise_init_failed:
        return None
    if _apprise_instance is not None:
        return _apprise_instance

    try:
        config = apprise.AppriseConfig()
        if not config.add(APPRISE_CONFIG_URL):
            raise RuntimeError(f"Could not load Apprise config from {APPRISE_CONFIG_URL}")

        instance = apprise.Apprise()
        if not instance.add(config):
            raise RuntimeError(f"Could not attach Apprise config from {APPRISE_CONFIG_URL}")

        _apprise_instance = instance
        return _apprise_instance
    except Exception as exc:  # noqa: BLE001
        _apprise_init_failed = True
        print(f"[notify] Failed to initialize Apprise: {exc}", file=sys.stderr)
        return None


def _notify_publish_result_sync(
    status: Literal["success", "failed"],
    trigger: str,
    message: str,
    exit_code: int | None = None,
    log_tail: str | None = None,
) -> None:
    instance = _build_apprise()
    if instance is None:
        return

    title = f"[{REPO_NAME}] Publish {'succeeded' if status == 'success' else 'failed'}"
    lines = [
        f"Trigger: {trigger}",
        f"Status: {status}",
        f"Message: {message}",
    ]
    if exit_code is not None:
        lines.append(f"Exit code: {exit_code}")
    if log_tail:
        lines.append("")
        lines.append("Log tail:")
        lines.append(log_tail[-500:])

    body = "\n".join(lines)
    try:
        if not instance.notify(body=body, title=title):
            # Apprise returns False when no services were notified.
            print(
                f"[notify] Apprise notify returned False for trigger={trigger}",
                file=sys.stderr,
            )
    except Exception as exc:  # noqa: BLE001
        print(f"[notify] Apprise notify failed: {exc}", file=sys.stderr)


async def notify_publish_result(
    status: Literal["success", "failed"],
    trigger: str,
    message: str,
    exit_code: int | None = None,
    log_tail: str | None = None,
) -> None:
    await asyncio.to_thread(
        _notify_publish_result_sync,
        status,
        trigger,
        message,
        exit_code,
        log_tail,
    )
