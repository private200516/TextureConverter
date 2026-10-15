"""Native JSON schema and source-direction regressions for approved guest tests.
Small schema fixtures reproduce settings observed in the eight native JSON
materials; no game textures, models, or original material files are bundled.
"""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from material_codec import MaterialError, parse, serialize
from material_converter import convert_file, convert_material
from material_jobs import scan_jobs


def effect_json(color='#ffffff',alpha=1.):
    return {
        'bAlphaTest':False,'bBaseTextureEnable':True,'bBloodEnabled':False,
        'bDecal':False,'bDecalNoFade':False,'bEffectLightingEnabled':False,
        'bEnvmapMaskTextureEnable':False,'bEnvmapTextureEnable':False,
        'bFalloffColorEnabled':False,'bFalloffEnabled':False,
        'bGrayscaleTextureEnable':False,'bGrayscaleToPaletteAlpha':False,
        'bGrayscaleToPaletteColor':False,'bNormalTextureEnable':False,
        'bRefraction':False,'bRefractionFalloff':False,'bSoftEnabled':False,
        'bTileU':True,'bTileV':True,'bTwoSided':False,'bZBufferTest':True,
        'bZBufferWrite':False,'cBaseColor':color,'eAlphaBlendMode':'Standard',
        'fAlpha':alpha,'fAlphaTestRef':128.,'fBaseColorScale':1.,
        'fEnvironmentMappingMaskScale':1.,'fFalloffStartAngle':0.,
        'fFalloffStartOpacity':1.,'fFalloffStopAngle':0.,'fFalloffStopOpacity':0.,
        'fLightingInfluence':1.,'fRefractionPower':0.,'fSoftDepth':100.,
        'fUOffset':0.,'fUScale':1.,'fVOffset':0.,'fVScale':1.,'iEnvmapMinLOD':0,
        'sBaseTexture':'Test/color.dds','sEnvmapMaskTexture':'',
        'sEnvmapTexture':'','sGreyscaleTexture':'','sNormalTexture':''}


def surface_json(alpha_test=False,two_sided=False):
    result={
        'eAlphaBlendMode':'None','bTileU':True,'bTileV':True,
        'bAlphaTest':alpha_test,'bTwoSided':two_sided,'bDepthBias':False,
        'bPBR':True,'bSpecularEnabled':True,'bDiffuseEnable':True,
        'bNormalEnable':True,'bLightingEnable':True,'bSpecularEnable':True,
        'bDistanceFieldAlpha':False,'bDistanceFieldAlphaEnable':False,
        'bTranslucency':False,'bTranslucencyThickObject':False,
        'bTranslucencyMixAlbedoWithSubsurfaceColor':True,
        'bEmitEnabled':False,'bTerrain':False,'bUseAdaptativeEmissive':True,
        'cSpecularColor':'#ffffff','cHairTintColor':'#808080',
        'cEmittanceColor':'#000000','cTranslucencySubsurfaceColor':'#ffffff',
        'fAlpha':1,'fAlphaTestRef':128,'fSmoothness':1,'fSpecularMult':1,
        'fTerrainThresholdFalloff':.1,'fTerrainTilingDistance':341.3333435058594,
        'fTerrainRotationAngle':0,'sDiffuseTexture':'Test/color_d.dds',
        'sNormalTexture':'Test/color_n.dds','sLightingTexture':'Test/color_l.dds',
        'sSpecularTexture':'Test/color_r.dds','sRootMaterialPath':'',
        'sDistanceFieldAlphaTexture':''}
    for key in ('Albedo','Normal','Specular','Gloss','Emissive','AmbientOcclusion'):
        result['bWriteMask'+key]=True
    return result


def encoded(value):return json.dumps(value).encode('utf8')


class MaterialJSONCompatibilityTests(unittest.TestCase):
    def test_eight_native_schema_cases_binary_readback_both_directions(self):
        cases=[('BGEM',2,effect_json()),('BGEM',2,effect_json()),
               ('BGEM',2,effect_json()),('BGEM',2,effect_json('#373737',.7)),
               ('BGEM',2,effect_json('#043504',.75)),
               ('BGSM',22,surface_json()),('BGSM',22,surface_json(True,True)),
               ('BGEM',22,effect_json())]
        for kind,version,properties in cases:
            with self.subTest(kind=kind,version=version,color=properties.get('cBaseColor')):
                source=parse(encoded(properties),json_kind=kind,json_version=version)
                self.assertEqual(source.source_info['native_properties'],properties)
                self.assertEqual(parse(serialize(source)).version,version)
                direction='4-to-76' if version==2 else '76-to-4'
                target,info=convert_material(source,direction)
                self.assertEqual(parse(serialize(target)).version,22 if version==2 else 2)
                self.assertEqual(info['source_format'],'json')
                self.assertEqual(info['source_info']['source_version_hint'],version)
                self.assertTrue(any('JSON_SOURCE_VERSION_HINT' in item for item in info['warnings']))
                self.assertFalse(info['game_verified'])

    def test_raw_json_requires_kind_and_source_version(self):
        payload=encoded(effect_json())
        for options in ({},{'json_kind':'BGEM'},{'json_version':2}):
            with self.assertRaisesRegex(MaterialError,'JSON_SOURCE_VERSION_REQUIRED'):
                parse(payload,**options)
        # The same legacy-compatible schema exists in both games: a caller
        # supplies the version; content does not silently choose its game.
        self.assertEqual(parse(payload,json_kind='BGEM',json_version=2).version,2)
        self.assertEqual(parse(payload,json_kind='BGEM',json_version=22).version,22)

    def test_json_scan_and_pipeline_dry_run_retain_explicit_provenance(self):
        from texture_api import run_job
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory)/'input';(base/'Materials').mkdir(parents=True)
            source=base/'Materials'/'sample.bgem';source.write_bytes(encoded(effect_json()))
            original=hashlib.sha256(source.read_bytes()).hexdigest()
            jobs=scan_jobs(base,direction='4-to-76',include_textures=False)
            self.assertEqual(jobs[0]['material_version'],2)
            self.assertEqual(jobs[0]['material_source_info']['source_format'],'json')
            self.assertIn('JSON_SOURCE_VERSION_HINT',jobs[0]['metadata']['material_import']['diagnostic'])
            output=Path(directory)/'output'
            info=run_job(jobs[0],output,direction='4-to-76',dry_run=True,options={'texconv_path':False})
            self.assertEqual(info['material']['source_format'],'json')
            self.assertEqual(info['material']['source_info']['source_version_hint'],2)
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),original)
            self.assertFalse(output.exists())

    def test_json_file_dry_run_uses_suffix_and_requested_source_direction(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'source.BGSM';source.write_bytes(encoded(surface_json()))
            output=Path(directory)/'result.bgsm'
            info=convert_file(source,output,dry_run=True)
            self.assertEqual(info['source_version'],22)
            self.assertEqual(info['source_format'],'json')
            self.assertFalse(output.exists())
            neutral=Path(directory)/'source.json';neutral.write_bytes(source.read_bytes())
            hinted=convert_file(neutral,output,dry_run=True,json_kind='BGSM')
            self.assertEqual(hinted['source_info']['source_kind_hint'],'BGSM')
            self.assertFalse(output.exists())

    def test_unknown_property_rejected_even_when_false(self):
        value=effect_json();value['bUnknownEngineFeature']=False
        with self.assertRaisesRegex(MaterialError,'Unknown JSON material property'):
            parse(encoded(value),json_kind='BGEM',json_version=2)

    def test_duplicate_and_canonical_alias_properties_rejected(self):
        duplicate=b'{"eAlphaBlendMode":"None","eAlphaBlendMode":"Standard"}'
        with self.assertRaisesRegex(MaterialError,'Duplicate JSON material property'):
            parse(duplicate,json_kind='BGEM',json_version=2)
        for extra in ({'iAlpha':1},{'sGrayscaleTexture':''},
                      {'iTileFlags':0},{'iAlphaBlend0':0},{'iMaskWrites':63}):
            value=effect_json();value.update(extra)
            with self.assertRaises(MaterialError):parse(encoded(value),json_kind='BGEM',json_version=22)

    def test_nonfinite_wrong_types_and_unsafe_paths_rejected(self):
        for extra in ({'fAlpha':float('nan')},{'bAlphaTest':1},
                      {'fAlphaTestRef':256},{'fAlpha':1e100},
                      {'sBaseTexture':'../private.dds'},
                      {'sBaseTexture':'C:/outside.dds'},
                      {'sBaseTexture':'bad\0.dds'},
                      {'cBaseColor':'#ffffffff'}):
            value=effect_json();value.update(extra)
            with self.assertRaises(MaterialError):parse(encoded(value),json_kind='BGEM',json_version=2)

    def test_disabled_texture_and_unverified_enum_rejected(self):
        for extra in ({'bBaseTextureEnable':False},{'eAlphaBlendMode':'Additive'}):
            value=effect_json();value.update(extra)
            with self.assertRaises(MaterialError):parse(encoded(value),json_kind='BGEM',json_version=2)

    def test_write_mask_requires_matching_wire_layout_and_verified_uniform_flags(self):
        value=effect_json()
        for key in ('Albedo','Normal','Specular','Gloss','Emissive','AmbientOcclusion'):
            value['bWriteMask'+key]=True
        with self.assertRaisesRegex(MaterialError,'UNSUPPORTED_JSON_WRITE_MASK'):
            parse(encoded(value),json_kind='BGEM',json_version=2)
        value=surface_json();value['bWriteMaskNormal']=False
        with self.assertRaisesRegex(MaterialError,'UNSUPPORTED_JSON_WRITE_MASK'):
            parse(encoded(value),json_kind='BGSM',json_version=22)

    def test_inactive_conditional_fields_remain_in_provenance_and_warn(self):
        material=parse(encoded(surface_json()),json_kind='BGSM',json_version=22)
        self.assertIn('TerrainThresholdFalloff',material.source_info['conditional_fields_not_written'])
        binary=parse(serialize(material))
        self.assertNotIn('TerrainThresholdFalloff',binary.fields)
        _,info=convert_material(material)
        self.assertTrue(any('JSON_CONDITIONAL_FIELDS_INACTIVE' in warning for warning in info['warnings']))
        self.assertIn('fTerrainThresholdFalloff',info['source_info']['native_properties'])


if __name__=='__main__':unittest.main()
