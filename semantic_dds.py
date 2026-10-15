"""Semantic mip generation and bounded DDS chain validation.

Prepared mip levels are given
to DirectXTex for compression only, without resizing, gamma conversion, or
normal-map synthesis. CPU compression is explicit. No downloaded components.
"""
from __future__ import annotations
import os, struct, subprocess, tempfile
from pathlib import Path
from dataclasses import replace
import numpy as np
from PIL import Image
import dds
from metal_reconstruction import srgb_to_linear, linear_to_srgb


def _check_rgba(rgba):
    arr=np.asarray(rgba)
    if arr.dtype != np.uint8 or arr.ndim != 3 or arr.shape[2] != 4 or min(arr.shape[:2])<1:
        raise ValueError('Expected a nonempty uint8 RGBA image')
    return arr


def _box(array,shape,cancel_event=None):
    # Floating point planes: Pillow does no color-space interpretation here.
    h,w=shape
    planes=[]
    for c in range(array.shape[2]):
        dds._check_cancel(cancel_event)
        planes.append(np.asarray(Image.fromarray(array[...,c].astype(np.float32)).resize((w,h),Image.Resampling.BOX),dtype=np.float64))
    dds._check_cancel(cancel_event)
    return np.stack(planes,axis=-1)


def build_semantic_mips(rgba,semantic='color',transfer_function='linear',addressing='clamp',alpha_mode='straight',*,cancel_event=None):
    dds._check_cancel(cancel_event)
    rgba=_check_rgba(rgba)
    if semantic not in ('color','normal','scalar'):raise ValueError('Unknown mip semantic')
    if transfer_function not in ('linear','srgb'):raise ValueError('Explicit mip transfer function required')
    if semantic!='color' and transfer_function!='linear':raise ValueError('Numeric mip data cannot use color gamma')
    if addressing not in ('clamp','wrap'):raise ValueError('Explicit texture addressing required')
    if alpha_mode not in ('straight','numeric'):raise ValueError('Unsupported alpha mode')
    levels=[rgba.copy()]
    work=rgba.astype(np.float64)/255
    if semantic=='normal':
        xy=work[...,:2]*2-1
        z=np.sqrt(np.maximum(0,1-np.sum(xy*xy,axis=-1)))
        xyz=np.dstack((xy,z));length=np.linalg.norm(xyz,axis=-1,keepdims=True)
        work=np.dstack((xyz/np.maximum(length,1e-12),work[...,3]))
    elif semantic=='color':
        if transfer_function=='srgb':work[...,:3]=srgb_to_linear(work[...,:3])
        if alpha_mode=='straight':work[...,:3]*=work[...,3:4]
    # Box footprints cover the source domain exactly and do not sample beyond
    # its boundaries. Clamp/wrap therefore agree for this non-expanding filter.
    while work.shape[0]>1 or work.shape[1]>1:
        dds._check_cancel(cancel_event)
        shape=(max(1,work.shape[0]//2),max(1,work.shape[1]//2))
        work=_box(work,shape,cancel_event)
        stored=work.copy()
        if semantic=='normal':
            length=np.linalg.norm(work[...,:3],axis=-1,keepdims=True)
            invalid=length[...,0]<1e-12
            work[...,:3]/=np.maximum(length,1e-12)
            work[invalid,:3]=(0,0,1)
            stored=work.copy();stored[...,:3]=stored[...,:3]*.5+.5
        elif semantic=='color':
            if alpha_mode=='straight':
                stored[...,:3]/=np.maximum(stored[...,3:4],1e-12)
                stored[stored[...,3]==0,:3]=0
            if transfer_function=='srgb':stored[...,:3]=linear_to_srgb(stored[...,:3])
        levels.append(np.rint(np.clip(stored,0,1)*255).astype(np.uint8))
    dds._check_cancel(cancel_event)
    return levels


def _level_size(width,height,fmt):
    if fmt.startswith(('BC1_','BC4_')):return max(1,(width+3)//4)*max(1,(height+3)//4)*8
    if fmt.startswith(('BC2_','BC3_','BC5_','BC6H_','BC7_')):return max(1,(width+3)//4)*max(1,(height+3)//4)*16
    if fmt.startswith(('R8G8B8A8_','B8G8R8A8_')):return width*height*4
    if fmt=='B8G8R8_UNORM':return width*height*3
    if fmt=='R8_UNORM':return width*height
    raise ValueError('Unsupported validation DDS format '+fmt)


def validate_mip_layout(path,*,cancel_event=None):
    """Validate DDS shape/header and every mip extent without reading pixels.

    The same strict EOF/cube/array/depth checks apply to the full chain. Callers
    that only decode mip zero avoid loading/slicing every unused lower mip.
    """
    dds._check_cancel(cancel_event)
    info=dds.parse_dds_header(str(path))
    with open(path,'rb') as stream:
        raw=stream.read(148)
        stream.seek(0,2);file_size=stream.tell()
    if not 0<info.width<=16384 or not 0<info.height<=16384:raise ValueError('Invalid dimensions')
    if not 1<=info.mipmaps<=max(info.width,info.height).bit_length():raise ValueError('Invalid mip count')
    header=struct.unpack_from('<31I',raw,4)
    if header[0]!=124 or header[18]!=32:raise ValueError('Invalid DDS header sizes')
    if header[5]>1 or header[27] & 0xFE00:raise ValueError('Only ordinary single 2D DDS images are supported')
    if info.dx10:
        fmt,dimension,misc,array_size,misc2=struct.unpack_from('<5I',raw,128)
        if dimension!=3 or array_size!=1 or misc & 4:raise ValueError('Array/cube/volume DDS is unsupported')
    padded=False
    if info.fmt in dds._UNCOMPRESSED_BYTES:
        tight=info.width*dds._UNCOMPRESSED_BYTES[info.fmt]
        padded=bool(info.row_pitch and info.row_pitch!=tight)
        if padded and info.mipmaps!=1:
            raise ValueError('Padded DDS mip chains have ambiguous lower-level row alignment')
    layout=[];offset=info.data_offset;w=info.width;h=info.height
    for level in range(info.mipmaps):
        dds._check_cancel(cancel_event)
        size=info.row_pitch*h if padded else _level_size(w,h,info.fmt)
        if offset+size>file_size:raise ValueError('Truncated mip payload at '+str(level))
        layout.append((w,h,offset,size));offset+=size;w=max(1,w//2);h=max(1,h//2)
    if offset!=file_size:raise ValueError('Unexpected payload after mip chain')
    return info,layout


def read_mip_payloads(path,*,cancel_event=None):
    """Validate and return a bounded ordinary 2D DDS mip chain."""
    info,layout=validate_mip_layout(path,cancel_event=cancel_event);result=[]
    with open(path,'rb') as stream:
        for w,h,offset,size in layout:
            dds._check_cancel(cancel_event)
            stream.seek(offset);payload=dds._read_bytes(stream,size,cancel_event)
            if len(payload)!=size:raise ValueError('DDS changed during read')
            result.append((w,h,payload))
    return info,result


def decode_mip_levels(path,*,cancel_event=None):
    info,blobs=read_mip_payloads(path,cancel_event=cancel_event)
    images=[]
    if info.fmt in dds._UNCOMPRESSED_BYTES:
        for index,(w,h,payload) in enumerate(blobs):
            dds._check_cancel(cancel_event)
            mip_info=replace(info,width=w,height=h,mipmaps=1,
                             row_pitch=info.row_pitch if index==0 else 0)
            images.append(dds._decode_uncompressed_payload(payload,mip_info,cancel_event))
        return images
    with tempfile.TemporaryDirectory(prefix='mc-mip-read-') as temp:
        for index,(w,h,payload) in enumerate(blobs):
            dds._check_cancel(cancel_event)
            single=Path(temp)/(str(index)+'.dds')
            single.write_bytes(dds._dds_header(w,h,1,info.fmt,len(payload))+payload)
            images.append(dds.decode_dds_pixels(str(single),cancel_event=cancel_event))
    return images


def _write_uncompressed(levels,path,srgb_tag=False,cancel_event=None):
    fmt='R8G8B8A8_UNORM_SRGB' if srgb_tag else 'R8G8B8A8_UNORM'
    with open(path,'wb') as f:
        dds._write_bytes(f,dds._dds_header(levels[0].shape[1],levels[0].shape[0],len(levels),fmt,levels[0].nbytes),cancel_event)
        for level in levels:
            dds._check_cancel(cancel_event)
            dds._write_bytes(f,level.tobytes(),cancel_event)


def _copy_exclusive(source,target,cancel_event=None):
    dds._check_cancel(cancel_event)
    created=False
    try:
        with source.open('rb') as src,target.open('xb') as dst:
            created=True
            while True:
                dds._check_cancel(cancel_event)
                block=src.read(1024*1024)
                if not block:break
                dds._write_bytes(dst,block,cancel_event)
    except Exception:
        if created:target.unlink(missing_ok=True)
        raise


def encode_semantic_dds(rgba,fmt,out_path,mipmaps=True,texconv_path=None,*,semantic='color',
                        transfer_function='linear',addressing='clamp',alpha_mode='straight',cancel_event=None):
    dds._check_cancel(cancel_event)
    rgba=_check_rgba(rgba);out_path=Path(out_path)
    if out_path.exists():raise FileExistsError('Refusing to overwrite '+str(out_path))
    if fmt.startswith('BC') and (rgba.shape[0]%4 or rgba.shape[1]%4):
        raise ValueError('BC top-level dimensions must be multiples of four; no automatic resize')
    if semantic in ('normal','scalar') and fmt.endswith('_SRGB'):
        raise ValueError('Numeric normal/specular data cannot be stored with an sRGB tag')
    levels=build_semantic_mips(rgba,semantic,transfer_function,addressing,alpha_mode,cancel_event=cancel_event) if mipmaps else [rgba.copy()]
    out_path.parent.mkdir(parents=True,exist_ok=True)
    # A locked private scratch file must not hide a fatal owned-process cleanup
    # error and accidentally let the batch launch the next encoder.
    with tempfile.TemporaryDirectory(prefix='mc-semantic-',ignore_cleanup_errors=True) as temp:
        source=Path(temp)/'prepared.dds';encoded=Path(temp)/'encoded';encoded.mkdir()
        # Match the destination tag so DirectXTex does not interpret this
        # compression step as a transfer-function conversion. Pixel encoding
        # and metadata are separate explicit contracts above.
        _write_uncompressed(levels,source,fmt.endswith('_SRGB'),cancel_event)
        if texconv_path and Path(texconv_path).is_file():
            command=[str(texconv_path),'-nologo','-nogpu','-f',fmt,'-m',str(len(levels)),
                     '-dx10','-y','-o',str(encoded),'--',str(source)]
            p=dds._run_texconv(command,cancel_event)
            if p.returncode:raise dds.DDSError('Semantic texconv failed: '+(p.stderr or p.stdout).strip())
            produced=encoded/'prepared.dds'
            info,payloads=read_mip_payloads(produced,cancel_event=cancel_event)
            if info.fmt!=fmt or len(payloads)!=len(levels):raise dds.DDSError('texconv changed the prepared DDS contract')
            if [(h,w) for w,h,_ in payloads]!=[x.shape[:2] for x in levels]:raise dds.DDSError('texconv resized prepared mip levels')
            # Exclusive creation avoids an accidental overwrite after validation.
            _copy_exclusive(produced,out_path,cancel_event)
            return fmt,None
        if fmt not in ('BC1_UNORM','BC3_UNORM','BC5_UNORM'):
            if fmt not in ('R8G8B8A8_UNORM','R8G8B8A8_UNORM_SRGB'):
                raise dds.DDSError('Exact requested semantic format requires bundled texconv; no silent downgrade')
            _copy_exclusive(source,out_path,cancel_event)
            return fmt,'numpy semantic mips; uncompressed DDS output'
        blobs=[dds._encode_level_fallback(level,fmt,cancel_event) for level in levels]
        prepared=Path(temp)/'fallback.dds'
        with prepared.open('wb') as target:
            dds._write_bytes(target,dds._dds_header(rgba.shape[1],rgba.shape[0],len(levels),fmt,len(blobs[0])),cancel_event)
            for blob in blobs:dds._write_bytes(target,blob,cancel_event)
        read_mip_payloads(prepared,cancel_event=cancel_event)
        _copy_exclusive(prepared,out_path,cancel_event)
        return fmt,'numpy fallback compression; semantic mips retained'


def decode_normal_dds(path,info=None,*,cancel_event=None):
    """Decode signed BC5 endpoints correctly, then map to UNORM exactly once.

    Output matches the existing uint8 RGBA decoder contract. Quantization uses
    the legacy truncation convention (zero -> 127). The -128 SNORM endpoint is
    clamped to -127 BEFORE interpolation, as in DirectXTex BC4BC5.cpp; this is
    an independent decoder correctness fix, not part of metal reconstruction.
    """
    dds._check_cancel(cancel_event)
    info=info or dds.parse_dds_header(str(path))
    if info.fmt!='BC5_SNORM':return dds.decode_dds_pixels(str(path),info,cancel_event=cancel_event)
    validate_mip_layout(path,cancel_event=cancel_event)
    bw,bh=dds._blocks_dims(info.width,info.height)
    with open(path,'rb') as stream:
        stream.seek(info.data_offset);raw=dds._read_bytes(stream,bw*bh*16,cancel_event)
    return dds._decode_bc_payload(raw,info,clamp_snorm=True,cancel_event=cancel_event)
