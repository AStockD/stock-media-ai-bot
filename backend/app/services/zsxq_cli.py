"""Subprocess wrapper around official zsxq-cli with per-user home isolation."""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

from app.config import DATA_DIR

logger = logging.getLogger(__name__)

ZSXQ_HOMES_DIR = DATA_DIR / "zsxq_homes"
ZSXQ_CLI_PATH = os.getenv("ZSXQ_CLI_PATH", "").strip() or shutil.which("zsxq-cli") or "zsxq-cli"


class ZsxqCliError(Exception):
    def __init__(self, message: str, exit_code: int = 1, raw: str = ""):
        super().__init__(message)
        self.exit_code = exit_code
        self.raw = raw


def user_home(user_id: int) -> Path:
    home = ZSXQ_HOMES_DIR / str(user_id)
    home.mkdir(parents=True, exist_ok=True)
    return home


def _build_env(user_id: int) -> dict[str, str]:
    home = str(user_home(user_id))
    env = os.environ.copy()
    env["HOME"] = home
    env["USERPROFILE"] = home
    env["XDG_CONFIG_HOME"] = str(Path(home) / ".config")
    env["XDG_DATA_HOME"] = str(Path(home) / ".local" / "share")
    # Avoid interactive prompts / browser popups in server context
    env["BROWSER"] = "echo"
    return env


def _extract_json(stdout: str) -> Optional[Any]:
    text = (stdout or "").strip()
    if not text:
        return None
    # CLI may print human text before/after JSON
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch not in "{[":
            continue
        try:
            obj, _ = decoder.raw_decode(text[i:])
            return obj
        except json.JSONDecodeError:
            continue
    return None


def run_cli(
    user_id: int,
    args: list[str],
    *,
    timeout: int = 120,
    want_json: bool = True,
) -> dict[str, Any]:
    cmd = [ZSXQ_CLI_PATH, *args]
    if want_json and "--json" not in args:
        cmd.append("--json")

    logger.info("zsxq-cli user=%s cmd=%s", user_id, " ".join(cmd[1:]))
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_build_env(user_id),
            timeout=timeout,
            cwd=str(user_home(user_id)),
        )
    except FileNotFoundError as e:
        raise ZsxqCliError(
            "zsxq-cli 未安装。请在服务器安装: npm install -g zsxq-cli",
            exit_code=127,
        ) from e
    except subprocess.TimeoutExpired as e:
        raise ZsxqCliError(f"zsxq-cli 超时 ({timeout}s)", exit_code=124) from e

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    combined = stdout + ("\n" + stderr if stderr else "")
    parsed = _extract_json(stdout) if want_json else None

    if proc.returncode != 0:
        msg = ""
        if isinstance(parsed, dict):
            msg = (
                parsed.get("error")
                or parsed.get("message")
                or (parsed.get("data") or {}).get("reason")
                or ""
            )
        if not msg:
            msg = (stderr or stdout or f"exit {proc.returncode}").strip()[:500]
        raise ZsxqCliError(msg, exit_code=proc.returncode, raw=combined)

    if want_json:
        if isinstance(parsed, dict):
            return parsed
        if parsed is not None:
            return {"ok": True, "data": parsed}
        # Some success paths print only human text
        return {"ok": True, "data": {"raw": stdout.strip()}}

    return {"ok": True, "data": {"raw": stdout.strip()}}


def unwrap_data(result: dict[str, Any]) -> Any:
    if not isinstance(result, dict):
        return result
    if "data" in result:
        return result["data"]
    return result
