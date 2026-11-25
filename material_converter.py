"""Field-level conversion. Texture synthesis is supplied by MaterialJob, never guessed here."""
from pathlib import Path, PurePosixPath
import os, uuid
from material_codec import Material, MaterialError, parse, serialize, layout, DEFAULTS
from security_io import assert_no_reparse,pinned_path,publish_new,safe_relative
from config import APP_VERSION

BUILD = APP_VERSION

def standalone_packed_reference(fields):
    """Name the ordinary separately exported _s only for an explicit matching _r/_l pair."""
    if fields.get('SmoothSpecTexture'):
        return ''
    reflection = fields.get('SpecularTexture')
    lighting = fields.get('LightingTexture')
    if not reflection or not lighting:
        return ''
    r = PurePosixPath(safe_relative(reflection))
    l = PurePosixPath(safe_relative(lighting))
    if (not r.name.lower().endswith('_r.dds') or not l.name.lower().endswith('_l.dds')
            or str(r)[:-6].casefold() != str(l)[:-6].casefold()):
        return ''
    return l.with_name(l.name[:-6] + '_s.dds').as_posix()


def convert_material(source,direction='76-to-4',texture_replacements=None,*,json_kind=None,standalone=False):
    if direction not in ('76-to-4','4-to-76'): raise MaterialError('Invalid direction')
    src=parse(source,json_kind=json_kind,json_version=22 if direction=='76-to-4' else 2) if not isinstance(source,Material) else source
    if direction=='76-to-4' and src.version<=2: raise MaterialError('Source is already FO4 material v1/v2')
    if direction=='4-to-76' and src.version>2: raise MaterialError('Source is already FO76 material')
    target=2 if direction=='76-to-4' else 22
    m=Material(src.kind,target,dict(src.fields)); warnings=[]
    if src.source_info:
        warnings.append(src.source_info['diagnostic'])
        if src.source_info.get('conditional_fields_not_written'):
            warnings.append('JSON_CONDITIONAL_FIELDS_INACTIVE: values remain in source provenance; inactive fields are not written to binary layout')
    if src.kind=='BGSM':
        if direction=='76-to-4':
            # Modern Specular is RGB reflectance, not FO4's packed R/G smooth-specular.
            if src.fields.get('SpecularTexture') or src.fields.get('LightingTexture'):
                m.fields['SmoothSpecTexture']=(texture_replacements or {}).get('SmoothSpecTexture','')
                if standalone and not m.fields['SmoothSpecTexture']:
                    packed = standalone_packed_reference(src.fields)
                    if packed:
                        m.fields['SmoothSpecTexture'] = packed
                        warnings.append('EXTERNAL_PACKED_SPECULAR: provide the separately converted '+packed+'; use combined export for per-material texture variants')
                if not m.fields['SmoothSpecTexture']:
                    warnings.append('PACKED_SPECULAR_REQUIRED: FO76 reflectance/lighting require generated FO4 _s; slot left empty')
        elif src.fields.get('SmoothSpecTexture'):
            warnings.append('REVERSE_TEXTURE_ADAPTATION_REQUIRED: FO4 packed _s is not FO76 RGB reflectance/lighting')
            m.fields['SmoothSpecTexture']=''
    if texture_replacements: m.fields.update(texture_replacements)
    for key,value in list(m.fields.items()):
        if value and isinstance(value,str):
            normalized=safe_relative(value)
            if normalized.lower().startswith('data/textures/') or normalized.lower().startswith('data/materials/'):
                normalized=normalized[5:];warnings.append('NORMALIZED_DATA_PREFIX: '+key)
            m.fields[key]=normalized
    active={n for n,f in layout(m.kind,m.version,m.fields)}
    removed={k:v for k,v in src.fields.items() if k not in active and v not in (False,0,0.,'')}
    for k in removed:
        warnings.append('UNMAPPED_FIELD: '+k)
    if m.fields.get('RootMaterialPath'): warnings.append('ROOT_MATERIAL_DEPENDENCY: referenced parent must also be converted')
    warnings.append('GAME_APPEARANCE_UNVERIFIED: binary conversion does not ensure cross-engine visual equivalence')
    m=parse(serialize(m))
    return m,{'source_version':src.version,'target_version':target,'kind':src.kind,'warnings':warnings,
              'unmapped_fields':removed,'texture_references':m.textures(),'game_verified':False,'build':BUILD,
              'source_format':src.source_info.get('source_format','binary'),'source_info':dict(src.source_info)}

def convert_file(src,dest,direction='76-to-4',dry_run=False,_staged=False,texture_replacements=None,json_kind=None,**unused):
    src=assert_no_reparse(src); dest=assert_no_reparse(dest)
    if src==dest: raise MaterialError('Input is read-only; choose another destination')
    m,info=convert_material(src,direction,texture_replacements,json_kind=json_kind,standalone=not _staged)
    info.update(source=str(src),output=str(dest),dry_run=dry_run,direction=direction)
    if dry_run:return info
    if dest.exists():raise FileExistsError(dest)
    with pinned_path(dest.parent):dest.parent.mkdir(parents=True,exist_ok=True)
    tmp=dest if _staged else dest.with_name('.material-'+uuid.uuid4().hex+'.tmp')
    with pinned_path(dest.parent):
        created = False
        try:
            with pinned_path(tmp), tmp.open('xb') as stream:
                created = True
                stream.write(serialize(m))
            check=parse(tmp)
            if check.fields!=m.fields:raise MaterialError('Written material differs from serialized fields')
            if not _staged:
                publish_new(tmp,dest)
                created = False
        finally:
            if created and not _staged:
                tmp.unlink(missing_ok=True)
    return info
