# -*- coding: utf-8 -*-
"""
dds.py — DDS 解析 / 解码 / 编码核心

完全本地实现：
- DDS 头部解析（legacy FourCC + DX10 扩展头）
- 纯 NumPy 解码器：BC1 / BC2 / BC3 / BC4 / BC5（含 SNORM 变体）/ 未压缩 RGBA·BGRA
- BC7 等其余格式经由 Pillow 解码
- 编码优先走内置打包的 texconv.exe（DirectXTex，支持 BC7/BC5/BC3/BC1 + 完整 Mipmap）
- texconv 不可用时的后备编码器：BC1 / BC3 / BC5 / 未压缩 BGRA8（自带 Mipmap 链生成）

不做任何网络访问。
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field

import numpy as np

from i18n import tr

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

# ---------------------------------------------------------------------------
# 格式常量
# ---------------------------------------------------------------------------

FOURCC_TO_FORMAT = {
    b"DXT1": "BC1_UNORM",
    b"DXT3": "BC2_UNORM",
    b"DXT5": "BC3_UNORM",
    b"ATI1": "BC4_UNORM",
    b"BC4U": "BC4_UNORM",
    b"BC4S": "BC4_SNORM",
    b"ATI2": "BC5_UNORM",
    b"BC5U": "BC5_UNORM",
    b"BC5S": "BC5_SNORM",
}

DXGI_TO_FORMAT = {
    2: "R32G32B32A32_FLOAT",
    28: "R8G8B8A8_UNORM",
    29: "R8G8B8A8_UNORM_SRGB",
    61: "R8_UNORM",
    71: "BC1_UNORM",
    72: "BC1_UNORM_SRGB",
    74: "BC2_UNORM",
    75: "BC2_UNORM_SRGB",
    77: "BC3_UNORM",
    78: "BC3_UNORM_SRGB",
    80: "BC4_UNORM",
    81: "BC4_SNORM",
    83: "BC5_UNORM",
    84: "BC5_SNORM",
    87: "B8G8R8A8_UNORM",
    91: "B8G8R8A8_UNORM_SRGB",
    95: "BC6H_UF16",
    96: "BC6H_SF16",
    98: "BC7_UNORM",
    99: "BC7_UNORM_SRGB",
}

FORMAT_TO_DXGI = {v: k for k, v in DXGI_TO_FORMAT.items()}

SNORM_FORMATS = {"BC4_SNORM", "BC5_SNORM"}

# 本模块自带解码器覆盖的格式（其余交给 Pillow）
OWN_DECODERS = {
    "BC1_UNORM", "BC1_UNORM_SRGB",
    "BC2_UNORM", "BC2_UNORM_SRGB",
    "BC3_UNORM", "BC3_UNORM_SRGB",
    "BC4_UNORM", "BC4_SNORM",
    "BC5_UNORM", "BC5_SNORM",
    "R8G8B8A8_UNORM", "R8G8B8A8_UNORM_SRGB",
    "B8G8R8A8_UNORM", "B8G8R8A8_UNORM_SRGB", "B8G8R8_UNORM", "R8_UNORM",
}

_UNCOMPRESSED_BYTES = {
    "R8G8B8A8_UNORM": 4, "R8G8B8A8_UNORM_SRGB": 4,
    "B8G8R8A8_UNORM": 4, "B8G8R8A8_UNORM_SRGB": 4,
    "B8G8R8_UNORM": 3, "R8_UNORM": 1,
}


class DDSError(Exception):
    """DDS 读取 / 写入失败。"""


class CancellationRequested(Exception):
    """Cooperative cancellation; never a codec error or a fallback request."""


class EncoderCleanupError(DDSError):
    """An owned encoder could not be reaped; do not start another codec."""


def _check_cancel(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise CancellationRequested("Cancelled before group commit")


def _read_bytes(stream, size, cancel_event=None):
    chunks = []
    while size:
        _check_cancel(cancel_event)
        chunk = stream.read(min(size, 1024 * 1024))
        if not chunk:
            break
        chunks.append(chunk)
        size -= len(chunk)
    _check_cancel(cancel_event)
    return b"".join(chunks)


def _write_bytes(stream, data, cancel_event=None):
    view = memoryview(data)
    for offset in range(0, len(view), 1024 * 1024):
        _check_cancel(cancel_event)
        stream.write(view[offset:offset + 1024 * 1024])
    _check_cancel(cancel_event)


def _encoder_exited(proc):
    try:
        return proc.poll() is not None
    except OSError as exc:
        raise EncoderCleanupError("Could not inspect the owned texconv process") from exc


def _stop_encoder(proc):
    """Stop and reap only the process created by this encoding operation."""
    if _encoder_exited(proc):
        return
    try:
        proc.terminate()
    except ProcessLookupError:
        pass
    except OSError as exc:
        if _encoder_exited(proc):
            return
        raise EncoderCleanupError("Could not stop the owned texconv process") from exc
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        except OSError as exc:
            if _encoder_exited(proc):
                return
            raise EncoderCleanupError("Could not kill the owned texconv process") from exc
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired as exc:
            raise EncoderCleanupError("Owned texconv process did not exit after cancellation") from exc
        except OSError as exc:
            if _encoder_exited(proc):
                return
            raise EncoderCleanupError("Could not reap the owned texconv process") from exc
    except OSError as exc:
        if _encoder_exited(proc):
            return
        raise EncoderCleanupError("Could not reap the owned texconv process") from exc


def _run_texconv(command, cancel_event=None, timeout=600):
    """Wait in short intervals without pipe deadlocks or unbounded diagnostics."""
    _check_cancel(cancel_event)
    # Separate files let the encoder write freely while wait() polls. The
    # bounded size check and bounded readback keep diagnostics off the heap.
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stdout,
                                stderr=stderr, shell=False,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            deadline = time.monotonic() + timeout
            def check_diagnostics():
                if any(os.fstat(stream.fileno()).st_size > 4 * 1024 * 1024
                       for stream in (stdout, stderr)):
                    raise DDSError("texconv diagnostic output exceeded its limit")
            while True:
                _check_cancel(cancel_event)
                check_diagnostics()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise DDSError("texconv timed out")
                try:
                    proc.wait(timeout=min(.1, remaining))
                    break
                except subprocess.TimeoutExpired:
                    continue
            _check_cancel(cancel_event)
            check_diagnostics()
            def diagnostic(stream):
                stream.seek(0, os.SEEK_END)
                stream.seek(max(0, stream.tell() - 65536))
                return stream.read(65536).decode("utf-8", errors="replace")
            return subprocess.CompletedProcess(command, proc.returncode,
                                               diagnostic(stdout), diagnostic(stderr))
        finally:
            _stop_encoder(proc)


@dataclass
class DdsInfo:
    width: int = 0
    height: int = 0
    mipmaps: int = 1
    fmt: str = "UNKNOWN"
    dx10: bool = False
    is_snorm: bool = False
    data_offset: int = 148  # 128 header + 20 dx10（默认按 DX10，解析时修正）
    row_pitch: int = 0  # Explicit DDSD_PITCH for ordinary byte formats only.
    opaque_alpha: bool = False  # Legacy RGBX padding is not an alpha channel.

    @property
    def is_bc7(self) -> bool:
        return self.fmt.startswith("BC7")

    @property
    def is_bc(self) -> bool:
        return self.fmt.startswith("BC")


# ---------------------------------------------------------------------------
# 头部解析
# ---------------------------------------------------------------------------

def parse_dds_header(path: str) -> DdsInfo:
    """解析 DDS 头部，返回 DdsInfo。不读取像素数据。"""
    with open(path, "rb") as f:
        magic = f.read(4)
        if magic != b"DDS ":
            raise DDSError(f"不是 DDS 文件: {path}")
        header = f.read(124)
        if len(header) != 124:
            raise DDSError(f"DDS 头长度不足: {path}")
        # DDS_HEADER: size, flags, height, width, pitch/linear, depth, mipmap,
        #             reserved[11], pf(32B), caps, caps2, caps3, caps4, reserved2
        fields = struct.unpack("<31I", header)
        if fields[0] != 124 or fields[18] != 32:
            raise DDSError(f"无效的 DDS 头大小: {path}")
        height, width, mipmap = fields[2], fields[3], fields[6]
        pf_flags = fields[19]
        pf_fourcc = struct.pack("<I", fields[20])
        pf_rgbbitcount = fields[21]

        info = DdsInfo(width=width, height=height, mipmaps=max(1, mipmap))

        if pf_flags & 0x4:  # DDPF_FOURCC
            if pf_fourcc == b"DX10":
                dx10 = f.read(20)
                if len(dx10) != 20:
                    raise DDSError(f"DX10 扩展头长度不足: {path}")
                dxgi = struct.unpack("<I", dx10[:4])[0]
                info.fmt = DXGI_TO_FORMAT.get(dxgi, f"DXGI_{dxgi}")
                info.dx10 = True
                info.data_offset = 148
            else:
                info.fmt = FOURCC_TO_FORMAT.get(pf_fourcc, pf_fourcc.decode("latin1"))
                info.data_offset = 128
        elif pf_flags & 0x40:  # DDPF_RGB: channel order comes from masks.
            masks = tuple(fields[22:26])
            if pf_flags & ~(0x40 | 0x1):
                raise DDSError(f"未支持的 DDS RGB 标志: {path}")
            alpha = bool(pf_flags & 0x1)
            if pf_rgbbitcount == 32 and masks[3] == (0xff000000 if alpha else 0):
                if masks[:3] == (0xff, 0xff00, 0xff0000):
                    info.fmt = "R8G8B8A8_UNORM"
                elif masks[:3] == (0xff0000, 0xff00, 0xff):
                    info.fmt = "B8G8R8A8_UNORM"
                else:
                    raise DDSError(f"未支持的 DDS RGB 通道掩码: {path}")
                info.opaque_alpha = not alpha
            elif pf_rgbbitcount == 24 and not alpha and masks == (0xff0000, 0xff00, 0xff, 0):
                info.fmt = "B8G8R8_UNORM"
                info.opaque_alpha = True
            else:
                raise DDSError(f"未支持的 DDS RGB 位宽或 Alpha 掩码: {path}")
            info.data_offset = 128
        elif pf_flags == 0x20000:  # DDPF_LUMINANCE, explicit L8 sample mask.
            if pf_rgbbitcount != 8 or tuple(fields[22:26]) != (0xff, 0, 0, 0):
                raise DDSError(f"未支持的 DDS 亮度通道掩码: {path}")
            info.fmt = "R8_UNORM"
            info.opaque_alpha = True
            info.data_offset = 128
        else:
            raise DDSError(f"无法识别的 DDS 像素格式: {path}")

        info.is_snorm = info.fmt in SNORM_FORMATS
        if info.fmt in _UNCOMPRESSED_BYTES and fields[1] & 0x8:
            info.row_pitch = fields[4]
            if info.row_pitch < info.width * _UNCOMPRESSED_BYTES[info.fmt]:
                raise DDSError(f"DDS 行步长小于像素宽度: {path}")
        return info


def get_dds_format(path: str) -> str:
    """便捷函数：返回格式名。"""
    return parse_dds_header(path).fmt


# ---------------------------------------------------------------------------
# 纯 NumPy BC 解码器
# ---------------------------------------------------------------------------

def _decode_bc4_block(data: bytes, signed: bool) -> np.ndarray:
    """解码一个 8 字节 BC4 块，返回 4x4 数组（值域按 signed 决定）。"""
    if signed:
        e0, e1 = struct.unpack("bb", data[:2])
    else:
        e0, e1 = data[0], data[1]
    bits = int.from_bytes(data[2:8], "little")

    if signed:
        if e0 > e1:
            palette = [e0, e1] + [
                ((7 - i) * e0 + i * e1) / 7.0 for i in range(1, 7)
            ]
        else:
            palette = [e0, e1] + [
                ((5 - i) * e0 + i * e1) / 5.0 for i in range(1, 5)
            ] + [-127, 127]
    else:
        if e0 > e1:
            palette = [e0, e1] + [
                ((7 - i) * e0 + i * e1) / 7.0 for i in range(1, 7)
            ]
        else:
            palette = [e0, e1] + [
                ((5 - i) * e0 + i * e1) / 5.0 for i in range(1, 5)
            ] + [0, 255]

    out = np.empty(16, dtype=np.float64)
    for i in range(16):
        idx = (bits >> (3 * i)) & 0x7
        out[i] = palette[idx]
    return out.reshape(4, 4)


def _decode_bc1_colors(data: bytes, force_opaque: bool = False):
    """解码 BC1 颜色部分，返回 (4x4x3 uint8 float 数组, 4x4 alpha mask 或 None)。"""
    c0, c1 = struct.unpack("<HH", data[:4])
    bits = struct.unpack("<I", data[4:8])[0]

    def rgb565(c):
        r = ((c >> 11) & 0x1F) * 255 / 31.0
        g = ((c >> 5) & 0x3F) * 255 / 63.0
        b = (c & 0x1F) * 255 / 31.0
        return (r, g, b)

    p0, p1 = rgb565(c0), rgb565(c1)
    palette = [p0, p1]
    if c0 > c1 or force_opaque:
        palette.append(tuple((2 * p0[i] + p1[i]) / 3.0 for i in range(3)))
        palette.append(tuple((p0[i] + 2 * p1[i]) / 3.0 for i in range(3)))
        alpha = None
    else:
        palette.append(tuple((p0[i] + p1[i]) / 2.0 for i in range(3)))
        palette.append((0.0, 0.0, 0.0))
        alpha = np.ones(16, dtype=np.float64)

    colors = np.empty((16, 3), dtype=np.float64)
    for i in range(16):
        idx = (bits >> (2 * i)) & 0x3
        colors[i] = palette[idx]
        if alpha is not None and idx == 3:
            alpha[i] = 0.0
    return colors.reshape(4, 4, 3), (alpha.reshape(4, 4) if alpha is not None else None)


def _blocks_dims(w: int, h: int):
    return (max(1, (w + 3) // 4), max(1, (h + 3) // 4))



def _decode_bc4_many(blocks, signed=False, clamp_snorm=False):
    # Keep float64 interpolation and legacy truncation exactly. Only the block
    # dispatch is vectorized; this is not a different compression/quality mode.
    endpoints = blocks[:, :2].view(np.int8) if signed else blocks[:, :2]
    original = endpoints.astype(np.float64)
    e = np.maximum(original, -127) if signed and clamp_snorm else original
    e0, e1 = e[:, 0], e[:, 1]
    eight = original[:, 0] > original[:, 1]
    palette = np.empty((len(blocks), 8), np.float64)
    palette[:, :2] = e
    for i in range(1, 7):
        long = ((7-i)*e0+i*e1)/7.0
        short = ((5-i)*e0+i*e1)/5.0 if i < 5 else (-127 if signed else 0) if i == 5 else (127 if signed else 255)
        palette[:, i+1] = np.where(eight, long, short)
    bits = blocks.copy().view('<u8').reshape(-1) >> np.uint64(16)
    indices = ((bits[:, None] >> (np.arange(16, dtype=np.uint64)*3)) & 7).astype(np.intp)
    return np.take_along_axis(palette, indices, axis=1)


def _decode_bc1_many(blocks, force_opaque=False):
    words = blocks[:, :4].copy().view('<u2').reshape(-1, 2).astype(np.int32)
    rgb = np.stack((((words >> 11) & 31)*255/31.0,
                    ((words >> 5) & 63)*255/63.0,
                    (words & 31)*255/31.0), axis=-1)
    palette = np.zeros((len(blocks), 4, 3), np.float64)
    palette[:, :2] = rgb
    opaque = (words[:, 0] > words[:, 1]) | force_opaque
    palette[:, 2] = np.where(opaque[:, None], (2*rgb[:, 0]+rgb[:, 1])/3.0, (rgb[:, 0]+rgb[:, 1])/2.0)
    palette[:, 3] = np.where(opaque[:, None], (rgb[:, 0]+2*rgb[:, 1])/3.0, 0.0)
    bits = blocks[:, 4:8].copy().view('<u4').reshape(-1)
    indices = ((bits[:, None] >> (np.arange(16, dtype=np.uint32)*2)) & 3).astype(np.intp)
    colors = palette[np.arange(len(blocks))[:, None], indices]
    alpha = np.where((~opaque[:, None]) & (indices == 3), 0, 255).astype(np.uint8)
    return colors, alpha


def _decode_bc_payload(raw, info, clamp_snorm=False, cancel_event=None):
    _check_cancel(cancel_event)
    w, h, fmt = info.width, info.height, info.fmt
    bw, bh = _blocks_dims(w, h)
    block_size = 8 if fmt.startswith(('BC1', 'BC4')) else 16
    need = bw*bh*block_size
    if len(raw) < need:
        raise DDSError('Truncated DDS base mip payload')
    blocks = np.frombuffer(raw, np.uint8, count=need).reshape(bh, bw, block_size)
    output = np.empty((bh*4, bw*4, 4), np.uint8)
    # Bound all temporary palettes/gathers independently of image dimensions.
    rows_per_batch = max(1, 8192//bw)
    for row in range(0, bh, rows_per_batch):
        _check_cancel(cancel_event)
        rows = min(rows_per_batch, bh-row)
        batch = blocks[row:row+rows].reshape(-1, block_size)
        pixels = np.zeros((len(batch), 16, 4), np.uint8)
        pixels[..., 3] = 255
        if fmt.startswith(('BC4', 'BC5')):
            channels = 1 if fmt.startswith('BC4') else 2
            for c in range(channels):
                values = _decode_bc4_many(batch[:, c*8:c*8+8], info.is_snorm, clamp_snorm)
                if info.is_snorm:
                    values = np.clip((values/127.0+1.0)*.5*255.0, 0, 255)
                pixels[..., c] = values.astype(np.uint8)
            if channels == 1:
                pixels[..., 1] = pixels[..., 0]; pixels[..., 2] = pixels[..., 0]
        else:
            colors, alpha = _decode_bc1_many(batch[:, -8:], not fmt.startswith('BC1'))
            pixels[..., :3] = colors.astype(np.uint8)
            if fmt.startswith('BC3'):
                alpha = _decode_bc4_many(batch[:, :8]).astype(np.uint8)
            elif fmt.startswith('BC2'):
                bits = batch[:, :8].copy().view('<u8').reshape(-1)
                alpha = (((bits[:, None] >> (np.arange(16, dtype=np.uint64)*4)) & 15)*17).astype(np.uint8)
            pixels[..., 3] = alpha
        output[row*4:(row+rows)*4] = pixels.reshape(rows, bw, 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(rows*4, bw*4, 4)
    _check_cancel(cancel_event)
    return output[:h, :w].copy() if h % 4 or w % 4 else output


def _byte_layout(info):
    tight = info.width * _UNCOMPRESSED_BYTES[info.fmt]
    pitch = info.row_pitch or tight
    if pitch < tight:
        raise DDSError('DDS row pitch is smaller than its pixel row')
    size = pitch * info.height
    if size > 256 * 1024 * 1024 or info.width * info.height * 4 > 256 * 1024 * 1024:
        raise DDSError('DDS base mip exceeds the bounded decoder budget')
    return pitch, size


def _decode_uncompressed_payload(raw, info, cancel_event=None):
    _check_cancel(cancel_event)
    w, h = info.width, info.height
    pitch, size = _byte_layout(info)
    if len(raw) != size:
        raise DDSError('Truncated DDS uncompressed mip payload')
    bpp = _UNCOMPRESSED_BYTES[info.fmt]
    rows = np.frombuffer(raw, np.uint8).reshape(h, pitch)
    pixels = rows[:, :w * bpp].reshape(h, w, bpp)
    output = np.empty((h, w, 4), np.uint8)
    if bpp == 1:
        output[..., :3] = pixels
    elif info.fmt.startswith('B8'):
        output[..., :3] = pixels[..., [2, 1, 0]]
    else:
        output[..., :3] = pixels[..., :3]
    output[..., 3] = 255 if bpp != 4 or info.opaque_alpha else pixels[..., 3]
    _check_cancel(cancel_event)
    return output


def decode_dds_pixels(path: str, info: DdsInfo | None = None, *, cancel_event=None) -> np.ndarray:
    """Decode mip zero to RGBA8 in bounded NumPy block batches.

    The established SNORM mapping remains unchanged. The semantic normal
    decoder explicitly requests its separate -128 endpoint correction.
    """
    _check_cancel(cancel_event)
    info = info or parse_dds_header(path)
    w, h, fmt = info.width, info.height, info.fmt
    if w <= 0 or h <= 0:
        raise DDSError('Invalid DDS dimensions')
    if fmt not in OWN_DECODERS:
        if Image is None:
            raise DDSError(f'Pillow required to decode {fmt}')
        try:
            with Image.open(path) as im:
                pixels = np.asarray(im.convert('RGBA'), dtype=np.uint8).copy()
            _check_cancel(cancel_event)
            return pixels
        except CancellationRequested:
            raise
        except Exception as exc:
            raise DDSError(f'Pillow cannot decode {fmt}: {exc}') from exc
    bw, bh = _blocks_dims(w, h)
    need = bw*bh*(8 if fmt.startswith(('BC1', 'BC4')) else 16) if info.is_bc else _byte_layout(info)[1]
    with open(path, 'rb') as stream:
        stream.seek(info.data_offset); raw = _read_bytes(stream, need, cancel_event)
    if len(raw) != need:
        raise DDSError(f'Truncated DDS base mip: {path}')
    if info.is_bc:
        return _decode_bc_payload(raw, info, cancel_event=cancel_event)
    return _decode_uncompressed_payload(raw, info, cancel_event)


# ---------------------------------------------------------------------------
# 编码：texconv（主路径）
# ---------------------------------------------------------------------------

def encode_with_texconv(texconv_path: str, rgba: np.ndarray, fmt: str,
                        out_path: str, mipmaps: bool = True, *, cancel_event=None) -> None:
    """用 texconv 把 RGBA 数组编码为指定 DDS 格式。"""
    _check_cancel(cancel_event)
    if Image is None:
        raise DDSError("Pillow 不可用，无法生成 texconv 输入")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    workdir = tempfile.mkdtemp(prefix="fo76_tex_", dir=tempfile.gettempdir())
    stem = os.path.splitext(os.path.basename(out_path))[0]
    # stem 里可能有 texconv 不喜欢的字符时回退到 uuid
    safe_stem = "".join(c if (c.isalnum() or c in "._- ") else "_" for c in stem) or uuid.uuid4().hex
    tga_path = os.path.join(workdir, safe_stem + ".tga")

    try:
        img = Image.fromarray(rgba, "RGBA")
        img.save(tga_path, format="TGA")
        _check_cancel(cancel_event)
    
        cmd = [
            texconv_path,
            "-f", fmt,
            "-m", "0" if mipmaps else "1",
            "-y",
            "-o", workdir,
            "--", tga_path,
        ]
        proc = _run_texconv(cmd, cancel_event)
        if proc.returncode != 0:
            raise DDSError(f"texconv 失败 ({proc.returncode}): {proc.stderr.strip() or proc.stdout.strip()}")
    
        produced = os.path.join(workdir, safe_stem + ".dds")
        if not os.path.exists(produced):
            raise DDSError(f"texconv 未生成输出: {produced}")
        _check_cancel(cancel_event)
        shutil.move(produced, out_path)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# 编码：纯 Python 后备（BC1 / BC3 / BC5 / 未压缩 BGRA8）
# ---------------------------------------------------------------------------

def _build_mip_chain(rgba: np.ndarray, cancel_event=None) -> list[np.ndarray]:
    """生成完整 Mipmap 链（直到 1x1）。"""
    _check_cancel(cancel_event)
    chain = [rgba]
    cur = rgba
    while cur.shape[0] > 1 or cur.shape[1] > 1:
        _check_cancel(cancel_event)
        nw = max(1, cur.shape[1] // 2)
        nh = max(1, cur.shape[0] // 2)
        img = Image.fromarray(cur, "RGBA").resize((nw, nh), Image.LANCZOS)
        cur = np.asarray(img, dtype=np.uint8).copy()
        chain.append(cur)
    _check_cancel(cancel_event)
    return chain


def _rgb_to_565(r, g, b):
    return ((r * 31 // 255) << 11) | ((g * 63 // 255) << 5) | (b * 31 // 255)


def _rgb_from_565(c):
    return (((c >> 11) & 0x1F) * 255 // 31,
            ((c >> 5) & 0x3F) * 255 // 63,
            (c & 0x1F) * 255 // 31)


def _encode_bc1_block(block: np.ndarray) -> bytes:
    """朴素 BC1 编码（两端点沿主轴搜索）。"""
    px = block[..., :3].reshape(-1, 3).astype(np.int32)
    # 主轴 = 方差最大的通道，取最大最小
    ch = int(np.argmax(px.max(axis=0) - px.min(axis=0)))
    lo = px[px[:, ch].argmin()]
    hi = px[px[:, ch].argmax()]
    if tuple(lo) == tuple(hi):
        hi = np.minimum(hi + 1, 255)
    c0 = _rgb_to_565(int(hi[0]), int(hi[1]), int(hi[2]))
    c1 = _rgb_to_565(int(lo[0]), int(lo[1]), int(lo[2]))
    if c0 <= c1:
        c0, c1 = c1, c0 if c1 != c0 else (c0 - 1) & 0xFFFF
        if c0 <= c1:
            c0 = (c1 + 1) & 0xFFFF
    p0 = np.array(_rgb_from_565(c0), dtype=np.int32)
    p1 = np.array(_rgb_from_565(c1), dtype=np.int32)
    palette = np.stack([
        p0, p1,
        (2 * p0 + p1) // 3,
        (p0 + 2 * p1) // 3,
    ])
    d = ((px[:, None, :] - palette[None, :, :]) ** 2).sum(axis=2)
    idx = d.argmin(axis=1)
    bits = 0
    for i in range(16):
        bits |= int(idx[i]) << (2 * i)
    return struct.pack("<HHI", c0, c1, bits)


def _encode_bc4_block(plane: np.ndarray) -> bytes:
    """朴素 BC4（unsigned）编码。"""
    v = plane.reshape(-1).astype(np.int32)
    e0, e1 = int(v.max()), int(v.min())
    if e0 == e1:
        e1 = max(0, e0 - 1)
    if e0 <= e1:  # 保证 8 值模式
        e0, e1 = max(e0, 1), e0 - 1 if e0 >= 1 else 0
        if e0 <= e1:
            e0 = e1 + 1
    palette = [e0, e1] + [((7 - i) * e0 + i * e1) // 7 for i in range(1, 7)]
    d = ((v[:, None] - np.array(palette)[None, :]) ** 2)
    idx = d.argmin(axis=1)
    bits = 0
    for i in range(16):
        bits |= int(idx[i]) << (3 * i)
    return bytes([e0 & 0xFF, e1 & 0xFF]) + bits.to_bytes(6, "little")


def _pad4(a: np.ndarray) -> np.ndarray:
    h, w = a.shape[:2]
    bw, bh = _blocks_dims(w, h)
    nw, nh = bw * 4, bh * 4
    out = np.zeros((nh, nw, a.shape[2]), dtype=a.dtype)
    out[:h, :w] = a
    # 边缘复制填充
    if h < nh:
        out[h:, :w] = out[h - 1:h, :w]
    if w < nw:
        out[:, w:] = out[:, w - 1:w]
    return out


def _encode_bc4_many(plane):
    v = plane.astype(np.int32)
    e0, e1 = v.max(axis=1), v.min(axis=1)
    e1 = np.where(e0 == e1, np.maximum(0, e0-1), e1)
    bad = e0 <= e1
    e1 = np.where(bad, np.maximum(0, e0-1), e1)
    e0 = np.where(bad, np.maximum(e0, 1), e0)
    palette = np.stack([e0, e1]+[((7-i)*e0+i*e1)//7 for i in range(1, 7)], axis=1)
    indices = ((v[:, :, None]-palette[:, None, :])**2).argmin(axis=2).astype(np.uint64)
    bits = np.sum(indices << (np.arange(16, dtype=np.uint64)*3), axis=1, dtype=np.uint64)
    packed = bits << np.uint64(16) | e0.astype(np.uint64) | (e1.astype(np.uint64) << np.uint64(8))
    return packed.astype('<u8').view(np.uint8).reshape(-1, 8)


def _encode_bc1_many(blocks):
    px = blocks[..., :3].astype(np.int32)
    channel = (px.max(axis=1)-px.min(axis=1)).argmax(axis=1)
    axis = np.take_along_axis(px, channel[:, None, None], axis=2)[..., 0]
    rows = np.arange(len(px))
    lo, hi = px[rows, axis.argmin(axis=1)], px[rows, axis.argmax(axis=1)]
    hi = np.where(np.all(lo == hi, axis=1)[:, None], np.minimum(hi+1, 255), hi)
    def rgb565(v):
        return ((v[:, 0]*31//255) << 11) | ((v[:, 1]*63//255) << 5) | (v[:, 2]*31//255)
    c0, c1 = rgb565(hi), rgb565(lo)
    swap = c0 <= c1
    n0 = np.where(swap, c1, c0)
    n1 = np.where(swap, np.where(c1 != c0, c0, (c0-1) & 0xffff), c1)
    c0 = np.where(n0 <= n1, (n1+1) & 0xffff, n0); c1 = n1
    def rgb(c):
        return np.stack((((c >> 11) & 31)*255//31, ((c >> 5) & 63)*255//63, (c & 31)*255//31), axis=1)
    p0, p1 = rgb(c0), rgb(c1)
    palette = np.stack((p0, p1, (2*p0+p1)//3, (p0+2*p1)//3), axis=1)
    distance = ((px[:, :, None, :]-palette[:, None, :, :])**2).sum(axis=3)
    indices = distance.argmin(axis=2).astype(np.uint64)
    bits = np.sum(indices << (np.arange(16, dtype=np.uint64)*2), axis=1, dtype=np.uint64)
    packed = c0.astype(np.uint64) | (c1.astype(np.uint64) << np.uint64(16)) | (bits << np.uint64(32))
    return packed.astype('<u8').view(np.uint8).reshape(-1, 8)


def _encode_level_fallback(level: np.ndarray, fmt: str, cancel_event=None) -> bytes:
    _check_cancel(cancel_event)
    if fmt not in ('BC1_UNORM', 'BC3_UNORM', 'BC5_UNORM'):
        raise DDSError(f'Fallback codec does not support {fmt}')
    a = _pad4(level); h, w = a.shape[:2]; bw = w//4
    out = bytearray(); rows_per_batch = max(1, 2048//bw)
    for row in range(0, h//4, rows_per_batch):
        _check_cancel(cancel_event)
        rows = min(rows_per_batch, h//4-row)
        blocks = a[row*4:(row+rows)*4].reshape(rows, 4, bw, 4, 4).transpose(0, 2, 1, 3, 4).reshape(-1, 16, 4)
        if fmt == 'BC1_UNORM':
            data = _encode_bc1_many(blocks)
        elif fmt == 'BC3_UNORM':
            data = np.concatenate((_encode_bc4_many(blocks[..., 3]), _encode_bc1_many(blocks)), axis=1)
        else:
            data = np.concatenate((_encode_bc4_many(blocks[..., 0]), _encode_bc4_many(blocks[..., 1])), axis=1)
        out.extend(data.tobytes())
    _check_cancel(cancel_event)
    return bytes(out)


def _dds_header(width: int, height: int, mipmaps: int, fmt: str,
                level0_size: int) -> bytes:
    DDSD_CAPS, DDSD_HEIGHT, DDSD_WIDTH = 0x1, 0x2, 0x4
    DDSD_PIXELFORMAT, DDSD_MIPMAPCOUNT, DDSD_LINEARSIZE = 0x1000, 0x20000, 0x80000
    flags = DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PIXELFORMAT | DDSD_LINEARSIZE
    if mipmaps > 1:
        flags |= DDSD_MIPMAPCOUNT

    legacy_fourcc = {"BC1_UNORM": b"DXT1", "BC3_UNORM": b"DXT5", "BC5_UNORM": b"ATI2"}
    if fmt in legacy_fourcc:
        pf = struct.pack("<II4sIIIII", 32, 0x4, legacy_fourcc[fmt], 0, 0, 0, 0, 0)
        dx10 = b""
    else:
        dxgi = FORMAT_TO_DXGI.get(fmt)
        if dxgi is None:
            raise DDSError(f"无 DXGI 映射: {fmt}")
        pf = struct.pack("<II4sIIIII", 32, 0x4, b"DX10", 0, 0, 0, 0, 0)
        dx10 = struct.pack("<IIIII", dxgi, 3, 0, 1, 0)

    caps = 0x1000 | (0x400008 if mipmaps > 1 else 0)  # TEXTURE | COMPLEX|MIPMAP
    header = struct.pack(
        "<31I",
        124, flags, height, width, level0_size, 0, mipmaps,
        *([0] * 11),
        *struct.unpack("<8I", pf),
        caps, 0, 0, 0, 0,
    )
    return b"DDS " + header + dx10


def encode_fallback(rgba: np.ndarray, fmt: str, out_path: str,
                    mipmaps: bool = True, *, cancel_event=None) -> str:
    """
    纯 Python 后备编码。返回实际使用的格式（可能与请求不同）。
    BC7 请求会降级为 BC3（有 alpha）或 BC1。
    """
    _check_cancel(cancel_event)
    actual = fmt
    if fmt.startswith("BC7"):
        has_alpha = bool((rgba[..., 3] < 250).any())
        actual = "BC3_UNORM" if has_alpha else "BC1_UNORM"
    if fmt == "BC2_UNORM":
        actual = "BC3_UNORM"

    chain = _build_mip_chain(rgba, cancel_event) if mipmaps else [rgba]
    blobs = [_encode_level_fallback(lv, actual, cancel_event) for lv in chain]
    header = _dds_header(rgba.shape[1], rgba.shape[0], len(chain), actual, len(blobs[0]))
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    fd, pending = tempfile.mkstemp(prefix=".dds-", dir=os.path.dirname(out_path) or ".")
    try:
        with os.fdopen(fd, "wb") as f:
            _write_bytes(f, header, cancel_event)
            for b in blobs:
                _write_bytes(f, b, cancel_event)
        _check_cancel(cancel_event)
        os.replace(pending, out_path)
    finally:
        if os.path.exists(pending):
            os.unlink(pending)
    return actual


def encode_dds(rgba: np.ndarray, fmt: str, out_path: str, mipmaps: bool = True,
               texconv_path: str | None = None, *, codec_info: dict | None = None,
               cancel_event=None) -> tuple[str, str | None]:
    """
    统一编码入口。返回 (实际格式, 警告信息或 None)。
    优先 texconv；失败时自动回退到内置编码器。
    """
    _check_cancel(cancel_event)
    if texconv_path and os.path.isfile(texconv_path):
        try:
            encode_with_texconv(texconv_path, rgba, fmt, out_path, mipmaps, cancel_event=cancel_event)
            if codec_info is not None:codec_info['backend']='texconv'
            return fmt, None
        except EncoderCleanupError:
            raise
        except DDSError:
            pass  # 落入后备
    _check_cancel(cancel_event)
    actual = encode_fallback(rgba, fmt, out_path, mipmaps, cancel_event=cancel_event)
    if codec_info is not None:codec_info['backend']='numpy-fallback'
    warn = None
    if actual != fmt:
        warn = tr("nt_texconv_fallback", fmt=fmt, actual=actual)
    return actual, warn
