"""Material references first; filename grouping is limited to unreferenced textures."""
from __future__ import annotations
import hashlib, os, time
from collections import OrderedDict
from pathlib import Path, PurePosixPath
from material_codec import parse, read_material_bytes

def sha256(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()

def safe_relative(value):
    s=str(value).replace('\\','/')
    if s.startswith('/') or s.endswith('/'):raise ValueError('Absolute or directory asset path is not allowed')
    p=PurePosixPath(s)
    if not s or ':' in s or any(t in ('..','.') for t in p.parts):raise ValueError('Unsafe relative asset path: '+str(value))
    return p.as_posix()

class PreparedJobs(list):
    """List-compatible queue with a directory signature for optional GUI reuse."""
    def __init__(self, values=(), scan_context=None):
        super().__init__(values)
        self.scan_context = scan_context


def _stamp(path):
    stat = os.stat(path, follow_symlinks=False)
    return (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino, stat.st_size)


def scan_context_matches(context, root, direction='76-to-4', include_textures=True, cancel_event=None):
    if not context or context.get('input_root') != str(Path(root).resolve()):return False
    if context.get('direction') != direction or context.get('include_textures') != include_textures:return False
    stamps=context.get('directory_stamps')
    if not stamps:return False
    for path,stamp in stamps.items():
        if cancel_event is not None and cancel_event.is_set():return False
        try:
            if tuple(stamp)!=_stamp(path):return False
        except OSError:return False
    return True


def walk_files(root,cancel_event=None,directory_stamps=None):
    root=Path(root).resolve()
    if root.is_file():
        if directory_stamps is not None:directory_stamps[str(root)]=_stamp(root)
        yield root;return
    for base,dirs,files in os.walk(root,followlinks=False):
        if cancel_event is not None and cancel_event.is_set():return
        if directory_stamps is not None:directory_stamps[str(base)]=_stamp(base)
        dirs[:]=sorted(x for x in dirs if not (Path(base)/x).is_symlink() and not (Path(base)/x).is_junction())
        for name in sorted(files):
            if cancel_event is not None and cancel_event.is_set():return
            p=Path(base)/name
            if not p.is_symlink() and not p.is_junction():yield p

def asset_root(root):
    p=Path(root).resolve()
    if p.is_file():p=p.parent
    # Only a named asset-tree ancestor authorizes looking outside the selected
    # directory. Do not discover unrelated sibling assets in private ancestors.
    for q in (p,*p.parents):
        if q.name.lower() in ('materials','textures'):return q.parent
    return p


def _scan_resource_root(files, fallback, cancel_event=None):
    """Find one wrapped Materials/Textures tree within the selected data.

    A shared Fallout 4/Fallout 76 parent is ambiguous: texture filenames alone
    cannot determine source game or safely choose overlapping output paths.
    """
    roots={}
    for path in files:
        if cancel_event is not None and cancel_event.is_set():return fallback
        parts=path.relative_to(fallback).parts
        candidate=fallback
        for index,part in enumerate(parts[:-1]):
            if part.lower() in ('materials','textures'):
                candidate=fallback.joinpath(*parts[:index]);break
        roots[str(candidate).lower()]=candidate
        if len(roots)>1:
            raise ValueError('MULTIPLE_RESOURCE_ROOTS: select one game asset directory '
                             '(Fallout 4 or Fallout 76), rather than their shared parent')
    return next(iter(roots.values()),fallback)

def asset_relative(path,base,category):
    rel=Path(path).resolve().relative_to(base).as_posix()
    return rel if rel.split('/')[0].lower() in ('textures','materials') else category+'/'+rel

def resolve_reference(base,reference,category='textures'):
    rel=safe_relative(reference)
    if rel.lower().startswith('data/'+category+'/'):rel=rel[5:]
    if rel.split('/')[0].lower()!=category:rel=category+'/'+rel
    candidate=(base/rel).resolve()
    if not candidate.is_relative_to(base):raise ValueError('Reference escapes resource root')
    return candidate,rel

def scan_jobs(root,direction='76-to-4',include_textures=True,cancel_event=None,progress_cb=None):
    if direction not in ('76-to-4','4-to-76'):raise ValueError('Invalid direction')
    root=Path(root).resolve();base=asset_root(root)
    if not root.exists():raise FileNotFoundError(root)
    stamps={};seen=0;last_progress=time.monotonic()
    def progress(name,force=False):
        nonlocal seen,last_progress
        seen+=1;now=time.monotonic()
        if progress_cb and (force or seen%250==0 or now-last_progress>=.1):
            progress_cb(seen,0,str(name));last_progress=now
    files=[]
    for path in walk_files(root,cancel_event,directory_stamps=stamps):
        if path.suffix.lower() in (('.dds','.bgsm','.bgem') if include_textures else ('.bgsm','.bgem')):files.append(path)
        progress(path)
    base=_scan_resource_root(files,base,cancel_event)
    context=dict(input_root=str(root),direction=direction,include_textures=include_textures,directory_stamps=stamps)
    jobs=PreparedJobs(scan_context=context);referenced=set();reference_cache=OrderedDict()
    def reference(ref):
        key=ref.lower()
        if key in reference_cache:
            reference_cache.move_to_end(key);return reference_cache[key]
        resolved,texrel=resolve_reference(base,ref)
        value=(resolved,texrel,resolved.is_file());reference_cache[key]=value
        parent=resolved.parent
        while not parent.exists() and parent!=base:parent=parent.parent
        if parent.is_dir() and str(parent) not in stamps:stamps[str(parent)]=_stamp(parent)
        if len(reference_cache)>8192:reference_cache.popitem(last=False)
        return value
    def scanned_relative(path,category):
        rel=path.relative_to(base).as_posix()
        return rel if rel.split('/')[0].lower() in ('textures','materials') else category+'/'+rel
    for path in files:
        if cancel_event is not None and cancel_event.is_set():return []
        if path.suffix.lower() not in ('.bgsm','.bgem'):continue
        rel=scanned_relative(path,'materials');j=dict(id=hashlib.sha256((str(base).lower()+'|'+rel.lower()).encode()).hexdigest()[:16],name=rel,material_path=str(path),input_root=str(root),asset_root=str(base),relative_path=rel,kind='material',texture_paths={},texture_references={},diagnostics=[],metadata={},include_textures=include_textures)
        try:
            payload=read_material_bytes(path);mat=parse(payload,json_kind=path.suffix[1:].upper(),json_version=22 if direction=='76-to-4' else 2);j['material_sha256']=hashlib.sha256(payload).hexdigest();j['material_version']=mat.version;j['material_kind']=mat.kind;j['material_fields']=mat.fields
            j['material_source_info']=dict(mat.source_info)
            if mat.source_info:j['metadata']['material_import']=dict(mat.source_info)
            if (direction=='76-to-4' and mat.version<=2) or (direction=='4-to-76' and mat.version>2):
                j['diagnostics'].append('SOURCE_DIRECTION_MISMATCH: select the source game and conversion direction explicitly')
            mapping={'DiffuseTexture':'d','BaseTexture':'d','NormalTexture':'n','SpecularTexture':'r','LightingTexture':'l','SmoothSpecTexture':'s','GlowTexture':'g'}
            for slot,ref in mat.textures().items():
                resolved,texrel,exists=reference(ref)
                referenced.add(str(resolved).lower())
                if not exists:j['diagnostics'].append('MATERIAL_REFERENCE_ERROR: '+slot+' '+ref)
                j['texture_references'][slot]=dict(source=str(resolved),relative=texrel,exists=exists)
                role=mapping.get(slot)
                if role and exists:j['texture_paths'][role]=str(resolved)
            if mat.kind=='BGEM' or any(mat.fields.get(k) for k in ('AlphaBlend0','AlphaTest','Refraction','GrayscaleToPaletteColor','Translucency','Hair','Tree','Facegen','SkinTint','Terrain','Glowmap','EmitEnabled','ModelSpaceNormals')) or any(mat.fields.get(k) for k in ('GreyscaleTexture','FlowTexture','InnerLayerTexture','WrinklesTexture')):
                j['diagnostics'].append('UNSUPPORTED_SHADER')
            if 'r' not in j['texture_paths']:j['diagnostics'].append('MISSING_REFLECTANCE')
            if not j['diagnostics']:j['diagnostics'].append('READY_FOR_METAL_ANALYSIS')
        except Exception as e:j['diagnostics'].append('MATERIAL_PARSE_ERROR: '+str(e))
        j['_paths_resolved']=True;jobs.append(j);progress(path)
    if include_textures:
        groups={}
        for path in files:
            if cancel_event is not None and cancel_event.is_set():return []
            if path.suffix.lower()!='.dds' or str(path).lower() in referenced:continue
            stem,sep,role=path.stem.rpartition('_')
            if not sep or role.lower() not in ('d','n','r','l','s','g'):
                stem=path.stem;role='unknown'
            key=(str(path.parent).lower(),stem.lower());groups.setdefault(key,[]).append((role.lower(),path))
        for (_,stem),items in sorted(groups.items()):
            if cancel_event is not None and cancel_event.is_set():return PreparedJobs()
            first=items[0][1];rel=scanned_relative(first,'textures');identity=rel.rsplit('/',1)[0]+'/'+stem
            maps={};diagnostics=[]
            for role,path in items:
                if role in maps:
                    diagnostics.append('AMBIGUOUS_TEXTURE_PAIRING')
                else:maps[role]=str(path)
            if 'r' not in maps:diagnostics.append('MISSING_REFLECTANCE')
            diagnostics.append('NEEDS_MANUAL_REVIEW')
            jobs.append(dict(id=hashlib.sha256((str(base).lower()+'|'+identity.lower()).encode()).hexdigest()[:16],name=identity,material_path=None,input_root=str(root),asset_root=str(base),relative_path=identity,kind='texture_group',_paths_resolved=True,texture_paths=maps,texture_references={},diagnostics=diagnostics,metadata={},include_textures=True))
            progress(first)
    counts={};samples={}
    for j in jobs:
        if cancel_event is not None and cancel_event.is_set():return PreparedJobs()
        for src in j['texture_paths'].values():
            key=src.lower();counts[key]=counts.get(key,0)+1
    for j in jobs:
        if cancel_event is not None and cancel_event.is_set():return PreparedJobs()
        for src in j['texture_paths'].values():
            key=src.lower()
            if counts[key]>1:
                sample=samples.setdefault(key,[])
                if len(sample)<32:sample.append(j['id'])
    for j in jobs:
        if cancel_event is not None and cancel_event.is_set():return PreparedJobs()
        shared={role:counts[src.lower()] for role,src in j['texture_paths'].items() if counts[src.lower()]>1}
        j['shared_references']={role:samples[j['texture_paths'][role].lower()] for role in shared}
        if shared:
            j['shared_reference_counts']=shared
            j['shared_references_truncated']=any(count>32 for count in shared.values())
    progress(root,True)
    return jobs

analyze_jobs=scan_jobs
