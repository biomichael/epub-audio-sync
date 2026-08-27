# EPUB Audio Sync Builder

EPUB Audio Sync Builder is a desktop application and command-line workflow for aligning narrated audio with the text in an EPUB. It creates EPUB 3 media overlays and supporting JSON data so reading systems can synchronize text and audio.

The project has two pieces:

- An Electron desktop application for EPUB inspection, chapter segmentation, audio assignment, boundary selection, building, and preview.
- A Python backend in `aeneas/epubsync` for EPUB inspection, alignment, preview generation, and export.

## Features

- Inspect the EPUB spine and choose sentence or paragraph segmentation.
- Map one audio file to a contiguous range of chapter segments.
- Reuse audio across assignments or split one chapter across assignments.
- Pick start and end text boundaries from a chapter preview.
- Find likely boundaries automatically.
- Use the Aeneas engine by default or optionally configure Montreal Forced Aligner (MFA).
- Preview generated chapter text and synchronized audio.
- Export embedded audio in the EPUB or external audio assets.
- Produce JSON, SMIL, preview, and diagnostic output.

## Requirements

For development from source:

- Python 3.8 or newer
- Node.js and npm
- FFmpeg and FFprobe
- eSpeak when required by the configured Aeneas workflow
- Python dependencies in `requirements.txt`
- MFA plus its acoustic model and dictionary only when using the MFA engine

The project includes Aeneas-derived code and keeps the upstream AGPL license in `LICENSE`. Aeneas is the default forced-alignment engine. MFA is an optional alternative and must be installed and configured separately.

## Run the desktop application

From the repository root:

```bash
pip install -r requirements.txt
npm install
npm start
```

In the application:

1. Use **File > Add EPUB** to select the source EPUB.
2. Use **File > Add Audio Files** to select narration tracks.
3. Choose an output folder.
4. Click **Inspect EPUB**.
5. Configure chapter segmentation and alignment assignments.
6. Use **Pick start**, **Pick end**, or **Find Sync** for boundaries.
7. Choose embedded or external audio and click **Run Build**.
8. Load an exported chapter in **Preview** to review the result.

The assignment audio preview is for checking a selected track; it does not change alignment values or the build pipeline.

## Command-line workflow

```bash
python -m aeneas.epubsync.cli \
  --epub book.epub \
  --audio chapter01.mp3 chapter02.mp3 \
  --map OEBPS/Text/chapter01.xhtml=chapter01.mp3 \
        OEBPS/Text/chapter02.xhtml=chapter02.mp3 \
  --language eng \
  --out output/epub-audio-sync
```

Useful options include:

- `--embedded-audio` to copy audio into the output EPUB.
- `--external-audio` to keep audio outside the EPUB.
- `--segment sentence|paragraph` to choose default segmentation.
- `--alignment-engine aeneas|mfa` to choose the alignment engine.
- `--dump-inspection` to inspect without building.
- `--serve` to launch the local web interface.

Run `python -m aeneas.epubsync.cli --help` for the complete option list.

## Output

Build output is written below the selected output folder and may include:

- A patched EPUB containing EPUB 3 SMIL media overlays, or an EPUB plus external audio assets.
- JSON alignment and preview data.
- SMIL files and intermediate/debug information.
- Logs and warnings requiring review.

## Repository layout

```text
aeneas/epubsync/     Python EPUB inspection, alignment, preview, and export
electron/             Electron main process, preload bridge, and renderer UI
requirements.txt      Python dependencies
package.json          Electron dependencies and start script
build.ps1             Windows build helpers
LICENSE               License for bundled Aeneas-derived code
```

## Development notes

The Electron renderer communicates with `aeneas.epubsync.desktop_api` through the preload bridge. Keep the assignment payload fields understood by the Python backend stable when changing the UI. Renderer-only state, such as preview-player state, should not be sent to the build backend.

Before committing changes, run the application against a small EPUB and verify inspection, manual boundary picking, automatic sync, preview playback, and both embedded and external audio modes.

## License and upstream references

This repository contains and builds upon Aeneas code. See `LICENSE` and source headers for the applicable AGPL licensing and required notices. MFA is an optional external alignment tool and is not part of the normal Python dependency installation.

