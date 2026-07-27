from __future__ import absolute_import
from __future__ import print_function

import io
import struct
import subprocess
import sys

import numpy
import wave

from aeneas.audiofile import AudioFile
from aeneas.exacttiming import TimeValue
from aeneas.language import Language
from aeneas.runtimeconfiguration import RuntimeConfiguration
from aeneas.ttswrappers.basettswrapper import BaseTTSWrapper
import aeneas.globalfunctions as gf


class FastSapiTTSWrapper(BaseTTSWrapper):
    LANGUAGE_TO_VOICE_CODE = dict((language, language) for language in Language.ALLOWED_VALUES)
    DEFAULT_LANGUAGE = Language.ENG
    OUTPUT_AUDIO_FORMAT = ("pcm_s16le", 1, 16000)
    HAS_SUBPROCESS_CALL = True
    TAG = u"FastSapiTTSWrapper"

    def __init__(self, rconf=None, logger=None):
        super(FastSapiTTSWrapper, self).__init__(rconf=rconf, logger=logger)
        helper_script = gf.relative_path("fast_sapi_tts_helper.py", __file__)
        self.set_subprocess_arguments([
            sys.executable or "python",
            helper_script,
        ])

    def _read_audio_data(self, file_path):
        try:
            with wave.open(file_path, "rb") as handle:
                sample_rate = handle.getframerate()
                channels = handle.getnchannels()
                sample_width = handle.getsampwidth()
                frames = handle.readframes(handle.getnframes())
            if channels != 1 or sample_width != 2:
                raise ValueError("Fast SAPI helper produced unexpected WAV format")
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
            proc = subprocess.Popen(
                self.subprocess_arguments,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            proc.stdin.write(text.encode("utf-8"))
            proc.stdin.write(b"\n")
            proc.stdin.close()
            size_bytes = proc.stdout.read(4)
            if len(size_bytes) < 4:
                raise RuntimeError("Fast SAPI helper did not return data")
            size = struct.unpack(">I", size_bytes)[0]
            wav_data = proc.stdout.read(size)
            proc.stdout.close()
            proc.stderr.read()
            proc.stderr.close()
            proc.wait()
            with open(output_file_path, "wb") as f:
                f.write(wav_data)
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

    def _synthesize_multiple_subprocess(self, text_file, output_file_path, quit_after=None, backwards=False):
        self.log(u"Synthesizing multiple via batched subprocess...")
        fragments = text_file.fragments
        if backwards:
            fragments = fragments[::-1]

        codec, channels, sample_rate = self.OUTPUT_AUDIO_FORMAT
        helper_script = gf.relative_path("fast_sapi_tts_helper.py", __file__)

        proc = subprocess.Popen(
            [sys.executable or "python", helper_script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        for fragment in fragments:
            if fragment.filtered_text:
                proc.stdin.write(fragment.filtered_text.encode("utf-8"))
                proc.stdin.write(b"\n")
        proc.stdin.close()

        output_file = AudioFile(rconf=self.rconf, logger=self.logger)
        output_file.audio_format = codec
        output_file.audio_channels = 1
        output_file.audio_sample_rate = sample_rate

        anchors = []
        current_time = TimeValue("0.000")
        num_chars = 0

        for num, fragment in enumerate(fragments):
            anchors.append([current_time, fragment.identifier, fragment.text])
            num_chars += fragment.characters

            if fragment.filtered_text:
                size_bytes = proc.stdout.read(4)
                if len(size_bytes) < 4:
                    self.log(u"Unexpected end of data from helper after %d fragments" % num)
                    break
                size = struct.unpack(">I", size_bytes)[0]
                wav_data = proc.stdout.read(size)

                with wave.open(io.BytesIO(wav_data), "rb") as w:
                    frames = w.readframes(w.getnframes())
                    sr = w.getframerate()
                    frame_count = w.getnframes()
                    duration = TimeValue("%.3f" % (float(frame_count) / float(sr)))

                if duration > 0:
                    current_time += duration
                    samples = numpy.frombuffer(frames, dtype="<i2").astype("float64") / 32768.0
                    output_file.add_samples(samples, reverse=backwards)

            if (quit_after is not None) and (current_time > quit_after):
                self.log(u"Quitting after reached duration %.3f" % current_time)
                break

        proc.stdout.close()
        proc.stderr.read()
        proc.stderr.close()
        proc.wait()

        output_file.minimize_memory()
        if backwards:
            output_file.reverse()
        output_file.write(file_path=output_file_path)

        self.log(u"Synthesizing multiple via batched subprocess... done")
        return (True, (anchors, current_time, num_chars))


# aeneas loads custom TTS by looking for a class named CustomTTSWrapper
CustomTTSWrapper = FastSapiTTSWrapper
