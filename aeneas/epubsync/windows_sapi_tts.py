#!/usr/bin/env python
# coding=utf-8

from __future__ import absolute_import
from __future__ import print_function

import io
import numpy
import subprocess
import wave

from aeneas.exacttiming import TimeValue
from aeneas.language import Language
from aeneas.ttswrappers.basettswrapper import BaseTTSWrapper
from aeneas.runtimeconfiguration import RuntimeConfiguration
import aeneas.globalfunctions as gf


class CustomTTSWrapper(BaseTTSWrapper):
    LANGUAGE_TO_VOICE_CODE = dict((language, language) for language in Language.ALLOWED_VALUES)
    DEFAULT_LANGUAGE = Language.ENG
    OUTPUT_AUDIO_FORMAT = ("pcm_s16le", 1, 16000)
    HAS_SUBPROCESS_CALL = True
    TAG = u"CustomTTSWrapperWindowsSAPI"

    def __init__(self, rconf=None, logger=None):
        super(CustomTTSWrapper, self).__init__(rconf=rconf, logger=logger)
        helper_script = gf.relative_path("windows_sapi_tts_helper.ps1", __file__)
        self.set_subprocess_arguments([
            u"powershell",
            u"-NoProfile",
            u"-ExecutionPolicy",
            u"Bypass",
            u"-File",
            helper_script,
            self.CLI_PARAMETER_VOICE_CODE_STRING,
            self.CLI_PARAMETER_WAVE_PATH,
            self.CLI_PARAMETER_TEXT_PATH,
        ])

    def _read_audio_data(self, file_path):
        try:
            with wave.open(file_path, "rb") as handle:
                sample_rate = handle.getframerate()
                channels = handle.getnchannels()
                sample_width = handle.getsampwidth()
                frames = handle.readframes(handle.getnframes())
            if channels != 1 or sample_width != 2:
                raise ValueError("Windows SAPI helper produced unexpected WAV format")
            samples = numpy.frombuffer(frames, dtype="<i2").astype("float64") / 32768.0
            duration = TimeValue("%.3f" % (len(samples) / float(sample_rate)))
            return (True, (duration, sample_rate, "pcm16", samples))
        except Exception as exc:
            self.log_exc(u"An unexpected error occurred while reading audio data", exc, True, None)
            return (False, None)

    def _synthesize_single_subprocess_helper(self, text, voice_code, output_file_path=None, return_audio_data=True):
        if len(text) == 0:
            return (True, (TimeValue("0.000"), None, None, None))

        synt_tmp_file = (output_file_path is None)
        if synt_tmp_file:
            output_file_handler, output_file_path = gf.tmp_file(
                suffix=u".wav",
                root=self.rconf[RuntimeConfiguration.TMP_PATH]
            )
            gf.close_file_handler(output_file_handler)

        try:
            if self.CLI_PARAMETER_TEXT_PATH in self.subprocess_arguments:
                tmp_text_file_handler, tmp_text_file_path = gf.tmp_file(
                    suffix=u".txt",
                    root=self.rconf[RuntimeConfiguration.TMP_PATH]
                )
                gf.close_file_handler(tmp_text_file_handler)
                with io.open(tmp_text_file_path, "w", encoding="utf-8") as tmp_text_file:
                    tmp_text_file.write(text)
            else:
                tmp_text_file_handler = None
                tmp_text_file_path = None

            arguments = []
            for arg in self.subprocess_arguments:
                if arg == self.CLI_PARAMETER_VOICE_CODE_FUNCTION:
                    arguments.extend(self._voice_code_to_subprocess(voice_code))
                elif arg == self.CLI_PARAMETER_VOICE_CODE_STRING:
                    arguments.append(voice_code)
                elif arg == self.CLI_PARAMETER_TEXT_PATH:
                    arguments.append(tmp_text_file_path)
                elif arg == self.CLI_PARAMETER_WAVE_PATH:
                    arguments.append(output_file_path)
                elif arg in [self.CLI_PARAMETER_TEXT_STDIN, self.CLI_PARAMETER_WAVE_STDOUT]:
                    pass
                else:
                    arguments.append(arg)

            proc = subprocess.Popen(
                arguments,
                stdout=subprocess.PIPE,
                stdin=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True
            )
            if self.CLI_PARAMETER_TEXT_STDIN in self.subprocess_arguments:
                proc.communicate(input=text)
            else:
                proc.communicate()
            proc.stdout.close()
            proc.stdin.close()
            proc.stderr.close()
            if tmp_text_file_path is not None:
                gf.delete_file(None, tmp_text_file_path)
        except Exception as exc:
            self.log_exc(u"An unexpected error occurred while calling TTS engine via subprocess", exc, False, None)
            return (False, None)

        if not gf.file_can_be_read(output_file_path):
            self.log_exc(u"Output file '%s' cannot be read" % (output_file_path), None, True, None)
            return (False, None)

        ret = self._read_audio_data(output_file_path) if return_audio_data else (True, None)
        if synt_tmp_file:
            gf.delete_file(None, output_file_path)
        return ret
