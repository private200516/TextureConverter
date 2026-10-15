"""Bounded, versioned BGSM/BGEM codec. Layout checked against Material Editor.
No image interpretation is done here. Floating point colors stay float32, not RGB8.
"""
from __future__ import annotations
import json, math, struct
from dataclasses import dataclass, field
from pathlib import Path

class MaterialError(ValueError): pass

MAX_MATERIAL_BYTES = 1024 * 1024


def read_material_bytes(path):
    """Bound the allocation even if a file grows between stat and read."""
    with Path(path).open('rb') as stream:
        data = stream.read(MAX_MATERIAL_BYTES + 1)
    if not 8 <= len(data) <= MAX_MATERIAL_BYTES:
        raise MaterialError('Material size outside 8 B - 1 MiB')
    return data


@dataclass
class Material:
    kind: str
    version: int
    fields: dict = field(default_factory=dict)
    source_info: dict = field(default_factory=dict)
    def textures(self):
        return {k:v for k,v in self.fields.items() if (k.endswith('Texture') or k in ('GlassRoughnessScratch','GlassDirtOverlay')) and v}

DEFAULTS = dict(TileFlags=3,UOffset=0.,VOffset=0.,UScale=1.,VScale=1.,Alpha=1.,
 AlphaTestRef=128,ZBufferWrite=True,ZBufferTest=True,EnvironmentMappingMaskScale=1.,
 MaskWrites=63,RimPower=2.,SubsurfaceLightingRolloff=.3,SpecularColor=(1.,1.,1.),
 SpecularMult=1.,Smoothness=1.,FresnelPower=5.,WetnessControlSpecScale=-1.,
 WetnessControlSpecPowerScale=-1.,WetnessControlSpecMinvar=-1.,WetnessControlEnvMapScale=-1.,
 WetnessControlFresnelPower=-1.,WetnessControlMetalness=-1.,EmittanceColor=(1.,1.,1.),
 EmittanceMult=1.,HairTintColor=(.5,.5,.5),DisplacementTextureBias=-.5,
 DisplacementTextureScale=10.,TessellationPnScale=1.,TessellationBaseFactor=1.,
 GrayscaleToPaletteScale=1.,BaseColor=(1.,1.,1.),BaseColorScale=1.,TranslucencyTransmissiveScale=1.)

def layout(kind,version,values):
    if kind not in ('BGSM','BGEM') or not 1 <= version <= 22:
        raise MaterialError(f'Unsupported material kind/version: {kind} v{version}')
    def group(fmt,names):
        for n in names.split(): yield n,fmt
    yield 'TileFlags','I'
    yield from group('f','UOffset VOffset UScale VScale Alpha')
    yield 'AlphaBlend0','B'; yield 'AlphaBlend1','I'; yield 'AlphaBlend2','I'
    yield 'AlphaTestRef','B'
    yield from group('?','AlphaTest ZBufferWrite ZBufferTest ScreenSpaceReflections WetnessControlScreenSpaceReflections Decal TwoSided DecalNoFade NonOccluder Refraction RefractionFalloff')
    yield 'RefractionPower','f'
    if version<10:
        yield 'EnvironmentMapping','?'; yield 'EnvironmentMappingMaskScale','f'
    else: yield 'DepthBias','?'
    yield 'GrayscaleToPaletteColor','?'
    if version>=6: yield 'MaskWrites','B'
    if kind=='BGSM':
        yield from group('s','DiffuseTexture NormalTexture SmoothSpecTexture GreyscaleTexture')
        if version>2:
            yield from group('s','GlowTexture WrinklesTexture SpecularTexture LightingTexture FlowTexture')
            if version>=17: yield 'DistanceFieldAlphaTexture','s'
        else: yield from group('s','EnvmapTexture GlowTexture InnerLayerTexture WrinklesTexture DisplacementTexture')
        yield 'EnableEditorAlphaRef','?'
        if version>=8:
            yield from group('?','Translucency TranslucencyThickObject TranslucencyMixAlbedoWithSubsurfaceColor')
            yield 'TranslucencySubsurfaceColor','3f'
            yield from group('f','TranslucencyTransmissiveScale TranslucencyTurbulence')
        else:
            yield 'RimLighting','?'; yield from group('f','RimPower BackLightPower')
            yield 'SubsurfaceLighting','?'; yield 'SubsurfaceLightingRolloff','f'
        yield 'SpecularEnabled','?'; yield 'SpecularColor','3f'
        yield from group('f','SpecularMult Smoothness FresnelPower WetnessControlSpecScale WetnessControlSpecPowerScale WetnessControlSpecMinvar')
        if version<10: yield 'WetnessControlEnvMapScale','f'
        yield from group('f','WetnessControlFresnelPower WetnessControlMetalness')
        if version>2:
            yield 'PBR','?'
            if version>=9: yield 'CustomPorosity','?'; yield 'PorosityValue','f'
        yield 'RootMaterialPath','s'; yield 'AnisoLighting','?'; yield 'EmitEnabled','?'
        if values.get('EmitEnabled',False): yield 'EmittanceColor','3f'
        yield 'EmittanceMult','f'; yield 'ModelSpaceNormals','?'; yield 'ExternalEmittance','?'
        if version>=12: yield 'LumEmittance','f'
        if version>=13:
            yield 'UseAdaptativeEmissive','?'
            yield from group('f','AdaptativeEmissive_ExposureOffset AdaptativeEmissive_FinalExposureMin AdaptativeEmissive_FinalExposureMax')
        if version<8: yield 'BackLighting','?'
        yield from group('?','ReceiveShadows HideSecret CastShadows DissolveFade AssumeShadowmask Glowmap')
        if version<7: yield from group('?','EnvironmentMappingWindow EnvironmentMappingEye')
        yield 'Hair','?'; yield 'HairTintColor','3f'
        yield from group('?','Tree Facegen SkinTint Tessellate')
        if version<3: yield from group('f','DisplacementTextureBias DisplacementTextureScale TessellationPnScale TessellationBaseFactor TessellationFadeDistance')
        yield 'GrayscaleToPaletteScale','f'; yield 'SkewSpecularAlpha','?'
        if version>=3:
            yield 'Terrain','?'
            if values.get('Terrain',False):
                if version==3: yield 'UnkInt1','I'
                yield from group('f','TerrainThresholdFalloff TerrainTilingDistance TerrainRotationAngle')
    else:
        yield from group('s','BaseTexture GrayscaleTexture EnvmapTexture NormalTexture EnvmapMaskTexture')
        if version>=11: yield from group('s','SpecularTexture LightingTexture GlowTexture')
        if version>=21:
            yield from group('s','GlassRoughnessScratch GlassDirtOverlay')
            yield 'GlassEnabled','?'
            if values.get('GlassEnabled',False):
                yield 'GlassFresnelColor','3f'; yield 'GlassBlurScaleBase','f'
                if version>=22: yield 'GlassBlurScaleFactor','f'
                yield 'GlassRefractionScaleBase','f'
        if version>=10: yield 'EnvironmentMapping','?'; yield 'EnvironmentMappingMaskScale','f'
        yield from group('?','BloodEnabled EffectLightingEnabled FalloffEnabled FalloffColorEnabled GrayscaleToPaletteAlpha SoftEnabled')
        yield 'BaseColor','3f'
        yield from group('f','BaseColorScale FalloffStartAngle FalloffStopAngle FalloffStartOpacity FalloffStopOpacity LightingInfluence')
        yield 'EnvmapMinLOD','B'; yield 'SoftDepth','f'
        if version>=11: yield 'EmittanceColor','3f'
        if version>=15: yield from group('f','AdaptativeEmissive_ExposureOffset AdaptativeEmissive_FinalExposureMin AdaptativeEmissive_FinalExposureMax')
        if version>=16: yield 'Glowmap','?'
        if version>=20: yield 'EffectPbrSpecular','?'

_JSON_TEXTURE_ENABLES = {
    'BGSM': {'bDiffuseEnable':'DiffuseTexture', 'bNormalEnable':'NormalTexture',
             'bSmoothSpecEnable':'SmoothSpecTexture', 'bGlowEnable':'GlowTexture',
             'bWrinklesEnable':'WrinklesTexture', 'bSpecularEnable':'SpecularTexture',
             'bLightingEnable':'LightingTexture', 'bFlowEnable':'FlowTexture',
             'bDistanceFieldAlphaEnable':'DistanceFieldAlphaTexture',
             'bGrayscaleTextureEnable':'GreyscaleTexture'},
    'BGEM': {'bBaseTextureEnable':'BaseTexture', 'bNormalTextureEnable':'NormalTexture',
             'bEnvmapTextureEnable':'EnvmapTexture', 'bEnvmapMaskTextureEnable':'EnvmapMaskTexture',
             'bGrayscaleTextureEnable':'GrayscaleTexture'},
}
_JSON_WRITE_MASKS = ('bWriteMaskAlbedo', 'bWriteMaskNormal', 'bWriteMaskSpecular',
                     'bWriteMaskGloss', 'bWriteMaskEmissive', 'bWriteMaskAmbientOcclusion')


def _json_pairs(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise MaterialError('Duplicate JSON material property: '+key)
        result[key]=value
    return result


def _json_bool(value,key):
    if type(value) is not bool:raise MaterialError('JSON boolean required: '+key)
    return value


def _json_nonfinite(value):
    raise MaterialError('Non-finite JSON value: '+value)


def _parse_json_material(data,kind,version):
    # Native JSON has no binary version. Never infer a source game from a
    # filename, a directory name, or the presence of modern optional fields.
    if kind not in ('BGSM','BGEM') or type(version) is not int or version not in (2,22):
        raise MaterialError('JSON_SOURCE_VERSION_REQUIRED: provide BGSM/BGEM kind and source version 2 or 22 explicitly')
    try:
        values=json.loads(data.decode('utf-8-sig'),object_pairs_hook=_json_pairs,
                          parse_constant=_json_nonfinite)
    except (UnicodeError,ValueError,RecursionError) as exc:
        raise MaterialError('Invalid JSON material: '+str(exc)) from exc
    if not isinstance(values,dict) or not 1<=len(values)<=256:
        raise MaterialError('JSON material must be an object with 1 - 256 properties')
    formats=dict(layout(kind,version,{'EmitEnabled':True,'Terrain':True,'GlassEnabled':True}))
    fields={};editor={}
    for key,value in values.items():
        if key in ('bTileU','bTileV') or key in _JSON_TEXTURE_ENABLES[kind] or key in _JSON_WRITE_MASKS:
            editor[key]=_json_bool(value,key);continue
        if key=='bDistanceFieldAlpha':
            # There is no corresponding boolean in the supported wire layout.
            # The false editor annotation is retained; an active flag is not
            # guessed or discarded as if conversion were complete.
            editor[key]=_json_bool(value,key)
            if value:raise MaterialError('UNSUPPORTED_JSON_PROPERTY: active '+key)
            continue
        if key=='eAlphaBlendMode':
            if value not in ('None','Standard'):raise MaterialError('Unsupported JSON alpha blend mode: '+str(value))
            editor[key]=value;continue
        if not isinstance(key,str) or len(key)<2 or key[0] not in 'bfisc':
            raise MaterialError('Unknown JSON material property: '+str(key))
        name=key[1:]
        if kind=='BGEM' and name=='GreyscaleTexture':name='GrayscaleTexture'
        if name in ('TileFlags','AlphaBlend0','AlphaBlend1','AlphaBlend2','MaskWrites'):
            raise MaterialError('Reserved JSON wire field: '+key)
        if name in fields:raise MaterialError('Duplicate canonical JSON material field: '+name)
        fmt=formats.get(name)
        if fmt is None:raise MaterialError('Unknown JSON material property for '+kind+' v'+str(version)+': '+key)
        if key[0]=='b' and fmt=='?':fields[name]=_json_bool(value,key)
        elif (key[0]=='f' and fmt=='f') or (fmt in ('B','I') and (key[0]=='i' or key=='fAlphaTestRef')):
            if type(value) not in (int,float) or not math.isfinite(value):raise MaterialError('Finite JSON number required: '+key)
            if fmt=='f':
                if abs(value)>3.4028234663852886e38:raise MaterialError('JSON float32 outside range: '+key)
                fields[name]=float(value)
            else:
                if value!=int(value) or not 0<=value<=(255 if fmt=='B' else 0xffffffff):raise MaterialError('JSON integer outside range: '+key)
                fields[name]=int(value)
        elif key[0]=='c' and fmt=='3f':
            if not isinstance(value,str) or len(value)!=7 or value[0]!='#' or any(c not in '0123456789abcdefABCDEF' for c in value[1:]):
                raise MaterialError('JSON color must be #RRGGBB: '+key)
            fields[name]=tuple(int(value[index:index+2],16)/255. for index in (1,3,5))
        elif key[0]=='s' and fmt=='s':
            if not isinstance(value,str):raise MaterialError('JSON string required: '+key)
            try:encoded=value.encode('utf8')
            except UnicodeError as exc:raise MaterialError('Invalid Unicode JSON path: '+key) from exc
            parts=value.replace('\\','/').split('/')
            if len(encoded)>=32768 or '\0' in value or (value and (value.startswith(('/','\\')) or ':' in value or any(part in ('.','..') for part in parts))):
                raise MaterialError('Unsafe JSON material path: '+key)
            fields[name]=value
        else:raise MaterialError('JSON property type does not match material field: '+key)
    if 'eAlphaBlendMode' not in editor:raise MaterialError('JSON_ALPHA_BLEND_REQUIRED: eAlphaBlendMode must be explicit')
    fields['TileFlags']=int(editor.get('bTileU',True)) | (int(editor.get('bTileV',True))<<1)
    # Both source games' native binary defaults use SRC_ALPHA/INV_SRC_ALPHA
    # factors 6/7. Only the two modes present in the inspected JSON corpus are
    # supported; other enums require a verified mapping.
    fields.update(AlphaBlend0=int(editor['eAlphaBlendMode']=='Standard'),AlphaBlend1=6,AlphaBlend2=7)
    masks=[editor[key] for key in _JSON_WRITE_MASKS if key in editor]
    if masks:
        if formats.get('MaskWrites')!='B' or len(masks)!=6 or any(value!=masks[0] for value in masks):
            raise MaterialError('UNSUPPORTED_JSON_WRITE_MASK: only explicit all-enabled/all-disabled masks are verified')
        fields['MaskWrites']=63 if masks[0] else 0
    for key,slot in _JSON_TEXTURE_ENABLES[kind].items():
        if key in editor and not editor[key] and fields.get(slot):
            raise MaterialError('JSON_DISABLED_TEXTURE_REFERENCE: '+key+' contradicts '+slot)
    source_info={'source_format':'json','source_kind_hint':kind,'source_version_hint':version,
                 'native_properties':values,'editor_properties':editor,
                 'diagnostic':'JSON_SOURCE_VERSION_HINT: '+kind+' v'+str(version)+' supplied by the selected source direction; JSON has no binary version'}
    active={name for name,fmt in layout(kind,version,fields)}
    source_info['conditional_fields_not_written']={name:value for name,value in fields.items() if name not in active}
    material=Material(kind,version,fields,source_info)
    serialize(material)  # The ordinary binary bounds/conditional reader still validates the result.
    return material


def parse(data,*,json_kind=None,json_version=None):
    if isinstance(data,(str,Path)):
        path=Path(data)
        if json_kind is None and path.suffix.lower() in ('.bgsm','.bgem'):json_kind=path.suffix[1:].upper()
        data=read_material_bytes(path)
    if len(data)<8 or len(data)>MAX_MATERIAL_BYTES: raise MaterialError('Material size outside 8 B - 1 MiB')
    prefix=data[3:] if data.startswith(b'\xef\xbb\xbf') else data
    if prefix.lstrip().startswith(b'{'):return _parse_json_material(data,json_kind,json_version)
    kind=data[:4].decode('ascii',errors='replace'); version=struct.unpack_from('<I',data,4)[0]
    m=Material(kind,version); pos=8
    for name,fmt in layout(kind,version,m.fields):
        try:
            if fmt=='s':
                if pos+4>len(data): raise MaterialError('truncated string length')
                size=struct.unpack_from('<I',data,pos)[0]; pos+=4
                if size>32768 or pos+size>len(data): raise MaterialError('invalid string length')
                raw=data[pos:pos+size]; pos+=size
                if raw.endswith(b'\0'): raw=raw[:-1]
                if b'\0' in raw: raise MaterialError('embedded NUL')
                v=raw.decode('utf8',errors='surrogateescape')
            else:
                size=struct.calcsize('<'+fmt)
                if pos+size>len(data): raise MaterialError('truncated field')
                if fmt=='?' and data[pos] not in (0,1): raise MaterialError('non-boolean byte')
                v=struct.unpack_from('<'+fmt,data,pos); pos+=size
                if 'f' in fmt and not all(math.isfinite(x) for x in v): raise MaterialError('non-finite value')
                v=v if len(v)>1 else v[0]
            m.fields[name]=v
        except (ValueError,struct.error) as e: raise MaterialError(f'{name} at {pos}: {e}') from e
    if pos!=len(data): raise MaterialError(f'Unrecognized trailing data: {len(data)-pos} bytes at {pos}')
    return m

def serialize(m):
    out=bytearray(m.kind.encode('ascii')+struct.pack('<I',m.version))
    for name,fmt in layout(m.kind,m.version,m.fields):
        default=DEFAULTS.get(name,'' if fmt=='s' else (0.,0.,0.) if fmt=='3f' else 0)
        v=m.fields.get(name,default)
        if fmt=='s':
            b=v.encode('utf8',errors='surrogateescape')+b'\0'
            if len(b)>32768 or b'\0' in b[:-1]: raise MaterialError('Invalid string '+name)
            out+=struct.pack('<I',len(b))+b
        else:
            vals=v if fmt=='3f' else (v,)
            if 'f' in fmt and not all(math.isfinite(x) for x in vals): raise MaterialError('Non-finite '+name)
            out+=struct.pack('<'+fmt,*vals)
    parse(out)
    return bytes(out)
