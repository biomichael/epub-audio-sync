#!/usr/bin/env python
# coding=utf-8

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from lxml import etree

from aeneas.epubsync.alignment import AeneasEngine
from aeneas.epubsync.alignment import AlignmentTiming
from aeneas.epubsync.alignment import AlignmentResult
from aeneas.epubsync.alignment import MfaEngine
from aeneas.epubsync.alignment import build_alignment_engine
from aeneas.epubsync.epub import repack_epub
from aeneas.epubsync.epub import unzip_epub
from aeneas.epubsync.mfa_textgrid import parse_mfa_textgrid_words
from aeneas.epubsync.pipeline import EPUBAudioSyncBuilder
from aeneas.ffprobewrapper import FFPROBEWrapper
from aeneas.runtimeconfiguration import RuntimeConfiguration
from aeneas.epubsync.xhtml import SEGMENT_ATTR
from aeneas.epubsync.xhtml import segment_xhtml_file


CONTAINER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

OPF_XML = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">test-book</dc:identifier>
    <dc:title>Test Book</dc:title>
    <dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="chap1" href="Text/chapter1.xhtml" media-type="application/xhtml+xml"/>
    <item id="chap2" href="Text/chapter2.xhtml" media-type="application/xhtml+xml"/>
    <item id="maincss" href="styles/main.css" media-type="text/css"/>
  </manifest>
  <spine>
    <itemref idref="chap1"/>
    <itemref idref="chap2"/>
  </spine>
</package>
"""

XHTML_XML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head>
    <title>Chapter 1</title>
  </head>
  <body>
    <section>
      <p>First sentence. Second sentence.</p>
    </section>
  </body>
</html>
"""

XHTML_COMPLEX_XML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head>
    <title>Chapter 2</title>
  </head>
  <body>
    <section>
      <p>Alpha <a href="#note">villain<span> </span>of the piece</a>. Next sentence.</p>
      <p>“through the mind” <br class="visible"/>or “through the soul” <br class="visible"/>(from Greek <span class="DefinitionItalic">dia,</span> through, <br class="visible"/>and <span class="DefinitionItalic">nous,</span> mind or soul). <br class="visible"/>It combines a workable <br class="visible"/>technique and a thoroughly <br class="visible"/>validated method.</p>
    </section>
  </body>
</html>
"""

TEXTGRID_WITH_WORD_TIER = """File type = "ooTextFile"
Object class = "TextGrid"

xmin = 0
xmax = 1.00
tiers? <exists>
size = 2
item []:
    item [1]:
        class = "IntervalTier"
        name = "phones"
        xmin = 0
        xmax = 1.00
        intervals: size = 2
        intervals [1]:
            xmin = 0
            xmax = 0.50
            text = "AA"
        intervals [2]:
            xmin = 0.50
            xmax = 1.00
            text = ""
    item [2]:
        class = "IntervalTier"
        name = "words"
        xmin = 0
        xmax = 1.00
        intervals: size = 3
        intervals [1]:
            xmin = 0
            xmax = 0.10
            text = ""
        intervals [2]:
            xmin = 0.12
            xmax = 0.48
            text = "example"
        intervals [3]:
            xmin = 0.48
            xmax = 1.00
            text = "sil"
"""

FAKE_MFA_WORDS = [
    {"word": "chapter", "begin": 10.00, "end": 10.40},
    {"word": "one", "begin": 10.40, "end": 10.80},
    {"word": "second", "begin": 11.20, "end": 11.60},
    {"word": "sentence", "begin": 11.60, "end": 12.10},
]

TEXTGRID_WITH_FOUR_WORDS = """File type = "ooTextFile"
Object class = "TextGrid"

xmin = 0
xmax = 1.00
tiers? <exists>
size = 1
item []:
    item [1]:
        class = "IntervalTier"
        name = "words"
        xmin = 0
        xmax = 1.00
        intervals: size = 4
        intervals [1]:
            xmin = 0.00
            xmax = 0.20
            text = "First"
        intervals [2]:
            xmin = 0.20
            xmax = 0.40
            text = "sentence."
        intervals [3]:
            xmin = 0.40
            xmax = 0.60
            text = "Second"
        intervals [4]:
            xmin = 0.60
            xmax = 1.00
            text = "sentence."
"""

FOOTNOTE_CHAPTER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head>
    <title>Footnotes</title>
  </head>
  <body>
    <section>
      <p>Sentence with one footnote<a epub:type="noteref" href="footnotes.xhtml#footnote-001">1</a>. Next sentence.</p>
      <p>Sentence with two footnotes<a epub:type="noteref" href="footnotes.xhtml#footnote-002">2</a><a epub:type="noteref" href="footnotes.xhtml#footnote-003">3</a>.</p>
    </section>
  </body>
</html>
"""

FOOTNOTE_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <head>
    <title>Footnotes</title>
  </head>
  <body>
    <aside id="footnote-001" epub:type="footnote"><p>First footnote body.</p></aside>
    <aside id="footnote-002" epub:type="footnote"><p>Second footnote body.</p></aside>
    <aside id="footnote-003" epub:type="footnote"><p>Third footnote body.</p></aside>
  </body>
</html>
"""


class TestEPUBSync(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="epubsync-test-"))
        self.epub_path = self.tmpdir / "book.epub"
        with zipfile.ZipFile(str(self.epub_path), "w") as archive:
            archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
            archive.writestr("META-INF/container.xml", CONTAINER_XML)
            archive.writestr("OEBPS/content.opf", OPF_XML)
            archive.writestr("OEBPS/Text/chapter1.xhtml", XHTML_XML)
            archive.writestr("OEBPS/Text/chapter2.xhtml", XHTML_COMPLEX_XML)
            archive.writestr("OEBPS/styles/main.css", "body {}")

    def tearDown(self):
        shutil.rmtree(str(self.tmpdir))

    def test_inspect_and_repack(self):
        inspection = unzip_epub(self.epub_path, self.tmpdir / "unpacked")
        self.assertEqual("OEBPS/content.opf", inspection["opf_rel_path"])
        self.assertEqual(2, len(inspection["spine_items"]))
        repacked = self.tmpdir / "roundtrip.epub"
        repack_epub(inspection["epub_root"], repacked)
        self.assertTrue(repacked.exists())
        with zipfile.ZipFile(str(repacked), "r") as archive:
            self.assertEqual("mimetype", archive.namelist()[0])
            self.assertIn("OEBPS/Text/chapter2.xhtml", archive.namelist())

    def test_alignment_engine_factory(self):
        self.assertIsInstance(build_alignment_engine("aeneas"), AeneasEngine)
        self.assertIsInstance(build_alignment_engine("mfa"), MfaEngine)
        self.assertIsInstance(build_alignment_engine(None), AeneasEngine)
        with self.assertRaises(NotImplementedError):
            build_alignment_engine("mfa").align(None, None, None, [])

    def test_mfa_settings_are_normalized_and_validated(self):
        builder = EPUBAudioSyncBuilder()
        mfa_exe = self.tmpdir / "montreal-forced-aligner.exe"
        mfa_exe.write_text("stub", encoding="utf-8")
        mfa_dictionary = self.tmpdir / "dictionary.txt"
        mfa_dictionary.write_text("stub", encoding="utf-8")
        settings = builder._normalize_mfa_settings(
            {
                "out": str(self.tmpdir / "build"),
                "mfa": {
                    "executable_path": str(mfa_exe),
                    "acoustic_model": "english_us_arpa",
                    "dictionary": str(mfa_dictionary),
                    "output_folder": str(self.tmpdir / "mfa-output"),
                    "corpus_dir": str(self.tmpdir / "mfa-corpus"),
                    "audio_conversion": "wav",
                },
            }
        )
        self.assertEqual(str(mfa_exe.resolve()), settings["executable_path"])
        self.assertEqual("english_us_arpa", settings["acoustic_model"])
        self.assertEqual("path", settings["dictionary"]["kind"])
        self.assertEqual(str(mfa_dictionary.resolve()), settings["dictionary"]["value"])
        self.assertEqual(str((self.tmpdir / "mfa-output").resolve()), settings["output_folder"])
        self.assertEqual(str((self.tmpdir / "mfa-corpus").resolve()), settings["corpus_dir"])
        self.assertEqual("wav", settings["audio_conversion"])

    def test_textgrid_parser_extracts_word_tier_only(self):
        textgrid_path = self.tmpdir / "fixture.TextGrid"
        textgrid_path.write_text(TEXTGRID_WITH_WORD_TIER, encoding="utf-8")
        words = parse_mfa_textgrid_words(textgrid_path)
        self.assertEqual(1, len(words))
        self.assertEqual("example", words[0]["word"])
        self.assertAlmostEqual(0.12, words[0]["begin"])
        self.assertAlmostEqual(0.48, words[0]["end"])

    def test_textgrid_parser_errors_when_word_tier_missing(self):
        textgrid_path = self.tmpdir / "missing_words.TextGrid"
        textgrid_path.write_text(TEXTGRID_WITH_WORD_TIER.replace('name = "words"', 'name = "phones"'), encoding="utf-8")
        with self.assertRaises(ValueError):
            parse_mfa_textgrid_words(textgrid_path)

    @mock.patch("aeneas.ffprobewrapper.subprocess.Popen")
    def test_ffprobe_wrapper_tolerates_non_utf8_stderr(self, popen_mock):
        audio_path = self.tmpdir / "sample.wav"
        audio_path.write_bytes(b"RIFF")
        process = mock.Mock()
        process.communicate.return_value = (
            b"[STREAM]\ncodec_name=pcm_s16le\nsample_rate=16000\nchannels=1\nduration=1.250\n[/STREAM]\n",
            "Copyright \xa9".encode("cp1252"),
        )
        process.stdout.close.return_value = None
        process.stdin.close.return_value = None
        process.stderr.close.return_value = None
        popen_mock.return_value = process
        rconf = RuntimeConfiguration()
        rconf[RuntimeConfiguration.FFPROBE_PATH] = "ffprobe"
        results = FFPROBEWrapper(rconf=rconf).read_properties(str(audio_path))
        self.assertEqual("pcm_s16le", results["codec_name"])
        self.assertEqual("16000", results["sample_rate"])
        self.assertEqual("1", results["channels"])

    def test_mfa_word_mapping_groups_words_by_segment(self):
        builder = EPUBAudioSyncBuilder()
        segment_records = [
            {"textId": "c01-s0001", "text": "chapter one", "audioSourceId": "audio001"},
            {"textId": "c01-s0002", "text": "second sentence", "audioSourceId": "audio001"},
        ]
        segments, warnings = builder._map_mfa_words_to_segments("assignment-01", segment_records, FAKE_MFA_WORDS)
        self.assertEqual([], warnings)
        self.assertEqual(2, len(segments))
        self.assertEqual("c01-s0001", segments[0]["textId"])
        self.assertEqual(10.0, segments[0]["begin"])
        self.assertEqual(10.8, segments[0]["end"])
        self.assertEqual("audio001", segments[0]["audioSourceId"])
        self.assertEqual([[0, 0.0, 0.4, "chapter"], [1, 0.4, 0.8, "one"]], segments[0]["words"])
        self.assertEqual("c01-s0002", segments[1]["textId"])
        self.assertEqual(11.2, segments[1]["begin"])
        self.assertEqual(12.1, segments[1]["end"])
        self.assertEqual([[0, 0.0, 0.4, "second"], [1, 0.4, 0.9, "sentence"]], segments[1]["words"])

    @mock.patch("aeneas.epubsync.alignment.ExecuteTask")
    @mock.patch("aeneas.epubsync.alignment.Task")
    def test_aeneas_engine_ignores_footnotes_for_fragment_count(self, task_mock, execute_task_mock):
        task_instance = mock.Mock()
        fragment = mock.Mock(begin=1.0, end=2.0)
        task_instance.sync_map_leaves.return_value = [fragment]
        task_instance.audio_file.audio_length = 2.0
        task_mock.return_value = task_instance
        execute_task_mock.return_value.execute.return_value = None
        engine = AeneasEngine()
        result = engine.align(
            audio_path="audio.wav",
            text_path="segments.txt",
            language="eng",
            segment_records=[
                {"syncId": "s1", "textId": "c01-s0001", "text": "spoken sentence"},
                {"syncId": "s2", "textId": "c01-s0001-fnref1", "text": "footnote", "is_footnote": True},
            ],
        )
        self.assertEqual("aeneas", result.engine_name)
        self.assertEqual(1, len(result.segments))
        self.assertEqual("c01-s0001", result.segments[0].text_id)

    @mock.patch("aeneas.epubsync.pipeline.subprocess.run")
    def test_mfa_proof_mode_uses_executable(self, run_mock):
        run_mock.return_value = mock.Mock(returncode=0, stdout="MFA help", stderr="")
        builder = EPUBAudioSyncBuilder()
        proof = builder._run_mfa_proof({"executable_path": "C:/fake/mfa.exe"})
        self.assertTrue(proof["success"])
        self.assertEqual(["C:/fake/mfa.exe", "--help"], proof["command"])
        run_mock.assert_called_once()

    @mock.patch("aeneas.epubsync.pipeline.subprocess.run")
    def test_mfa_job_path_creates_textgrid_target(self, run_mock):
        def side_effect(command, capture_output=True, text=True, **kwargs):
            if len(command) > 2 and command[1] == "align":
                output_dir = Path(command[-1])
                output_dir.mkdir(parents=True, exist_ok=True)
                textgrid_path = output_dir / "assignment-01.TextGrid"
                textgrid_path.write_text(TEXTGRID_WITH_FOUR_WORDS, encoding="utf-8")
                return mock.Mock(returncode=0, stdout="align ok", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        run_mock.side_effect = side_effect
        builder = EPUBAudioSyncBuilder()
        mfa_exe = self.tmpdir / "mfa.exe"
        mfa_exe.write_text("stub", encoding="utf-8")
        audio_path = self.tmpdir / "chapter01.wav"
        audio_path.write_bytes(b"RIFF")
        result = builder.run(
            {
                "epub": str(self.epub_path),
                "audio_files": [str(audio_path)],
                "mappings": [
                    {
                        "chapter": "OEBPS/Text/chapter1.xhtml",
                        "audio": str(audio_path),
                        "segment": "sentence",
                    }
                ],
                "language": "eng",
                "segment": "sentence",
                "external_audio": True,
                "out": str(self.tmpdir / "out"),
                "alignment_engine": "mfa",
                "mfa": {
                    "executable_path": str(mfa_exe),
                    "acoustic_model": "english_us_arpa",
                    "dictionary": "english_us_arpa",
                    "output_folder": str(self.tmpdir / "mfa-output"),
                    "corpus_dir": str(self.tmpdir / "mfa-corpus"),
                    "audio_conversion": "none",
                },
            }
        )
        self.assertIn("mfa_jobs", result)
        self.assertEqual(1, len(result["mfa_jobs"]))
        job = result["mfa_jobs"][0]
        self.assertTrue(job["textgridPath"].endswith("assignment-01.TextGrid"))
        self.assertEqual(4, len(job["words"]))
        self.assertEqual(2, len(job["segments"]))
        self.assertEqual("c01-s0001", job["segments"][0]["textId"])
        self.assertEqual("audio001", job["segments"][0]["audioSourceId"])
        self.assertEqual(2, len(job["segments"][0]["words"]))
        self.assertEqual("c01-s0002", job["segments"][1]["textId"])
        self.assertEqual(2, len(job["segments"][1]["words"]))
        self.assertIsNone(job["error"])
        self.assertEqual([], job["warnings"])
        self.assertTrue(job["success"])
        self.assertTrue(run_mock.called)
        self.assertEqual("align", run_mock.call_args[0][0][1])

    def test_assignment_slicing_sentence_segmentation_and_opf_patch(self):
        inspection = unzip_epub(self.epub_path, self.tmpdir / "unpacked2")
        chapter_path = Path(inspection["spine_items"][0]["full_path"])
        css_path = Path(inspection["package_dir"]) / "styles" / "audio-sync.css"
        css_path.parent.mkdir(parents=True, exist_ok=True)
        css_path.write_text(".audio-active{}", encoding="utf-8")
        segments = segment_xhtml_file(
            chapter_path,
            chapter_number=1,
            segmentation_mode="sentence",
            css_href="../styles/audio-sync.css",
        )
        self.assertEqual(2, len(segments))
        self.assertTrue(all(segment["id"].startswith("c01-s") for segment in segments))
        builder = EPUBAudioSyncBuilder()
        chapter_segments = {
            inspection["spine_items"][0]["root_href"]: {
                "chapter": inspection["spine_items"][0],
                "chapterHref": inspection["spine_items"][0]["root_href"],
                "chapterPath": str(chapter_path),
                "segmentMode": "sentence",
                "segments": [
                    {"syncId": "chapter1__%s" % segments[0]["id"], "textId": segments[0]["id"], "text": segments[0]["text"], "segmentOrder": 1},
                    {"syncId": "chapter1__%s" % segments[1]["id"], "textId": segments[1]["id"], "text": segments[1]["text"], "segmentOrder": 2},
                ],
            }
        }
        resolved = builder._resolve_assignment_segments(
            {
                "id": "assignment-01",
                "audio": str(self.epub_path),
                "startChapter": inspection["spine_items"][0]["root_href"],
                "endChapter": inspection["spine_items"][0]["root_href"],
                "startSegment": 2,
                "endSegment": 2,
            },
            chapter_segments,
            inspection["spine_items"],
        )
        self.assertEqual(1, len(resolved))
        self.assertEqual(segments[1]["id"], resolved[0]["textId"])
        smil_dir = self.tmpdir / "smil"
        smil_dir.mkdir()
        audio_registry = {
            str(self.epub_path.resolve()): {
                "id": "audio001",
                "path": str(self.epub_path.resolve()),
                "external": True,
                "embeddedHref": None,
                "src": self.epub_path.name,
                "duration": 2.0,
            }
        }
        chapter_export = {
            "chapter": inspection["spine_items"][0],
            "chapterHref": inspection["spine_items"][0]["root_href"],
            "chapterPath": str(chapter_path),
            "audioSourceIds": {"audio001"},
            "audioSources": [builder._public_audio_source(next(iter(audio_registry.values())))],
            "segments": [
                {"textId": segments[0]["id"], "begin": 0.0, "end": 1.0, "text": segments[0]["text"], "audioSourceId": "audio001", "segmentOrder": 1},
                {"textId": segments[1]["id"], "begin": 1.0, "end": 2.0, "text": segments[1]["text"], "audioSourceId": "audio001", "segmentOrder": 2},
            ],
        }
        chapter_json = builder._build_chapter_json(chapter_export)
        self.assertEqual("Chapter 1", chapter_json["chapterTitle"])
        self.assertEqual(1, len(chapter_json["audioSources"]))
        self.assertEqual("audio001", chapter_json["segments"][0]["audioSourceId"])
        self.assertNotIn("text", chapter_json["segments"][0])
        smil_href = builder._write_chapter_smil(
            inspection=inspection,
            chapter_export=chapter_export,
            audio_registry=audio_registry,
            embedded_audio=False,
            smil_dir=smil_dir,
        )
        builder._patch_opf(
            inspection=inspection,
            manifest_updates=[
                {
                    "chapter": inspection["spine_items"][0],
                    "smil_href": smil_href,
                    "json_href": "MediaOverlays/chapter1.json",
                    "duration": 2.0,
                }
            ],
            embed_audio=False,
            audio_registry=audio_registry,
        )
        opf_text = Path(inspection["opf_path"]).read_text(encoding="utf-8")
        self.assertIn('media-overlay="chap1-mo"', opf_text)
        self.assertIn("audio-active", opf_text)
        self.assertIn('href="MediaOverlays/chapter1.json"', opf_text)
        self.assertIn('href="styles/audio-sync.css"', opf_text)

    @mock.patch("aeneas.epubsync.pipeline.subprocess.run")
    def test_mfa_json_output_shape(self, run_mock):
        def side_effect(command, capture_output=True, text=True, **kwargs):
            if len(command) > 2 and command[1] == "align":
                output_dir = Path(command[-1])
                output_dir.mkdir(parents=True, exist_ok=True)
                textgrid_path = output_dir / "assignment-01.TextGrid"
                textgrid_path.write_text(TEXTGRID_WITH_FOUR_WORDS, encoding="utf-8")
                return mock.Mock(returncode=0, stdout="align ok", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        run_mock.side_effect = side_effect
        builder = EPUBAudioSyncBuilder()
        mfa_exe = self.tmpdir / "mfa.exe"
        mfa_exe.write_text("stub", encoding="utf-8")
        audio_path = self.tmpdir / "chapter01.wav"
        audio_path.write_bytes(b"RIFF")
        result = builder.run(
            {
                "epub": str(self.epub_path),
                "audio_files": [str(audio_path)],
                "mappings": [
                    {
                        "chapter": "OEBPS/Text/chapter1.xhtml",
                        "audio": str(audio_path),
                        "segment": "sentence",
                    }
                ],
                "language": "eng",
                "segment": "sentence",
                "external_audio": True,
                "out": str(self.tmpdir / "out"),
                "alignment_engine": "mfa",
                "mfa": {
                    "executable_path": str(mfa_exe),
                    "acoustic_model": "english_us_arpa",
                    "dictionary": "english_us_arpa",
                    "output_folder": str(self.tmpdir / "mfa-output"),
                    "corpus_dir": str(self.tmpdir / "mfa-corpus"),
                    "audio_conversion": "none",
                },
            }
        )
        self.assertIn("mfa_jobs", result)
        self.assertIn("json_dir", result)
        json_dir = Path(result["json_dir"])
        self.assertTrue(json_dir.exists())
        json_path = json_dir / "chapter1.json"
        self.assertTrue(json_path.exists(), "MFA JSON file was not created at %s" % json_path)
        with json_path.open(encoding="utf-8") as f:
            chapter_json = json.load(f)

        self.assertEqual("OEBPS/Text/chapter1.xhtml", chapter_json["chapterHref"])
        self.assertEqual("Chapter 1", chapter_json["chapterTitle"])
        self.assertIn("audioSources", chapter_json)
        self.assertIsInstance(chapter_json["audioSources"], list)
        self.assertEqual(1, len(chapter_json["audioSources"]))
        audio_source = chapter_json["audioSources"][0]
        self.assertEqual("audio001", audio_source["id"])
        self.assertIn("src", audio_source)
        self.assertIn("external", audio_source)
        self.assertIn("duration", audio_source)
        self.assertNotIn("audio", chapter_json, "MFA JSON should not have top-level 'audio' shorthand")

        self.assertIn("segments", chapter_json)
        self.assertEqual(2, len(chapter_json["segments"]))
        for segment in chapter_json["segments"]:
            self.assertIn("textId", segment)
            self.assertIn("begin", segment)
            self.assertIn("end", segment)
            self.assertIn("audioSourceId", segment)
            self.assertIn("words", segment)
            self.assertIn("text", segment, "MFA segment should include 'text' field")
            self.assertIsInstance(segment["words"], list)
            for word in segment["words"]:
                self.assertIsInstance(word, list)
                self.assertGreaterEqual(len(word), 4)
                word_index, word_begin, word_end, word_text = word[0], word[1], word[2], word[3]
                self.assertIsInstance(word_index, int)
                self.assertIsInstance(word_begin, float)
                self.assertIsInstance(word_end, float)
                self.assertIsInstance(word_text, str)
                self.assertTrue(word_begin >= 0.0)
                self.assertTrue(word_end >= word_begin)

        first_segment = chapter_json["segments"][0]
        self.assertEqual("c01-s0001", first_segment["textId"])
        self.assertEqual(2, len(first_segment["words"]))
        self.assertEqual(0, first_segment["words"][0][0])
        self.assertEqual(1, first_segment["words"][1][0])

        self.assertIn("patched_epub", result)
        patched = result["patched_epub"]
        self.assertIsNotNone(patched)
        self.assertTrue(Path(patched).exists())

    def test_sentence_segmentation_preserves_text_with_inline_whitespace(self):
        inspection = unzip_epub(self.epub_path, self.tmpdir / "unpacked3")
        chapter_path = Path(inspection["spine_items"][1]["full_path"])
        before = chapter_path.read_text(encoding="utf-8")
        segment_xhtml_file(
            chapter_path,
            chapter_number=2,
            segmentation_mode="sentence",
            css_href="../styles/audio-sync.css",
        )
        after = chapter_path.read_text(encoding="utf-8")
        self.assertIn("villain", after)
        self.assertIn("of the piece", after)
        self.assertNotIn("villainof the piece", after)
        self.assertIn("Next sentence.", after)
        self.assertEqual(1, after.count("or “through the soul”"))
        self.assertEqual(1, after.count("(from Greek"))
        self.assertEqual(1, after.count("technique and a thoroughly"))
        self.assertEqual(1, after.count("validated method"))
    def test_sentence_segmentation_marks_only_real_segment_nodes(self):
        inspection = unzip_epub(self.epub_path, self.tmpdir / "unpacked4")
        chapter_path = Path(inspection["spine_items"][1]["full_path"])
        segments = segment_xhtml_file(
            chapter_path,
            chapter_number=2,
            segmentation_mode="sentence",
            css_href="../styles/audio-sync.css",
        )
        tree = etree.parse(str(chapter_path))
        marked = tree.xpath(f"//*[@{SEGMENT_ATTR}]")
        self.assertEqual(len(segments), len(marked))
        self.assertEqual([segment["id"] for segment in segments], [node.get("id") for node in marked])
        self.assertFalse(tree.xpath(f"//xhtml:a[@{SEGMENT_ATTR}]", namespaces={"xhtml": "http://www.w3.org/1999/xhtml"}))

    def test_inspect_preview_payload_returns_only_selectable_segment_ids(self):
        builder = EPUBAudioSyncBuilder()
        inspection = unzip_epub(self.epub_path, self.tmpdir / "unpacked5")
        preview = builder.inspect_preview_payload(
            inspection,
            inspection["spine_items"][1]["root_href"],
            [{"chapter": inspection["spine_items"][1]["root_href"], "segment": "sentence"}],
        )
        preview_doc = etree.fromstring(f"<root>{preview['xhtml']}</root>".encode("utf-8"))
        marked = preview_doc.xpath(f".//*[@{SEGMENT_ATTR}]")
        self.assertEqual([segment["textId"] for segment in preview["segments"]], [node.get("id") for node in marked])

    def test_inspect_preview_payload_segment_indexes_match_build_order_with_footnotes(self):
        builder = EPUBAudioSyncBuilder()
        work = self.tmpdir / "unpacked6"
        inspection = unzip_epub(Path("C:/Projects/aeneas/tests/Evolution of a Science_fixed.synced.epub"), work)
        chapter = next(item for item in inspection["spine_items"] if item["root_href"].endswith("EOS_Chapter4.xhtml"))
        preview = builder.inspect_preview_payload(
            inspection,
            chapter["root_href"],
            [{"chapter": chapter["root_href"], "segment": "sentence"}],
        )
        by_id = {segment["textId"]: segment["segmentIndex"] for segment in preview["segments"]}
        self.assertEqual(54, by_id["c11-s0053"])
        self.assertEqual(55, by_id["c11-s0054"])
        self.assertEqual(56, by_id["c11-s0055"])
        self.assertEqual(57, by_id["c11-s0056"])

    def test_parse_footnotes_reads_footnotes_xhtml_only(self):
        builder = EPUBAudioSyncBuilder()
        footnotes_path = self.tmpdir / "footnotes.xhtml"
        footnotes_path.write_text(FOOTNOTE_XHTML, encoding="utf-8")
        inspection = {
            "spine_items": [
                {"href": "Text/chapter1.xhtml", "full_path": str(self.tmpdir / "chapter1.xhtml")},
                {"href": "Text/footnotes.xhtml", "full_path": str(footnotes_path)},
            ]
        }
        footnotes = builder._parse_footnotes(inspection)
        self.assertEqual(
            {
                "footnote-001": "First footnote body.",
                "footnote-002": "Second footnote body.",
                "footnote-003": "Third footnote body.",
            },
            footnotes,
        )

    def test_inject_footnotes_uses_noteref_targets_and_preserves_order(self):
        builder = EPUBAudioSyncBuilder()
        chapter_path = self.tmpdir / "chapter-footnotes.xhtml"
        chapter_path.write_text(FOOTNOTE_CHAPTER_XML, encoding="utf-8")
        segments = segment_xhtml_file(
            chapter_path,
            chapter_number=3,
            segmentation_mode="sentence",
            css_href="../styles/audio-sync.css",
        )
        injected = builder._inject_footnotes(
            chapter_path,
            segments,
            {
                "footnote-001": "First footnote body.",
                "footnote-002": "Second footnote body.",
                "footnote-003": "Third footnote body.",
            },
        )
        self.assertEqual(
            [
                segments[0]["id"],
                "c03-s0001-fnref1",
                segments[1]["id"],
                segments[2]["id"],
                "c03-s0003-fnref1",
                "c03-s0003-fnref2",
            ],
            [segment["id"] for segment in injected],
        )
        self.assertEqual(
            [
                "First footnote body.",
                "Second footnote body.",
                "Third footnote body.",
            ],
            [segment["text"] for segment in injected if segment.get("is_footnote")],
        )
        chapter_after = chapter_path.read_text(encoding="utf-8")
        self.assertIn('id="c03-s0001-fnref1"', chapter_after)
        self.assertIn('id="c03-s0003-fnref1"', chapter_after)
        self.assertIn('id="c03-s0003-fnref2"', chapter_after)

    def test_mfa_mapping_assigns_words_to_footnotes_from_aligned_text(self):
        builder = EPUBAudioSyncBuilder()
        segment_records = [
            {"textId": "c03-s0001", "text": "Sentence with one footnote", "segmentOrder": 1, "audioSourceId": "audio001"},
            {"textId": "c03-s0001-fnref1", "text": "First footnote body.", "segmentOrder": 2, "is_footnote": True, "audioSourceId": "audio001"},
            {"textId": "c03-s0002", "text": "Next sentence", "segmentOrder": 3, "audioSourceId": "audio001"},
            {"textId": "c03-s0003", "text": "Sentence with two footnotes", "segmentOrder": 4, "audioSourceId": "audio001"},
            {"textId": "c03-s0003-fnref1", "text": "Second footnote body.", "segmentOrder": 5, "is_footnote": True, "audioSourceId": "audio001"},
            {"textId": "c03-s0003-fnref2", "text": "Third footnote body.", "segmentOrder": 6, "is_footnote": True, "audioSourceId": "audio001"},
        ]
        words = [
            {"word": "Sentence", "begin": 1.0, "end": 1.2},
            {"word": "with", "begin": 1.2, "end": 1.3},
            {"word": "one", "begin": 1.3, "end": 1.4},
            {"word": "footnote", "begin": 1.4, "end": 1.7},
            {"word": "First", "begin": 1.7, "end": 1.9},
            {"word": "footnote", "begin": 1.9, "end": 2.1},
            {"word": "body.", "begin": 2.1, "end": 2.3},
            {"word": "Next", "begin": 2.4, "end": 2.6},
            {"word": "sentence", "begin": 2.6, "end": 3.0},
            {"word": "Sentence", "begin": 3.1, "end": 3.3},
            {"word": "with", "begin": 3.3, "end": 3.4},
            {"word": "two", "begin": 3.4, "end": 3.5},
            {"word": "footnotes", "begin": 3.5, "end": 3.8},
            {"word": "Second", "begin": 3.8, "end": 4.0},
            {"word": "footnote", "begin": 4.0, "end": 4.2},
            {"word": "body.", "begin": 4.2, "end": 4.4},
            {"word": "Third", "begin": 4.4, "end": 4.6},
            {"word": "footnote", "begin": 4.6, "end": 4.8},
            {"word": "body.", "begin": 4.8, "end": 5.0},
        ]
        mapped, warnings = builder._map_mfa_words_to_segments("assignment-02", segment_records, words, "audio001")
        self.assertEqual([], warnings)
        # c03-s0001-fnref1 (footnote after parent): has real begin/end but empty words
        self.assertEqual([1.7, 2.3], [mapped[1]["begin"], mapped[1]["end"]])
        self.assertEqual([], mapped[1]["words"])
        # c03-s0003-fnref1 (first of two consecutive footnotes): real timing, no words
        self.assertEqual([3.8, 4.4], [mapped[4]["begin"], mapped[4]["end"]])
        self.assertEqual([], mapped[4]["words"])
        # c03-s0003-fnref2 (second of two consecutive footnotes): real timing, no words
        self.assertEqual([4.4, 5.0], [mapped[5]["begin"], mapped[5]["end"]])
        self.assertEqual([], mapped[5]["words"])

    def test_write_assignment_text_keeps_footnotes_on_separate_mfa_lines(self):
        builder = EPUBAudioSyncBuilder()
        path = self.tmpdir / "assignment-mfa.txt"
        builder._write_assignment_text(
            path,
            [
                {"textId": "c01-s0001", "text": "Parent sentence.", "audioSourceId": "audio001"},
                {"textId": "c01-s0001-fnref1", "text": "Footnote body.", "is_footnote": True, "audioSourceId": "audio001"},
                {"textId": "c01-s0002", "text": "Next sentence.", "audioSourceId": "audio001"},
            ],
            for_mfa=True,
        )
        self.assertEqual(
            [
                "Parent sentence.",
                "Footnote body.",
                "Next sentence.",
            ],
            path.read_text(encoding="utf-8").splitlines(),
        )

    def test_mfa_alignment_tokens_split_hyphenated_compounds(self):
        builder = EPUBAudioSyncBuilder()
        self.assertEqual(
            ["electronic", "screw", "ups"],
            builder._alignment_tokens("electronic screw-ups"),
        )
        self.assertEqual(
            ["color", "visio", "recall"],
            builder._alignment_tokens("color-visio recall"),
        )
        self.assertEqual(
            ["well", "greased", "computer"],
            builder._alignment_tokens("well-greased computer"),
        )
        self.assertEqual(
            ["If", "this", "was", "basic", "brain"],
            builder._alignment_tokens("If this was basic brain …"),
        )

if __name__ == "__main__":
    unittest.main()
