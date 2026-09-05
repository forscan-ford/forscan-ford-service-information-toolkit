#!/usr/bin/env python3
"""
IDICOMP decoder for archive member payloads.

Entry payload layout (the bytes stored for one archive member):
  [9]  signature  b"\\x01IDICOMP\\x01"   (else: not IDICOMP -> caller stores raw)
  then a sequence of blocks, each:
    [2]  int16 length (little-endian, signed)
         == 0  -> end of stream
         <  0  -> STORED block: next |length| bytes are literal output
         >  0  -> COMPRESSED block: next `length` bytes are an LZSS stream,
                  decoded independently (back-references are block-local).

LZSS stream, decoded into a fresh per-block buffer:
  Maintain a 16-bit flag word, consumed MSB-first (0x8000 down). Refill by
  reading 2 bytes (little-endian) when exhausted.
    flag bit == 0 -> copy 1 literal byte
    flag bit == 1 -> read command byte C; hi = C>>4, lo = C & 0xF
        hi == 0 : RLE   value=next byte;         count = lo + 3
        hi == 1 : RLE   B2=next, value=next+1;   count = lo + B2*16 + 0x13
        hi == 2 : COPY  B2=next, len=next+1+0x10; dist = lo + B2*16 + 3
        hi >= 3 : COPY  B2=next; len = hi;        dist = lo + B2*16 + 3
  COPY is an overlapping byte-wise copy from (output_end - dist).
"""
from __future__ import annotations

SIG = b"\x01IDICOMP\x01"
MAX_BLOCK = 0x4000  # matches the 16 KB work buffers in the DLL


def is_idicomp(data: bytes) -> bool:
    return len(data) >= 9 and data[:9] == SIG


def _decompress_block(src: bytes) -> bytearray:
    """Decode one LZSS-compressed block into a fresh buffer."""
    out = bytearray()
    i = 0
    n = len(src)
    flags = 0
    mask = 0
    while i < n:
        mask >>= 1
        if mask == 0:
            if i + 2 > n:
                break
            flags = src[i] | (src[i + 1] << 8)
            i += 2
            mask = 0x8000
            if i >= n:
                break
        if (flags & mask) == 0:
            out.append(src[i])
            i += 1
        else:
            c = src[i]
            hi = c >> 4
            lo = c & 0xF
            if hi == 0:
                val = src[i + 1]
                out.extend(bytes([val]) * (lo + 3))
                i += 2
            elif hi == 1:
                b2 = src[i + 1]
                val = src[i + 2]
                out.extend(bytes([val]) * (lo + b2 * 16 + 0x13))
                i += 3
            elif hi == 2:
                b2 = src[i + 1]
                length = src[i + 2] + 0x10
                dist = lo + b2 * 16 + 3
                start = len(out) - dist
                if start < 0:
                    raise ValueError("back-reference before buffer start (decode desync)")
                for k in range(length):
                    out.append(out[start + k])
                i += 3
            else:  # hi >= 3
                b2 = src[i + 1]
                length = hi
                dist = lo + b2 * 16 + 3
                start = len(out) - dist
                if start < 0:
                    raise ValueError("back-reference before buffer start (decode desync)")
                for k in range(length):
                    out.append(out[start + k])
                i += 2
    return out


def decode(data: bytes) -> bytes | None:
    """Decode a full IDICOMP entry payload. Returns None if not IDICOMP."""
    if not is_idicomp(data):
        return None
    pos = 9
    out = bytearray()
    n = len(data)
    while pos + 2 <= n:
        length = int.from_bytes(data[pos:pos + 2], "little", signed=True)
        pos += 2
        if length == 0:
            break
        if length < 0:
            count = -length
            out.extend(data[pos:pos + count])
            pos += count
        else:
            block = data[pos:pos + length]
            pos += length
            out.extend(_decompress_block(block))
    return bytes(out)


if __name__ == "__main__":
    import sys
    raw = open(sys.argv[1], "rb").read()
    res = decode(raw)
    if res is None:
        print("not IDICOMP")
    else:
        sys.stdout.buffer.write(res)
