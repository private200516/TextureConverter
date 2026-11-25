"""Local file boundaries. Windows handles pin checked names during an operation.

These checks protect ordinary asset I/O; they are not an OS sandbox.
"""
from __future__ import annotations
from contextlib import ExitStack, contextmanager
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
            part.split('.', 1)[0].rstrip(' ').upper() in _DEVICES for part in parts)):
        raise ValueError('Unsafe relative asset path: ' + value[:160])
    return PurePosixPath(*parts).as_posix()


def absolute_path(value):
    text = os.fspath(value)
    # Reject Win32 device, extended namespace, UNC and ADS before Path normalizes.
    if '\0' in text or text.replace('\\', '/').startswith('//'):
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


if os.name == 'nt':
    import ctypes
    from ctypes import wintypes

    class _FileInfo(ctypes.Structure):
        _fields_ = [('attributes', wintypes.DWORD), ('creation', wintypes.FILETIME),
                    ('access', wintypes.FILETIME), ('write', wintypes.FILETIME),
                    ('volume', wintypes.DWORD), ('size_high', wintypes.DWORD),
                    ('size_low', wintypes.DWORD), ('links', wintypes.DWORD),
                    ('index_high', wintypes.DWORD), ('index_low', wintypes.DWORD)]

    _kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    _create = _kernel.CreateFileW
    _create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                        wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _create.restype = wintypes.HANDLE
    _inspect = _kernel.GetFileInformationByHandle
    _inspect.argtypes = [wintypes.HANDLE, ctypes.POINTER(_FileInfo)]
    _close = _kernel.CloseHandle
    _close.argtypes = [wintypes.HANDLE]
    _INVALID_HANDLE = wintypes.HANDLE(-1).value
    _mutex = _kernel.CreateMutexW
    _mutex.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    _mutex.restype = wintypes.HANDLE
    _wait = _kernel.WaitForSingleObject
    _wait.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _release = _kernel.ReleaseMutex
    _release.argtypes = [wintypes.HANDLE]


@contextmanager
def publication_lock(directory, check_cancel):
    # Serialize the check/publish/rollback interval across converter instances.
    # A process exit releases the OS lock without leaving a file in the game tree.
    import time
    deadline = time.monotonic() + 60
    def check_wait():
        check_cancel()
        if time.monotonic() >= deadline:
            raise TimeoutError('Another export is publishing to this directory. Retry when it finishes.')
    if os.name == 'nt':
        import hashlib
        key = hashlib.sha256(os.fsencode(os.path.normcase(os.path.realpath(directory)))).hexdigest()
        handle = _mutex(None, False, 'Local\\FO76TexturePublish-' + key)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            while True:
                check_wait()
                result = _wait(handle, 200)
                if result in (0, 0x80):  # acquired, including an abandoned owner
                    break
                if result != 0x102:
                    raise ctypes.WinError(ctypes.get_last_error())
            try:
                yield
            finally:
                _release(handle)
        finally:
            _close(handle)
    else:
        import fcntl
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            while True:
                check_wait()
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    time.sleep(.1)
            yield
        finally:
            os.close(descriptor)


@contextmanager
def pinned_path(value, *, must_exist=False, expect_directory=False):
    """Pin each Windows component before inspecting the next component."""
    if os.name != 'nt':
        path = assert_no_reparse(value)
        if must_exist or path.exists():
            mode=path.stat().st_mode
            if expect_directory and not stat.S_ISDIR(mode):
                raise NotADirectoryError(path)
        yield path
        assert_no_reparse(path)
        return

    path = absolute_path(value)
    handles = []
    try:
        for component in reversed((path, *path.parents)):
            directory = True
            if component == path and not expect_directory:
                try:
                    mode = component.lstat().st_mode
                except FileNotFoundError:
                    if must_exist: raise
                    directory = False
                else:
                    directory = stat.S_ISDIR(mode)
            # OPEN_REPARSE_POINT inspects the entry itself. Never share-delete;
            # permit share-write only for directories, so source files stay stable.
            handle = _create(str(component), 0x80000000, 3 if directory else 1,
                             None, 3, 0x02200000, None)
            if handle == _INVALID_HANDLE:
                error = ctypes.get_last_error()
                if not must_exist and error in (2, 3): break
                raise ctypes.WinError(error)
            handles.append(handle)
            info = _FileInfo()
            if not _inspect(handle, ctypes.byref(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            if info.attributes & 0x400:
                raise ValueError('Reparse/symlink asset paths are not supported')
            if bool(info.attributes & 0x10) != directory:
                raise ValueError('Asset path type changed while opening')
        yield path
    finally:
        for handle in reversed(handles):
            _close(handle)


@contextmanager
def export_directory(value, *, fresh=False):
    # Pin the nearest existing directory and create each remaining component
    # under a checked parent. Cooperating processes can race to create it.
    path = absolute_path(value)
    missing = []
    current = path.parent if fresh else path
    with ExitStack() as pins:
        while True:
            try:
                pins.enter_context(pinned_path(current, must_exist=True, expect_directory=True))
                break
            except FileNotFoundError:
                if current == current.parent:
                    raise
                missing.append(current)
                current = current.parent
        for component in reversed(missing):
            component.mkdir(exist_ok=True)
            pins.enter_context(pinned_path(component, must_exist=True, expect_directory=True))
        if fresh:
            path.mkdir()
            pins.enter_context(pinned_path(path, must_exist=True, expect_directory=True))
        yield path


@contextmanager
def open_read(value):
    with pinned_path(value, must_exist=True) as path, path.open('rb') as stream:
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


def json_bounded(text, limit=MAX_JSON_BYTES, *, max_depth=64):
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
            if depth > max_depth: raise ValueError(f'JSON nesting exceeds {max_depth} levels')
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
