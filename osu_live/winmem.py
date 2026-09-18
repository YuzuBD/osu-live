"""Windows process memory access: handle, typed reads, AOB pattern scans.

Only reads. Uses ctypes so no compiler or extra dependency is required.
"""

from __future__ import annotations

import ctypes
import struct
from ctypes import wintypes
from dataclasses import dataclass
from typing import Iterator, Sequence

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010

MEM_COMMIT = 0x1000
PAGE_READWRITE = 0x04
PAGE_EXECUTE_READWRITE = 0x40

TH32CS_SNAPPROCESS = 0x00000002
MAX_PATH = 260


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_char * MAX_PATH),
    ]


kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
kernel32.ReadProcessMemory.restype = wintypes.BOOL
kernel32.ReadProcessMemory.argtypes = (
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
)
kernel32.VirtualQueryEx.restype = ctypes.c_size_t
kernel32.VirtualQueryEx.argtypes = (
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.POINTER(MEMORY_BASIC_INFORMATION),
    ctypes.c_size_t,
)
kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
kernel32.Process32First.argtypes = (wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32))
kernel32.Process32Next.argtypes = (wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32))
kernel32.IsWow64Process.restype = wintypes.BOOL
kernel32.IsWow64Process.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL))


class MemoryError_(RuntimeError):
    """Raised when a read or scan cannot be satisfied."""


@dataclass(frozen=True)
class Pattern:
    """Byte signature where ``None`` marks a wildcard nibble."""

    bytes_: bytes
    mask: bytes

    @classmethod
    def parse(cls, signature: str) -> "Pattern":
        parts = signature.split()
        raw = bytearray()
        mask = bytearray()
        for part in parts:
            if part in ("??", "?"):
                raw.append(0)
                mask.append(0)
            else:
                raw.append(int(part, 16))
                mask.append(0xFF)
        return cls(bytes(raw), bytes(mask))

    def find_in(self, data: bytes, start: int = 0) -> int:
        """First offset of the pattern in ``data``, or -1."""
        needle, mask = self.bytes_, self.mask
        if not any(m == 0 for m in mask):
            return data.find(needle, start)
        # Wildcards: anchor on the longest run of exact bytes, then verify.
        anchor_start, anchor_len = 0, 0
        run_start = 0
        for i, m in enumerate(mask):
            if m:
                if i == run_start:
                    run_start = i
                if i - run_start + 1 > anchor_len:
                    anchor_start, anchor_len = run_start, i - run_start + 1
            else:
                run_start = i + 1
        anchor = needle[anchor_start : anchor_start + anchor_len]
        idx = data.find(anchor, max(start, 0) + anchor_start) - anchor_start
        size = len(needle)
        while idx != -1 and start >= 0:
            if idx >= start and idx + size <= len(data):
                chunk = data[idx : idx + size]
                if all(not m or chunk[i] == needle[i] for i, m in enumerate(mask)):
                    return idx
            nxt = data.find(anchor, idx + anchor_start + 1)
            if nxt == -1:
                return -1
            idx = nxt - anchor_start
        return -1


def list_processes(names: Sequence[str]) -> list[tuple[int, str]]:
    """(pid, exe name) for every process whose name matches ``names`` (ci)."""
    wanted = {n.lower() for n in names}
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == wintypes.HANDLE(-1).value:
        raise MemoryError_("CreateToolhelp32Snapshot failed")
    entry = PROCESSENTRY32()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
    found: list[tuple[int, str]] = []
    try:
        ok = kernel32.Process32First(snapshot, ctypes.byref(entry))
        while ok:
            name = entry.szExeFile.decode("mbcs", errors="replace")
            if name.lower() in wanted:
                found.append((entry.th32ProcessID, name))
            ok = kernel32.Process32Next(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return found


class Process:
    """A read-only handle onto another process, with typed accessors."""

    def __init__(self, pid: int):
        self.pid = pid
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid
        )
        if not handle:
            raise MemoryError_(
                f"OpenProcess({pid}) failed: {ctypes.get_last_error()}"
            )
        self.handle = handle
        wow64 = wintypes.BOOL()
        kernel32.IsWow64Process(handle, ctypes.byref(wow64))
        # A 32-bit target (osu! stable) reports wow64 == True on a 64-bit OS.
        self.bitness = 32 if wow64.value else (
            32 if ctypes.sizeof(ctypes.c_void_p) == 4 else 64
        )
        self.ptr_size = self.bitness // 8

    def close(self) -> None:
        if self.handle:
            kernel32.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self) -> "Process":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- raw -----------------------------------------------------------------

    def read(self, address: int, size: int) -> bytes:
        buf = ctypes.create_string_buffer(size)
        read = ctypes.c_size_t()
        ok = kernel32.ReadProcessMemory(
            self.handle, ctypes.c_void_p(address), buf, size, ctypes.byref(read)
        )
        if not ok or read.value != size:
            raise MemoryError_(
                f"ReadProcessMemory({address:#x}, {size}) failed: "
                f"{ctypes.get_last_error()}"
            )
        return buf.raw[:size]

    def try_read(self, address: int, size: int) -> bytes | None:
        try:
            return self.read(address, size)
        except MemoryError_:
            return None

    # -- typed ---------------------------------------------------------------

    def read_u8(self, address: int) -> int:
        return self.read(address, 1)[0]

    def read_i16(self, address: int) -> int:
        return struct.unpack("<h", self.read(address, 2))[0]

    def read_i32(self, address: int) -> int:
        return struct.unpack("<i", self.read(address, 4))[0]

    def read_u32(self, address: int) -> int:
        return struct.unpack("<I", self.read(address, 4))[0]

    def read_i64(self, address: int) -> int:
        return struct.unpack("<q", self.read(address, 8))[0]

    def read_f32(self, address: int) -> float:
        return struct.unpack("<f", self.read(address, 4))[0]

    def read_f64(self, address: int) -> float:
        return struct.unpack("<d", self.read(address, 8))[0]

    def read_ptr(self, address: int) -> int:
        if self.ptr_size == 4:
            return self.read_u32(address)
        return struct.unpack("<Q", self.read(address, 8))[0]

    def read_ptr_ptr(self, address: int) -> int:
        """Double dereference, matching tosu's ``readPointer``."""
        return self.read_ptr(self.read_ptr(address))

    def read_csharp_string(self, address: int) -> str:
        """.NET ``System.String``: [vtable][length:i32][utf-16 chars]."""
        if address == 0:
            return ""
        length = self.read_i32(address + 0x4)
        if length < 0 or length > 512:
            raise MemoryError_(f"implausible C# string length {length}")
        if length == 0:
            return ""
        return self.read(address + 0x8, length * 2).decode("utf-16-le", "replace")

    def read_csharp_string_ptr(self, address: int) -> str:
        return self.read_csharp_string(self.read_ptr(address))

    # -- region walking ------------------------------------------------------

    def regions(self) -> Iterator[tuple[int, int]]:
        """(base, size) for committed regions we are allowed to read.

        Matches tosu's native scanner, which only walks
        ``PAGE_READWRITE | PAGE_EXECUTE_READWRITE`` commits. osu!'s .NET types
        live in those regions; widening this only finds garbage in images.
        """
        mbi = MEMORY_BASIC_INFORMATION()
        address = 0
        limit = 0x7FFFFFFF if self.bitness == 32 else 0x7FFFFFFFFFFF
        while address < limit:
            written = kernel32.VirtualQueryEx(
                self.handle,
                ctypes.c_void_p(address),
                ctypes.byref(mbi),
                ctypes.sizeof(mbi),
            )
            if not written:
                break
            base = mbi.BaseAddress or 0
            size = mbi.RegionSize
            if mbi.State == MEM_COMMIT and (
                mbi.Protect & (PAGE_READWRITE | PAGE_EXECUTE_READWRITE)
            ):
                yield base, size
            nxt = base + size
            if nxt <= address:
                break
            address = nxt

    def scan(self, pattern: str | bytes | Pattern, max_results: int = 1) -> list[int]:
        """All matches of ``pattern`` in readable memory (addresses)."""
        if isinstance(pattern, Pattern):
            pat = pattern
        elif isinstance(pattern, bytes):
            pat = Pattern(pattern, b"\xff" * len(pattern))
        else:
            pat = Pattern.parse(pattern)
        size = len(pat.bytes_)
        overlap = size - 1
        results: list[int] = []
        for base, region_size in self.regions():
            offset = 0
            carry = b""
            while offset < region_size:
                chunk_size = min(1 << 20, region_size - offset)
                data = self.try_read(base + offset, chunk_size)
                if data is None:
                    # Unreadable page inside a readable region: step over it.
                    carry = b""
                    offset += chunk_size
                    continue
                haystack = carry + data
                start = 0
                while True:
                    hit = pat.find_in(haystack, start)
                    if hit == -1:
                        break
                    address = base + offset - len(carry) + hit
                    results.append(address)
                    if len(results) >= max_results:
                        return results
                    start = hit + 1
                carry = haystack[-overlap:] if overlap else b""
                offset += chunk_size
        return results

    def scan_one(self, pattern: str | Pattern) -> int:
        found = self.scan(pattern, max_results=1)
        if not found:
            raise MemoryError_(f"signature not found: {pattern}")
        return found[0]
