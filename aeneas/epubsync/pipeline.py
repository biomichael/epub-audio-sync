#!/usr/bin/env python
# coding=utf-8

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import subprocess
from pathlib import Path

import numpy as np
from xml.etree import ElementTree as ET

from lxml import etree

from .alignment import build_alignment_engine
from .mfa_textgrid import parse_mfa_textgrid_words
from .epub import OPF_NS
from .epub import ensure_parent_dir
from .epub import guess_media_type
from .epub import parse_xml
from .epub import repack_epub
from .epub import unzip_epub
from .xhtml import xhtml_document_title
from .xhtml import xhtml_body_html
from .xhtml import SEGMENT_ATTR
from .xhtml import segment_xhtml_file

XHTML_NS = "http://www.w3.org/1999/xhtml"
EPUB_NS = "http://www.idpf.org/2007/ops"
FOOTNOTE_NSMAP = {"xhtml": XHTML_NS}

_NUM_WORDS = {
    "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
    "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine",
    "10": "ten", "11": "eleven", "12": "twelve", "13": "thirteen",
    "14": "fourteen", "15": "fifteen", "16": "sixteen", "17": "seventeen",
    "18": "eighteen", "19": "nineteen", "20": "twenty",
    "30": "thirty", "40": "forty", "50": "fifty", "60": "sixty",
    "70": "seventy", "80": "eighty", "90": "ninety", "100": "one hundred",
}

HIGHLIGHT_CSS = """.audio-active {
  text-decoration-line: underline;
  text-decoration-style: wavy;
  text-decoration-color: rgba(24, 117, 179, 0.92);
  text-decoration-thickness: 0.12em;
  text-underline-offset: 0.18em;
}
"""


class EPUBAudioSyncBuilder(object):
    def __init__(self, aeneas_repo_path=None, logger=None):
        self.aeneas_repo_path = str(Path(aeneas_repo_path).resolve()) if aeneas_repo_path else None
        self.logger = logger
        if self.aeneas_repo_path and self.aeneas_repo_path not in sys.path:
            sys.path.insert(0, self.aeneas_repo_path)
        self._maybe_prepend_local_ffmpeg_paths()

    def log(self, message):
        if self.logger is not None:
            self.logger(message)

    def inspect_epub(self, epub_path, work_root):
        unpack_dir = Path(work_root) / "unpacked"
        inspection = unzip_epub(epub_path, unpack_dir)
        if inspection["has_encryption_xml"]:
            self.log(
                "Warning: META-INF/encryption.xml was found. Run this tool before encryption or on a decrypted editable EPUB only."
            )
        return inspection

    def run(self, config):
        epub_path = Path(config["epub"]).resolve()
        output_root = Path(config["out"]).resolve()
        work_root = output_root / "work"
        work_root.mkdir(parents=True, exist_ok=True)
        mfa_settings = self._normalize_mfa_settings(config)
        config = dict(config)
        config["mfa"] = mfa_settings
        if mfa_settings.get("proof_mode"):
            proof_result = self._run_mfa_proof(mfa_settings)
            return {
                "alignment_settings": {
                    "alignment_engine": config.get("alignment_engine", "aeneas"),
                    "mfa": mfa_settings,
                },
                "mfa_proof": proof_result,
                "inspection": None,
                "patched_epub": None,
                "json_dir": None,
                "smil_dir": None,
                "text_dir": None,
                "logs_dir": None,
                "debug_log": None,
                "preview": [],
                "warnings": [],
            }
        self._ensure_alignment_prerequisites()
        inspection = self.inspect_epub(str(epub_path), work_root)
        chapters_by_href, ordered_chapters = self._index_spine_items(inspection)
        chapter_configs = self._normalize_chapter_configs(config, ordered_chapters)
        assignments = self._normalize_assignments(config, ordered_chapters)
        chapter_configs = self._canonicalize_chapter_configs(chapter_configs, chapters_by_href)
        assignments = self._canonicalize_assignments(assignments, chapters_by_href)
        if not assignments:
            raise ValueError("At least one alignment assignment with an audio file is required")
        alignment_engine_name = (config.get("alignment_engine") or "aeneas").strip().lower()

        css_path = self._write_highlight_css(inspection)
        json_dir = output_root / "json"
        smil_dir = output_root / "smil"
        log_dir = output_root / "logs"
        text_dir = output_root / "text"
        for path in [json_dir, smil_dir, log_dir, text_dir]:
            path.mkdir(parents=True, exist_ok=True)

        chapter_segments = self._segment_selected_chapters(inspection, assignments, chapter_configs, chapters_by_href, css_path)
        if alignment_engine_name == "mfa":
            mfa_jobs, audio_registry = self._run_mfa_jobs(
                inspection=inspection,
                assignments=assignments,
                chapter_segments=chapter_segments,
                ordered_chapters=ordered_chapters,
                config=config,
                output_root=output_root,
            )
            text_id_to_chapter_href = {}
            for chapter_href, chapter_data in chapter_segments.items():
                for segment in chapter_data["segments"]:
                    text_id_to_chapter_href[segment["textId"]] = chapter_href
            mfa_chapter_exports = {}
            for job in mfa_jobs:
                if not job["success"]:
                    continue
                audio_source_id = job["audioSourceId"]
                for seg in job["segments"]:
                    chapter_href = text_id_to_chapter_href.get(seg["textId"])
                    if chapter_href is None:
                        continue
                    if chapter_href not in mfa_chapter_exports:
                        chapter_data = chapter_segments.get(chapter_href, {})
                        mfa_chapter_exports[chapter_href] = {
                            "chapter": chapters_by_href[chapter_href],
                            "chapterHref": chapter_href,
                            "chapterPath": chapter_data.get("chapterPath"),
                            "chapterTitle": chapter_data.get("chapterTitle"),
                            "segments": [],
                            "audioSourceIds": set(),
                        }
                    mfa_chapter_exports[chapter_href]["audioSourceIds"].add(audio_source_id)
                    mfa_chapter_exports[chapter_href]["segments"].append(seg)

            if mfa_chapter_exports:
                preview = []
                exported_chapters = []
                manifest_updates = []

                for chapter_href in [chapter["root_href"] for chapter in ordered_chapters]:
                    chapter_export = mfa_chapter_exports.get(chapter_href)
                    if chapter_export is None or len(chapter_export["segments"]) == 0:
                        continue
                    chapter_export["segments"].sort(key=lambda item: item.get("segmentOrder") or 0)
                    chapter_export["audioSources"] = [
                        self._public_audio_source(self._find_audio_source_by_id(audio_registry, source_id))
                        for source_id in sorted(chapter_export["audioSourceIds"])
                    ]
                    chapter_json = self._build_mfa_chapter_json(chapter_export)
                    json_path = json_dir / ("%s.json" % Path(chapter_href).stem)
                    json_path.write_text(json.dumps(chapter_json, indent=2, ensure_ascii=False), encoding="utf-8")
                    json_rel_path = self._write_chapter_json_to_epub(inspection, chapter_href, chapter_json)

                    smil_rel_path = self._write_chapter_smil(
                        inspection=inspection,
                        chapter_export=chapter_export,
                        audio_registry=audio_registry,
                        embedded_audio=not config["external_audio"],
                        smil_dir=smil_dir,
                    )
                    chapter_duration = round(sum(segment["end"] - segment["begin"] for segment in chapter_export["segments"]), 3)
                    manifest_updates.append(
                        {
                            "chapter": chapter_export["chapter"],
                            "smil_href": smil_rel_path,
                            "json_href": json_rel_path,
                            "duration": chapter_duration,
                        }
                    )
                    aligned_by_text_id = {}
                    for seg in chapter_export["segments"]:
                        aligned_by_text_id[seg["textId"]] = seg
                    full_segments = chapter_segments.get(chapter_href, {}).get("segments", [])
                    merged_segments = []
                    for seg in full_segments:
                        text_id = seg["textId"]
                        aligned = aligned_by_text_id.get(text_id)
                        if aligned:
                            merged_segments.append(aligned)
                        elif seg.get("is_footnote"):
                            merged_segments.append({
                                "textId": text_id,
                                "begin": 0.0,
                                "end": 0.0,
                                "audioSourceId": None,
                                "segmentOrder": seg.get("segmentOrder", 0),
                            })
                        else:
                            merged_segments.append({
                                "textId": text_id,
                                "begin": 0.0,
                                "end": 0.0,
                                "audioSourceId": None,
                                "segmentOrder": seg.get("segmentOrder", 0),
                            })
                    preview.append(
                        {
                            "chapter_href": chapter_export["chapterHref"],
                            "chapter_root_href": chapter_export["chapterHref"],
                            "chapter_path": chapter_export["chapterPath"],
                            "chapter_title": chapter_export.get("chapterTitle"),
                            "json_path": str(json_path),
                            "audio_sources": [
                                self._preview_audio_source(self._find_audio_source_by_id(audio_registry, source["id"]))
                                for source in chapter_export["audioSources"]
                            ],
                            "segments": [
                                {
                                    "textId": seg["textId"],
                                    "begin": seg["begin"],
                                    "end": seg["end"],
                                    "audioSourceId": seg.get("audioSourceId"),
                                }
                                for seg in merged_segments
                            ],
                        }
                    )
                    exported_chapters.append(chapter_export["chapterHref"])

                if not config["external_audio"]:
                    self._copy_embedded_audio_files(inspection, audio_registry)

                self._patch_opf(
                    inspection=inspection,
                    manifest_updates=manifest_updates,
                    embed_audio=not config["external_audio"],
                    audio_registry=audio_registry,
                )

                patched_epub_path = output_root / ("%s.synced.epub" % epub_path.stem)
                self.log("Repacking EPUB")
                repack_epub(inspection["epub_root"], patched_epub_path)
            else:
                patched_epub_path = None
                error_messages = []
                for job in mfa_jobs:
                    job_errors = []
                    if job.get("error"):
                        job_errors.append(job["error"])
                    if job.get("stderr"):
                        job_errors.append(job["stderr"])
                    if job_errors:
                        error_messages.append("Job %s: %s" % (job["jobId"], " | ".join(job_errors)))
                if error_messages:
                    raise ValueError("All MFA jobs failed:\n%s" % "\n".join(error_messages))

            debug_log_path = log_dir / "build.json"
            debug_log_path.write_text(
                json.dumps(
                    {
                        "alignment_settings": {
                            "alignment_engine": "mfa",
                            "mfa": mfa_settings,
                        },
                        "inspection": inspection,
                        "chapters": chapter_configs,
                        "assignments": assignments,
                        "mfa_jobs": mfa_jobs,
                        "audio_sources": [self._debug_audio_source(source) for source in audio_registry.values()],
                        "manifest_updates": manifest_updates if mfa_chapter_exports else [],
                        "preview": preview if mfa_chapter_exports else [],
                        "patched_epub": str(patched_epub_path) if patched_epub_path else None,
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            return {
                "alignment_settings": {
                    "alignment_engine": "mfa",
                    "mfa": mfa_settings,
                },
                "inspection": inspection,
                "mfa_jobs": mfa_jobs,
                "patched_epub": str(patched_epub_path) if patched_epub_path else None,
                "json_dir": str(json_dir),
                "smil_dir": str(smil_dir),
                "text_dir": str(text_dir),
                "logs_dir": str(log_dir),
                "debug_log": str(debug_log_path),
                "preview": preview if mfa_chapter_exports else [],
                "warnings": [
                    "META-INF/encryption.xml detected. Use a decrypted, editable EPUB and do not attempt to bypass DRM."
                ]
                if inspection["has_encryption_xml"]
                else [],
            }
        audio_registry = {}
        chapter_exports = self._initialize_chapter_exports(chapter_segments, chapters_by_href)
        used_text_ids = set()
        manifest_updates = []

        for assignment_index, assignment in enumerate(assignments, start=1):
            segment_records = self._resolve_assignment_segments(assignment, chapter_segments, ordered_chapters)
            if not segment_records:
                raise ValueError("Assignment %d does not include any text segments" % assignment_index)
            for record in segment_records:
                if record["syncId"] in used_text_ids:
                    raise ValueError("Text segment '%s' was assigned more than once" % record["syncId"])
                used_text_ids.add(record["syncId"])

            assignment_text_path = text_dir / ("assignment%02d.txt" % assignment_index)
            self._write_assignment_text(assignment_text_path, segment_records)
            audio_path = Path(assignment["audio"]).resolve()
            audio_source = self._register_audio_source(
                audio_registry=audio_registry,
                audio_path=audio_path,
                external_audio=config["external_audio"],
            )

            alignment_engine = self._create_alignment_engine(config)
            self.log("Running %s for assignment %d" % (alignment_engine.__class__.__name__, assignment_index))
            alignment_result = alignment_engine.align(
                audio_path=audio_path,
                text_path=assignment_text_path,
                language=config.get("language", "eng"),
                segment_records=segment_records,
            )
            audio_source["duration"] = round(float(alignment_result.audio_duration), 3)
            alignment_index = 0
            last_end = 0.0
            for record in segment_records:
                chapter_export = chapter_exports[record["chapterHref"]]
                chapter_export["audioSourceIds"].add(audio_source["id"])
                if record.get("is_footnote"):
                    begin = last_end
                    end = begin
                else:
                    timing = alignment_result.segments[alignment_index]
                    begin = timing.begin
                    end = timing.end
                    alignment_index += 1
                    last_end = end
                chapter_export["segments"].append(
                    {
                        "syncId": record["syncId"],
                        "textId": record["textId"],
                        "begin": begin,
                        "end": end,
                        "text": record["text"],
                        "audioSourceId": audio_source["id"],
                        "segmentOrder": record["segmentOrder"],
                    }
                )

        if not chapter_exports:
            raise ValueError("No chapter segments were exported")

        preview = []
        exported_chapters = []
        for chapter_href in [chapter["root_href"] for chapter in ordered_chapters]:
            chapter_export = chapter_exports.get(chapter_href)
            if chapter_export is None or len(chapter_export["segments"]) == 0:
                continue
            chapter_export["segments"].sort(key=lambda item: item["segmentOrder"])
            chapter_export["audioSources"] = [
                self._public_audio_source(self._find_audio_source_by_id(audio_registry, source_id))
                for source_id in sorted(chapter_export["audioSourceIds"])
            ]
            chapter_json = self._build_chapter_json(chapter_export)
            json_path = json_dir / ("%s.json" % Path(chapter_href).stem)
            json_path.write_text(json.dumps(chapter_json, indent=2, ensure_ascii=False), encoding="utf-8")
            json_rel_path = self._write_chapter_json_to_epub(inspection, chapter_href, chapter_json)

            self.log("Generating SMIL for %s" % chapter_href)
            smil_rel_path = self._write_chapter_smil(
                inspection=inspection,
                chapter_export=chapter_export,
                audio_registry=audio_registry,
                embedded_audio=not config["external_audio"],
                smil_dir=smil_dir,
            )
            chapter_duration = round(sum(segment["end"] - segment["begin"] for segment in chapter_export["segments"]), 3)
            manifest_updates.append(
                {
                    "chapter": chapter_export["chapter"],
                    "smil_href": smil_rel_path,
                    "json_href": json_rel_path,
                    "duration": chapter_duration,
                }
            )
            aligned_by_text_id = {}
            for seg in chapter_export["segments"]:
                aligned_by_text_id[seg["textId"]] = seg
            full_segments = chapter_segments.get(chapter_href, {}).get("segments", [])
            merged_segments = []
            for seg in full_segments:
                text_id = seg["textId"]
                aligned = aligned_by_text_id.get(text_id)
                if aligned:
                    merged_segments.append(aligned)
                else:
                    merged_segments.append({
                        "textId": text_id,
                        "begin": 0.0,
                        "end": 0.0,
                        "audioSourceId": None,
                    })
            preview.append(
                {
                    "chapter_href": chapter_export["chapterHref"],
                    "chapter_root_href": chapter_export["chapterHref"],
                    "chapter_path": chapter_export["chapterPath"],
                    "chapter_title": chapter_export.get("chapterTitle"),
                    "json_path": str(json_path),
                    "audio_sources": [
                        self._preview_audio_source(self._find_audio_source_by_id(audio_registry, source["id"]))
                        for source in chapter_export["audioSources"]
                    ],
                    "segments": [self._segment_without_order(seg) for seg in merged_segments],
                }
            )
            exported_chapters.append(chapter_export["chapterHref"])

        if not config["external_audio"]:
            self.log("Embedding audio into EPUB")
            self._copy_embedded_audio_files(inspection, audio_registry)

        self.log("Patching OPF")
        self._patch_opf(
            inspection=inspection,
            manifest_updates=manifest_updates,
            embed_audio=not config["external_audio"],
            audio_registry=audio_registry,
        )

        patched_epub_path = output_root / ("%s.synced.epub" % epub_path.stem)
        self.log("Repacking EPUB")
        repack_epub(inspection["epub_root"], patched_epub_path)

        debug_log_path = log_dir / "build.json"
        debug_log_path.write_text(
            json.dumps(
                {
                    "alignment_settings": {
                        "alignment_engine": config.get("alignment_engine", "aeneas"),
                        "mfa": mfa_settings,
                    },
                    "inspection": inspection,
                    "chapters": chapter_configs,
                    "assignments": assignments,
                    "audio_sources": [self._debug_audio_source(source) for source in audio_registry.values()],
                    "manifest_updates": manifest_updates,
                    "preview": preview,
                    "patched_epub": str(patched_epub_path),
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return {
            "alignment_settings": {
                "alignment_engine": config.get("alignment_engine", "aeneas"),
                "mfa": mfa_settings,
            },
            "inspection": inspection,
            "patched_epub": str(patched_epub_path),
            "json_dir": str(json_dir),
            "smil_dir": str(smil_dir),
            "text_dir": str(text_dir),
            "logs_dir": str(log_dir),
            "debug_log": str(debug_log_path),
            "preview": preview,
            "warnings": [
                "META-INF/encryption.xml detected. Use a decrypted, editable EPUB and do not attempt to bypass DRM."
            ]
            if inspection["has_encryption_xml"]
            else [],
        }

    def _create_alignment_engine(self, config):
        media_tools = self._media_tool_paths()
        return build_alignment_engine(
            config.get("alignment_engine", "aeneas"),
            logger=self.logger,
            ffmpeg_path=media_tools.get("ffmpeg"),
            ffprobe_path=media_tools.get("ffprobe"),
        )

    def _ensure_alignment_prerequisites(self):
        media_tools = self._media_tool_paths()
        missing = [name for name in ("ffprobe", "ffmpeg") if not media_tools.get(name)]
        if missing:
            raise RuntimeError(
                "Missing required media tool(s): %s. Install FFmpeg and ensure ffprobe/ffmpeg are on PATH before running alignment."
                % ", ".join(missing)
            )

    def _maybe_prepend_local_ffmpeg_paths(self):
        search_roots = []
        if self.aeneas_repo_path:
            search_roots.append(Path(self.aeneas_repo_path))
        search_roots.append(Path.cwd())
        candidate_dirs = []
        for root in search_roots:
            ffmpeg_dir = root / "node_modules" / "ffmpeg-static"
            ffprobe_dir = root / "node_modules" / "ffprobe-static" / "bin" / "win32" / "x64"
            for directory in (ffmpeg_dir, ffprobe_dir):
                if directory.exists():
                    candidate_dirs.append(str(directory.resolve()))
        if not candidate_dirs:
            return
        existing = os.environ.get("PATH", "")
        segments = [segment for segment in existing.split(os.pathsep) if segment]
        for directory in reversed(candidate_dirs):
            if directory not in segments:
                segments.insert(0, directory)
        os.environ["PATH"] = os.pathsep.join(segments)

    def _media_tool_paths(self):
        search_roots = []
        if self.aeneas_repo_path:
            search_roots.append(Path(self.aeneas_repo_path))
        search_roots.append(Path.cwd())
        candidates = {"ffmpeg": [], "ffprobe": []}
        for root in search_roots:
            candidates["ffmpeg"].extend(
                [
                    root / "node_modules" / "ffmpeg-static" / "ffmpeg.exe",
                    root / "node_modules" / "ffmpeg-static" / "ffmpeg",
                ]
            )
            candidates["ffprobe"].extend(
                [
                    root / "node_modules" / "ffprobe-static" / "bin" / "win32" / "x64" / "ffprobe.exe",
                    root / "node_modules" / "ffprobe-static" / "bin" / "linux" / "x64" / "ffprobe",
                    root / "node_modules" / "ffprobe-static" / "bin" / "darwin" / "x64" / "ffprobe",
                    root / "node_modules" / "ffprobe-static" / "bin" / "darwin" / "arm64" / "ffprobe",
                ]
            )
        result = {}
        for name, paths in candidates.items():
            found = next((str(path.resolve()) for path in paths if path.exists()), None)
            result[name] = found or shutil.which(name)
        return result

    def _index_spine_items(self, inspection):
        chapters_by_href = {}
        ordered = []
        for chapter in inspection["spine_items"]:
            chapters_by_href[chapter["root_href"]] = chapter
            chapters_by_href[chapter["href"]] = chapter
            ordered.append(chapter)
        return chapters_by_href, ordered

    def _normalize_chapter_configs(self, config, ordered_chapters):
        default_segment = config.get("segment", "sentence")
        chapter_configs = {}
        for chapter in ordered_chapters:
            chapter_configs[chapter["root_href"]] = {
                "chapter": chapter["root_href"],
                "segment": default_segment,
            }
        for chapter_config in config.get("chapters", []):
            chapter_href = chapter_config.get("chapter")
            if chapter_href in chapter_configs:
                chapter_configs[chapter_href]["segment"] = chapter_config.get("segment", default_segment)
        for mapping in config.get("mappings", []):
            chapter_href = mapping.get("chapter")
            if chapter_href in chapter_configs:
                chapter_configs[chapter_href]["segment"] = mapping.get("segment", default_segment)
        return list(chapter_configs.values())

    def _normalize_assignments(self, config, ordered_chapters):
        if config.get("assignments"):
            rows = config.get("assignments", [])
        else:
            rows = []
            for mapping in config.get("mappings", []):
                rows.append(
                    {
                        "audio": mapping.get("audio"),
                        "startChapter": mapping.get("chapter"),
                        "endChapter": mapping.get("chapter"),
                        "startSegment": 1,
                        "endSegment": None,
                    }
                )
        normalized = []
        for index, row in enumerate(rows, start=1):
            audio = (row.get("audio") or "").strip()
            if not audio:
                continue
            start_chapter = row.get("startChapter") or row.get("chapter")
            end_chapter = row.get("endChapter") or start_chapter
            if not start_chapter or not end_chapter:
                raise ValueError("Assignment %d is missing a start or end chapter" % index)
            normalized.append(
                {
                    "id": row.get("id") or ("assignment-%02d" % index),
                    "audio": audio,
                    "startChapter": start_chapter,
                    "endChapter": end_chapter,
                    "startSegment": self._parse_optional_int(row.get("startSegment"), default=1),
                    "endSegment": self._parse_optional_int(row.get("endSegment"), default=None),
                }
            )
        return normalized

    def _canonicalize_chapter_configs(self, chapter_configs, chapters_by_href):
        normalized = []
        for chapter_config in chapter_configs:
            chapter = chapters_by_href.get(chapter_config["chapter"])
            if chapter is None:
                continue
            normalized.append(
                {
                    "chapter": chapter["root_href"],
                    "segment": chapter_config.get("segment", "sentence"),
                }
            )
        return normalized

    def _canonicalize_assignments(self, assignments, chapters_by_href):
        normalized = []
        for assignment in assignments:
            start_chapter = chapters_by_href.get(assignment["startChapter"])
            end_chapter = chapters_by_href.get(assignment["endChapter"])
            if start_chapter is None or end_chapter is None:
                raise ValueError("Assignment '%s' references a chapter that is not in the spine" % assignment["id"])
            normalized.append(
                {
                    "id": assignment["id"],
                    "audio": assignment["audio"],
                    "startChapter": start_chapter["root_href"],
                    "endChapter": end_chapter["root_href"],
                    "startSegment": assignment["startSegment"],
                    "endSegment": assignment["endSegment"],
                }
            )
        return normalized

    def _segment_selected_chapters(self, inspection, assignments, chapter_configs, chapters_by_href, css_path):
        segment_by_chapter = {}
        chapter_config_map = dict((item["chapter"], item) for item in chapter_configs)
        selected_chapters = set()
        for assignment in assignments:
            selected_chapters.add(assignment["startChapter"])
            selected_chapters.add(assignment["endChapter"])
            start_chapter = chapters_by_href.get(assignment["startChapter"])
            end_chapter = chapters_by_href.get(assignment["endChapter"])
            if start_chapter is None or end_chapter is None:
                raise ValueError("Assignment references a chapter that is not in the spine")
            start_index = start_chapter["index"]
            end_index = end_chapter["index"]
            if start_index > end_index:
                raise ValueError("Assignment '%s' starts after it ends" % assignment["id"])
            for chapter in inspection["spine_items"]:
                if start_index <= chapter["index"] <= end_index:
                    selected_chapters.add(chapter["root_href"])

        footnotes_by_id = self._parse_footnotes(inspection)

        for chapter_href in selected_chapters:
            chapter = chapters_by_href.get(chapter_href)
            chapter_path = Path(chapter["full_path"])
            css_href = os.path.relpath(css_path, str(chapter_path.parent)).replace("\\", "/")
            segment_mode = chapter_config_map.get(chapter_href, {}).get("segment", "sentence")
            self.log("Tagging XHTML %s" % chapter_href)
            segments = segment_xhtml_file(
                chapter_path,
                chapter_number=chapter["index"],
                segmentation_mode=segment_mode,
                css_href=css_href,
            )
            if footnotes_by_id:
                segments = self._inject_footnotes(chapter_path, segments, footnotes_by_id)
            segment_by_chapter[chapter["root_href"]] = {
                "chapter": chapter,
                "chapterHref": chapter["root_href"],
                "chapterPath": str(chapter_path),
                "chapterTitle": xhtml_document_title(chapter_path),
                "segmentMode": segment_mode,
                "segments": [
                    {
                        "syncId": self._sync_text_id(chapter["root_href"], segment.get("id") or segment.get("textId")),
                        "textId": segment.get("id") or segment.get("textId"),
                        "text": segment["text"],
                        "segmentOrder": segment_index,
                        "is_footnote": segment.get("is_footnote", False),
                    }
                    for segment_index, segment in enumerate(segments, start=1)
                ],
            }
        return segment_by_chapter

    def _parse_footnotes(self, inspection):
        footnotes = {}
        for item in inspection["spine_items"]:
            if Path(item["href"]).name.lower() not in ("footnotes.xhtml", "footnote.xhtml"):
                continue
            fn_path = Path(item["full_path"])
            if not fn_path.exists():
                break
            tree = parse_xml(fn_path)
            for elem in tree.iter():
                tag = etree.QName(elem.tag).localname.lower()
                if tag != "aside":
                    continue
                fn_id = elem.get("id")
                if not fn_id:
                    continue
                raw = "".join(elem.itertext())
                text = re.sub(r"\s+", " ", raw).strip()
                if text:
                    footnotes[fn_id] = text
            break
        return footnotes

    def _inject_footnotes(self, chapter_path, segments, footnotes_by_id):
        tree = parse_xml(chapter_path)
        body = tree.find(".//xhtml:body", namespaces=FOOTNOTE_NSMAP)
        if body is None:
            return segments
        epub_type_key = "{%s}type" % EPUB_NS
        existing_ids = set()
        for elem in body.iter():
            elem_id = elem.get("id")
            if elem_id:
                existing_ids.add(elem_id)
        footnoted_spans = {}
        dirty = False
        for elem in body.iter():
            tag = etree.QName(elem.tag).localname.lower()
            if tag != "a":
                continue
            if elem.get(epub_type_key) != "noteref":
                continue
            href = elem.get("href", "")
            if "#" not in href:
                continue
            fn_id = href.split("#", 1)[1]
            if fn_id not in footnotes_by_id:
                continue
            parent = elem
            span_id = None
            while parent is not None:
                if parent.get(SEGMENT_ATTR) == "1" and parent.get("id"):
                    span_id = parent.get("id")
                    break
                parent = parent.getparent()
            if not span_id:
                continue
            ref_id = elem.get("id")
            if not ref_id:
                ref_id = self._unique_footnote_ref_id(existing_ids, span_id)
                elem.set("id", ref_id)
                dirty = True
            footnoted_spans.setdefault(span_id, []).append({"textId": ref_id, "text": footnotes_by_id[fn_id]})
        if not footnoted_spans:
            return segments
        if dirty:
            tree.write(str(chapter_path), encoding="utf-8", xml_declaration=True, pretty_print=True)
        new_segments = []
        for segment in segments:
            new_segments.append(segment)
            text_id = segment.get("id") or segment.get("textId")
            if text_id and text_id in footnoted_spans:
                for footnote in footnoted_spans[text_id]:
                    new_segments.append({
                        "id": footnote["textId"],
                        "text": footnote["text"],
                        "is_footnote": True,
                    })
        return new_segments

    def _unique_footnote_ref_id(self, existing_ids, segment_id):
        counter = 1
        while True:
            candidate = "%s-fnref%d" % (segment_id, counter)
            if candidate not in existing_ids:
                existing_ids.add(candidate)
                return candidate
            counter += 1

    def _initialize_chapter_exports(self, chapter_segments, chapters_by_href):
        exports = {}
        for chapter_href, chapter_data in chapter_segments.items():
            exports[chapter_href] = {
                "chapter": chapters_by_href[chapter_href],
                "chapterHref": chapter_href,
                "chapterPath": chapter_data["chapterPath"],
                "chapterTitle": chapter_data["chapterTitle"],
                "segments": [],
                "audioSourceIds": set(),
            }
        return exports

    def _resolve_assignment_segments(self, assignment, chapter_segments, ordered_chapters):
        chapter_order = [chapter["root_href"] for chapter in ordered_chapters]
        start_chapter = assignment["startChapter"]
        end_chapter = assignment["endChapter"]
        if start_chapter not in chapter_order or end_chapter not in chapter_order:
            raise ValueError("Assignment '%s' references a chapter outside the spine" % assignment["id"])
        start_index = chapter_order.index(start_chapter)
        end_index = chapter_order.index(end_chapter)
        if start_index > end_index:
            raise ValueError("Assignment '%s' starts after it ends" % assignment["id"])

        records = []
        for chapter_position in range(start_index, end_index + 1):
            chapter_href = chapter_order[chapter_position]
            chapter_data = chapter_segments.get(chapter_href)
            if chapter_data is None:
                raise ValueError("Chapter '%s' was not segmented" % chapter_href)
            total_segments = len(chapter_data["segments"])
            if total_segments == 0:
                continue
            first_segment = 1 if chapter_href != start_chapter else assignment["startSegment"]
            last_segment = total_segments if chapter_href != end_chapter else (assignment["endSegment"] or total_segments)
            if first_segment < 1 or last_segment < 1 or first_segment > total_segments or last_segment > total_segments:
                raise ValueError("Assignment '%s' references a segment outside chapter '%s'" % (assignment["id"], chapter_href))
            if first_segment > last_segment:
                raise ValueError("Assignment '%s' has an invalid segment range for chapter '%s'" % (assignment["id"], chapter_href))
            for segment in chapter_data["segments"][first_segment - 1:last_segment]:
                records.append(
                    {
                        "chapterHref": chapter_href,
                        "syncId": segment["syncId"],
                        "textId": segment["textId"],
                        "text": segment["text"],
                        "segmentOrder": segment["segmentOrder"],
                        "is_footnote": segment.get("is_footnote", False),
                    }
                )
        return records

    def _register_audio_source(self, audio_registry, audio_path, external_audio):
        absolute_path = str(audio_path.resolve())
        source = audio_registry.get(absolute_path)
        if source is not None:
            return source
        source_id = "audio%03d" % (len(audio_registry) + 1)
        embedded_href = None if external_audio else self._embedded_audio_href(audio_path, source_id)
        source = {
            "id": source_id,
            "path": absolute_path,
            "external": bool(external_audio),
            "embeddedHref": embedded_href,
            "src": os.path.basename(absolute_path) if external_audio else embedded_href,
            "duration": None,
        }
        audio_registry[absolute_path] = source
        return source

    def _write_highlight_css(self, inspection):
        package_dir = Path(inspection["package_dir"])
        css_dir = self._resolve_support_dir(package_dir, preferred_name="styles")
        css_dir.mkdir(parents=True, exist_ok=True)
        css_path = css_dir / "audio-sync.css"
        css_path.write_text(HIGHLIGHT_CSS, encoding="utf-8")
        return str(css_path)

    def _write_assignment_text(self, path, segment_records, for_mfa=False):
        lines = []
        if for_mfa:
            # MFA should see one utterance per record so footnotes keep their own timings
            # and can no longer steal words from the following segment.
            for record in segment_records:
                lines.append(self._normalize_mfa_text(record["text"]))
        else:
            i = 0
            while i < len(segment_records):
                record = segment_records[i]
                if record.get("is_footnote"):
                    i += 1
                    continue
                text = record["text"]
                i += 1
                while i < len(segment_records) and segment_records[i].get("is_footnote"):
                    text += " " + segment_records[i]["text"]
                    i += 1
                lines.append(self._sanitize_alignment_text(text))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _build_chapter_json(self, chapter_export):
        segments = [self._segment_without_order(segment) for segment in chapter_export["segments"]]
        chapter_title = chapter_export.get("chapterTitle")
        if chapter_title is None and chapter_export.get("chapterPath"):
            chapter_title = xhtml_document_title(chapter_export["chapterPath"])
        result = {
            "chapterHref": chapter_export["chapterHref"],
            "chapterTitle": chapter_title,
            "audioSources": chapter_export["audioSources"],
            "segments": segments,
        }
        if len(chapter_export["audioSources"]) == 1:
            source = chapter_export["audioSources"][0]
            result["audio"] = {
                "src": source["src"],
                "external": source["external"],
                "duration": source["duration"],
            }
        return result

    def _build_mfa_chapter_json(self, chapter_export):
        chapter_title = chapter_export.get("chapterTitle")
        if chapter_title is None and chapter_export.get("chapterPath"):
            chapter_title = xhtml_document_title(chapter_export["chapterPath"])
        return {
            "chapterHref": chapter_export["chapterHref"],
            "chapterTitle": chapter_title,
            "audioSources": chapter_export["audioSources"],
            "segments": [
                {
                    "textId": segment["textId"],
                    "text": segment["text"],
                    "begin": segment["begin"],
                    "end": segment["end"],
                    "audioSourceId": segment["audioSourceId"],
                    "words": segment["words"],
                }
                for segment in chapter_export["segments"]
            ],
        }

    def _segment_without_order(self, segment):
        return {
            "textId": segment["textId"],
            "begin": segment["begin"],
            "end": segment["end"],
            "audioSourceId": segment["audioSourceId"],
        }

    def _public_audio_source(self, audio_source):
        return {
            "id": audio_source["id"],
            "src": audio_source["src"].replace("\\", "/"),
            "external": audio_source["external"],
            "duration": audio_source["duration"],
        }

    def _preview_audio_source(self, audio_source):
        return {
            "id": audio_source["id"],
            "src": audio_source["src"].replace("\\", "/"),
            "path": audio_source["path"],
            "external": audio_source["external"],
            "duration": audio_source["duration"],
        }

    def _debug_audio_source(self, audio_source):
        return {
            "id": audio_source["id"],
            "path": audio_source["path"],
            "src": audio_source["src"],
            "external": audio_source["external"],
            "embedded_href": audio_source["embeddedHref"],
            "duration": audio_source["duration"],
        }

    def _embedded_audio_href(self, audio_path, source_id):
        suffix = Path(audio_path).suffix.lower()
        return ("Audio/%s%s" % (source_id, suffix)).replace("\\", "/")

    def _sync_text_id(self, chapter_href, text_id):
        return "%s__%s" % (Path(chapter_href).stem, text_id)

    def _resolve_support_dir(self, package_dir, preferred_name):
        preferred_lower = preferred_name.lower()
        for child in package_dir.iterdir():
            if child.is_dir() and child.name.lower() == preferred_lower:
                return child
        return package_dir / preferred_name

    def _write_chapter_json_to_epub(self, inspection, chapter_href, chapter_json):
        package_dir = Path(inspection["package_dir"])
        overlay_dir = self._resolve_support_dir(package_dir, preferred_name="MediaOverlays")
        overlay_dir.mkdir(parents=True, exist_ok=True)
        json_name = "%s.json" % Path(chapter_href).stem
        json_path = overlay_dir / json_name
        json_path.write_text(json.dumps(chapter_json, indent=2, ensure_ascii=False), encoding="utf-8")
        return os.path.relpath(str(json_path), str(package_dir)).replace("\\", "/")

    def _write_chapter_smil(self, inspection, chapter_export, audio_registry, embedded_audio, smil_dir):
        package_dir = Path(inspection["package_dir"])
        chapter_package_href = chapter_export["chapter"]["href"]
        chapter_dir = (package_dir / Path(chapter_package_href)).parent
        smil_name = "%s.smil" % Path(chapter_package_href).stem
        epub_smil_dir = self._resolve_support_dir(package_dir, preferred_name="MediaOverlays")
        epub_smil_dir.mkdir(parents=True, exist_ok=True)
        smil_path = epub_smil_dir / smil_name
        text_ref = os.path.basename(chapter_package_href)
        root = ET.Element("smil", {"xmlns": "http://www.w3.org/ns/SMIL", "version": "3.0"})
        body = ET.SubElement(root, "body")
        seq = ET.SubElement(
            body,
            "seq",
            {"id": "seq-%s" % Path(chapter_package_href).stem, "{http://www.idpf.org/2007/ops}textref": text_ref}
        )
        for index, segment in enumerate(chapter_export["segments"], start=1):
            par = ET.SubElement(seq, "par", {"id": "par%06d" % index})
            ET.SubElement(par, "text", {"src": "%s#%s" % (text_ref, segment["textId"])})
            audio_source = self._find_audio_source_by_id(audio_registry, segment["audioSourceId"])
            audio_ref = audio_source["src"]
            if embedded_audio:
                audio_ref = os.path.relpath(
                    str((Path(inspection["epub_root"]) / audio_source["embeddedHref"]).resolve()),
                    str(chapter_dir.resolve()),
                ).replace("\\", "/")
            ET.SubElement(
                par,
                "audio",
                {
                    "src": audio_ref.replace("\\", "/"),
                    "clipBegin": self._seconds_to_clock(segment["begin"]),
                    "clipEnd": self._seconds_to_clock(segment["end"]),
                },
            )
        ET.ElementTree(root).write(str(smil_path), encoding="utf-8", xml_declaration=True)
        shutil.copyfile(str(smil_path), str(smil_dir / smil_name))
        return os.path.relpath(str(smil_path), str(package_dir)).replace("\\", "/")

    def _seconds_to_clock(self, seconds):
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = seconds - (hours * 3600) - (minutes * 60)
        return "%02d:%02d:%06.3f" % (hours, minutes, secs)

    def _copy_embedded_audio_files(self, inspection, audio_registry):
        for audio_source in audio_registry.values():
            if audio_source["external"] or not audio_source["embeddedHref"]:
                continue
            destination = Path(inspection["epub_root"]) / audio_source["embeddedHref"]
            ensure_parent_dir(destination)
            shutil.copyfile(audio_source["path"], destination)

    def _patch_opf(self, inspection, manifest_updates, embed_audio, audio_registry):
        opf_path = Path(inspection["opf_path"])
        tree = parse_xml(opf_path)
        root = tree.getroot()
        manifest = root.find(".//opf:manifest", namespaces=OPF_NS)
        metadata = root.find(".//opf:metadata", namespaces=OPF_NS)
        if manifest is None or metadata is None:
            raise ValueError("OPF is missing manifest or metadata")

        total_duration = 0.0
        for update in manifest_updates:
            chapter = update["chapter"]
            chapter_item = manifest.find("opf:item[@id='%s']" % chapter["idref"], namespaces=OPF_NS)
            if chapter_item is None:
                continue
            smil_id = "%s-mo" % chapter["idref"]
            smil_item = manifest.find("opf:item[@id='%s']" % smil_id, namespaces=OPF_NS)
            if smil_item is None:
                smil_item = etree.SubElement(manifest, "{%s}item" % OPF_NS["opf"])
                smil_item.set("id", smil_id)
            smil_item.set("href", update["smil_href"])
            smil_item.set("media-type", "application/smil+xml")
            chapter_item.set("media-overlay", smil_id)
            json_id = "%s-json" % chapter["idref"]
            json_item = manifest.find("opf:item[@id='%s']" % json_id, namespaces=OPF_NS)
            if json_item is None:
                json_item = etree.SubElement(manifest, "{%s}item" % OPF_NS["opf"])
                json_item.set("id", json_id)
            json_item.set("href", update["json_href"])
            json_item.set("media-type", "application/json")
            total_duration += float(update["duration"])
            meta = etree.SubElement(metadata, "{%s}meta" % OPF_NS["opf"])
            meta.set("property", "media:duration")
            meta.set("refines", "#%s" % smil_id)
            meta.text = self._seconds_to_clock(float(update["duration"]))

        css_rel_href = self._audio_sync_css_rel_href(inspection)
        if css_rel_href:
            css_item = manifest.find("opf:item[@href='%s']" % css_rel_href, namespaces=OPF_NS)
            if css_item is None:
                css_item = etree.SubElement(manifest, "{%s}item" % OPF_NS["opf"])
                css_item.set("id", "audio-sync-css")
            css_item.set("href", css_rel_href)
            css_item.set("media-type", "text/css")

        if embed_audio:
            embedded_seen = set()
            for audio_source in audio_registry.values():
                if audio_source["external"] or not audio_source["embeddedHref"]:
                    continue
                if audio_source["embeddedHref"] in embedded_seen:
                    continue
                embedded_seen.add(audio_source["embeddedHref"])
                audio_id = audio_source["id"]
                audio_item = manifest.find("opf:item[@id='%s']" % audio_id, namespaces=OPF_NS)
                if audio_item is None:
                    audio_item = etree.SubElement(manifest, "{%s}item" % OPF_NS["opf"])
                    audio_item.set("id", audio_id)
                audio_item.set("href", audio_source["embeddedHref"])
                audio_item.set("media-type", guess_media_type(audio_source["embeddedHref"]))

        active_class = etree.SubElement(metadata, "{%s}meta" % OPF_NS["opf"])
        active_class.set("property", "media:active-class")
        active_class.text = "audio-active"
        total_meta = etree.SubElement(metadata, "{%s}meta" % OPF_NS["opf"])
        total_meta.set("property", "media:duration")
        total_meta.text = self._seconds_to_clock(total_duration)
        tree.write(str(opf_path), encoding="utf-8", xml_declaration=True, pretty_print=True)

    def _audio_sync_css_rel_href(self, inspection):
        package_dir = Path(inspection["package_dir"])
        for candidate in package_dir.rglob("audio-sync.css"):
            if candidate.is_file():
                return os.path.relpath(str(candidate), str(package_dir)).replace("\\", "/")
        return None

    def preview_payload(self, result, chapter_root_href):
        for chapter in result["preview"]:
            if chapter["chapter_root_href"] == chapter_root_href:
                return {
                    "chapterHref": chapter["chapter_href"],
                    "chapterTitle": chapter["chapter_title"],
                    "audioSources": chapter["audio_sources"],
                    "xhtml": xhtml_body_html(chapter["chapter_path"]),
                    "segments": chapter["segments"],
                }
        raise ValueError("Preview chapter not found: %s" % chapter_root_href)

    def inspect_preview_payload(self, inspection, chapter_root_href, chapter_configs):
        chapters_by_href, _ = self._index_spine_items(inspection)
        chapter_configs = self._canonicalize_chapter_configs(chapter_configs, chapters_by_href)
        chapter_config_map = dict((item["chapter"], item) for item in chapter_configs)
        chapter = chapters_by_href.get(chapter_root_href)
        if chapter is None:
            raise ValueError("Preview chapter not found: %s" % chapter_root_href)
        css_path = self._write_highlight_css(inspection)
        chapter_path = Path(chapter["full_path"])
        preview_root = Path(tempfile.mkdtemp(prefix="epubsync-preview-", dir=str(chapter_path.parent)))
        preview_path = preview_root / chapter_path.name
        shutil.copyfile(str(chapter_path), str(preview_path))
        css_href = os.path.relpath(css_path, str(preview_path.parent)).replace("\\", "/")
        segment_mode = chapter_config_map.get(chapter["root_href"], {}).get("segment", "sentence")
        segments = segment_xhtml_file(
            preview_path,
            chapter_number=chapter["index"],
            segmentation_mode=segment_mode,
            css_href=css_href,
        )
        footnotes_by_id = self._parse_footnotes(inspection)
        if footnotes_by_id:
            segments = self._inject_footnotes(preview_path, segments, footnotes_by_id)
        return {
            "chapterHref": chapter["root_href"],
            "segmentMode": segment_mode,
            "xhtml": xhtml_body_html(preview_path),
            "segments": [
                {
                    "textId": segment["id"],
                    "segmentIndex": index,
                    "text": segment["text"],
                }
                for index, segment in enumerate(segments, start=1)
            ],
        }

    def _parse_optional_int(self, value, default=None):
        if value in [None, "", "end"]:
            return default
        return int(value)

    def _normalize_mfa_settings(self, config):
        raw = config.get("mfa") or {}
        output_folder = raw.get("output_folder") or config.get("out")
        corpus_dir = raw.get("corpus_dir")
        if not corpus_dir:
            corpus_dir = str(Path(config["out"]).resolve() / "mfa-corpus")
        exe_path = raw.get("executable_path")
        if not exe_path:
            exe_path = os.environ.get("MFA_EXECUTABLE_PATH")
        normalized = {
            "executable_path": self._normalize_optional_path(exe_path, "MFA executable path", must_exist=True),
            "acoustic_model": self._normalize_optional_text(raw.get("acoustic_model")),
            "dictionary": self._normalize_mfa_dictionary(raw.get("dictionary")),
            "output_folder": self._normalize_optional_path(output_folder, "MFA output folder", must_exist=False),
            "corpus_dir": self._normalize_optional_path(corpus_dir, "MFA temporary corpus folder", must_exist=False),
            "audio_conversion": self._normalize_mfa_audio_conversion(raw.get("audio_conversion")),
            "proof_mode": bool(raw.get("proof_mode", False)),
        }
        return dict((key, value) for key, value in normalized.items() if value is not None)

    def _conda_env_for_mfa(self, executable_path):
        resolved = Path(executable_path).resolve()
        parts = resolved.parts
        try:
            idx = parts.index("envs")
            env_root = Path(*parts[:idx + 2])
            lib_bin = env_root / "Library" / "bin"
            if lib_bin.is_dir():
                return str(lib_bin)
        except (ValueError, IndexError):
            pass
        try:
            idx = parts.index("Library")
            env_root = Path(*parts[:idx])
            lib_bin = env_root / "Library" / "bin"
            if lib_bin.is_dir():
                return str(lib_bin)
        except (ValueError, IndexError):
            pass
        return None

    def _mfa_subprocess_env(self, executable_path):
        env = os.environ.copy()
        lib_bin = self._conda_env_for_mfa(executable_path)
        if lib_bin:
            existing = env.get("PATH", "")
            if lib_bin not in existing.split(os.pathsep):
                env["PATH"] = lib_bin + os.pathsep + existing
        return env

    def _run_mfa_proof(self, mfa_settings):
        executable_path = mfa_settings.get("executable_path") or os.environ.get("MFA_EXECUTABLE_PATH") or shutil.which("mfa")
        if not executable_path:
            raise ValueError("MFA executable path is not configured and mfa is not on PATH")
        command = [executable_path, "--help"]
        completed = subprocess.run(command, capture_output=True, text=True, env=self._mfa_subprocess_env(executable_path))
        return {
            "command": command,
            "return_code": completed.returncode,
            "stdout": (completed.stdout or "").strip(),
            "stderr": (completed.stderr or "").strip(),
            "success": completed.returncode == 0,
        }

    def _run_mfa_jobs(self, inspection, assignments, chapter_segments, ordered_chapters, config, output_root):
        mfa_settings = config.get("mfa") or {}
        executable_path = mfa_settings.get("executable_path") or os.environ.get("MFA_EXECUTABLE_PATH") or shutil.which("mfa")
        if not executable_path:
            raise ValueError("MFA executable path is not configured and mfa is not on PATH")
        dictionary_argument = self._mfa_dictionary_argument(mfa_settings.get("dictionary"))
        acoustic_model = self._normalize_optional_text(mfa_settings.get("acoustic_model"))
        if not acoustic_model:
            raise ValueError("MFA acoustic model name is required")
        corpus_root = Path(mfa_settings.get("corpus_dir") or (output_root / "mfa-corpus"))
        output_root_dir = Path(mfa_settings.get("output_folder") or output_root)
        corpus_root.mkdir(parents=True, exist_ok=True)
        output_root_dir.mkdir(parents=True, exist_ok=True)
        job_results = []
        audio_registry = {}
        for assignment_index, assignment in enumerate(assignments, start=1):
            segment_records = self._resolve_assignment_segments(assignment, chapter_segments, ordered_chapters)
            if not segment_records:
                raise ValueError("Assignment %d does not include any text segments" % assignment_index)
            job_id = assignment.get("id") or ("job-%02d" % assignment_index)
            audio_path = Path(assignment["audio"]).resolve()
            audio_source = self._register_audio_source(
                audio_registry=audio_registry,
                audio_path=audio_path,
                external_audio=config["external_audio"],
            )
            job_corpus_dir = corpus_root / job_id
            job_output_dir = output_root_dir / job_id
            job_corpus_dir.mkdir(parents=True, exist_ok=True)
            job_output_dir.mkdir(parents=True, exist_ok=True)
            transcript_path = job_corpus_dir / ("%s.txt" % job_id)
            self._write_assignment_text(transcript_path, segment_records, for_mfa=True)
            wav_path = job_corpus_dir / ("%s.wav" % job_id)
            self._prepare_mfa_audio(audio_path, wav_path, mfa_settings.get("audio_conversion"))
            textgrid_path = job_output_dir / ("%s.TextGrid" % job_id)
            if textgrid_path.exists():
                textgrid_path.unlink()
            mfa_temp_dir = Path(os.environ.get("MFA_ROOT_DIR", "~/Documents/MFA")).expanduser() / job_id
            if mfa_temp_dir.exists():
                shutil.rmtree(str(mfa_temp_dir))
            command = [
                executable_path,
                "align",
                str(job_corpus_dir),
                dictionary_argument,
                acoustic_model,
                str(job_output_dir),
            ]
            completed = subprocess.run(command, capture_output=True, text=True, env=self._mfa_subprocess_env(executable_path))
            words = []
            parse_error = None
            if completed.returncode == 0 and textgrid_path.exists():
                try:
                    words = parse_mfa_textgrid_words(textgrid_path)
                    if words:
                        audio_source["duration"] = round(max(w["end"] for w in words), 3)
                except Exception as exc:
                    parse_error = str(exc)
            elif completed.returncode == 0:
                parse_error = "MFA completed successfully but TextGrid was not created: %s" % textgrid_path
            else:
                stderr_text = (completed.stderr or "").strip()[:500]
                parse_error = "MFA exited with code %d: %s" % (completed.returncode, stderr_text)
            mapped_segments = []
            mapping_warnings = []
            if parse_error is None:
                mapped_segments, mapping_warnings = self._map_mfa_words_to_segments(
                    job_id,
                    segment_records,
                    words,
                    audio_source["id"],
                )
            job_results.append(
                {
                    "jobId": job_id,
                    "audioSourceId": audio_source["id"],
                    "textgridPath": str(textgrid_path),
                    "words": words,
                    "segments": mapped_segments,
                    "returnCode": completed.returncode,
                    "stdout": (completed.stdout or "").strip(),
                    "stderr": (completed.stderr or "").strip(),
                    "error": parse_error,
                    "warnings": mapping_warnings,
                    "success": completed.returncode == 0 and parse_error is None,
                }
            )
        return job_results, audio_registry

    def _assign_words_by_token_count(self, job_id, segment_records, words, audio_source_id, mapped_segments, warnings):
        total_expected = 0
        word_cursor = 0
        total_words = len(words)
        previous_end = mapped_segments[-1]["end"] if mapped_segments else None

        for record in segment_records:
            tokens = self._alignment_tokens(record["text"])
            token_count = len(tokens)
            total_expected += token_count
            assigned = words[word_cursor:word_cursor + token_count]
            if token_count > 0 and len(assigned) < token_count:
                warnings.append(
                    "MFA job %s word count mismatch for textId %s: expected %d words, got %d"
                    % (job_id, record["textId"], token_count, len(assigned))
                )
            word_cursor += len(assigned)

            if assigned:
                segment_begin = float(assigned[0]["begin"])
                segment_end = float(assigned[-1]["end"])
                previous_end = segment_end
            else:
                segment_begin = previous_end if previous_end is not None else 0.0
                segment_end = segment_begin

            if record.get("is_footnote"):
                word_entries = []
            else:
                word_entries = [
                    [
                        index,
                        round(float(word["begin"]) - segment_begin, 3),
                        round(float(word["end"]) - segment_begin, 3),
                        tokens[index] if index < len(tokens) else word["word"],
                    ]
                    for index, word in enumerate(assigned)
                ]

            mapped_segments.append(
                {
                    "textId": record["textId"],
                    "text": record["text"],
                    "segmentOrder": record.get("segmentOrder"),
                    "begin": round(segment_begin, 3),
                    "end": round(segment_end, 3),
                    "audioSourceId": record.get("audioSourceId") or audio_source_id,
                    "words": word_entries,
                }
            )

        if word_cursor < total_words and mapped_segments:
            extra_words = words[word_cursor:]
            warnings.append(
                "MFA job %s word count mismatch: expected %d words, got %d"
                % (job_id, total_expected, total_words)
            )
            last_segment = mapped_segments[-1]
            if last_segment["words"]:
                segment_begin = last_segment["begin"]
            else:
                segment_begin = float(extra_words[0]["begin"])
                last_segment["begin"] = round(segment_begin, 3)
            for extra_index, word in enumerate(extra_words, start=len(last_segment["words"])):
                last_segment["words"].append(
                    [
                        extra_index,
                        round(float(word["begin"]) - segment_begin, 3),
                        round(float(word["end"]) - segment_begin, 3),
                        word["word"],
                    ]
                )
            last_segment["end"] = round(float(extra_words[-1]["end"]), 3)

        if word_cursor != total_words and not warnings:
            warnings.append(
                "MFA job %s word count mismatch: expected %d words, got %d"
                % (job_id, total_expected, total_words)
            )

    def _map_mfa_words_to_segments(self, job_id, segment_records, words, audio_source_id=None):
        mapped_segments = []
        warnings = []
        self._assign_words_by_token_count(job_id, segment_records, words, audio_source_id, mapped_segments, warnings)
        return mapped_segments, warnings

    def _normalize_mfa_text(self, value):
        text = self._sanitize_alignment_text(value)
        if not text:
            return ""
        text = re.sub(r"\(([^)]*)\)", r"\1", text)
        text = re.sub(r"\[([^]]*)\]", r"\1", text)
        # MFA splits possessive 's as a separate word
        text = re.sub(r"[\u2019']s\b", " 's", text)
        # MFA drops standalone ellipsis punctuation.
        text = text.replace("…", "")
        text = text.replace("...", "")
        # MFA also splits hyphenated compounds into separate words.
        text = re.sub(r"(?<=\w)[\-\u2010-\u2015](?=\w)", " ", text)
        # MFA strips asterisks (maps them to <unk> or drops them)
        text = text.replace("*", "")
        return text

    def _alignment_tokens(self, value):
        text = self._normalize_mfa_text(value)
        if not text:
            return []
        return text.split()

    def _mfa_dictionary_argument(self, dictionary_value):
        if isinstance(dictionary_value, dict) and dictionary_value.get("value"):
            return dictionary_value["value"]
        dictionary_value = self._normalize_mfa_dictionary(dictionary_value)
        if dictionary_value is None:
            raise ValueError("MFA dictionary path or model name is required")
        return dictionary_value["value"]

    def _prepare_mfa_audio(self, source_audio_path, destination_wav_path, audio_conversion):
        if source_audio_path.suffix.lower() == ".wav" and self._normalize_mfa_audio_conversion(audio_conversion) != "wav":
            shutil.copyfile(str(source_audio_path), str(destination_wav_path))
            return
        if self._normalize_mfa_audio_conversion(audio_conversion) != "wav" and source_audio_path.suffix.lower() != ".wav":
            raise ValueError("MFA requires WAV audio unless audio conversion is set to wav: %s" % source_audio_path)
        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(source_audio_path),
            "-ac",
            "1",
            "-ar",
            "16000",
            str(destination_wav_path),
        ]
        completed = subprocess.run(command, capture_output=True, text=True)
        if completed.returncode != 0:
            raise RuntimeError("Audio conversion failed for %s: %s" % (source_audio_path, (completed.stderr or "").strip()))

    def _normalize_optional_text(self, value):
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _normalize_optional_path(self, value, label, must_exist=False):
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        path = Path(text).expanduser()
        if must_exist and not path.exists():
            raise ValueError("%s does not exist: %s" % (label, text))
        return str(path.resolve())

    def _normalize_mfa_dictionary(self, value):
        text = self._normalize_optional_text(value)
        if text is None:
            return None
        pathish = any(sep and sep in text for sep in (os.sep, os.altsep, ":"))
        candidate = Path(text).expanduser()
        if pathish:
            if not candidate.exists():
                raise ValueError("MFA dictionary path does not exist: %s" % text)
            if candidate.is_dir():
                raise ValueError("MFA dictionary path must point to a file: %s" % text)
            return {"kind": "path", "value": str(candidate.resolve())}
        return {"kind": "model", "value": text}

    def _normalize_mfa_audio_conversion(self, value):
        text = self._normalize_optional_text(value)
        if text is None:
            return None
        normalized = text.lower()
        if normalized not in ["none", "wav"]:
            raise ValueError("MFA audio conversion must be 'none' or 'wav'")
        return normalized

    def _sanitize_alignment_text(self, value):
        if value is None:
            return ""
        text = " ".join(str(value).split())
        for ch in ("|", "-", "\u2013", "\u2014", "\u2015"):
            text = text.replace(ch, " ")
        return text

    def _find_audio_source_by_id(self, audio_registry, source_id):
        for audio_source in audio_registry.values():
            if audio_source["id"] == source_id:
                return audio_source
        raise ValueError("Audio source '%s' was not found" % source_id)

    def _get_audio_duration(self, audio_path):
        media_tools = self._media_tool_paths()
        ffprobe_path = media_tools.get("ffprobe") or shutil.which("ffprobe")
        if not ffprobe_path:
            return None
        result = subprocess.run(
            [ffprobe_path, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(audio_path)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return None
        try:
            return float(result.stdout.strip())
        except (ValueError, TypeError):
            return None

    def _get_content_end(self, audio_path, total_duration):
        """
        Estimate the end of audio content (excluding trailing silence).
        Extracts the last few seconds and detects trailing silence.
        """
        try:
            import tempfile
            probe_duration = 10.0
            probe_clip = Path(tempfile.mktemp(suffix=".wav", prefix="epubsync-probe-"))
            probe_start = max(0, total_duration - probe_duration)
            self._extract_audio_clip(audio_path, probe_clip, probe_start, total_duration - probe_start)
            if not probe_clip.exists():
                return total_duration
            trailing = self._get_trailing_silence_duration(probe_clip, check_seconds=probe_duration)
            try:
                os.unlink(str(probe_clip))
            except OSError:
                pass
            return total_duration - trailing
        except Exception:
            return total_duration

    def _get_trailing_silence_duration(self, audio_path, check_seconds=5.0):
        """Return trailing silence duration at the end of an audio file (0 if none)."""
        import wave
        import numpy as np
        try:
            with wave.open(str(audio_path), 'rb') as w:
                sr = w.getframerate()
                total_frames = w.getnframes()
                frames_to_check = min(int(sr * check_seconds), total_frames)
                w.setpos(total_frames - frames_to_check)
                frames = w.readframes(frames_to_check)
                if w.getsampwidth() == 2:
                    samples = np.frombuffer(frames, dtype=np.int16).astype(np.float64) / 32768.0
                else:
                    samples = np.frombuffer(frames, dtype=np.uint8).astype(np.float64) / 255.0 * 2 - 1
            frame_size = int(sr * 0.1)
            threshold = 0.01
            for i in range(len(samples) - frame_size, -1, -frame_size):
                frame = samples[max(0, i):i + frame_size]
                rms = np.sqrt(np.mean(frame ** 2))
                if rms > threshold:
                    silence_frames = len(samples) - (i + frame_size)
                    return silence_frames / sr
            return check_seconds
        except Exception:
            return 0.0

    def _extract_audio_clip(self, src_path, dst_path, start_sec, duration_sec):
        media_tools = self._media_tool_paths()
        ffmpeg_path = media_tools.get("ffmpeg") or shutil.which("ffmpeg")
        if not ffmpeg_path:
            raise RuntimeError("ffmpeg not found")
        subprocess.run(
            [ffmpeg_path, "-y", "-i", str(src_path), "-ss", str(start_sec), "-t", str(duration_sec), "-ac", "1", "-ar", "16000", str(dst_path)],
            capture_output=True, text=True, timeout=60, check=True,
        )

    def _prepare_chapter_for_find(self, inspection, chapter_href, chapter_config_map):
        chapters_by_href, _ = self._index_spine_items(inspection)
        chapter = chapters_by_href.get(chapter_href)
        if not chapter:
            return [], None
        chapter_path = Path(chapter["full_path"])
        preview_root = Path(tempfile.mkdtemp(prefix="epubsync-find-"))
        preview_path = preview_root / chapter_path.name
        shutil.copyfile(str(chapter_path), str(preview_path))
        css_path = self._write_highlight_css(inspection)
        css_href = os.path.relpath(css_path, str(preview_path.parent)).replace("\\", "/")
        segment_mode = chapter_config_map.get(chapter_href, {}).get("segment", "sentence")
        segments = segment_xhtml_file(preview_path, chapter_number=chapter["index"], segmentation_mode=segment_mode, css_href=css_href)
        footnotes_by_id = self._parse_footnotes(inspection)
        if footnotes_by_id:
            segments = self._inject_footnotes(preview_path, segments, footnotes_by_id)
        all_records = []
        for i, seg in enumerate(segments, start=1):
            text_id = seg.get("id") or seg.get("textId", "seg_%d" % i)
            all_records.append({
                "chapterHref": chapter_href,
                "syncId": self._sync_text_id(chapter_href, text_id),
                "textId": text_id,
                "text": seg["text"],
                "segmentOrder": i,
                "is_footnote": seg.get("is_footnote", False),
            })
        spoken, text_path = self._write_find_text(all_records)
        return spoken, text_path

    def _write_find_text(self, all_records):
        spoken = []
        lines = []
        i = 0
        while i < len(all_records):
            record = all_records[i]
            if record.get("is_footnote"):
                i += 1
                continue
            spoken.append(record)
            text = record["text"]
            i += 1
            while i < len(all_records) and all_records[i].get("is_footnote"):
                text += " " + all_records[i]["text"]
                i += 1
            lines.append(self._sanitize_alignment_text(text))
        if not lines:
            return [], None
        text_path = Path(tempfile.mktemp(suffix=".txt", prefix="epubsync-find-"))
        text_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return spoken, text_path

    def _merge_records_for_alignment(self, records, group_size=4):
        """
        Merge consecutive records into groups of group_size for better DTW alignment.
        Individual segments often have too little text for TTS to produce distinguishable audio.
        Returns (merged_spoken, text_path, expand_map) where expand_map maps group indices
        back to original segment Orders.
        """
        merged = []
        lines = []
        expand_map = []
        for i in range(0, len(records), group_size):
            group = records[i:i + group_size]
            combined = " ".join(r["text"] for r in group)
            merged.append({
                "syncId": group[0]["syncId"],
                "textId": group[0]["textId"],
                "text": combined,
                "segmentOrder": group[0]["segmentOrder"],
            })
            lines.append(self._sanitize_alignment_text(combined))
            expand_map.append([r["segmentOrder"] for r in group])
        if not lines:
            return records, None, []
        text_path = Path(tempfile.mktemp(suffix=".txt", prefix="epubsync-find-merged-"))
        text_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return merged, text_path, expand_map

    def _get_batch_tts_durations(self, texts):
        """Synthesize multiple texts via the fast SAPI helper and return per-text WAV duration in seconds."""
        import io
        import struct
        import wave
        helper_path = Path(__file__).with_name("fast_sapi_tts_helper.py")
        proc = subprocess.Popen(
            [sys.executable, str(helper_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for text in texts:
            proc.stdin.write(text.encode("utf-8"))
            proc.stdin.write(b"\n")
        proc.stdin.close()
        durations = []
        for _ in texts:
            size_bytes = proc.stdout.read(4)
            if len(size_bytes) < 4:
                break
            size = struct.unpack(">I", size_bytes)[0]
            wav_data = proc.stdout.read(size)
            if len(wav_data) < 44:
                durations.append(0.0)
            else:
                with wave.open(io.BytesIO(wav_data), "rb") as w:
                    durations.append(w.getnframes() / float(w.getframerate()))
        proc.stdout.close()
        proc.stderr.read()
        proc.stderr.close()
        proc.wait()
        return durations

    _asr_model = None

    def _transcribe_audio(self, audio_path, clip_start=None, clip_duration=None):
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            self.log("Find sync: faster-whisper not installed; run: pip install faster-whisper")
            return []
        actual_path = str(audio_path)
        cleanup = None
        if clip_start is not None and clip_duration is not None:
            import tempfile, os
            fd, clip_path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            self._extract_audio_clip(audio_path, clip_path, clip_start, clip_duration)
            actual_path = clip_path
            cleanup = lambda: os.unlink(clip_path) if os.path.exists(clip_path) else None
        try:
            if EPUBAudioSyncBuilder._asr_model is None:
                try:
                    EPUBAudioSyncBuilder._asr_model = WhisperModel(
                        "tiny", device="cpu", compute_type="int8", local_files_only=True)
                except Exception:
                    EPUBAudioSyncBuilder._asr_model = WhisperModel(
                        "tiny", device="cpu", compute_type="int8")
            model = EPUBAudioSyncBuilder._asr_model
            segments, _ = model.transcribe(actual_path, language="en")
            return [(seg.start, seg.end, seg.text.strip()) for seg in segments]
        except Exception as exc:
            self.log("Find sync: ASR transcription failed: %s" % exc)
            return []
        finally:
            if cleanup is not None:
                cleanup()

    @staticmethod
    def _normalize_text(text):
        """Lowercase, convert digits to words, remove punctuation, collapse space."""
        text = text.lower()
        text = re.sub(r"\b(\d+)\b", lambda m: _NUM_WORDS.get(m.group(1), m.group(1)), text)
        text = re.sub(r"[^\w\s]", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    def _match_asr_to_segments(self, asr_segments, all_spoken):
        """Match ASR sentences against chapter segments using longest-common-substring."""
        from difflib import SequenceMatcher

        chapter_texts = [self._normalize_text(r["text"]) for r in all_spoken]

        # Concatenate all ASR text and split into sentences
        full_asr = " ".join(t for _, _, t in asr_segments)
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", full_asr) if s.strip()]

        matched = {}  # seg_idx -> best_score (deduplicate)

        for sent in sentences:
            asr_norm = self._normalize_text(sent)
            if len(asr_norm) < 8:
                continue

            best_w1_idx = -1
            best_w1_score = 0.0
            best_w2_idx = -1
            best_w2_score = 0.0

            for w in (1, 2):
                for idx in range(len(chapter_texts) - w + 1):
                    combined = " ".join(chapter_texts[idx:idx + w])
                    sm = SequenceMatcher(None, asr_norm, combined)
                    match = sm.find_longest_match(0, len(asr_norm), 0, len(combined))
                    lcs_len = match.size
                    if lcs_len == 0:
                        continue
                    score = 2.0 * lcs_len / (len(asr_norm) + len(combined))
                    if w == 1 and score > best_w1_score:
                        best_w1_score = score
                        best_w1_idx = idx
                    if w == 2 and score > best_w2_score:
                        best_w2_score = score
                        best_w2_idx = idx

            # Record best 1-window match
            if best_w1_score > 0.3 and best_w1_idx >= 0:
                matched[best_w1_idx] = max(matched.get(best_w1_idx, 0), best_w1_score)
            # Record best 2-window match (captures boundary-straddling sentences)
            # Only record the second index when the 2-window actually beats the
            # best 1-window; otherwise the extra index is likely spurious noise
            # from an adjacent segment that doesn't belong in this audio range.
            if best_w2_score > 0.3 and best_w2_idx >= 0:
                if best_w2_score > best_w1_score:
                    for di in range(2):
                        matched[best_w2_idx + di] = max(
                            matched.get(best_w2_idx + di, 0), best_w2_score)

        self.log("Find sync: ASR matched %d/%d chapter segs from %d sentences" % (
            len(matched), len(all_spoken), len(sentences)))
        if matched:
            self.log("Find sync: matched indices=%s" % json.dumps(sorted(matched)))

        if not matched:
            return 1, len(all_spoken), "low"

        # Cluster matched indices: pick the longest dense run to discard outliers.
        GAP = 8
        sorted_idx = sorted(matched)
        clusters = []
        cur = [sorted_idx[0]]
        for i in range(1, len(sorted_idx)):
            if sorted_idx[i] - sorted_idx[i - 1] <= GAP:
                cur.append(sorted_idx[i])
            else:
                clusters.append(cur)
                cur = [sorted_idx[i]]
        clusters.append(cur)
        main = max(clusters, key=len)
        start_idx = main[0]
        end_idx = main[-1]

        # Gap-fill: if there's an unmatched index just below the cluster start,
        # check if any ASR sentence matches it above threshold. This handles
        # ASR sentences that straddle segment boundaries without being the best
        # match for either segment individually.
        # Use a higher threshold for the start side (0.5) to avoid extending
        # backwards into the previous audio file's territory.
        if start_idx > 0:
            prev_idx = start_idx - 1
            prev_text = chapter_texts[prev_idx]
            for sent in sentences:
                asr_norm = self._normalize_text(sent)
                if len(asr_norm) < 8:
                    continue
                sm = SequenceMatcher(None, asr_norm, prev_text)
                m = sm.find_longest_match(0, len(asr_norm), 0, len(prev_text))
                score = 2.0 * m.size / (len(asr_norm) + len(prev_text))
                if score > 0.3:
                    start_idx = prev_idx
                    break

        # Gap-fill after cluster (safe — no next audio to overlap with)
        if end_idx < len(chapter_texts) - 1:
            next_idx = end_idx + 1
            next_text = chapter_texts[next_idx]
            for sent in sentences:
                asr_norm = self._normalize_text(sent)
                if len(asr_norm) < 8:
                    continue
                sm = SequenceMatcher(None, asr_norm, next_text)
                m = sm.find_longest_match(0, len(asr_norm), 0, len(next_text))
                score = 2.0 * m.size / (len(asr_norm) + len(next_text))
                if score > 0.3:
                    end_idx = next_idx
                    break

        start_seg = all_spoken[start_idx]["segmentOrder"]
        end_seg = all_spoken[end_idx]["segmentOrder"]

        avg_score = sum(matched.values()) / len(matched)
        if len(matched) >= 3 and avg_score >= 0.4:
            confidence = "high"
        elif len(matched) >= 1:
            confidence = "medium"
        else:
            confidence = "low"

        self.log("Find sync: matched range seg[%d..%d] (%d segs matched, avg_score=%.3f)" % (
            start_idx, end_idx, len(matched), avg_score))
        return start_seg, end_seg, confidence

    def find_sync(self, config):
        self._ensure_alignment_prerequisites()
        inspection = config["inspection"]
        chapters_by_href, _ = self._index_spine_items(inspection)
        chapter_configs = self._canonicalize_chapter_configs(config.get("chapters", []), chapters_by_href)
        chapter_config_map = dict((item["chapter"], item) for item in chapter_configs)
        audio_path = Path(config["audio"]).resolve()
        start_chapter_href = config["startChapter"]
        end_chapter_href = config["endChapter"]
        language = config.get("language", "eng")
        if not audio_path.exists():
            return {"startSegment": None, "endSegment": None, "confidence": "low"}
        total_duration = self._get_audio_duration(audio_path)
        if total_duration is None or total_duration < 5:
            return {"startSegment": None, "endSegment": None, "confidence": "low"}
        self.log("Find sync: segmenting start chapter")
        start_spoken, start_text = self._prepare_chapter_for_find(inspection, start_chapter_href, chapter_config_map)
        if not start_spoken:
            return {"startSegment": None, "endSegment": None, "confidence": "low"}

        # When start and end chapters differ, combine both chapter texts.
        # When they're the same (the common case), just use the start chapter.
        if start_chapter_href == end_chapter_href:
            all_spoken = start_spoken
        else:
            self.log("Find sync: segmenting end chapter")
            end_spoken, end_text = self._prepare_chapter_for_find(inspection, end_chapter_href, chapter_config_map)
            if not end_spoken:
                return {"startSegment": None, "endSegment": None, "confidence": "low"}
            all_spoken = start_spoken + end_spoken

        # Transcribe clips at the start and end of the audio to detect boundaries.
        # Full transcription is unnecessary — we only need the first and last few
        # seconds of spoken content to match against chapter segments.
        clip_len = max(20, min(60, total_duration * 0.2))
        early_asr = self._transcribe_audio(audio_path, clip_start=0, clip_duration=clip_len)
        late_asr = self._transcribe_audio(audio_path,
            clip_start=max(0, total_duration - clip_len), clip_duration=clip_len)

        if early_asr:
            for asr_start, asr_end, asr_text in early_asr[:3]:
                self.log("  ASR early [%.2f-%.2f]: %s" % (asr_start, asr_end, asr_text[:120]))
        if late_asr:
            for asr_start, asr_end, asr_text in late_asr[-3:]:
                self.log("  ASR late [%.2f-%.2f]: %s" % (asr_start, asr_end, asr_text[:120]))

        if not early_asr and not late_asr:
            return {"startSegment": None, "endSegment": None, "confidence": "low"}

        confidence = "low"
        start_seg = end_seg = None
        if early_asr:
            start_seg, _, s_conf = self._match_asr_to_segments(early_asr, all_spoken)
            if s_conf == "high":
                confidence = s_conf
        if late_asr:
            _, end_seg, e_conf = self._match_asr_to_segments(late_asr, all_spoken)
            if e_conf == "high":
                confidence = e_conf

        # If either clip failed, fall back to full transcription
        if start_seg is None or end_seg is None:
            self.log("Find sync: falling back to full audio transcription")
            full_asr = self._transcribe_audio(audio_path)
            if full_asr:
                full_start, full_end, full_conf = self._match_asr_to_segments(full_asr, all_spoken)
                if start_seg is None:
                    start_seg = full_start
                if end_seg is None:
                    end_seg = full_end
                confidence = full_conf

        if start_seg is None or end_seg is None:
            return {"startSegment": None, "endSegment": None, "confidence": "low"}
        # Clamp start to avoid overlapping with the previous audio file
        previous_end = config.get("previousEndSegment")
        if previous_end is not None and start_seg is not None:
            try:
                prev_int = int(previous_end)
                clamped = prev_int + 1
            except (ValueError, TypeError):
                clamped = start_seg
            if start_seg < clamped:
                self.log("Find sync: clamping start %d -> %d (previous end=%s)" % (
                    start_seg, clamped, previous_end))
                start_seg = clamped
        self.log("Find sync: result start=%s end=%s confidence=%s" % (start_seg, end_seg, confidence))
        return {"startSegment": start_seg, "endSegment": end_seg, "confidence": confidence}
