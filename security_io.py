"""Local file boundaries. Windows handles pin checked names during an operation.

These checks protect ordinary asset I/O; they are not an OS sandbox.
"""
from __future__ import annotations
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path, PurePosixPath
import stat
import struct

MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_ASSET_BYTES = 512 * 1024 * 1024
_DEVICES = {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$'} | {
    prefix + digit for prefix in ('COM', 'LPT') for digit in '123456789¹²³'}


def safe_relative(value):
    value = str(value).replace('\\', '/')
    parts = value.split('/')
    if (not value or len(value) > 32760 or any(not part or part in ('.', '..') or
            part[-1:] in (' ', '.') or any(ord(c) < 32 or c in ':<>"|?*' for c in part) or
            part.split('.', 1)[0].upper() in _DEVICES for part in parts)):
        raise ValueError('Unsafe relative asset path: ' + value[:160])
    return PurePosixPath(*parts).as_posix()


def absolute_path(value):
    text = os.fspath(value)
    # Reject Win32 device, extended namespace, UNC and ADS before Path normalizes.
    if '\0' in text or text.startswith(('\\\\', '//')):
        raise ValueError('Device/UNC paths are not supported')
    path = Path(os.path.abspath(text))
    if path.parts[1:]: safe_relative('/'.join(path.parts[1:]))
    return path


def assert_no_reparse(value):
    path = absolute_path(value)
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Reparse/symlink asset paths are not supported: ' + str(part))
    return path


@contextmanager
def pinned_path(value):
    """Reject links and hold existing Windows components against replacement.

    FILE_SHARE_DELETE is deliberately absent. Check the opened handle itself,
    so a rename/reparse race before CreateFile cannot turn into a followed link.
    Non-Windows callers still get lstat checks and exclusive publication.
    """
    path = assert_no_reparse(value)
    handles = []
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                           wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        create.restype = wintypes.HANDLE
        close = kernel.CloseHandle
        close.argtypes = [wintypes.HANDLE]
        class FileInfo(ctypes.Structure):
            _fields_ = [('attributes', wintypes.DWORD), ('creation', wintypes.FILETIME),
                       ('access', wintypes.FILETIME), ('write', wintypes.FILETIME),
                       ('volume', wintypes.DWORD), ('size_high', wintypes.DWORD),
                       ('size_low', wintypes.DWORD), ('links', wintypes.DWORD),
                       ('index_high', wintypes.DWORD), ('index_low', wintypes.DWORD)]
        inspect = kernel.GetFileInformationByHandle
        inspect.argtypes = [wintypes.HANDLE, ctypes.POINTER(FileInfo)]
        try:
            for component in reversed((path, *path.parents)):
                # Directory share-write permits ordinary child rename/create;
                # no share-delete still pins the directory's own name. Files
                # additionally exclude writers for coherent header/payload reads.
                directory=component.is_dir()
                handle = create(str(component), 0x80000000, 3 if directory else 1, None, 3, 0x02200000, None)
                if handle == wintypes.HANDLE(-1).value:
                    error = ctypes.get_last_error()
                    if error in (2, 3):
                        break
                    raise ctypes.WinError(error)
                handles.append(handle)
                info = FileInfo()
                if not inspect(handle, ctypes.byref(info)):
                    raise ctypes.WinError(ctypes.get_last_error())
                if info.attributes & 0x400:
                    raise ValueError('Reparse/symlink asset paths are not supported')
                if bool(info.attributes & 0x10)!=directory:
                    raise ValueError('Asset path type changed while opening')
            yield path
        finally:
            for handle in reversed(handles):
                close(handle)
    else:
        yield path
        assert_no_reparse(path)


@contextmanager
def open_read(value):
    with pinned_path(value) as path, path.open('rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('A regular local file is required')
        yield stream


def read_bounded(value, limit, label='Input'):
    with open_read(value) as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError(label + ' exceeds ' + str(limit) + ' byte limit')
    return data


def png_header(stream, shape=None):
    """Check fixed PNG IHDR before Pillow examines any ancillary chunks."""
    stream.seek(0);header=stream.read(33);stream.seek(0)
    if len(header)!=33 or header[:16]!=b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR':
        raise ValueError('Import a grayscale PNG mask with a valid PNG header')
    width,height=struct.unpack_from('>II',header,16)
    if not 0<width<=16384 or not 0<height<=16384 or width*height>64*1024*1024:
        raise ValueError('PNG dimensions exceed 64 megapixel budget')
    if shape is not None and (height,width)!=tuple(shape):
        raise ValueError('MASK_SIZE_MISMATCH: masks must match source diffuse dimensions; no silent resize')
    return width,height


def json_bounded(text, limit=MAX_JSON_BYTES):
    if not isinstance(text, (str, bytes)) or len(text) > limit:
        raise ValueError('JSON exceeds input byte limit')
    if isinstance(text, str) and len(text.encode('utf-8')) > limit:
        raise ValueError('JSON exceeds input byte limit')
    # Bound nesting before the JSON decoder builds a deeply nested object.
    depth = 0; quoted = escaped = False
    for char in text.decode('utf-8-sig') if isinstance(text, bytes) else text:
        if quoted:
            if escaped: escaped = False
            elif char == '\\': escaped = True
            elif char == '"': quoted = False
        elif char == '"': quoted = True
        elif char in '[{':
            depth += 1
            if depth > 64: raise ValueError('JSON nesting exceeds 64 levels')
        elif char in ']}': depth -= 1
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: raise ValueError('Duplicate JSON property: ' + key[:160])
            result[key] = value
        return result
    def constant(value): raise ValueError('Non-finite JSON number: ' + value)
    def number(value):
        result=float(value)
        if not math.isfinite(result):raise ValueError('Non-finite JSON number')
        return result
    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant, parse_float=number)


def publish_new(source, target):
    """Publish without overwriting a destination created after preflight."""
    with pinned_path(Path(target).parent), pinned_path(target):
        if Path(target).exists(): raise FileExistsError(target)
        if os.name == 'nt':
            Path(source).rename(target)  # Windows rename is exclusive.
        else:
            os.link(source, target, follow_symlinks=False)
            Path(source).unlink()
