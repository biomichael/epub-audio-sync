#!/usr/bin/env python
# coding=utf-8

from __future__ import annotations

from pathlib import Path


SILENCE_LABELS = {"", "sil", "sp", "spn", "silence", "<s>", "<sil>", "<sp>"}


def parse_mfa_textgrid_words(textgrid_path):
    path = Path(textgrid_path)
    text = path.read_text(encoding="utf-8")
    return parse_mfa_textgrid_words_text(text)


def parse_mfa_textgrid_words_text(text):
    lines = [line.rstrip() for line in text.splitlines()]
    tiers = _extract_tiers(lines)
    word_tier = None
    for tier in tiers:
        name = (tier.get("name") or "").strip().lower()
        if "word" in name and "phone" not in name:
            word_tier = tier
            break
    if word_tier is None:
        raise ValueError("No word tier found in TextGrid")

    words = []
    for interval in word_tier["intervals"]:
        label = _normalize_label(interval.get("text"))
        if label is None:
            continue
        words.append(
            {
                "word": label,
                "begin": float(interval["xmin"]),
                "end": float(interval["xmax"]),
            }
        )
    return words


def _extract_tiers(lines):
    tiers = []
    current_tier = None
    current_interval = None
    in_tier = False
    in_intervals = False

    for raw_line in lines:
        line = raw_line.strip()
        if line.startswith("item ["):
            if current_interval is not None and current_tier is not None:
                current_tier["intervals"].append(current_interval)
                current_interval = None
            if current_tier is not None:
                tiers.append(current_tier)
            current_tier = {"name": None, "intervals": []}
            in_tier = True
            in_intervals = False
            continue
        if not in_tier or current_tier is None:
            continue
        if line.startswith("name ="):
            current_tier["name"] = _parse_quoted_value(line)
            continue
        if line.startswith("intervals: size ="):
            in_intervals = True
            continue
        if in_intervals and line.startswith("intervals ["):
            if current_interval is not None:
                current_tier["intervals"].append(current_interval)
            current_interval = {}
            continue
        if current_interval is not None:
            if line.startswith("xmin ="):
                current_interval["xmin"] = _parse_float_value(line)
            elif line.startswith("xmax ="):
                current_interval["xmax"] = _parse_float_value(line)
            elif line.startswith("text ="):
                current_interval["text"] = _parse_quoted_value(line)
    if current_interval is not None and current_tier is not None:
        current_tier["intervals"].append(current_interval)
    if current_tier is not None:
        tiers.append(current_tier)
    return tiers


def _parse_quoted_value(line):
    parts = line.split("=", 1)
    if len(parts) != 2:
        return ""
    value = parts[1].strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def _parse_float_value(line):
    parts = line.split("=", 1)
    if len(parts) != 2:
        raise ValueError("Invalid TextGrid numeric line: %s" % line)
    return float(parts[1].strip())


def _normalize_label(value):
    if value is None:
        return None
    label = str(value).strip()
    if not label:
        return None
    if label.lower() in SILENCE_LABELS:
        return None
    return label
