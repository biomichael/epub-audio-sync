# Development Guide

This repository contains the EPUB Audio Sync Builder desktop application and its
Python alignment backend.

## Before changing code

- Keep the Electron renderer and Python backend payload contracts compatible.
- Preserve the existing EPUB inspection, alignment, preview, and export flows.
- Keep renderer-only UI state out of backend build payloads.
- Test changes with a small EPUB and representative audio files.

## Main areas

- `electron/`: Electron process, preload bridge, and desktop UI.
- `aeneas/epubsync/`: EPUB inspection, alignment, preview, and export logic.
- `tests/`: Existing automated tests.
- `build.ps1` and packaging scripts: Windows build and distribution helpers.

## Verification

For UI changes:

```bash
node --check electron/renderer/renderer.js
npm start
```

For Python changes, run the relevant tests and verify a complete build in both
embedded-audio and external-audio modes.

The repository includes Aeneas-derived code. Preserve the applicable license
and source notices in `LICENSE` and source files.

