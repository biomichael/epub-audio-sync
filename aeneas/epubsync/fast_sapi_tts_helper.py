from __future__ import absolute_import
from __future__ import print_function

import os
import struct
import sys
import tempfile
import wave

import win32com.client


def main():
    speaker = win32com.client.Dispatch("SAPI.SpVoice")
    speaker.Rate = -1  # slightly slower for better articulation
    # Try to use Microsoft Zira (female) if available — often more natural than David
    try:
        voices = speaker.GetVoices()
        for i in range(voices.Count):
            desc = voices.Item(i).GetDescription()
            if "Zira" in desc:
                speaker.Voice = voices.Item(i)
                break
    except Exception:
        pass
    stream = win32com.client.Dispatch("SAPI.SpFileStream")
    fmt = win32com.client.Dispatch("SAPI.SpAudioFormat")
    fmt.Type = 18  # SAFT16kHz16BitMono (not 4, which is SAFT8kHz8BitMono)

    for raw_line in sys.stdin:
        line = raw_line.rstrip("\r\n")
        if not line:
            # Return empty WAV for empty text
            sys.stdout.buffer.write(struct.pack(">I", 44))
            sys.stdout.buffer.write(b"\x00" * 44)
            sys.stdout.buffer.flush()
            continue
        tmp = tempfile.mktemp(suffix=".wav")
        try:
            stream.Format = fmt
            stream.Open(tmp, 3)  # SSFMCreateForWrite
            speaker.AudioOutputStream = stream
            speaker.Speak(line)
            speaker.WaitUntilDone(10000)
            stream.Close()
            with open(tmp, "rb") as f:
                data = f.read()
            sys.stdout.buffer.write(struct.pack(">I", len(data)))
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass


if __name__ == "__main__":
    main()
