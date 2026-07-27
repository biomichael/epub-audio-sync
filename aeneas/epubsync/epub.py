#!/usr/bin/env python
# coding=utf-8

from __future__ import annotations

import mimetypes
import os
from pathlib import Path
import shutil
import zipfile

from lxml import etree


CONTAINER_NS = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
OPF_NS = {
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
}

XHTML_EXTENSIONS = {".xhtml", ".html", ".htm"}


def parse_xml(path):
    parser = etree.XMLParser(remove_blank_text=False, recover=True)
    return etree.parse(str(path), parser)


def get_opf_package_path(epub_root):
    container_path = Path(epub_root) / "META-INF" / "container.xml"
    if not container_path.exists():
        raise ValueError("EPUB is missing META-INF/container.xml")
    tree = parse_xml(container_path)
    rootfile = tree.find(".//c:rootfile", namespaces=CONTAINER_NS)
    if rootfile is None:
        raise ValueError("EPUB container.xml does not contain a rootfile")
    full_path = rootfile.get("full-path")
    if not full_path:
        raise ValueError("EPUB rootfile is missing full-path")
    return full_path.replace("\\", "/")


def inspect_epub_root(epub_root):
    epub_root = Path(epub_root)
    opf_rel_path = get_opf_package_path(epub_root)
    opf_path = epub_root / Path(opf_rel_path)
    if not opf_path.exists():
        raise ValueError("OPF file referenced by container.xml was not found: %s" % opf_rel_path)
    opf_tree = parse_xml(opf_path)
    package_dir = opf_path.parent
    manifest = {}
    for item in opf_tree.findall(".//opf:manifest/opf:item", namespaces=OPF_NS):
        item_id = item.get("id")
        href = item.get("href")
        if item_id and href:
            manifest[item_id] = {
                "href": href,
                "media_type": item.get("media-type"),
                "properties": item.get("properties", ""),
                "element": item,
            }
    spine_items = []
    for index, itemref in enumerate(opf_tree.findall(".//opf:spine/opf:itemref", namespaces=OPF_NS), start=1):
        idref = itemref.get("idref")
        manifest_item = manifest.get(idref)
        if manifest_item is None:
            continue
        href = manifest_item["href"]
        full_path = (package_dir / href).resolve()
        rel_from_root = full_path.relative_to(epub_root.resolve()).as_posix()
        if Path(href).suffix.lower() in XHTML_EXTENSIONS or (
            manifest_item["media_type"] or ""
        ) in {"application/xhtml+xml", "text/html"}:
            spine_items.append(
                {
                    "index": index,
                    "idref": idref,
                    "href": href.replace("\\", "/"),
                    "root_href": rel_from_root,
                    "full_path": str(full_path),
                    "media_overlay": manifest_item["element"].get("media-overlay"),
                    "properties": manifest_item["properties"],
                }
            )
    encryption_path = epub_root / "META-INF" / "encryption.xml"
    return {
        "epub_root": str(epub_root),
        "opf_rel_path": opf_rel_path,
        "opf_path": str(opf_path),
        "package_dir": str(package_dir),
        "spine_items": spine_items,
        "has_encryption_xml": encryption_path.exists(),
        "encryption_path": str(encryption_path),
    }


def unzip_epub(epub_path, destination):
    epub_path = Path(epub_path)
    destination = Path(destination)
    if destination.exists():
        shutil.rmtree(str(destination))
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(str(epub_path), "r") as archive:
        archive.extractall(str(destination))
    return inspect_epub_root(destination)


def ensure_parent_dir(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def guess_media_type(path):
    media_type, _ = mimetypes.guess_type(str(path))
    if media_type:
        return media_type
    suffix = Path(path).suffix.lower()
    if suffix == ".smil":
        return "application/smil+xml"
    if suffix == ".css":
        return "text/css"
    if suffix == ".xhtml":
        return "application/xhtml+xml"
    return "application/octet-stream"


def repack_epub(source_root, output_epub_path):
    source_root = Path(source_root)
    output_epub_path = Path(output_epub_path)
    ensure_parent_dir(output_epub_path)
    mimetype_path = source_root / "mimetype"
    if not mimetype_path.exists():
        raise ValueError("EPUB root is missing required mimetype file")
    with zipfile.ZipFile(str(output_epub_path), "w") as archive:
        archive.write(str(mimetype_path), "mimetype", compress_type=zipfile.ZIP_STORED)
        for path in sorted(source_root.rglob("*")):
            if path.is_dir() or path == mimetype_path:
                continue
            rel_path = path.relative_to(source_root).as_posix()
            archive.write(str(path), rel_path, compress_type=zipfile.ZIP_DEFLATED)
    return str(output_epub_path)
