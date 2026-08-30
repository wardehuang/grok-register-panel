# -*- coding: utf-8 -*-
"""List / download local CPA and Grok2API auth JSON files for the panel."""
from __future__ import annotations

import base64
import io
import json
import os
import re
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.environ.get("EMAIL_PROVIDER_CONFIG_FILE", str(ROOT / "config.json")))

_SAFE_NAME = re.compile(r"^[A-Za-z0-9._@+\-\[\]]{1,240}\.json$")


class AuthFilesError(ValueError):
    pass


def _load_cfg() -> dict:
    if not CONFIG_PATH.is_file():
        return {}
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8") or "{}")
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _resolve_dir(raw: object, default_rel: str) -> Path:
    text = str(raw or "").strip() or default_rel
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    else:
        path = path.resolve()
    return path


def resolve_kind_dir(kind: str) -> tuple[str, Path]:
    k = str(kind or "").strip().lower()
    cfg = _load_cfg()
    if k in {"cpa", "cpa_auth"}:
        return "cpa", _resolve_dir(cfg.get("cpa_auth_dir"), "cpa_auth")
    if k in {"g2a", "grok2api", "grok2api_auth"}:
        return "g2a", _resolve_dir(cfg.get("grok2api_auth_dir"), "grok2api_auth")
    raise AuthFilesError("kind 必须是 cpa 或 g2a")


def _safe_filename(name: str) -> str:
    base = Path(str(name or "").strip()).name
    if not base or base in {".", ".."}:
        raise AuthFilesError("文件名无效")
    if not _SAFE_NAME.match(base):
        raise AuthFilesError("仅允许安全的 .json 文件名")
    return base


def _resolve_file(directory: Path, name: str) -> Path:
    base = _safe_filename(name)
    path = (directory / base).resolve()
    try:
        path.relative_to(directory.resolve())
    except Exception as exc:
        raise AuthFilesError("路径越界") from exc
    if not path.is_file():
        raise AuthFilesError("文件不存在")
    return path


def _peek_meta(path: Path) -> dict[str, Any]:
    email = ""
    disabled = None
    has_access = False
    try:
        raw = json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception:
        raw = None
    if isinstance(raw, dict):
        email = str(raw.get("email") or "").strip()
        if "disabled" in raw:
            disabled = bool(raw.get("disabled"))
        if raw.get("access_token") or raw.get("refresh_token"):
            has_access = True
        # grok2api nested form: { "issuer::client": { ... } }
        if not email:
            for val in raw.values():
                if isinstance(val, dict):
                    email = str(val.get("email") or "").strip()
                    if val.get("access_token") or val.get("refresh_token"):
                        has_access = True
                    if email:
                        break
    stem = path.stem
    if not email:
        for prefix in ("xai-", "g2a-"):
            if stem.lower().startswith(prefix):
                email = stem[len(prefix) :]
                break
    try:
        st = path.stat()
        size = int(st.st_size)
        mtime = float(st.st_mtime)
    except OSError:
        size = 0
        mtime = None
    return {
        "name": path.name,
        "email": email,
        "bytes": size,
        "mtime": mtime,
        "disabled": disabled,
        "has_token": has_access,
    }


def list_auth_files(kind: str = "cpa", *, limit: int = 2000) -> dict[str, Any]:
    label, directory = resolve_kind_dir(kind)
    keep = max(1, min(10000, int(limit or 2000)))
    files: list[dict[str, Any]] = []
    if directory.is_dir():
        for path in sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime if p.is_file() else 0, reverse=True):
            if not path.is_file():
                continue
            if path.name.startswith("."):
                continue
            try:
                files.append(_peek_meta(path))
            except Exception:
                continue
            if len(files) >= keep:
                break
    total = 0
    if directory.is_dir():
        total = sum(1 for p in directory.glob("*.json") if p.is_file() and not p.name.startswith("."))
    return {
        "ok": True,
        "kind": label,
        "path": str(directory.relative_to(ROOT)) if str(directory).startswith(str(ROOT)) else str(directory),
        "abs_path": str(directory),
        "exists": directory.is_dir(),
        "count": total,
        "files": files,
        "truncated": total > len(files),
    }


def read_auth_file(kind: str, name: str) -> dict[str, Any]:
    label, directory = resolve_kind_dir(kind)
    path = _resolve_file(directory, name)
    text = path.read_text(encoding="utf-8")
    # validate json
    json.loads(text)
    meta = _peek_meta(path)
    return {
        "ok": True,
        "kind": label,
        "filename": path.name,
        "path": str(path.relative_to(ROOT)) if str(path).startswith(str(ROOT)) else str(path),
        "bytes": len(text.encode("utf-8")),
        "text": text,
        "meta": meta,
    }


def read_auth_zip(kind: str) -> dict[str, Any]:
    label, directory = resolve_kind_dir(kind)
    if not directory.is_dir():
        raise AuthFilesError(f"{label} 目录不存在")
    buf = io.BytesIO()
    count = 0
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(directory.glob("*.json")):
            if not path.is_file() or path.name.startswith("."):
                continue
            try:
                _safe_filename(path.name)
            except AuthFilesError:
                continue
            zf.write(path, arcname=path.name)
            count += 1
    if count <= 0:
        raise AuthFilesError(f"{label} 目录没有可打包的 json")
    data = buf.getvalue()
    return {
        "ok": True,
        "kind": label,
        "filename": f"{label}_auth_{count}.zip",
        "count": count,
        "bytes": len(data),
        "content_b64": base64.b64encode(data).decode("ascii"),
    }
