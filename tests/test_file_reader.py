# -*- coding: utf-8 -*-
"""文件消息读取（2026-10-13 老板：引用/发来的 pdf、md 能不能读）。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import importlib.util  # noqa: E402


def _load_file_reader():
    spec = importlib.util.spec_from_file_location(
        "ai_file_reader_t",
        ROOT / "src" / "qbotmanager" / "assets" / "plugins" / "ai" / "file_reader.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


FR = _load_file_reader()


def _make_pdf(text: str = "EVA PDF TEST") -> bytes:
    """手写一个最小可解析的 PDF（pypdf 能读出 text）。"""
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(text) + 30).encode() + b" >>\nstream\n"
        b"BT /F1 24 Tf 72 700 Td (" + text.encode("ascii") + b") Tj ET\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    xref_pos = len(out)
    out += b"xref\n0 " + str(len(objs) + 1).encode() + b"\n"
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += ("%010d 00000 n \n" % off).encode()
    out += (b"trailer\n<< /Size " + str(len(objs) + 1).encode()
            + b" /Root 1 0 R >>\nstartxref\n" + str(xref_pos).encode() + b"\n%%EOF\n")
    return bytes(out)


def test_markdown_and_text_files_read_directly():
    text, note = FR.extract_text("notes.md", "暗号是紫罗兰。".encode("utf-8"))
    assert "紫罗兰" in text and note == ""
    text, note = FR.extract_text("readme.txt", "hello".encode("gb18030"))
    assert text == "hello" and note == ""


def test_unknown_ext_gives_note_not_text():
    text, note = FR.extract_text("binary.bin", b"\x00\x01\x02")
    assert text == "" and note


def test_pdf_text_extracted_via_pypdf():
    text, note = FR.extract_text("demo.pdf", _make_pdf("SECRET-VIOLET"))
    assert "SECRET-VIOLET" in text and note == "", note


def test_describe_always_non_empty():
    block = FR.describe({"name": "a.pdf", "size": 0, "url": "",
                         "text": "", "note": "读不出来"}, 1)
    assert "a.pdf" in block and "读不出来" in block
    block2 = FR.describe({"name": "notes.md", "size": 2048, "url": "https://x/y",
                          "text": "暗号是紫罗兰", "note": ""}, 2)
    assert "暗号是紫罗兰" in block2 and "https://x/y" in block2


def test_files_text_from_raw_variants():
    import asyncio
    raw_list = [{"type": "text", "data": {"text": "hi"}},
                {"type": "file", "data": {"file_name": "a.md", "url": "file:///nonexistent"}}]
    # file:// 不存在 → 走异常兜底，但整体必须不抛、且至少有一段说明
    out = asyncio.run(FR.files_text_from_raw(None, raw_list))
    assert "a.md" in out
    out2 = asyncio.run(FR.files_text_from_raw(
        None, {"type": "file", "data": {"file_name": "b.md"}}))
    assert "b.md" in out2
    out3 = asyncio.run(FR.files_text_from_raw(
        None, "[CQ:file,file_name=c.md,file_id=9]"))
    assert "c.md" in out3


def test_qq_adapter_wires_file_reader():
    src = (ROOT / "src" / "qbotmanager" / "assets" / "plugins" / "ai"
           / "adapters" / "qq.py").read_text(encoding="utf-8")
    assert "from .. import file_reader" in src
    assert "file_reader.read_file(" in src, "直发文件没有接线"
    assert "file_reader.files_text_from_raw(" in src, "引用的文件没有接线"
