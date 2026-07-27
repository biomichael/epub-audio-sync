#!/usr/bin/env python
# coding=utf-8

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from .pipeline import EPUBAudioSyncBuilder


def clean_surrogates(value):
    if isinstance(value, str):
        return value.encode("utf-8", errors="replace").decode("utf-8")
    if isinstance(value, list):
        return [clean_surrogates(item) for item in value]
    if isinstance(value, dict):
        return {
            clean_surrogates(key): clean_surrogates(item)
            for key, item in value.items()
        }
    return value


def _candidate_ffmpeg_dirs():
    env_dir = os.environ.get("AENEAS_FFMPEG_DIR")
    if env_dir:
        yield Path(env_dir)
    exe_dir = Path(sys.executable).resolve().parent
    yield exe_dir
    yield exe_dir.parent


def emit(payload):
    safe_payload = clean_surrogates(payload)
    sys.stdout.write(json.dumps(safe_payload, ensure_ascii=True) + "\n")
    sys.stdout.flush()


def read_stdin_json():
    raw = sys.stdin.read()
    if not raw.strip():
        raise ValueError("Expected JSON payload on stdin")
    return json.loads(raw)


def command_inspect():
    payload = read_stdin_json()
    builder = EPUBAudioSyncBuilder(aeneas_repo_path=payload.get("aeneas_repo_path"))
    inspection = builder.inspect_epub(payload["epub"], payload["out"])
    emit({"type": "result", "data": inspection})


def command_run():
    payload = read_stdin_json()
    builder = EPUBAudioSyncBuilder(
        aeneas_repo_path=payload.get("aeneas_repo_path"),
        logger=lambda message: emit({"type": "log", "message": message}),
    )
    try:
        result = builder.run(payload)
    except Exception as exc:
        emit({"type": "error", "message": str(exc)})
        return 1
    emit({"type": "result", "data": result})
    return 0


def command_proof_mfa():
    payload = read_stdin_json()
    builder = EPUBAudioSyncBuilder(
        aeneas_repo_path=payload.get("aeneas_repo_path"),
        logger=lambda message: emit({"type": "log", "message": message}),
    )
    try:
        result = builder.run(payload)
    except Exception as exc:
        emit({"type": "error", "message": str(exc)})
        return 1
    emit({"type": "result", "data": result})
    return 0


def command_preview():
    payload = read_stdin_json()
    builder = EPUBAudioSyncBuilder(aeneas_repo_path=payload.get("aeneas_repo_path"))
    preview = builder.preview_payload(payload["result"], payload["chapter_root_href"])
    emit({"type": "result", "data": preview})


def command_inspect_preview():
    payload = read_stdin_json()
    builder = EPUBAudioSyncBuilder(aeneas_repo_path=payload.get("aeneas_repo_path"))
    preview = builder.inspect_preview_payload(
        payload["inspection"],
        payload["chapter_root_href"],
        payload.get("chapters", []),
    )
    emit({"type": "result", "data": preview})


def command_find_sync():
    payload = read_stdin_json()
    builder = EPUBAudioSyncBuilder(
        aeneas_repo_path=payload.get("aeneas_repo_path"),
        logger=lambda message: emit({"type": "log", "message": message}),
    )
    try:
        result = builder.find_sync(payload)
    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        emit({"type": "error", "message": str(exc)})
        emit({"type": "log", "message": "Find Sync error: " + str(exc)})
        emit({"type": "log", "message": tb})
        return 1
    emit({"type": "result", "data": result})
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m aeneas.epubsync.desktop_api")
    parser.add_argument("command", choices=["inspect", "run", "preview", "inspect_preview", "proof_mfa", "find_sync"])
    args = parser.parse_args(argv)
    for directory in _candidate_ffmpeg_dirs():
        ffmpeg = directory / "ffmpeg.exe"
        ffprobe = directory / "ffprobe.exe"
        if ffmpeg.exists() and ffprobe.exists():
            existing = os.environ.get("PATH", "")
            os.environ["PATH"] = os.pathsep.join([str(directory)] + [segment for segment in existing.split(os.pathsep) if segment])
            break
    if args.command == "inspect":
        command_inspect()
        return 0
    if args.command == "run":
        return command_run()
    if args.command == "proof_mfa":
        return command_proof_mfa()
    if args.command == "preview":
        command_preview()
        return 0
    if args.command == "inspect_preview":
        command_inspect_preview()
        return 0
    if args.command == "find_sync":
        return command_find_sync()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
