#!/usr/bin/env python
# coding=utf-8

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .pipeline import EPUBAudioSyncBuilder


def build_parser():
    parser = argparse.ArgumentParser(prog="epub-audio-sync", description="Build synchronized EPUB 3 media overlays and app JSON from an EPUB and audio files.")
    parser.add_argument("--epub", required=True, help="Path to the source EPUB")
    parser.add_argument("--audio", nargs="+", default=[], help="Audio files available for mapping")
    parser.add_argument("--map", nargs="+", default=[], dest="mappings", help="Chapter mappings of the form xhtml/ch1.xhtml=chapter01.mp3")
    parser.add_argument("--language", default="eng", help="aeneas language code, default: eng")
    parser.add_argument("--segment", choices=["sentence", "paragraph"], default="sentence", help="Default segmentation mode")
    parser.add_argument("--external-audio", action="store_true", help="Keep audio outside the EPUB and point JSON at external assets")
    parser.add_argument("--embedded-audio", action="store_true", help="Copy audio files into the EPUB")
    parser.add_argument("--aeneas-repo-path", help="Optional path to the aeneas repo root")
    parser.add_argument("--alignment-engine", choices=["aeneas", "mfa"], default="aeneas", help="Alignment engine to use")
    parser.add_argument("--mfa-executable-path", help="Optional path to the MFA executable")
    parser.add_argument("--mfa-acoustic-model", help="Optional MFA acoustic model or language model name")
    parser.add_argument("--mfa-dictionary", help="Optional MFA pronunciation dictionary path or model name")
    parser.add_argument("--mfa-output-folder", help="Optional MFA output folder")
    parser.add_argument("--mfa-corpus-folder", help="Optional MFA temporary corpus folder")
    parser.add_argument("--mfa-audio-conversion", choices=["none", "wav"], default="none", help="Audio conversion mode for MFA")
    parser.add_argument("--mfa-proof", action="store_true", help="Run an MFA proof check and exit without building")
    parser.add_argument("--out", required=True, help="Output folder")
    parser.add_argument("--dump-inspection", action="store_true", help="Print EPUB inspection JSON and exit")
    parser.add_argument("--serve", action="store_true", help="Launch the local web UI instead of running the CLI build")
    parser.add_argument("--host", default="127.0.0.1", help="Host for --serve")
    parser.add_argument("--port", default=8765, type=int, help="Port for --serve")
    return parser


def parse_mapping_args(values, default_segment):
    mappings = []
    for value in values:
        if "=" not in value:
            raise ValueError("Invalid mapping '%s', expected chapter=audio" % value)
        chapter, audio = value.split("=", 1)
        mappings.append({"chapter": chapter, "audio": audio, "segment": default_segment})
    return mappings


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.serve:
        from .webapp import run_server

        run_server(host=args.host, port=args.port, aeneas_repo_path=args.aeneas_repo_path)
        return 0
    external_audio = True
    if args.embedded_audio:
        external_audio = False
    elif args.external_audio:
        external_audio = True
    builder = EPUBAudioSyncBuilder(aeneas_repo_path=args.aeneas_repo_path, logger=print)
    inspection = builder.inspect_epub(args.epub, Path(args.out) / "work")
    if args.dump_inspection:
        print(json.dumps(inspection, indent=2))
        return 0
    mappings = parse_mapping_args(args.mappings, args.segment)
    result = builder.run(
        {
            "epub": args.epub,
            "audio_files": args.audio,
            "mappings": mappings,
            "language": args.language,
            "segment": args.segment,
            "external_audio": external_audio,
            "out": args.out,
            "alignment_engine": args.alignment_engine,
            "mfa": {
                "executable_path": args.mfa_executable_path,
                "acoustic_model": args.mfa_acoustic_model,
                "dictionary": args.mfa_dictionary,
                "output_folder": args.mfa_output_folder,
                "corpus_dir": args.mfa_corpus_folder,
                "audio_conversion": args.mfa_audio_conversion,
                "proof_mode": args.mfa_proof,
            },
        }
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
