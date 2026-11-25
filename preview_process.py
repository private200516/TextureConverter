"""Fault-isolated local previews with bounded, data-only IPC.

The same-user worker is not an OS security sandbox. Its Windows Job Object
contains resource use and lifetime; parser/native faults cannot crash Qt.
"""
from __future__ import annotations
import dataclasses
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import numpy as np
from security_io import json_bounded, open_read, read_bounded, MAX_JSON_BYTES

MAX_ARRAY_BYTES = 256 * 1024 * 1024
MAX_PROCESS_BYTES = 2 * 1024 * 1024 * 1024
PREVIEW_TIMEOUT = 120

# Accept only scalar numeric descriptors produced by ndarray.dtype.str.
_WIRE_DTYPES = {'|b1', '|i1', '|u1'} | {
    order + kind + str(size)
    for order in '<>' for kind in 'iuf' for size in (2, 4, 8)
}


class PreviewProcessError(ValueError): pass


def write_wire(directory, name, value):
    """Only JSON scalars/containers and raw numeric arrays; never pickle."""
    total = 0;arrays={}
    with (Path(directory)/(name+'.bin')).open('xb') as data:
        def encode(value, depth=0):
            nonlocal total
            if depth>48:raise PreviewProcessError('Preview result nesting limit')
            if isinstance(value,np.ndarray):
                if id(value) in arrays:return {'$ref':arrays[id(value)]}
                if value.dtype.kind not in 'buif' or value.dtype.itemsize>8 or value.ndim>4:
                    raise PreviewProcessError('Unsupported preview numeric array')
                size=value.nbytes
                if total+size>MAX_ARRAY_BYTES:raise PreviewProcessError('Preview IPC exceeds 256 MiB array budget')
                index=len(arrays);arrays[id(value)]=index
                record={'$array':index,'shape':list(value.shape),'dtype':value.dtype.str,'offset':total,'nbytes':size}
                data.write(np.ascontiguousarray(value).tobytes());total+=size
                return record
            if isinstance(value,np.generic):return encode(value.item(),depth+1)
            if dataclasses.is_dataclass(value):
                return {'$class':type(value).__name__,'fields':{f.name:encode(getattr(value,f.name),depth+1) for f in dataclasses.fields(value)}}
            if isinstance(value,dict):
                if any(not isinstance(key,str) or key.startswith('$') for key in value):raise PreviewProcessError('Unsupported preview object key')
                return {key:encode(item,depth+1) for key,item in value.items()}
            if isinstance(value,(list,tuple)):return [encode(item,depth+1) for item in value]
            if value is None or type(value) in (str,bool,int):return value
            if type(value) is float and math.isfinite(value):return value
            raise PreviewProcessError('Unsupported preview value')
        tree=encode(value)
    raw=json.dumps(tree,ensure_ascii=True,allow_nan=False,separators=(',',':')).encode('utf8')
    if len(raw)>MAX_JSON_BYTES:raise PreviewProcessError('Preview JSON exceeds 8 MiB budget')
    with (Path(directory)/(name+'.json')).open('xb') as metadata:
        metadata.write(raw)


def read_wire(directory, name):
    root=Path(directory)
    tree=json_bounded(read_bounded(root/(name+'.json'),MAX_JSON_BYTES,'Preview JSON'))
    binary=root/(name+'.bin')
    allocated=0;arrays=[]
    with open_read(binary) as data:
        binary_size = os.fstat(data.fileno()).st_size
        if binary_size > MAX_ARRAY_BYTES:
            raise PreviewProcessError('Preview IPC exceeds 256 MiB array budget')
        def decode(value,depth=0):
            nonlocal allocated
            if depth>48:raise PreviewProcessError('Preview result nesting limit')
            if isinstance(value,list):return [decode(item,depth+1) for item in value]
            if not isinstance(value,dict):return value
            if '$ref' in value:
                index=value['$ref']
                if set(value)!={'$ref'} or type(index) is not int or not 0<=index<len(arrays):raise PreviewProcessError('Invalid preview array reference')
                return arrays[index]
            if '$array' in value:
                if set(value)!={'$array','shape','dtype','offset','nbytes'}:raise PreviewProcessError('Invalid preview array descriptor')
                if type(value['$array']) is not int or value['$array']!=len(arrays):raise PreviewProcessError('Invalid preview array order')
                descriptor, shape = value['dtype'], value['shape']
                if (not isinstance(descriptor, str) or descriptor not in _WIRE_DTYPES
                        or not isinstance(shape, list) or len(shape) > 4
                        or any(type(dim) is not int or not 0 <= dim <= MAX_ARRAY_BYTES for dim in shape)):
                    raise PreviewProcessError('Invalid preview numeric type/shape')
                dtype = np.dtype(descriptor)
                size, offset = value['nbytes'], value['offset']
                if (type(offset) is not int or offset != allocated or type(size) is not int
                        or size != math.prod(shape) * dtype.itemsize or offset + size > binary_size):
                    raise PreviewProcessError('Preview array exceeds IPC budget or has a non-sequential offset')
                array = np.empty(shape, dtype=dtype)
                if size and data.readinto(array.reshape(-1).view(np.uint8)) != size:
                    raise PreviewProcessError('Truncated preview array')
                allocated += size
                arrays.append(array)
                return array
            if '$class' in value:
                from nif_preview_data import PreviewScene,PreviewMesh,PreviewMaterial
                kinds={c.__name__:c for c in (PreviewScene,PreviewMesh,PreviewMaterial)}
                cls=kinds.get(value['$class'])
                if cls is None or set(value)!={'$class','fields'} or set(value['fields'])!={f.name for f in dataclasses.fields(cls)}:
                    raise PreviewProcessError('Invalid preview scene record')
                return cls(**{key:decode(item,depth+1) for key,item in value['fields'].items()})
            if any(key.startswith('$') for key in value):raise PreviewProcessError('Invalid preview object')
            return {key:decode(item,depth+1) for key,item in value.items()}
        result = decode(tree)
        if allocated != binary_size:
            raise PreviewProcessError('Unexpected trailing preview array data')
        return result


class _WindowsJob:
    def __init__(self,process):
        self.handle=None
        if os.name!='nt':return
        import ctypes
        from ctypes import wintypes as w
        class Basic(ctypes.Structure):
            _fields_=[('process_time',ctypes.c_int64),('job_time',ctypes.c_int64),('flags',w.DWORD),
                      ('minimum',ctypes.c_size_t),('maximum',ctypes.c_size_t),('active',w.DWORD),
                      ('affinity',ctypes.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]
        class Counters(ctypes.Structure):
            _fields_=[(key,ctypes.c_uint64) for key in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
        class Extended(ctypes.Structure):
            _fields_=[('basic',Basic),('io',Counters),('process_memory',ctypes.c_size_t),
                      ('job_memory',ctypes.c_size_t),('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes=[w.LPVOID,w.LPCWSTR];self.kernel.CreateJobObjectW.restype=w.HANDLE
        self.kernel.SetInformationJobObject.argtypes=[w.HANDLE,ctypes.c_int,w.LPVOID,w.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE]
        self.kernel.CloseHandle.argtypes=[w.HANDLE]
        self.handle=self.kernel.CreateJobObjectW(None,None)
        if not self.handle:raise ctypes.WinError(ctypes.get_last_error())
        limits=Extended();limits.basic.flags=0x2000|0x100|0x8;limits.basic.active=1
        limits.process_memory=MAX_PROCESS_BYTES
        if (not self.kernel.SetInformationJobObject(self.handle,9,ctypes.byref(limits),ctypes.sizeof(limits)) or
                not self.kernel.AssignProcessToJobObject(self.handle,w.HANDLE(int(process._handle)))):
            error=ctypes.get_last_error();self.close();raise ctypes.WinError(error)
    def close(self):
        if self.handle:self.kernel.CloseHandle(self.handle);self.handle=None


def _stop(process):
    if process.poll() is None:
        try:process.terminate()
        except OSError:
            if process.poll() is None:raise
        try:process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill();process.wait(timeout=2)


def _validate_scene(scene):
    from nif_preview_data import PreviewScene,PreviewMesh,MAX_TRIANGLES,MAX_VERTICES,MAX_TEXTURE_PIXELS
    if not isinstance(scene,PreviewScene) or not isinstance(scene.meshes,list) or len(scene.meshes)>100000:
        raise PreviewProcessError('Invalid preview scene')
    if not isinstance(scene.center,np.ndarray) or scene.center.shape!=(3,) or not np.isfinite(scene.center).all() or not 0<scene.radius<=2e9:
        raise PreviewProcessError('Invalid preview bounds')
    rows=triangles=pixels=0;seen=set()
    for mesh in scene.meshes:
        if (not isinstance(mesh,PreviewMesh) or not isinstance(mesh.vertices,np.ndarray)
                or mesh.vertices.dtype!=np.float32 or mesh.vertices.ndim!=2 or mesh.vertices.shape[1]!=12
                or not 0<len(mesh.vertices)<=65535):
            raise PreviewProcessError('Invalid preview vertices')
        indices = mesh.indices
        if (not isinstance(indices,np.ndarray) or indices.dtype!=np.uint16 or indices.ndim!=2
                or indices.shape[1]!=3 or len(indices)==0):
            raise PreviewProcessError('Invalid preview triangle indices')
        rows+=len(mesh.vertices);triangles+=len(indices)
        if rows>MAX_VERTICES or triangles>MAX_TRIANGLES or not np.isfinite(mesh.vertices).all():
            raise PreviewProcessError('Preview geometry budget exceeded')
        if int(indices.max())>=len(mesh.vertices):
            raise PreviewProcessError('Preview triangle references missing vertex')
        for entry in mesh.material.textures.values():
            if not isinstance(entry,list) or len(entry)!=2:raise PreviewProcessError('Invalid preview texture')
            image=entry[1]
            if image.dtype!=np.uint8 or image.ndim!=3 or image.shape[2]!=4:raise PreviewProcessError('Invalid preview texture pixels')
            if id(image) not in seen:pixels+=image.shape[0]*image.shape[1];seen.add(id(image))
            if pixels>MAX_TEXTURE_PIXELS:raise PreviewProcessError('Preview texture budget exceeded')
    if type(scene.triangles) is not int or scene.triangles!=triangles:
        raise PreviewProcessError('Preview triangle count mismatch')
    return scene


def load_isolated(kind, args, cancel=None, timeout=PREVIEW_TIMEOUT):
    if kind not in ('nif','metal'):raise ValueError('Invalid preview action')
    if cancel is not None and cancel.is_set():raise InterruptedError('Preview loading cancelled')
    trusted=Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix='texture-preview-') as directory:
        write_wire(directory,'request',{'kind':kind,'args':args})
        environment={key:value for key,value in os.environ.items() if not key.upper().startswith('PYTHON') and key.upper() not in ('QT_PLUGIN_PATH','QT_QPA_PLATFORM_PLUGIN_PATH','QML2_IMPORT_PATH')}
        if os.name=='nt':environment['PATH']=os.pathsep.join((str(Path(sys.executable).parent),str(Path(os.environ.get('SystemRoot','C:/Windows'))/'System32')))
        with tempfile.TemporaryFile() as diagnostics:
            process=subprocess.Popen([sys.executable,'-I','-B','-X','utf8',str(trusted/'preview_worker.py'),directory],
                cwd=trusted,env=environment,stdin=subprocess.PIPE,stdout=diagnostics,stderr=diagnostics,
                shell=False,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            job=None
            try:
                job=_WindowsJob(process)
                process.stdin.write(b'1');process.stdin.close()
                deadline=time.monotonic()+timeout
                while process.poll() is None:
                    if cancel is not None and cancel.is_set():raise InterruptedError('Preview loading cancelled')
                    if time.monotonic()>=deadline:raise PreviewProcessError('Preview timed out after '+str(timeout)+' seconds')
                    if os.fstat(diagnostics.fileno()).st_size>4*1024*1024:raise PreviewProcessError('Preview diagnostics exceeded 4 MiB limit')
                    for name,limit in (('response.json',MAX_JSON_BYTES),('response.bin',MAX_ARRAY_BYTES)):
                        path=Path(directory)/name
                        if path.exists() and path.stat().st_size>limit:raise PreviewProcessError('Preview output exceeded resource budget')
                    try:process.wait(timeout=.05)
                    except subprocess.TimeoutExpired:pass
                if cancel is not None and cancel.is_set():raise InterruptedError('Preview loading cancelled')
                if process.returncode:
                    detail=''
                    error=Path(directory)/'error.txt'
                    if error.is_file():detail=read_bounded(error,16384,'Preview error').decode('utf8',errors='replace')
                    raise PreviewProcessError('Preview worker failed (exit '+str(process.returncode)+', '+str(MAX_PROCESS_BYTES//(1024*1024))+' MiB memory limit). '+detail)
                result=read_wire(directory,'response')
                return _validate_scene(result) if kind=='nif' else result
            finally:
                if job is not None:job.close()
                if process.stdin is not None and not process.stdin.closed:process.stdin.close()
                _stop(process)


def load_scene_isolated(path,converted='',source='',cancel=None,timeout=PREVIEW_TIMEOUT):
    return load_isolated('nif',[str(path),str(converted),str(source)],cancel,timeout)


def preview_job_isolated(job,options=None,cancel=None,timeout=PREVIEW_TIMEOUT):
    return load_isolated('metal',[job,options],cancel,timeout)
