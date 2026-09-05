#!/usr/bin/env python3
"""
Minimal pure-Python reader for Jet 3 (Access 97) .mdb databases.

The 2004-vintage EVTM books (E4*) ship navigation data as an .mdb per book
(tables CELLS, PAGEREF, CONNREF, SPLCREF, SPLICE, ...) instead of the
XML-per-page model of later books. This reads just enough of the Jet 3
format to recover those tables: single-file, read-only, no indexes, text
and fixed scalar columns. Long (>255 byte) rows use the 1-byte var-offset
jump-table encoding; memo/OLE columns are returned as empty strings.

Format references: the mdbtools project's HACKING notes; layout verified
byte-level against the shipped EVTM databases.
"""
from __future__ import annotations

import struct
from pathlib import Path

PGSIZE = 2048          # Jet 3 page size
ENC = "cp1252"

# column type codes
T_BOOL, T_BYTE, T_INT, T_LONG = 0x01, 0x02, 0x03, 0x04
T_MONEY, T_FLOAT, T_DOUBLE, T_DATE = 0x05, 0x06, 0x07, 0x08
T_BINARY, T_TEXT, T_OLE, T_MEMO, T_GUID = 0x09, 0x0A, 0x0B, 0x0C, 0x0F

FIXED_SIZE = {T_BOOL: 0, T_BYTE: 1, T_INT: 2, T_LONG: 4,
              T_MONEY: 8, T_FLOAT: 4, T_DOUBLE: 8, T_DATE: 8, T_GUID: 16}


class Column:
    __slots__ = ("name", "type", "num", "var_idx", "fixed_off", "size", "is_fixed")

    def __init__(self, name, ctype, num, var_idx, fixed_off, size, is_fixed):
        self.name, self.type, self.num = name, ctype, num
        self.var_idx, self.fixed_off, self.size = var_idx, fixed_off, size
        self.is_fixed = is_fixed


class JetDb:
    def __init__(self, path: Path | str):
        self.data = Path(path).read_bytes()
        if self.data[4:19] != b"Standard Jet DB":
            raise ValueError(f"not a Jet database: {path}")
        if self.data[0x14] != 0:
            raise ValueError(f"not Jet 3 (version byte {self.data[0x14]}): {path}")
        self.npages = len(self.data) // PGSIZE
        self._catalog = None

    def page(self, n: int) -> bytes:
        return self.data[n * PGSIZE:(n + 1) * PGSIZE]

    # ---- table definitions ------------------------------------------------
    def _tdef_bytes(self, pg: int) -> bytes:
        """TDEF may span pages: continuation chained at offset 4, payload
        of continuation pages starts at offset 8."""
        p = self.page(pg)
        out = bytearray(p)
        nxt = struct.unpack_from("<I", p, 4)[0]
        while nxt:
            p = self.page(nxt)
            out += p[8:]
            nxt = struct.unpack_from("<I", p, 4)[0]
        return bytes(out)

    def table_def(self, pg: int) -> tuple[list[Column], int]:
        """-> (columns, num_rows) for the table whose TDEF is at page pg."""
        t = self._tdef_bytes(pg)
        if t[0] != 0x02:
            raise ValueError(f"page {pg} is not a table definition")
        num_rows = struct.unpack_from("<I", t, 12)[0]
        num_cols = struct.unpack_from("<H", t, 25)[0]
        num_real_idx = struct.unpack_from("<I", t, 31)[0]
        off = 43 + num_real_idx * 8
        cols = []
        for i in range(num_cols):
            r = t[off + i * 18: off + i * 18 + 18]
            ctype = r[0]
            flags = r[13]
            cols.append(Column(
                name="",
                ctype=ctype,
                num=struct.unpack("<H", r[1:3])[0],
                var_idx=struct.unpack("<H", r[3:5])[0],
                fixed_off=struct.unpack("<H", r[14:16])[0],
                size=struct.unpack("<H", r[16:18])[0],
                is_fixed=bool(flags & 0x01),
            ))
        off += num_cols * 18
        for c in cols:
            ln = t[off]
            c.name = t[off + 1: off + 1 + ln].decode(ENC, errors="replace")
            off += 1 + ln
        return cols, num_rows

    # ---- rows ---------------------------------------------------------------
    def _data_pages(self, tdef_pg: int):
        """All data pages owned by tdef_pg. The used-pages map would be the
        authoritative source; an ownership scan is equivalent for these
        write-once shipped databases and much simpler."""
        for n in range(1, self.npages):
            off = n * PGSIZE
            if self.data[off] == 0x01 and \
                    struct.unpack_from("<I", self.data, off + 4)[0] == tdef_pg:
                yield n

    @staticmethod
    def _row_spans(pg: bytes):
        """-> (start, end_exclusive, deleted, overflow) per row slot."""
        nrows = struct.unpack_from("<H", pg, 8)[0]
        offs = [struct.unpack_from("<H", pg, 10 + 2 * i)[0] for i in range(nrows)]
        for i, o in enumerate(offs):
            start = o & 0x1FFF
            end = PGSIZE if i == 0 else (offs[i - 1] & 0x1FFF)
            yield start, end, bool(o & 0x8000), bool(o & 0x4000)

    def _crack_row(self, cols: list[Column], row: bytes) -> dict:
        num_cols = row[0]
        bitmask_sz = (num_cols + 7) // 8
        nullmask = row[len(row) - bitmask_sz:]

        def is_null(colnum):
            byte, bit = divmod(colnum, 8)
            return byte < len(nullmask) and not (nullmask[byte] & (1 << bit))

        var_cols = sorted((c for c in cols if not c.is_fixed and c.num < num_cols),
                          key=lambda c: c.var_idx)
        var_data = {}
        if var_cols:
            row_len = len(row)
            row_var_cols = row[row_len - bitmask_sz - 1]
            num_jumps = (row_len - 1) // 256
            col_ptr = row_len - bitmask_sz - num_jumps - 1
            if (col_ptr - row_var_cols) // 256 < num_jumps:
                num_jumps -= 1
                col_ptr = row_len - bitmask_sz - num_jumps - 1
            # jump table: one byte per 256-byte window, immediately below the
            # var-offset table; entry j = index of first var column whose data
            # starts in window j+1
            jumps = [row[row_len - bitmask_sz - 1 - j] for j in range(1, num_jumps + 1)]
            offsets = []
            for i in range(row_var_cols + 1):
                base = sum(256 for j in jumps if i >= j)
                offsets.append(row[col_ptr - 1 - i] + base)
            for c in var_cols:
                i = c.var_idx
                if i >= row_var_cols:
                    var_data[c.num] = b""
                    continue
                var_data[c.num] = row[offsets[i]:offsets[i + 1]]

        out = {}
        fixed_base = 1
        for c in cols:
            if c.num >= num_cols:      # column added after this row was written
                out[c.name] = None
                continue
            if is_null(c.num) and c.type != T_BOOL:
                out[c.name] = None
                continue
            if c.type == T_BOOL:
                out[c.name] = not is_null(c.num)
            elif c.is_fixed:
                raw = row[fixed_base + c.fixed_off:
                          fixed_base + c.fixed_off + FIXED_SIZE.get(c.type, c.size)]
                out[c.name] = self._scalar(c.type, raw)
            elif c.type == T_TEXT:
                out[c.name] = var_data.get(c.num, b"").decode(ENC, errors="replace")
            else:                      # memo/OLE/binary: not needed here
                out[c.name] = ""
        return out

    @staticmethod
    def _scalar(ctype: int, raw: bytes):
        try:
            if ctype == T_BYTE:
                return raw[0]
            if ctype == T_INT:
                return struct.unpack("<h", raw)[0]
            if ctype == T_LONG:
                return struct.unpack("<i", raw)[0]
            if ctype == T_FLOAT:
                return struct.unpack("<f", raw)[0]
            if ctype in (T_DOUBLE, T_DATE):
                return struct.unpack("<d", raw)[0]
        except struct.error:
            return None
        return raw.hex()

    def read_table_at(self, tdef_pg: int) -> list[dict]:
        cols, _ = self.table_def(tdef_pg)
        rows = []
        for n in self._data_pages(tdef_pg):
            pg = self.page(n)
            for start, end, deleted, overflow in self._row_spans(pg):
                if deleted or overflow or start >= end or end > PGSIZE:
                    continue
                try:
                    rows.append(self._crack_row(cols, pg[start:end]))
                except (IndexError, struct.error):
                    continue
            del pg
        return rows

    # ---- catalog ------------------------------------------------------------
    def catalog(self) -> dict[str, int]:
        """table name -> TDEF page, from MSysObjects (TDEF fixed at page 2)."""
        if self._catalog is None:
            self._catalog = {}
            for r in self.read_table_at(2):
                # Type 1 = table; Id low 3 bytes = TDEF page
                if r.get("Type") == 1 and r.get("Name") and r.get("Id") is not None:
                    self._catalog[r["Name"]] = r["Id"] & 0x00FFFFFF
        return self._catalog

    def read_table(self, name: str) -> list[dict]:
        cat = self.catalog()
        for k, pg in cat.items():
            if k.upper() == name.upper():
                return self.read_table_at(pg)
        raise KeyError(f"table {name!r} not in {sorted(cat)}")


if __name__ == "__main__":
    import sys
    db = JetDb(sys.argv[1])
    if len(sys.argv) == 2:
        for name, pg in sorted(db.catalog().items()):
            print(f"{pg:6d}  {name}")
    else:
        for row in db.read_table(sys.argv[2]):
            print(row)
