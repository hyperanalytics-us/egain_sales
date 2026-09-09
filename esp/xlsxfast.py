"""Streaming reader for the fixed-layout weblog workbooks.

openpyxl's read-only mode works but spends most of its time building cell
objects we throw away.  These files are ~530k rows, so we go straight at the
sheet XML with iterparse and yield plain tuples of strings.
"""
from __future__ import annotations

import csv
import io
import os
import re
import zipfile
from typing import Iterator, List, Optional
from xml.etree import ElementTree as ET

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_COL_RE = re.compile(r"([A-Z]+)")


def _col_index(ref: str) -> int:
    """'BC12' -> 54 (0-based column index)."""
    m = _COL_RE.match(ref)
    if not m:
        return 0
    n = 0
    for ch in m.group(1):
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _shared_strings(zf: zipfile.ZipFile) -> List[str]:
    try:
        raw = zf.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    out: List[str] = []
    buf: List[str] = []
    depth_si = False
    for event, elem in ET.iterparse(io.BytesIO(raw), events=("start", "end")):
        if event == "start" and elem.tag == NS + "si":
            depth_si = True
            buf = []
        elif event == "end":
            if elem.tag == NS + "t" and depth_si:
                buf.append(elem.text or "")
            elif elem.tag == NS + "si":
                out.append("".join(buf))
                depth_si = False
                elem.clear()
    return out


def _first_sheet_path(zf: zipfile.ZipFile) -> str:
    names = [n for n in zf.namelist() if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")]
    if not names:
        raise ValueError("workbook contains no worksheets")
    names.sort(key=lambda n: int(re.search(r"sheet(\d+)", n).group(1)))
    return names[0]


def iter_xlsx_rows(path: str, max_cols: int = 32) -> Iterator[List[str]]:
    """Yield each row of the first worksheet as a list of strings."""
    with zipfile.ZipFile(path) as zf:
        strings = _shared_strings(zf)
        sheet = _first_sheet_path(zf)
        with zf.open(sheet) as fh:
            row: List[str] = []
            cell_type: Optional[str] = None
            cell_col = 0
            value_parts: List[str] = []
            in_value = False
            for event, elem in ET.iterparse(fh, events=("start", "end")):
                tag = elem.tag
                if event == "start":
                    if tag == NS + "row":
                        row = []
                    elif tag == NS + "c":
                        cell_type = elem.get("t")
                        ref = elem.get("r")
                        cell_col = _col_index(ref) if ref else len(row)
                        value_parts = []
                    elif tag in (NS + "v", NS + "t"):
                        in_value = True
                else:
                    if tag in (NS + "v", NS + "t"):
                        if in_value and elem.text:
                            value_parts.append(elem.text)
                        in_value = False
                    elif tag == NS + "c":
                        raw = "".join(value_parts)
                        if cell_type == "s" and raw:
                            try:
                                val = strings[int(raw)]
                            except (ValueError, IndexError):
                                val = raw
                        else:
                            val = raw
                        while len(row) < cell_col:
                            row.append("")
                        if cell_col < max_cols:
                            row.append(val)
                        elem.clear()
                    elif tag == NS + "row":
                        yield row
                        elem.clear()


def iter_csv_rows(path: str) -> Iterator[List[str]]:
    with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as fh:
        sample = fh.read(8192)
        fh.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
        except csv.Error:
            dialect = csv.excel
        for row in csv.reader(fh, dialect):
            yield row


def iter_rows(path: str) -> Iterator[List[str]]:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm"):
        return iter_xlsx_rows(path)
    return iter_csv_rows(path)
