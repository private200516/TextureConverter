"""Read-only, bounded NIF/material preview data. GPL-3.0.

Container/vertex layouts adapted from this toolkit's inspect_nif and
convert_preserved readers (niftools/nifxml schema). No animation or script runs.
Rendering is a material inspection aid, not the Bethesda game renderer.
"""
from __future__ import annotations
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
import io
import math
import struct
import threading
import numpy as np
from PIL import Image
import dds
from semantic_dds import validate_mip_layout
from material_codec import parse as parse_material

MAX_NIF_BYTES = 64 * 1024 * 1024
MAX_TRIANGLES = 600_000
MAX_VERTICES = 600_000
MAX_TEXTURE_BYTES = 128 * 1024 * 1024
MAX_TEXTURE_PIXELS = 32 * 1024 * 1024  # aggregate resident preview pixels
SHAPES = {'BSTriShape', 'BSSubIndexTriShape', 'BSMeshLODTriShape'}
NODES = {'NiNode', 'BSFadeNode', 'BSValueNode', 'NiBillboardNode', 'BSLeafAnimNode'}

class PreviewError(ValueError):
    pass

def check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise InterruptedError('Preview loading cancelled')

class Reader:
    def __init__(self, data):
        self.data, self.pos = data, 0
    def read(self, size):
        if size < 0 or size > len(self.data) - self.pos:
            raise PreviewError('Truncated NIF payload')
        value = self.data[self.pos:self.pos + size]
        self.pos += size
        return value
    def get(self, fmt):
        return struct.unpack('<' + fmt, self.read(struct.calcsize('<' + fmt)))
    def u(self): return self.get('I')[0]
    def i(self): return self.get('i')[0]
    def array(self, fmt, count):
        if count < 0 or count > (len(self.data) - self.pos) // struct.calcsize('<' + fmt):
            raise PreviewError('Array exceeds NIF block')
        return self.get(fmt * count)
    def string(self):
        size = self.u()
        if size > 32768: raise PreviewError('Oversized NIF string')
        return self.read(size).decode('utf8', errors='replace').rstrip('\0')
    def short_string(self): return self.read(self.get('B')[0])

class NifReader:
    def __init__(self, path):
        path = Path(path)
        if path.stat().st_size > MAX_NIF_BYTES:
            raise PreviewError('Preview limit: NIF must be at most 64 MiB')
        r = Reader(path.read_bytes())
        newline = r.data.find(b'\n', 0, 256)
        if newline < 0 or not r.data.startswith(b'Gamebryo File Format, Version 20.2.0.7'):
            raise PreviewError('Expected Fallout 4 / Fallout 76 NIF 20.2.0.7')
        r.read(newline + 1)
        version, endian, user, count, self.bs = r.get('IBIII')
        if (version, endian, user) != (0x14020007, 1, 12) or self.bs not in (130, 155):
            raise PreviewError('Preview supports FO4 BS130 and FO76 BS155 NIFs')
        if not 0 < count <= 100_000 or count > len(r.data) // 6:
            raise PreviewError('Invalid NIF block count')
        r.short_string()
        if self.bs > 130: r.u()
        else: r.short_string()
        r.short_string(); r.short_string()
        type_count = r.get('H')[0]
        if not 0 < type_count <= 4096: raise PreviewError('Invalid NIF type count')
        self.types = [r.string() for _ in range(type_count)]
        indices = r.array('H', count)
        if any(i >= type_count for i in indices): raise PreviewError('Invalid block type')
        self.kinds = [self.types[i] for i in indices]
        sizes = r.array('I', count)
        string_count = r.u(); r.u()
        if string_count > 100_000: raise PreviewError('Too many NIF strings')
        self.strings = [r.string() for _ in range(string_count)]
        r.array('I', r.u())
        if sum(sizes) > len(r.data) - r.pos - 4: raise PreviewError('NIF blocks exceed file size')
        self.blocks = [r.read(size) for size in sizes]
        self.roots = r.array('i', r.u())
        if r.pos != len(r.data) or not self.roots: raise PreviewError('Invalid NIF footer')
        for index in self.roots: self.ref(index, False)
    def ref(self, index, nullable=True):
        if nullable and index == -1: return
        if not 0 <= index < len(self.blocks): raise PreviewError('Invalid NIF block reference')
    def net(self, r):
        name_index = r.i()
        if name_index != -1 and not 0 <= name_index < len(self.strings):
            raise PreviewError('Invalid NIF name index')
        extras = r.array('i', r.u()); controller = r.i()
        for i in extras: self.ref(i, False)
        self.ref(controller)
        return self.strings[name_index] if name_index != -1 else ''
    def av(self, r):
        name = self.net(r); flags = r.u(); transform = r.get('13f'); self.ref(r.i())
        if not all(math.isfinite(x) for x in transform) or abs(transform[-1]) > 1e6:
            raise PreviewError('Invalid scene transform')
        matrix = np.eye(4, dtype=np.float64)
        matrix[:3, :3] = np.asarray(transform[3:12]).reshape(3, 3) * transform[12]
        matrix[:3, 3] = transform[:3]
        return name, flags, matrix

def clean_reference(reference, category):
    value = str(reference).replace('\\', '/').strip()
    # Bethesda files sometimes retain editor C:/.../Data/Materials paths.
    # Resolve only the virtual asset suffix under selected roots, never that drive.
    marker = '/data/' + category.lower() + '/'
    anchor = value.lower().find(marker)
    if anchor >= 0 and ':' in value[:anchor]:
        value = value[anchor + len('/data/'):]
    parts = PurePosixPath(value).parts
    if not value or value.startswith('/') or ':' in value or '\0' in value or '..' in parts:
        raise PreviewError('Unsafe resource reference: ' + value[:160])
    if parts and parts[0].lower() == 'data': parts = parts[1:]
    if parts and parts[0].lower() == category.lower(): parts = parts[1:]
    if not parts: raise PreviewError('Empty resource reference')
    return Path(category, *parts)

class ResourceResolver:
    """Converted roots take priority. Never search outside user-selected roots."""
    def __init__(self, converted_root='', source_root=''):
        self.roots = []
        for label, value in (('converted', converted_root), ('source fallback', source_root)):
            if not value: continue
            root = Path(value).resolve()
            # Accept the converter's run folder, its converted child, or Data root.
            if (root / 'converted').is_dir(): root = (root / 'converted').resolve()
            if root.name.casefold() in ('materials', 'textures', 'meshes'): root = root.parent
            if root.is_dir() and root not in [r for _, r in self.roots]: self.roots.append((label, root))
    def resolve(self, reference, category):
        relative = clean_reference(reference, category)
        for label, root in self.roots:
            candidate = (root / relative).resolve()
            if not candidate.is_relative_to(root): raise PreviewError('Resource symlink escapes selected root')
            if candidate.is_file(): return candidate, label
        return None, 'missing'

def _normalize(v):
    length = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.maximum(length, 1e-12)

@dataclass
class PreviewMaterial:
    name: str = ''
    source: str = 'unassigned'
    fields: dict = field(default_factory=dict)
    textures: dict = field(default_factory=dict)
    version: int = 2
    kind: str = 'BGSM'

@dataclass
class PreviewMesh:
    name: str
    vertices: np.ndarray  # position3, normal3, uv2, color4; triangle-expanded
    material: PreviewMaterial
    skinned: bool = False

@dataclass
class PreviewScene:
    path: str
    meshes: list
    center: np.ndarray
    radius: float
    diagnostics: list
    triangles: int
    source_version: int

def _mip_size(w, h, fmt):
    if fmt.startswith(('BC1', 'BC4')): return max(1, (w + 3)//4) * max(1, (h + 3)//4) * 8
    if fmt.startswith(('BC2', 'BC3', 'BC5', 'BC6', 'BC7')): return max(1, (w + 3)//4) * max(1, (h + 3)//4) * 16
    if fmt.startswith(('R8G8B8A8', 'B8G8R8A8')): return w * h * 4
    if fmt == 'B8G8R8_UNORM': return w * h * 3
    if fmt == 'R8_UNORM': return w * h
    raise PreviewError('Unsupported preview DDS format ' + fmt)

def load_preview_texture(path, cancel=None, max_side=1024):
    """Read an existing mip directly; no full-size decode or source rewrite."""
    try:
        return _load_preview_texture(path, cancel, max_side)
    except dds.CancellationRequested as exc:
        raise InterruptedError('Preview loading cancelled') from exc
    except (dds.DDSError, ValueError) as exc:
        raise PreviewError(str(exc)) from exc


def _load_preview_texture(path, cancel, max_side):
    check_cancel(cancel)
    path = Path(path)
    if path.stat().st_size > MAX_TEXTURE_BYTES: raise PreviewError('DDS exceeds 128 MiB preview limit')
    # Share exact mip extents with conversion, including legacy row pitch.
    # This reads only the header/file length, not every mip's pixel payload.
    info, layout = validate_mip_layout(path, cancel_event=cancel)
    level = 0
    while level + 1 < len(layout) and max(layout[level][:2]) > max_side:
        level += 1
    width, height, offset, size = layout[level]
    if width * height > 16 * 1024 * 1024:
        raise PreviewError('Large DDS has no bounded preview mip')
    with path.open('rb') as stream:
        header = bytearray(stream.read(info.data_offset))
        if len(header) != info.data_offset or struct.unpack_from('<I', header, 4)[0] != 124:
            raise PreviewError('Invalid DDS header')
        stream.seek(offset); payload = dds._read_bytes(stream, size, cancel)
        if len(payload) != size: raise PreviewError('Truncated DDS mip')
    if info.fmt in dds._UNCOMPRESSED_BYTES:
        # Preserve actual legacy masks/opaque RGBX alpha. Pillow's DDS decoder
        # does not cover every uncompressed tag/layout in the resource corpus.
        selected = replace(info, width=width, height=height, mipmaps=1,
                           row_pitch=info.row_pitch if level == 0 else 0)
        result = dds._decode_uncompressed_payload(payload, selected, cancel)
        if max(width, height) > max_side:
            image = Image.fromarray(result)
            image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            result = np.asarray(image, dtype=np.uint8).copy()
        check_cancel(cancel)
        return result
    struct.pack_into('<II', header, 12, height, width)
    struct.pack_into('<I', header, 28, 1)
    if info.fmt.startswith('BC'): struct.pack_into('<I', header, 20, size)
    else: struct.pack_into('<I', header, 20, width * dds._UNCOMPRESSED_BYTES.get(info.fmt, 1))
    if info.dx10:
        code = struct.unpack_from('<I', header, 128)[0]
        # sRGB affects lighting, not block decoding; keep stored samples intact.
        struct.pack_into('<I', header, 128, {72:71,75:74,78:77,99:98,29:28,91:87}.get(code,code))
    check_cancel(cancel)
    # Pillow's native BC decoder avoids the Python per-block path.
    with Image.open(io.BytesIO(bytes(header) + payload)) as image:
        image.load()
        if max(image.size) > max_side: image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        result = np.asarray(image.convert('RGBA'), dtype=np.uint8).copy()
    check_cancel(cancel)
    return result

def _shader_material(nif, index, resolver, cache, images, diagnostics, cancel):
    if index in cache: return cache[index]
    material = PreviewMaterial()
    cache[index] = material
    if index == -1:
        diagnostics.append('Shape has no shader; neutral surface displayed.')
        return material
    nif.ref(index, False)
    kind = nif.kinds[index]
    if kind not in ('BSLightingShaderProperty', 'BSEffectShaderProperty'):
        diagnostics.append('Unsupported shader: ' + kind)
        return material
    r = Reader(nif.blocks[index])
    if kind == 'BSLightingShaderProperty' and nif.bs == 130: r.u()
    reference = nif.net(r)
    material.name = reference
    inline_textures = {}
    if reference.lower().endswith(('.bgsm', '.bgem')):
        path, label = resolver.resolve(reference, 'materials')
        if path is None:
            diagnostics.append('Missing material: ' + reference)
            return material
        if path.stat().st_size > 1024 * 1024: raise PreviewError('Oversized material')
        try:
            parsed = parse_material(path)
        except (ValueError, OSError) as exc:
            diagnostics.append('Material could not be read: ' + reference + ': ' + str(exc))
            return material
        material.fields, material.version, material.kind, material.source = parsed.fields, parsed.version, parsed.kind, label
        diagnostics.append('Material [' + label + ']: ' + reference)
        if parsed.fields.get('RootMaterialPath'):
            diagnostics.append('Root-material inheritance is not evaluated: ' + parsed.fields['RootMaterialPath'])
        for field_name in ('GrayscaleToPaletteColor', 'Translucency', 'Refraction', 'Terrain', 'Hair', 'GlassEnabled'):
            if parsed.fields.get(field_name): diagnostics.append('Approximate preview only: ' + field_name)
        if parsed.fields.get('EnvmapTexture'):
            diagnostics.append('Cubemap reflections use a studio approximation in this preview.')
    elif nif.bs == 130 and kind == 'BSLightingShaderProperty':
        # Explicit FO4 fallback texture-set references (no arbitrary string scan).
        r.get('II4f'); texture_set = r.i(); nif.ref(texture_set)
        if texture_set != -1 and nif.kinds[texture_set] == 'BSShaderTextureSet':
            tr = Reader(nif.blocks[texture_set]); count = tr.u()
            if count > 32: raise PreviewError('Invalid texture-set size')
            paths = [tr.string() for _ in range(count)]
            for slot, role in ((0, 'DiffuseTexture'), (1, 'NormalTexture'), (2, 'GlowTexture'), (7, 'SmoothSpecTexture')):
                if slot < len(paths): inline_textures[role] = paths[slot]
        material.fields = inline_textures
        material.source = 'inline FO4 texture set'
    else:
        diagnostics.append('Embedded shader is unsupported for material preview; neutral surface displayed.')
        return material
    fields = material.fields
    roles = {'base': fields.get('DiffuseTexture') or fields.get('BaseTexture'),
             'normal': fields.get('NormalTexture'),
             'specular': fields.get('SmoothSpecTexture') if material.version <= 2 else '',
             'glow': fields.get('GlowTexture') if fields.get('Glowmap') or fields.get('EmitEnabled') else ''}
    if material.version > 2:
        diagnostics.append('FO76 fallback material: base color / normal shown; FO4 specular interpretation is disabled.')
    for role, texture_reference in roles.items():
        check_cancel(cancel)
        if not texture_reference: continue
        path, label = resolver.resolve(texture_reference, 'textures')
        if path is None:
            diagnostics.append('Missing ' + role + ' texture: ' + texture_reference)
            continue
        key = str(path)
        if key not in images:
            if len(images) >= 96: raise PreviewError('Preview limit: at most 96 distinct textures')
            try:
                pixels = load_preview_texture(path, cancel)
                if sum(x.size//4 for x in images.values() if x is not None) + pixels.size//4 > MAX_TEXTURE_PIXELS:
                    raise PreviewError('Preview textures exceed the 128 MiB memory budget')
                images[key] = pixels
            except (ValueError, OSError, NotImplementedError, dds.DDSError) as exc:
                images[key] = None
                diagnostics.append('Texture skipped: ' + texture_reference + ': ' + str(exc))
        if images[key] is not None:
            material.textures[role] = (key, images[key])
            diagnostics.append('Texture [' + label + '/' + role + ']: ' + texture_reference)
    return material

def load_scene(path, converted_root='', source_root='', cancel=None):
    """Parse geometry, apply scene transforms, and load resolved material maps."""
    check_cancel(cancel)
    path = Path(path).resolve()
    if path.suffix.lower() != '.nif': raise PreviewError('Choose a local .nif file')
    nif = NifReader(path)
    resolver = ResourceResolver(converted_root, source_root)
    diagnostics, meshes, materials, images = [], [], {}, {}
    total_triangles = total_vertices = 0
    active, visited = set(), set()
    bounds_min, bounds_max = np.full(3, np.inf), np.full(3, -np.inf)
    def walk(index, parent, depth=0):
        nonlocal total_triangles, total_vertices, bounds_min, bounds_max
        check_cancel(cancel)
        if index == -1: return
        nif.ref(index, False)
        if index in active or depth > 128: raise PreviewError('Cyclic or excessively deep NIF scene')
        if index in visited: raise PreviewError('Shared scene node has multiple parents')
        active.add(index); visited.add(index)
        kind = nif.kinds[index]
        if kind not in SHAPES | NODES:
            diagnostics.append('Scene block not displayed: ' + kind)
            active.remove(index)
            return
        r = Reader(nif.blocks[index]); name, flags, local = nif.av(r)
        world = parent @ local
        if not np.isfinite(world).all(): raise PreviewError('Invalid accumulated scene transform')
        if kind in NODES:
            children = r.array('i', r.u())
            for child in children: walk(child, world, depth + 1)
        else:
            r.get('4f')
            if nif.bs == 155: r.get('6f')
            skin, shader, alpha = r.get('iii')
            for ref in (skin, shader, alpha): nif.ref(ref)
            descriptor = r.get('Q')[0]
            triangles, vertices, size = r.get('IHI')
            stride = (descriptor & 15) * 4; attributes = descriptor >> 44
            total_triangles += triangles; total_vertices += vertices
            if total_triangles > MAX_TRIANGLES or total_vertices > MAX_VERTICES:
                raise PreviewError('Preview limit: 600,000 vertices / triangles per NIF')
            if not vertices or not triangles:
                diagnostics.append('Empty geometry skipped: ' + name)
                active.remove(index); return
            if size != stride * vertices + 6 * triangles or not attributes & 1:
                raise PreviewError('Invalid vertex descriptor or geometry size')
            fields = [(0, 16 if attributes & 0x400 else 8)]
            for flag, shift, length in ((2, 8, 4), (8, 16, 4), (16, 20, 4), (32, 24, 4), (64, 28, 12), (256, 36, 4)):
                if attributes & flag: fields.append((((descriptor >> shift) & 15) * 4, length))
            fields.sort()
            if any(off + length > stride for off, length in fields) or any(a[0]+a[1] > b[0] for a,b in zip(fields,fields[1:])):
                raise PreviewError('Vertex attribute overlaps / exceeds stride')
            raw = r.read(size)
            indices = np.frombuffer(raw, dtype='<u2', count=triangles*3, offset=stride*vertices).reshape(-1, 3)
            if np.max(indices) >= vertices: raise PreviewError('Triangle references missing vertex')
            positions = np.ndarray((vertices, 3), dtype='<f4' if attributes & 0x400 else '<f2', buffer=raw, strides=(stride, 4 if attributes & 0x400 else 2)).astype(np.float32)
            if not np.isfinite(positions).all(): raise PreviewError('Non-finite vertex positions')
            positions = positions @ world[:3, :3].T + world[:3, 3]
            if np.max(np.abs(positions)) > 1e9: raise PreviewError('Preview geometry coordinates are too large')
            bounds_min = np.minimum(bounds_min, positions.min(axis=0)); bounds_max = np.maximum(bounds_max, positions.max(axis=0))
            uv = np.zeros((vertices, 2), np.float32)
            if attributes & 2:
                uv = np.ndarray((vertices, 2), dtype='<f2', buffer=raw, offset=((descriptor >> 8)&15)*4, strides=(stride, 2)).astype(np.float32)
                if not np.isfinite(uv).all(): raise PreviewError('Non-finite texture coordinates')
            normal = np.zeros((vertices, 3), np.float32)
            if attributes & 8:
                normal = np.ndarray((vertices, 3), dtype='u1', buffer=raw, offset=((descriptor >> 16)&15)*4, strides=(stride, 1)).astype(np.float32) / 127.5 - 1
                try: normal = _normalize(normal @ np.linalg.inv(world[:3, :3]))
                except np.linalg.LinAlgError: raise PreviewError('Singular shape transform')
            else:
                face = np.cross(positions[indices[:, 1]]-positions[indices[:, 0]], positions[indices[:, 2]]-positions[indices[:, 0]])
                for corner in range(3): np.add.at(normal, indices[:, corner], face)
                normal = _normalize(normal)
            colors = np.ones((vertices, 4), np.float32)
            if attributes & 32:
                colors = np.ndarray((vertices, 4), dtype='u1', buffer=raw, offset=((descriptor >> 24)&15)*4, strides=(stride, 1)).astype(np.float32) / 255.
            material = _shader_material(nif, shader, resolver, materials, images, diagnostics, cancel)
            packed = np.column_stack((positions, normal, uv, colors)).astype(np.float32)[indices.reshape(-1)]
            meshes.append(PreviewMesh(name or ('Shape ' + str(index)), np.ascontiguousarray(packed), material, skin != -1))
        active.remove(index)
    for root in nif.roots: walk(root, np.eye(4))
    check_cancel(cancel)
    if not meshes: raise PreviewError('NIF has no supported triangle geometry')
    radius = max(float(np.linalg.norm(bounds_max - bounds_min) * .5), 1e-3)
    if any(m.skinned for m in meshes): diagnostics.append('Skinned mesh: static bind geometry; animation / bone retargeting is not evaluated.')
    diagnostics.append('Local read-only preview. Studio lighting is approximate; use the game to verify final appearance.')
    return PreviewScene(str(path), meshes, (bounds_min + bounds_max) * .5, radius,
                        list(dict.fromkeys(diagnostics))[:250], total_triangles, nif.bs)
