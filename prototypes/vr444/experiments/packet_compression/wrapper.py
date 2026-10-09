#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Offline stateless compression experiment. Not a deployed network protocol.

No dictionaries/references. Exact length, bounded allocation and raw escape.
CRC detects accidental corruption; authenticated transport is still required.
"""
import ctypes as C
import ctypes.util
import struct
import zlib

MAX = 16 * 1024 * 1024
HEADER = struct.Struct('<4sBBHIIII')
BLOCK = struct.Struct('<B3xII')


class Native:
    def __init__(self):
        self.lz4 = C.CDLL(ctypes.util.find_library('lz4'))
        self.zstd = C.CDLL(ctypes.util.find_library('zstd'))
        def bind(lib, name, result, args):
            fn = getattr(lib, name); fn.restype = result; fn.argtypes = args
        bind(self.lz4, 'LZ4_compressBound', C.c_int, [C.c_int])
        bind(self.lz4, 'LZ4_compress_default', C.c_int, [C.c_void_p, C.c_void_p, C.c_int, C.c_int])
        bind(self.lz4, 'LZ4_decompress_safe', C.c_int, [C.c_void_p, C.c_void_p, C.c_int, C.c_int])
        bind(self.lz4, 'LZ4_versionString', C.c_char_p, [])
        bind(self.zstd, 'ZSTD_compressBound', C.c_size_t, [C.c_size_t])
        bind(self.zstd, 'ZSTD_compress', C.c_size_t, [C.c_void_p, C.c_size_t, C.c_void_p, C.c_size_t, C.c_int])
        bind(self.zstd, 'ZSTD_decompress', C.c_size_t, [C.c_void_p, C.c_size_t, C.c_void_p, C.c_size_t])
        bind(self.zstd, 'ZSTD_isError', C.c_uint, [C.c_size_t])
        bind(self.zstd, 'ZSTD_getFrameContentSize', C.c_ulonglong, [C.c_void_p, C.c_size_t])
        bind(self.zstd, 'ZSTD_findFrameCompressedSize', C.c_size_t, [C.c_void_p, C.c_size_t])
        bind(self.zstd, 'ZSTD_versionString', C.c_char_p, [])

    def compress(self, data, method, level):
        if method == 1:
            cap = self.lz4.LZ4_compressBound(len(data)); out = C.create_string_buffer(cap)
            n = self.lz4.LZ4_compress_default(data, out, len(data), cap)
            if n <= 0: raise ValueError('LZ4 compression failed')
        else:
            cap = self.zstd.ZSTD_compressBound(len(data)); out = C.create_string_buffer(cap)
            n = self.zstd.ZSTD_compress(out, cap, data, len(data), level)
            if self.zstd.ZSTD_isError(n): raise ValueError('zstd compression failed')
        return out.raw[:n]

    def decompress(self, data, method, length):
        if not 1 <= length <= MAX: raise ValueError('decoded size bound')
        if method == 2:
            if self.zstd.ZSTD_getFrameContentSize(data, len(data)) != length:
                raise ValueError('zstd decoded length mismatch')
            if self.zstd.ZSTD_findFrameCompressedSize(data, len(data)) != len(data):
                raise ValueError('zstd trailing or invalid frame')
        out = C.create_string_buffer(length)
        if method == 1:
            n = self.lz4.LZ4_decompress_safe(data, out, len(data), length)
        else:
            n = self.zstd.ZSTD_decompress(out, length, data, len(data))
        if n != length: raise ValueError('native decode error or length mismatch')
        return out.raw


def encode(data, native, method=1, block_size=MAX, level=1):
    if not 1 <= len(data) <= MAX or method not in (1, 2) or not 256 <= block_size <= MAX:
        raise ValueError('invalid compression input')
    if level not in (1, 3): raise ValueError('unsupported experimental level')
    blocks = []
    for at in range(0, len(data), block_size):
        raw = data[at:at+block_size]; packed = native.compress(raw, method, level)
        compressed = len(packed) < len(raw)
        body = packed if compressed else raw
        blocks.append(BLOCK.pack(int(compressed), len(raw), len(body)) + body)
    body = b''.join(blocks)
    crc = zlib.crc32(data)
    if len(body) >= len(data):
        return HEADER.pack(b'WPC1', 1, 0, 0, len(data), 0, 0, crc) + data
    return HEADER.pack(b'WPC1', 1, method, 0, len(data), block_size, len(blocks), crc) + body


def decode(wire, native):
    if not HEADER.size < len(wire) <= MAX + HEADER.size: raise ValueError('wire bound')
    magic, version, method, flags, total, block_size, count, crc = HEADER.unpack_from(wire)
    if magic != b'WPC1' or version != 1 or flags or not 1 <= total <= MAX:
        raise ValueError('invalid wrapper')
    if method == 0:
        if block_size or count or len(wire) != HEADER.size + total: raise ValueError('raw framing')
        output = wire[HEADER.size:]
    else:
        if method not in (1, 2) or not 256 <= block_size <= MAX or count != (total+block_size-1)//block_size:
            raise ValueError('block count or codec')
        # Validate every record before any native decompression/allocation.
        records = []; at = HEADER.size; remaining = total
        for _ in range(count):
            if at + BLOCK.size > len(wire): raise ValueError('truncated record')
            compressed, raw_len, packed_len = BLOCK.unpack_from(wire, at)
            if wire[at+1:at+4] != b'\0\0\0': raise ValueError('record reserved bits')
            at += BLOCK.size
            if compressed not in (0, 1) or raw_len != min(block_size, remaining): raise ValueError('record size')
            if not 1 <= packed_len <= raw_len or (compressed == 0 and packed_len != raw_len):
                raise ValueError('stored size')
            if at + packed_len > len(wire): raise ValueError('truncated block')
            records.append((compressed, raw_len, at, packed_len)); at += packed_len; remaining -= raw_len
        if remaining or at != len(wire): raise ValueError('trailing data')
        output = bytearray()
        for compressed, raw_len, at, packed_len in records:
            data = wire[at:at+packed_len]
            output.extend(native.decompress(data, method, raw_len) if compressed else data)
        output = bytes(output)
    if zlib.crc32(output) != crc: raise ValueError('content checksum')
    return output
