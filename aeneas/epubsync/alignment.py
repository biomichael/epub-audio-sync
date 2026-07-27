#!/usr/bin/env python
# coding=utf-8

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from aeneas.executetask import ExecuteTask
from aeneas.runtimeconfiguration import RuntimeConfiguration
from aeneas.syncmap.fragment import SyncMapFragment
from aeneas.task import Task


@dataclass(frozen=True)
class AlignmentTiming:
    sync_id: str
    text_id: str
    begin: float
    end: float


@dataclass(frozen=True)
class AlignmentResult:
    engine_name: str
    audio_duration: float
    segments: list[AlignmentTiming]


class AlignmentEngine(ABC):
    """Common alignment interface used by the EPUB sync pipeline."""

    @abstractmethod
    def align(self, audio_path, text_path, language, segment_records):
        raise NotImplementedError


class AeneasEngine(AlignmentEngine):
    def __init__(self, logger=None, ffmpeg_path=None, ffprobe_path=None, tts_wrapper_path=None):
        self.logger = logger
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path
        self.tts_wrapper_path = tts_wrapper_path

    def align(self, audio_path, text_path, language, segment_records):
        config_string = "task_language=%s|is_text_type=plain|os_task_file_format=json" % language
        spoken_records = [record for record in segment_records if not record.get("is_footnote")]
        rconf = None
        tts_wrapper_path = Path(self.tts_wrapper_path) if self.tts_wrapper_path else Path(__file__).with_name("windows_sapi_tts.py")
        if tts_wrapper_path.exists() or self.ffmpeg_path or self.ffprobe_path:
            rconf = RuntimeConfiguration()
        if self.ffmpeg_path:
            rconf[RuntimeConfiguration.FFMPEG_PATH] = str(self.ffmpeg_path)
        if self.ffprobe_path:
            rconf[RuntimeConfiguration.FFPROBE_PATH] = str(self.ffprobe_path)
        if tts_wrapper_path.exists():
            rconf[RuntimeConfiguration.TTS] = "custom"
            rconf[RuntimeConfiguration.TTS_PATH] = str(tts_wrapper_path)
        task = Task(config_string=config_string, rconf=rconf)
        task.audio_file_path_absolute = str(audio_path)
        task.text_file_path_absolute = str(text_path)
        executor = ExecuteTask(task, rconf=rconf)
        executor.execute()

        regular_fragments = [fragment for fragment in task.sync_map_leaves(SyncMapFragment.REGULAR)]
        if len(regular_fragments) != len(spoken_records):
            raise ValueError(
                "Aeneas returned %d fragments but %d text segments were expected"
                % (len(regular_fragments), len(spoken_records))
            )

        return AlignmentResult(
            engine_name="aeneas",
            audio_duration=round(float(task.audio_file.audio_length), 3),
            segments=[
                AlignmentTiming(
                    sync_id=record["syncId"],
                    text_id=record["textId"],
                    begin=round(float(fragment.begin), 3),
                    end=round(float(fragment.end), 3),
                )
                for record, fragment in zip(spoken_records, regular_fragments)
            ],
        )


class MfaEngine(AlignmentEngine):
    def align(self, audio_path, text_path, language, segment_records):
        raise NotImplementedError("MFA alignment engine is not implemented yet")


def build_alignment_engine(engine_name="aeneas", logger=None, ffmpeg_path=None, ffprobe_path=None, tts_wrapper_path=None):
    normalized_name = (engine_name or "aeneas").strip().lower()
    if normalized_name == "aeneas":
        return AeneasEngine(logger=logger, ffmpeg_path=ffmpeg_path, ffprobe_path=ffprobe_path, tts_wrapper_path=tts_wrapper_path)
    if normalized_name == "mfa":
        return MfaEngine()
    raise ValueError("Unknown alignment engine: %s" % engine_name)
