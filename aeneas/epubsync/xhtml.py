#!/usr/bin/env python
# coding=utf-8

from __future__ import annotations

import copy
import re

from lxml import etree


XML_NS = "http://www.w3.org/XML/1998/namespace"
XHTML_NS = "http://www.w3.org/1999/xhtml"
NSMAP = {"xhtml": XHTML_NS}
SEGMENT_ATTR = "data-epubsync-segment"

UNSAFE_TAGS = {
    "script",
    "style",
    "img",
    "audio",
    "video",
    "svg",
    "math",
    "canvas",
    "iframe",
    "object",
}
BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "caption",
    "dd",
    "div",
    "dt",
    "figcaption",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "li",
    "p",
    "section",
    "td",
    "th",
}
INLINE_ZERO_LENGTH_INCLUDE = {"br"}
WHITESPACE_RE = re.compile(r"\s+")
SENTENCE_BOUNDARY_RE = re.compile(r"(?:(?<=[\.\!\?。！？][\"\u201d])|(?<=[\.\!\?。！？]))\s+")


def local_name(element):
    return etree.QName(element.tag).localname.lower()


def normalize_text(text):
    return WHITESPACE_RE.sub(" ", text or "").strip()


def is_unsafe(element):
    return local_name(element) in UNSAFE_TAGS


def has_unsafe_ancestor(element):
    parent = element
    while parent is not None:
        if is_unsafe(parent):
            return True
        parent = parent.getparent()
    return False


def has_block_descendant(element):
    for child in element.iterdescendants():
        if is_unsafe(child):
            continue
        if local_name(child) in BLOCK_TAGS and normalize_text("".join(child.itertext())):
            return True
    return False


def text_content_length(element):
    length = len(element.text or "")
    for child in element:
        if local_name(child) in INLINE_ZERO_LENGTH_INCLUDE:
            length += 1
        else:
            length += text_content_length(child)
        length += len(child.tail or "")
    return length


def flatten_text(element):
    parts = []
    if element.text:
        parts.append(element.text)
    for child in element:
        if local_name(child) in INLINE_ZERO_LENGTH_INCLUDE:
            parts.append("\n")
        else:
            parts.append(flatten_text(child))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts)


def append_text(target, text):
    if not text:
        return
    if len(target) > 0:
        last = target[-1]
        last.tail = (last.tail or "") + text
    else:
        target.text = (target.text or "") + text


def clone_shell(element):
    clone = etree.Element(element.tag)
    for key, value in element.attrib.items():
        clone.set(key, value)
    return clone


def mark_segment_element(element):
    element.set(SEGMENT_ATTR, "1")


def has_clone_content(element):
    if element.text is not None:
        return True
    if len(element) > 0:
        return True
    return False


def slice_element(element, start, end):
    total = text_content_length(element)
    start = max(0, start)
    end = min(total, end)
    clone = clone_shell(element)
    if start > 0 or end < total:
        clone.attrib.pop("id", None)
        clone.attrib.pop("{%s}id" % XML_NS, None)
    position = 0
    text = element.text or ""
    if text:
        overlap_start = max(start, position)
        overlap_end = min(end, position + len(text))
        if overlap_start < overlap_end:
            append_text(clone, text[overlap_start - position:overlap_end - position])
        position += len(text)
    for child in element:
        child_name = local_name(child)
        if child_name in INLINE_ZERO_LENGTH_INCLUDE:
            if start <= position < end:
                child_clone = copy.deepcopy(child)
                child_clone.tail = None
                clone.append(child_clone)
            position += 1
        else:
            child_length = text_content_length(child)
            overlap_start = max(start, position)
            overlap_end = min(end, position + child_length)
            if overlap_start < overlap_end or (child_length == 0 and child.get("id") and start <= position < end):
                child_clone = slice_element(child, overlap_start - position, overlap_end - position)
                if has_clone_content(child_clone) or local_name(child) in INLINE_ZERO_LENGTH_INCLUDE or (child_length == 0 and child.get("id") and start <= position < end):
                    clone.append(child_clone)
            position += child_length
        tail = child.tail or ""
        if tail:
            overlap_start = max(start, position)
            overlap_end = min(end, position + len(tail))
            if overlap_start < overlap_end:
                append_text(clone, tail[overlap_start - position:overlap_end - position])
            position += len(tail)
    return clone


def choose_block_candidates(body):
    candidates = []
    for element in body.iter():
        if element is body or has_unsafe_ancestor(element):
            continue
        if local_name(element) not in BLOCK_TAGS:
            continue
        if not normalize_text(flatten_text(element)):
            continue
        if has_block_descendant(element):
            continue
        candidates.append(element)
    return candidates


def split_sentences(raw_text):
    raw_text = raw_text or ""
    stripped = raw_text.strip()
    if not stripped:
        return []
    parts = []
    start = 0
    for match in SENTENCE_BOUNDARY_RE.finditer(raw_text):
        end = match.start()
        if raw_text[start:end].strip():
            parts.append((start, end))
        start = match.end()
    if raw_text[start:].strip():
        parts.append((start, len(raw_text)))
    if not parts:
        return [(0, len(raw_text))]
    return parts


def ensure_stylesheet_link(tree, xhtml_path, href):
    head = tree.find(".//xhtml:head", namespaces=NSMAP)
    if head is None:
        html = tree.getroot()
        head = etree.SubElement(html, "{%s}head" % XHTML_NS)
    existing = head.xpath("xhtml:link[@rel='stylesheet']", namespaces=NSMAP)
    normalized_href = href.replace("\\", "/")
    for link in existing:
        if link.get("href") == normalized_href:
            return
    link = etree.Element("{%s}link" % XHTML_NS)
    link.set("rel", "stylesheet")
    link.set("type", "text/css")
    link.set("href", normalized_href)
    head.append(link)


def xhtml_document_title(xhtml_path):
    parser = etree.XMLParser(remove_blank_text=False, recover=True)
    tree = etree.parse(str(xhtml_path), parser)
    title_node = tree.find(".//xhtml:title", namespaces=NSMAP)
    if title_node is None:
        return None
    title = normalize_text("".join(title_node.itertext()))
    return title or None


def segment_xhtml_file(xhtml_path, chapter_number, segmentation_mode, css_href=None):
    parser = etree.XMLParser(remove_blank_text=False, recover=True)
    tree = etree.parse(str(xhtml_path), parser)
    body = tree.find(".//xhtml:body", namespaces=NSMAP)
    if body is None:
        raise ValueError("XHTML file is missing a body element: %s" % xhtml_path)
    if css_href:
        ensure_stylesheet_link(tree, xhtml_path, css_href)
    candidates = choose_block_candidates(body)
    segments = []
    if segmentation_mode == "paragraph":
        for counter, element in enumerate(candidates, start=1):
            text = normalize_text(flatten_text(element))
            if not text:
                continue
            segment_id = element.get("id") or "c%02d-p%04d" % (chapter_number, counter)
            if not element.get("id"):
                element.set("id", segment_id)
            mark_segment_element(element)
            segments.append({"id": segment_id, "text": text})
    elif segmentation_mode == "sentence":
        sentence_counter = 1
        for element in candidates:
            raw_text = flatten_text(element)
            sentence_ranges = split_sentences(raw_text)
            if len(sentence_ranges) <= 1:
                text = normalize_text(raw_text)
                if not text:
                    continue
                segment_id = element.get("id") or "c%02d-s%04d" % (chapter_number, sentence_counter)
                if not element.get("id"):
                    element.set("id", segment_id)
                mark_segment_element(element)
                segments.append({"id": segment_id, "text": text})
                sentence_counter += 1
                continue
            source_element = copy.deepcopy(element)
            original_children = list(element)
            for child in original_children:
                element.remove(child)
            element.text = None
            for index, (start, end) in enumerate(sentence_ranges):
                sentence_clone = etree.Element("{%s}span" % XHTML_NS)
                sentence_clone.set("id", "c%02d-s%04d" % (chapter_number, sentence_counter))
                mark_segment_element(sentence_clone)
                content = slice_element(source_element, start, end)
                append_text(sentence_clone, content.text or "")
                for child in list(content):
                    content.remove(child)
                    sentence_clone.append(child)
                element.append(sentence_clone)
                segments.append(
                    {
                        "id": sentence_clone.get("id"),
                        "text": normalize_text(raw_text[start:end]),
                    }
                )
                sentence_counter += 1
                whitespace_between = raw_text[end:sentence_ranges[index + 1][0]] if index < len(sentence_ranges) - 1 else ""
                if whitespace_between and len(element) > 0:
                    element[-1].tail = (element[-1].tail or "") + whitespace_between
    else:
        raise ValueError("Unsupported segmentation mode: %s" % segmentation_mode)
    tree.write(str(xhtml_path), encoding="utf-8", xml_declaration=True)
    return segments


def xhtml_body_html(xhtml_path):
    parser = etree.XMLParser(remove_blank_text=False, recover=True)
    tree = etree.parse(str(xhtml_path), parser)
    body = tree.find(".//xhtml:body", namespaces=NSMAP)
    if body is None:
        return ""
    parts = []
    if body.text:
        parts.append(body.text)
    for child in body:
        parts.append(etree.tostring(child, encoding="unicode"))
    return "".join(parts)
