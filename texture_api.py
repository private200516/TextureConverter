"""One local pipeline shared by Qt preview, grouped export and CLI."""
from __future__ import annotations
import csv, dataclasses, hashlib, json, os, shutil, tempfile, time, uuid
from pathlib import Path
import numpy as np
from PIL import Image
import dds, material
from material_codec import Material, parse, serialize, read_material_bytes
from options_policy import processing_options, job_options
from material_converter import convert_material, BUILD
from material_jobs import scan_jobs, sha256, safe_relative, asset_relative
from metal_reconstruction import ColorContract, MetalOptions, TargetProfile, reconstruct, align_numeric, MetalResult, ALGORITHM_VERSION, _validate_config, _array
from logger import ConversionLog

Cancelled=dds.CancellationRequested

def _check(cancel):
    dds._check_cancel(cancel)


def _source_hash(path,cancel=None):
    digest=hashlib.sha256()
    with open(path,'rb') as stream:
        while True:
            _check(cancel);block=stream.read(1024*1024)
            if not block:break
            digest.update(block)
    _check(cancel)
    return digest.hexdigest()


def _copy_file(source,target,cancel=None):
    with open(source,'rb') as src,open(target,'wb') as dst:
        while True:
            _check(cancel);block=src.read(1024*1024)
            if not block:break
            dds._write_bytes(dst,block,cancel)
    _check(cancel);shutil.copystat(source,target)

def _json(value):
    if isinstance(value,np.ndarray):return {'array_sha256':hashlib.sha256(value.tobytes()).hexdigest(),'shape':list(value.shape),'dtype':str(value.dtype)}
    if isinstance(value,Path):return str(value)
    if dataclasses.is_dataclass(value):return dataclasses.asdict(value)
    raise TypeError(type(value).__name__)

class _RuntimeOptions(dict):
    """Internal pipeline state; it cannot be constructed by JSON deserialization."""


def _options(cfg=None,extra=None):
    is_config=cfg is not None and dataclasses.is_dataclass(cfg)
    values={f.name:getattr(cfg,f.name) for f in dataclasses.fields(cfg)} if is_config else processing_options(cfg)
    values.update(processing_options(values.get('metal_options')))
    values['mode']=values.get('metal_mode',values.get('mode','off'))
    if extra is not None:
        values.update(extra if isinstance(extra,_RuntimeOptions) else processing_options(extra))
    values.pop('job_overrides',None);values.pop('metal_options',None)
    return _RuntimeOptions(values)


def _codec_identity(external=True):
    # This fixed installation path is never taken from processing configuration.
    tool=(Path(__file__).parent/'tools'/'texconv.exe').resolve()
    if external and tool.is_file():
        return {'backend':'texconv','path':str(tool),'sha256':sha256(tool)}
    return {'backend':'numpy-fallback','path':None,'sha256':None}


def _codec_context(external=True):
    identity=_codec_identity(external)
    code_paths=[Path(__file__),Path(__file__).parent/'dds.py',
                Path(__file__).parent/'semantic_dds.py',
                Path(__file__).parent/'metal_reconstruction.py',
                Path(__file__).parent/'options_policy.py']
    data={'code':{x.name:sha256(x) for x in code_paths},'codec':identity}
    return {'_codec_identity':identity,
            'codec_fingerprint':hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()}


def _checked_codec(opts):
    selected=opts['_codec_identity']
    current=_codec_identity(opts.get('texconv_path') is not False)
    if current!=selected:
        raise ValueError('CODEC_CHANGED_DURING_BATCH: selected codec identity changed; restart the batch')
    return selected['path']

def _header(path):
    info=dds.parse_dds_header(str(path))
    if 'FLOAT' in info.fmt or info.fmt.startswith('BC6H'):raise ValueError('HDR_DDS_UNSUPPORTED: refusing implicit 8-bit clipping')
    if not 0<info.width<=16384 or not 0<info.height<=16384 or info.width*info.height>67108864:raise ValueError('DDS dimensions exceed bounded decoder budget')
    if not 1<=info.mipmaps<=1+int(np.log2(max(info.width,info.height))):raise ValueError('Invalid mipmap count')
    return info

def _load(job,cancel=None,semantic_normal=False,preview=False,dependency_hashes=None,stage_cb=None):
    if 'AMBIGUOUS_TEXTURE_PAIRING' in job.get('diagnostics',[]):raise ValueError('AMBIGUOUS_TEXTURE_PAIRING: refusing to choose a filename match')
    arrays={};headers={};hashes=dict(dependency_hashes or {})
    if job.get('material_path') and job['material_path'] not in hashes:hashes[job['material_path']]=_source_hash(job['material_path'],cancel)
    if job.get('material_path') and job.get('material_sha256') and hashes.get(job.get('material_path'))!=job['material_sha256']:raise ValueError('MATERIAL_CHANGED_SINCE_SCAN: rescan before conversion')
    preheaders={role:_header(path) for role,path in job['texture_paths'].items()}
    total=sum(h.width*h.height*16 for h in preheaders.values())
    diffuse=preheaders.get('d')
    if diffuse:total+=diffuse.width*diffuse.height*(320 if semantic_normal or preview else 80)
    if total>1024*1024*1024:raise ValueError('MEMORY_BUDGET_EXCEEDED: estimated working set exceeds 1 GiB; reduce the source asset outside this tool or process a smaller supported asset')
    for role,path in job['texture_paths'].items():
        _check(cancel);h=preheaders[role];headers[role]=dataclasses.asdict(h)
        if path not in hashes:hashes[path]=_source_hash(path,cancel)
        if role!='unknown' and not (preview and role in ('n','g')):
            if stage_cb:stage_cb('Decoding / 解码 '+role.upper())
            from semantic_dds import validate_mip_layout
            validate_mip_layout(path,cancel_event=cancel)
            if role=='n' and semantic_normal:
                from semantic_dds import decode_normal_dds
                arrays[role]=decode_normal_dds(path,h,cancel_event=cancel)
            else:arrays[role]=dds.decode_dds_pixels(path,h,cancel_event=cancel)
            _check(cancel)
    return arrays,headers,hashes

def _contract(fmt,semantic,override=None):
    scalar=semantic=='reflectance_scalar'
    transfer='linear' if scalar else 'srgb' if fmt.endswith('_SRGB') else 'unknown'
    why='scalar reflectance data' if scalar else 'DDS explicit SRGB metadata' if transfer=='srgb' else 'DDS UNORM does not establish authoring transfer'
    if override and not scalar:return ColorContract(fmt,semantic,'explicit_override','stored',override,'explicit user override')
    return ColorContract(fmt,semantic,transfer,'stored',None,why)

MAX_MASK_PIXELS = 67108864
MAX_MASK_WORKING_BYTES = 1024 * 1024 * 1024


def _mask_shape(value,shape):
    # Check arrays/lists without first allocating a float64 copy.
    numeric=(int,float,bool,np.integer,np.floating,np.bool_)
    if isinstance(value,np.ndarray):
        valid=(value.shape==shape and value.dtype.kind in 'buif' and
               value.nbytes+value.size*16<=MAX_MASK_WORKING_BYTES)
    elif isinstance(value,(list,tuple)):
        valid=(len(value)==shape[0] and
               all(isinstance(row,(list,tuple,np.ndarray)) and
                   (not isinstance(row,np.ndarray) or row.ndim==1) and
                   len(row)==shape[1] and
                   all(isinstance(item,numeric) for item in row)
                   for row in value))
    else:
        valid=False
    if not valid:
        raise ValueError('MASK_SIZE_MISMATCH: masks must match source diffuse dimensions; no silent resize')


def _mask_budget(shape):
    shape=tuple(shape)
    if len(shape)!=2 or any(not isinstance(x,(int,np.integer)) or x<=0 for x in shape):
        raise ValueError('MASK_MEMORY_BUDGET_EXCEEDED: invalid mask dimensions')
    pixels=int(shape[0])*int(shape[1])
    if pixels>MAX_MASK_PIXELS or pixels*20>MAX_MASK_WORKING_BYTES:
        raise ValueError('MASK_MEMORY_BUDGET_EXCEEDED: mask working allocation is too large')
    return shape


def _mask(opts,key,pathkey,shape):
    v=opts.get(key);path=opts.get(pathkey)
    if v is None and not path:return None
    shape=_mask_budget(shape)
    if v is None:
        with Image.open(path) as im:
            if im.format!='PNG' or im.mode not in ('1','L','I;16','I'):raise ValueError('Masks must be grayscale PNG, not RGB/palette images')
            if im.size!=(shape[1],shape[0]):
                raise ValueError('MASK_SIZE_MISMATCH: masks must match source diffuse dimensions; no silent resize')
            if im.mode in ('I;16','I'):v=np.asarray(im,dtype=np.float64)/65535.
            else:v=np.asarray(im.convert('L'),dtype=np.float64)/255.
    _mask_shape(v,shape)
    a=np.asarray(v,dtype=np.float64)
    if a.shape!=shape:raise ValueError('MASK_SIZE_MISMATCH: masks must match source diffuse dimensions; no silent resize')
    return a

def _baseline(arrays,opts,cancel=None):
    _check(cancel)
    a={k:v.copy() for k,v in arrays.items()}
    if 'd' in a:
        if opts.get('ao_mode') and 'l' in a:a['d']=material.apply_ao_to_diffuse(a['d'],a['l'],opts.get('ao_strength',.3))
        _check(cancel)
        if opts.get('metal_compensation') and 'r' in a:a['d']=material.apply_metal_compensation(a['d'],a['r'],opts.get('metal_strength',.5))
        _check(cancel)
    if 'n' in a and opts.get('invert_green'):a['n']=material.invert_green_channel(a['n'])
    if 's' not in a and 'r' in a and 'l' in a and opts.get('generate_s',True):a['s']=material.build_specular(a['l'],a['r'])
    _check(cancel)
    if 'g' not in a and 'l' in a and opts.get('glow_from_l_alpha'):a['g']=material.build_glow_from_l_alpha(a['l'])
    _check(cancel)
    return a

def _prepare(job,opts,intent='export',cancel=None,dependency_hashes=None):
    arrays,headers,hashes=_load(job,cancel,opts.get('mode','off')!='off',intent=='preview',dependency_hashes,opts.get('_stage_cb'))
    if opts.get('_stage_cb'):opts['_stage_cb']('Channels / 通道处理')
    base=_baseline(arrays,opts,cancel)
    diagnostics=[x.split(':',1)[0] for x in job.get('diagnostics',[])]
    if 'd' not in base:return arrays,base,None,headers,hashes,diagnostics
    if base['d'].shape!=arrays['d'].shape:raise ValueError('LEGACY_RESIZE_CONFLICT: AO/metal compensation would resize diffuse; use matching grids or disable it')
    shape=base['d'].shape[:2];s=base.get('s')
    addressing='wrap' if job.get('material_fields',{}).get('TileFlags')==3 else 'clamp' if job.get('material_fields',{}).get('TileFlags')==0 else 'unknown'
    addressing=opts.get('addressing',addressing)
    if s is not None and s.shape[:2]!=shape:
        diagnostics.append('BASELINE_GRID_MISMATCH')
    rf=headers.get('r',{}).get('fmt','missing');df=headers['d']['fmt'];scalar=rf.startswith('BC4') or rf=='R8_UNORM'
    override=opts.get('color_contract_override','auto');dov=opts.get('source_d_transfer');rov=opts.get('source_r_transfer');tov=opts.get('target_d_transfer')
    if override=='srgb_linear':dov='srgb';rov='linear';tov='srgb'
    if override=='linear_linear':dov=rov=tov='linear'
    dc=_contract(df,'color',dov);rc=_contract(rf,'reflectance_scalar' if scalar else 'reflectance_rgb',rov)
    tc=_contract(opts.get('diffuse_fmt','BC7_UNORM'),'color',tov)
    if not tov and df.endswith('_SRGB'):tc=ColorContract(tc.storage_format,'color','srgb','stored',None,'baseline preserves source SRGB sample encoding; DDS tag is separately recorded')
    mode={'auto':'auto_conservative'}.get(opts.get('mode','off'),opts.get('mode','off'))
    confirmed=bool(opts.get('allow_experimental',False))
    if intent=='export' and confirmed:
        reviewed=opts.get('reviewed_source_hashes') or {}
        if reviewed!=hashes:
            confirmed=False;diagnostics.append('REVIEWED_SOURCE_CHANGED' if reviewed else 'SOURCE_REVIEW_REQUIRED')
    mo=MetalOptions(mode=mode,color_strength=float(opts.get('reconstruction_strength',.65)),specular_strength=float(opts.get('specular_strength',.35)),smoothness_strength=float(opts.get('smoothness_strength',.25)),user_confirmed_task=confirmed,erode_px=int(opts.get('erode_px',1 if opts.get('boundary')=='erode' else 0)),feather_px=int(opts.get('feather_px',1 if opts.get('boundary')=='feather' else 0)),addressing=addressing)
    profile=TargetProfile(tint_retention=float(opts.get('tint_retention',.25)))
    if mode=='off' and intent=='export':
        _validate_config(profile,mo)
        # Disabled reconstruction does not read, hash or decode unused masks.
        report=dict(algorithm=ALGORITHM_VERSION,mode=mode,intent=intent,
            profile=dataclasses.asdict(profile),profile_status='approved' if profile.is_approved() else 'experimental',
            options=dataclasses.asdict(mo),colorspaces={'source_d':dataclasses.asdict(dc),'source_r':dataclasses.asdict(rc),'target':dataclasses.asdict(tc)},
            diagnostic_codes=['DISABLED_LEGACY_BASELINE'],warnings=[],applied=False,game_verified=False,
            protection_coverage=None,masks_evaluated=False,edited_coverage=0.,
            input_dimensions=list(shape),precompression_protection_exact=True,alpha_preserved=True,
            score_interpretation='heuristic score, not probability')
        return arrays,base,MetalResult(None,None,None,None,None,None,report),headers,hashes,diagnostics
    edit=_mask(opts,'manual_mask','edit_mask_path',shape);protect=_mask(opts,'protect_mask','protect_mask_path',shape)
    zero=np.zeros(shape,dtype=np.float64)
    spec=s[...,0]/255. if s is not None else zero;smooth=s[...,1]/255. if s is not None else zero
    if s is not None and s.shape[:2]!=shape:
        spec=align_numeric(spec,shape,'clamp');smooth=align_numeric(smooth,shape,'clamp')
    if opts.get('_stage_cb'):opts['_stage_cb']('Reconstruction / 材质重建')
    r=arrays.get('r');r=None if r is None else r[...,0].astype(np.float64)/255 if scalar else r.astype(np.float64)/255
    # Explicit pairing/confirmation can authorize texture-only analysis; missing references cannot.
    if not job.get('material_path') and opts.get('allow_experimental'):
        diagnostics=[x for x in diagnostics if x!='NEEDS_MANUAL_REVIEW']
    result=reconstruct(arrays['d'].astype(np.float64)/255,r,None if 'l' not in arrays else arrays['l'][...,0].astype(np.float64)/255,base['d'].astype(np.float64)/255,spec,smooth,d_contract=dc,r_contract=rc,target_contract=tc,profile=profile,options=mo,manual_mask=edit,protect_mask=protect,diagnostics=diagnostics,intent=intent)
    _check(cancel)
    return arrays,base,result,headers,hashes,diagnostics

def preview_job(job,options=None,*,cancel_event=None):
    opts=_options(extra=options);arrays,base,result,headers,hashes,diagnostics=_prepare(job,opts,'preview',cancel_event)
    if result is None:raise ValueError('No diffuse texture available for this material')
    views=dict(source_d=arrays['d'],baseline_d=base['d'],reconstructed_d=np.rint(result.diffuse*255).astype(np.uint8),candidate=result.candidate_mask,protect=result.protect_mask,final_mask=result.final_mask,specular=result.specular_strength,smoothness=result.smoothness,difference=np.abs(result.diffuse[...,:3]-base['d'][...,:3]/255.),alpha=base['d'][...,3]/255.)
    return dict(views=views,source_shape=base['d'].shape[:2],candidate=result.candidate_mask,diagnostics=result.report['diagnostic_codes']+result.report['warnings'],metadata=dict(headers=headers,hashes=hashes,metal=result.report))

def _relative_for(job,role):
    path=job['texture_paths'].get(role)
    if path:return asset_relative(path,Path(job['asset_root']),'textures')
    donor=job['texture_paths'].get('l') or job['texture_paths'].get('r') or job['texture_paths'].get('d')
    if not donor:raise ValueError('No path for synthesized channel')
    rel=Path(asset_relative(donor,Path(job['asset_root']),'textures'))
    stem=rel.stem.rsplit('_',1)[0] if '_' in rel.stem else rel.stem
    return rel.with_name(stem+'_'+role+'.dds').as_posix()

def _encode(array,role,path,opts,cancel_event=None):
    _check(cancel_event)
    fmt=opts.get({'d':'diffuse_fmt','g':'glow_fmt','n':'normal_fmt','s':'specular_fmt'}.get(role,''),'BC7_UNORM' if role in ('d','g') else 'BC5_UNORM')
    if role in ('d','g') and fmt=='BC1_UNORM' and material.has_alpha(array):fmt='BC3_UNORM'
    tool=_checked_codec(opts)
    codec_info={}
    semantic=opts.get('mode','off')!='off'
    override=opts.get('color_contract_override','auto')
    transfer=opts.get('target_d_transfer') or ('srgb' if override=='srgb_linear' else 'linear' if override=='linear_linear' else None)
    if semantic and (role in ('n','s') or transfer):
        from semantic_dds import encode_semantic_dds
        used,warn=encode_semantic_dds(array,fmt,str(path),mipmaps=opts.get('generate_mipmap',True),texconv_path=tool or None,semantic='normal' if role=='n' else 'scalar' if role=='s' else 'color',transfer_function='linear' if role in ('n','s') else transfer,addressing=opts.get('addressing','clamp'),alpha_mode='numeric' if role in ('n','s') else 'straight',cancel_event=cancel_event)
    else:
        used,warn=dds.encode_dds(array,fmt,str(path),mipmaps=opts.get('generate_mipmap',True),texconv_path=tool,codec_info=codec_info,cancel_event=cancel_event)
        if semantic:warn=(warn+'; ' if warn else '')+'UNRESOLVED_TARGET_COLOR_ENCODING: legacy mip filter retained'
    _check(cancel_event);_checked_codec(opts)
    h=_header(path)
    if (h.height,h.width)!=array.shape[:2]:raise ValueError('Encoded DDS dimensions changed')
    expected=1+int(np.log2(max(h.width,h.height))) if opts.get('generate_mipmap',True) else 1
    if h.mipmaps!=expected:raise ValueError('Encoded DDS mip chain incomplete')
    if opts.get('_stage_cb'):opts['_stage_cb']('Verifying / 校验 '+role.upper())
    from semantic_dds import validate_mip_layout
    validate_mip_layout(path,cancel_event=cancel_event)
    decoded=dds.decode_dds_pixels(str(path),h,cancel_event=cancel_event)
    compare=2 if role in ('s','n') else 4
    err=np.abs(decoded[...,:compare].astype(np.int16)-array[...,:compare].astype(np.int16))
    _check(cancel_event)
    backend=codec_info.get('backend','texconv' if tool else 'numpy-fallback')
    identity=opts['_codec_identity'] if backend=='texconv' else {'backend':backend,'path':None,'sha256':None}
    return dict(format=used,width=h.width,height=h.height,mips=h.mipmaps,compression_mae=float(err.mean()),compression_max=float(err.max()),warning=warn,backend=backend,codec_identity=identity,semantic=role)

def _safe_output(root,relative):
    p=(root/safe_relative(relative)).resolve()
    if not p.is_relative_to(root.resolve()):raise ValueError('Output escapes run root')
    return p

def run_job(job,output_root,direction='76-to-4',dry_run=False,options=None,cancel_event=None,log=None):
    opts=_options(extra=options)
    if '_codec_identity' not in opts:
        opts.update(_codec_context(opts.get('texconv_path') is not False))
    out=Path(output_root).resolve();source_root=Path(job['asset_root']).resolve()
    if source_root.is_file():source_root=source_root.parent
    if out==source_root or out.is_relative_to(source_root) or source_root.is_relative_to(out):raise ValueError('Input and output may not overlap')
    _check(cancel_event)
    fingerprint=_variant_fingerprint(job,opts)
    if opts.get('_planned_fingerprint',fingerprint)!=fingerprint:raise ValueError('SETTINGS_CHANGED_DURING_BATCH: mask or processing settings changed; rescan before export')
    material_source=None;material_hash=None
    if job.get('material_path'):
        payload=read_material_bytes(job['material_path']);material_hash=hashlib.sha256(payload).hexdigest()
        if job.get('material_sha256') and material_hash!=job['material_sha256']:raise ValueError('MATERIAL_CHANGED_SINCE_SCAN: rescan before conversion')
        material_source=Material(job['material_kind'],job['material_version'],dict(job['material_fields']),dict(job.get('material_source_info',{}))) if job.get('material_sha256') and 'material_fields' in job else parse(payload,json_kind=job.get('material_kind',Path(job['material_path']).suffix[1:].upper()),json_version=22 if direction=='76-to-4' else 2)
    info=dict(id=job['id'],name=job['name'],direction=direction,outputs=[],warnings=[],game_verified=False,dry_run=dry_run,build=BUILD)
    if dry_run:
        if job['material_path']:
            _,mi=convert_material(material_source,direction);info['material']=mi
        info['plan']=dict(textures=job['texture_paths'],references=job['texture_references'],diagnostics=job['diagnostics'])
        return info
    if direction!='76-to-4' and job['texture_paths'] and job.get('include_textures',True):raise ValueError('REVERSE_DDS_UNSUPPORTED: packed FO4 channels cannot reconstruct original FO76 reflectance and lighting')
    out.mkdir(parents=True,exist_ok=True);stage=Path(tempfile.mkdtemp(prefix='.staging-'+job['id']+'-',dir=out));made=[];manifest_tmp=None;committed=False
    try:
        replacements={};records={};hashes={};meta=None;cache_key=None;texture_cache=opts.get('_texture_cache')
        if job.get('include_textures',True):
            dependency_hashes={}
            for src in job['texture_paths'].values():
                _check(cancel_event)
                if src not in dependency_hashes:dependency_hashes[src]=_source_hash(src,cancel_event)
            if job.get('material_path'):dependency_hashes[job['material_path']]=material_hash
            review_valid=opts.get('reviewed_source_hashes')==dependency_hashes
            if job.get('material_path') and job.get('material_sha256') and dependency_hashes.get(job.get('material_path'))!=job['material_sha256']:raise ValueError('MATERIAL_CHANGED_SINCE_SCAN: rescan before conversion')
            cache_key=hashlib.sha256((json.dumps({k:v for k,v in dependency_hashes.items() if k!=job.get('material_path')},sort_keys=True)+fingerprint+json.dumps(job.get('_variant_keys',{}),sort_keys=True)+str(review_valid)).encode()).hexdigest()
            cached=texture_cache.get(cache_key) if texture_cache is not None else None
            if cached and all(Path(file).is_file() and _source_hash(file,cancel_event)==record['sha256'] for file,record in cached['files'].items()):
                hashes.update(dependency_hashes);replacements.update(cached['replacements'])
                for file,record in cached['files'].items():
                    rel=record['relative'];dst=_safe_output(stage,rel);dst.parent.mkdir(parents=True,exist_ok=True);_copy_file(file,dst,cancel_event);records[rel]=dict(record['validation'])
                if cached.get('metal') is not None:info['metal']=cached['metal']
                info['source_headers']=cached.get('source_headers',{});info['texture_cache_reused']=True
            else:
                arrays,base,result,headers,hashes,diagnostics=_prepare(job,opts,'export',cancel_event,dependency_hashes)
                info['source_headers']=headers;info['diagnostics']=diagnostics
                if result is not None:
                    info['metal']=result.report
                    if result.report['applied']:
                        base['d']=np.rint(np.clip(result.diffuse,0,1)*255).astype(np.uint8)
                        if 's' in base and base['s'].shape[:2]==base['d'].shape[:2]:
                            base['s'][...,0]=np.rint(result.specular_strength*255).astype(np.uint8);base['s'][...,1]=np.rint(result.smoothness*255).astype(np.uint8)
                for role in ('d','n','s','g'):
                    if role not in base:continue
                    _check(cancel_event);rel=_relative_for(job,role)
                    variant=job.get('_variant_keys',{}).get(role)
                    if variant:
                        rp=Path(rel);rel=rp.with_name(rp.stem+'__mc_'+variant+rp.suffix).as_posix()
                    path=_safe_output(stage,rel);path.parent.mkdir(parents=True,exist_ok=True)
                    encode_opts=dict(opts)
                    if role in ('d','g') and not encode_opts.get('target_d_transfer') and headers.get(role,{}).get('fmt','').endswith('_SRGB') and opts.get('color_contract_override','auto')=='auto':encode_opts['target_d_transfer']='srgb'
                    if opts.get('_stage_cb'):opts['_stage_cb']('Compressing / 压缩 '+role.upper())
                    records[rel]=_encode(base[role],role,path,encode_opts,cancel_event)
                    if result is not None and role in ('d','s') and result.report['applied']:
                        baseline_path=stage/('.baseline-'+role+'.dds')
                        baseline_array=_baseline(arrays,opts,cancel_event)[role]
                        _encode(baseline_array,role,baseline_path,encode_opts,cancel_event)
                        base_read=dds.decode_dds_pixels(str(baseline_path),cancel_event=cancel_event);new_read=dds.decode_dds_pixels(str(path),cancel_event=cancel_event)
                        protected=result.protect_mask>0
                        if protected.shape==new_read.shape[:2]:
                            delta=np.abs(new_read.astype(float)-base_read.astype(float));records[rel]['protected_postcompression_mae']=float(delta[protected].mean()) if protected.any() else 0.
                        baseline_path.unlink()
                    slot={'d':'BaseTexture' if job.get('material_kind')=='BGEM' else 'DiffuseTexture','n':'NormalTexture','s':'EnvmapMaskTexture' if job.get('material_kind')=='BGEM' else 'SmoothSpecTexture','g':'GlowTexture'}[role]
                    replacements[slot]=rel
                if 'unknown' in job['texture_paths']:
                    src=Path(job['texture_paths']['unknown']);rel=asset_relative(src,Path(job['asset_root']),'textures');dst=_safe_output(stage,rel);dst.parent.mkdir(parents=True,exist_ok=True);_copy_file(src,dst,cancel_event);records[rel]={'copied_unclassified':True}
        if job['material_path']:
            hashes[job['material_path']]=material_hash;mat,mi=convert_material(material_source,direction,replacements);info['material']=mi;info['warnings']+=mi['warnings']
            # Resolve all target references before material publication. Unchanged extra slots are copied as data.
            if job.get('include_textures',True):
                base_root=Path(job['asset_root']).resolve()
                from material_jobs import resolve_reference
                for slot,ref in mat.textures().items():
                    _check(cancel_event)
                    target=_safe_output(stage,ref if ref.lower().startswith('textures/') else 'textures/'+ref)
                    if target.exists():continue
                    src,rel=resolve_reference(base_root,ref)
                    if not src.is_file():raise ValueError('MATERIAL_REFERENCE_ERROR: missing '+slot+' '+ref)
                    if not target.exists():target.parent.mkdir(parents=True,exist_ok=True);_copy_file(src,target,cancel_event);records[rel]={'copied_dependency':True};hashes[str(src)]=_source_hash(src,cancel_event)
                if mat.fields.get('RootMaterialPath'):raise ValueError('ROOT_MATERIAL_DEPENDENCY: convert parent chain explicitly before grouped deployment')
            else:info['warnings'].append('MATERIAL_ONLY: texture dependencies were not packaged or verified')
            rel=job['relative_path'];target=_safe_output(stage,rel);target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(serialize(mat));parse(target);records[rel]={'material_readback':True}
        _check(cancel_event)
        for src,h in hashes.items():
            _check(cancel_event)
            if _source_hash(src,cancel_event)!=h:raise ValueError('Source changed during conversion: '+src)
        if _variant_fingerprint(job,opts)!=fingerprint:raise ValueError('SETTINGS_CHANGED_DURING_BATCH: mask changed before commit')
        pending=[]
        for rel,record in records.items():
            _check(cancel_event)
            src=_safe_output(stage,rel);dst=_safe_output(out,rel);h=_source_hash(src,cancel_event)
            if dst.exists():
                if _source_hash(dst,cancel_event)!=h:raise FileExistsError('Conflicting existing output: '+str(dst))
            else:pending.append((src,dst))
            record['sha256']=h;info['outputs'].append(rel)
        # Completion manifest is published last. Roll back only new files if any rename fails.
        for src,dst in pending:
            _check(cancel_event);dst.parent.mkdir(parents=True,exist_ok=True);src.rename(dst);made.append(dst)
        info.update(validation=records,source_hashes=hashes,status='committed',processing_key=hashlib.sha256((json.dumps(hashes,sort_keys=True)+fingerprint).encode()).hexdigest(),variants=job.get('_variant_keys',{}))
        manifests=out/'.manifests';manifests.mkdir(exist_ok=True)
        manifest=manifests/(job['id']+'.json');tmp=manifests/(job['id']+'.tmp')
        _check(cancel_event)
        with tmp.open('x',encoding='utf8') as stream:
            manifest_tmp=tmp;stream.write(json.dumps(info,indent=2,default=_json))
        _check(cancel_event);tmp.replace(manifest);manifest_tmp=None;committed=True
        if texture_cache is not None and cache_key:
            entry={'replacements':replacements,'metal':info.get('metal'),'source_headers':info.get('source_headers'), 'files':{str(_safe_output(out,rel)):{'relative':rel,'sha256':record['sha256'],'validation':record} for rel,record in records.items() if rel.lower().endswith('.dds')}}
            texture_cache[cache_key]=entry
            while len(texture_cache)>32:texture_cache.pop(next(iter(texture_cache)))
        return info
    except Exception:
        if not committed:
            for path in reversed(made):path.unlink(missing_ok=True)
        raise
    finally:
        if manifest_tmp is not None:manifest_tmp.unlink(missing_ok=True)
        shutil.rmtree(stage)


def _variant_fingerprint(job, options):
    # Processing identity excludes destination paths/time/UI fields. Shared dependencies
    # and material settings can change output even when the diffuse file is identical.
    ignored={'input_dir','output_dir','generate_log','generate_report','about_seen','lang','overwrite','keep_structure','job_overrides','metal_options','manual_mask','protect_mask','reviewed_source_hashes'}
    values={k:v for k,v in options.items() if k not in ignored and not k.startswith('_')}
    masks_enabled=options.get('mode','off')!='off'
    for key in ('edit_mask_path','protect_mask_path'):
        if not masks_enabled:values.pop(key,None)
        elif values.get(key):values[key]=sha256(values[key])
    for key in ('manual_mask','protect_mask'):
        if masks_enabled and options.get(key) is not None:
            value=options[key]
            # Hash only a source-sized numeric grid; JSON nested lists must
            # not trigger a large conversion before normal _mask validation.
            diffuse=job['texture_paths'].get('d')
            if not diffuse:continue
            header=_header(diffuse)
            shape=_mask_budget((header.height,header.width))
            _mask_shape(value,shape)
            values[key]=_json(np.asarray(value))
    values['material_fields']=job.get('material_fields',{})
    values['source_dependencies']={k:(str(v) if job.get('_paths_resolved') else str(Path(v).resolve())).lower() for k,v in job['texture_paths'].items()}
    values['algorithm']=BUILD
    return hashlib.sha256(json.dumps(values,sort_keys=True,default=_json).encode()).hexdigest()[:12]

class _BatchReports:
    """Append complete reports once per job; never retain the full result tree in RAM."""
    def __init__(self, directory):
        from contextlib import ExitStack
        self.stack=ExitStack();self.count=0;self.ordinary=0;self.metal=0;self.review=0
        def open_report(name,**kw):return self.stack.enter_context((directory/name).open('w',buffering=1024*1024,**kw))
        self.report=open_report('report.json',encoding='utf8');self.report.write('{"results":[\n')
        self.validation=open_report('validation_results.json',encoding='utf8');self.validation.write('{\n')
        self.manifest=open_report('conversion_manifest.json',encoding='utf8');self.manifest.write('[\n')
        self.unresolved=open_report('unresolved_materials.txt',encoding='utf8')
        self.csv=csv.writer(open_report('report.csv',encoding='utf-8-sig',newline=''))
        self.csv.writerow(['id','name','status','metal_applied','error_or_diagnostics'])

    def add(self,result):
        separator=',\n' if self.count else ''
        applied=bool(result.get('metal',{}).get('applied'));codes=result.get('metal',{}).get('diagnostic_codes',[])
        description=result.get('error','; '.join(codes))
        self.report.write(separator);json.dump(result,self.report,default=_json,ensure_ascii=True)
        self.validation.write(separator+json.dumps(result['id'])+':');json.dump(result.get('validation',{}),self.validation,default=_json)
        self.manifest.write(separator);json.dump({'id':result['id'],'outputs':result.get('outputs',[]),'status':result.get('status','planned')},self.manifest)
        self.csv.writerow([result['id'],result['name'],result.get('status','planned'),applied,description])
        if result.get('status')=='failed' or 'NEEDS_MANUAL_REVIEW' in codes:self.unresolved.write(result['name']+': '+description+'\n')
        self.count+=1;self.ordinary+=result.get('status')=='committed' and not applied;self.metal+=applied;self.review+='NEEDS_MANUAL_REVIEW' in codes

    def finish(self,summary):
        summary.update(processed=self.count,ordinary_success=self.ordinary,metal_success=self.metal,needs_review=self.review)
        try:
            self.report.write('\n]')
            for key,value in summary.items():self.report.write(','+json.dumps(key)+':'+json.dumps(value,default=_json))
            self.report.write('}\n');self.validation.write('\n}\n');self.manifest.write('\n]\n')
        finally:self.stack.close()


def run_jobs(jobs,cfg,progress_cb=None,log_cb=None,cancel_event=None,analyze_only=False,dry_run=False):
    opts=_options(cfg);overrides=job_options(getattr(cfg,'job_overrides',{}))
    root=Path(cfg.output_dir).resolve();input_root=Path(cfg.input_dir).resolve()
    if root==input_root or root.is_relative_to(input_root) or input_root.is_relative_to(root):raise ValueError('Input and output directories must not overlap')
    checked_roots=set()
    for job in jobs:
        if cancel_event is not None and cancel_event.is_set():break
        if job['asset_root'] in checked_roots:continue
        asset=Path(job['asset_root']).resolve();checked_roots.add(job['asset_root'])
        if root==asset or root.is_relative_to(asset) or asset.is_relative_to(root):raise ValueError('Output may not overlap any source resource root')
    root.mkdir(parents=True,exist_ok=True);run=root/('run_'+time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]);run.mkdir()
    reportdir=run/'reports';reportdir.mkdir();log=ConversionLog();log.on_entry=log_cb;log.output_dir=str(run)
    log.start_stream(str(reportdir/'conversion_log.txt'));reports=_BatchReports(reportdir)
    fingerprints=[];planning_errors={};variants={};texture_cache={};codec_contexts={}
    def effective_options(job):
        effective=_RuntimeOptions(opts);effective.update(overrides.get(job['id'],{}))
        external=effective.get('texconv_path') is not False
        if external not in codec_contexts:codec_contexts[external]=_codec_context(external)
        effective.update(codec_contexts[external])
        if getattr(cfg,'metal_mode','off')=='off':effective['mode']='off'
        effective.pop('job_overrides',None);effective.pop('metal_options',None)
        return effective
    try:
        _check(cancel_event)
        for index,job in enumerate(jobs):
            _check(cancel_event)
            try:fingerprint=_variant_fingerprint(job,effective_options(job))
            except Cancelled:raise
            except Exception as exc:
                fingerprints.append(None);planning_errors[index]=str(exc);continue
            fingerprints.append(fingerprint)
            for role,src in job['texture_paths'].items():
                if role in ('d','s','r','l'):
                    key=str(src).lower();prior=variants.get(key)
                    if prior is None:variants[key]=fingerprint
                    elif prior!=fingerprint:variants[key]=False
            if progress_cb and index%250==0:progress_cb(0,len(jobs),'Preparing: '+job['name'])
        snapshot={f.name:getattr(cfg,f.name) for f in dataclasses.fields(cfg)}
        snapshot['direction']=getattr(cfg,'direction','76-to-4')
        snapshot['codec_selection']={str(key):value['_codec_identity'] for key,value in codec_contexts.items()}
        with (run/'config_snapshot.json').open('w',encoding='utf8') as stream:json.dump(snapshot,stream,indent=2,default=_json)
        for index,original in enumerate(jobs):
            _check(cancel_event)
            job=dict(original);opt=effective_options(job);opt['_texture_cache']=texture_cache;opt['_planned_fingerprint']=fingerprints[index];job['_variant_keys']={}
            for role,src in job['texture_paths'].items():
                if variants.get(str(src).lower()) is False:job['_variant_keys']['s' if role in ('l','r') else role]=fingerprints[index]
            if progress_cb:opt['_stage_cb']=lambda stage,i=index,name=job['name']:progress_cb(i,len(jobs),stage+' · '+name)
            try:
                if index in planning_errors:raise ValueError(planning_errors[index])
                if analyze_only:
                    result=preview_job(job,opt,cancel_event=cancel_event);dest=run/'previews'/job['id'];dest.mkdir(parents=True)
                    for name,arr in result['views'].items():
                        _check(cancel_event)
                        a=np.asarray(arr);a=np.rint(np.clip(a,0,1)*255).astype(np.uint8) if a.dtype.kind=='f' else a
                        Image.fromarray(a).save(dest/(name+'.png'))
                    result={'id':job['id'],'name':job['name'],'status':'analyzed','diagnostics':result['diagnostics'],'metadata':result['metadata']}
                else:result=run_job(job,run/'converted',direction=getattr(cfg,'direction','76-to-4'),dry_run=dry_run,options=opt,cancel_event=cancel_event)
                log.add('OK',job['name'],'Plan validated' if dry_run else 'Analysis exported' if analyze_only else 'Validated group committed')
                for warning in result.get('warnings',[]):log.add('WARNING',job['name'],warning)
                for code in result.get('metal',{}).get('diagnostic_codes',[]):
                    if code not in ('READY_FOR_METAL_ANALYSIS','DISABLED_LEGACY_BASELINE'):log.add('WARNING',job['name'],code)
            except Cancelled:raise
            except dds.EncoderCleanupError as exc:
                result=dict(id=job['id'],name=job['name'],status='failed',error=str(exc))
                log.add('ERROR',job['name'],str(exc));reports.add(result)
                raise
            except Exception as exc:
                result=dict(id=job['id'],name=job['name'],status='failed',error=str(exc));log.add('ERROR',job['name'],str(exc))
            reports.add(result)
            if progress_cb:progress_cb(index+1,len(jobs),job['name'])
    except Cancelled:pass
    finally:
        cancelled=bool(cancel_event is not None and cancel_event.is_set())
        reports.finish(dict(build=BUILD,total=len(jobs),errors=log.errors,cancelled=cancelled,game_verified=False))
        (reportdir/'summary.txt').write_text(log.summary()+'\nGame appearance is unverified.\n',encoding='utf8');log.write(str(reportdir/'conversion_log.txt'))
        (run/('PARTIAL' if cancelled or log.errors or reports.count!=len(jobs) else 'COMPLETE')).write_text('analysis-only' if analyze_only else 'dry-run' if dry_run else 'binary-validated; game-unverified',encoding='utf8')
    return log
