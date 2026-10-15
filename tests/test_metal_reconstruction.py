"""Synthetic, copyright-free numeric tests; none implies game validation."""
import unittest
from dataclasses import replace
import numpy as np
from metal_reconstruction import (ColorContract, TargetProfile, MetalOptions,
    reconstruct, decode_target, encode_target, srgb_to_linear, linear_to_srgb,
    inward_boundary, align_numeric, pack_specular, configuration_digest)


class MetalReconstructionTests(unittest.TestCase):
    def setUp(self):
        self.d = np.full((16, 16, 4), .005, dtype=np.float32)
        self.d[..., 3] = np.arange(256, dtype=np.float32).reshape(16, 16)/255
        self.r = np.full((16, 16, 3), .6, dtype=np.float32)
        self.l = np.full((16, 16), .3, dtype=np.float32)
        self.s = np.full((16, 16), .4, dtype=np.float32)
        self.g = np.full((16, 16), .7, dtype=np.float32)
        self.dcontract = ColorContract('synthetic_float', 'color', 'linear', 'stored', provenance='synthetic numeric fixture')
        self.rcontract = ColorContract('synthetic_float', 'reflectance_rgb', 'linear', 'stored', provenance='synthetic numeric fixture')
        self.opts = MetalOptions(mode='auto_conservative', color_strength=1, specular_strength=1,
                                 smoothness_strength=1, addressing='clamp')
        self.profile = TargetProfile()

    def run_core(self, **kw):
        args = dict(diffuse_source=self.d, reflectance_source=self.r, smoothness_source=self.l,
                    diffuse_base=self.d, specular_base=self.s, smoothness_base=self.g,
                    d_contract=self.dcontract, r_contract=self.rcontract,
                    target_contract=self.dcontract, profile=self.profile, options=self.opts,
                    intent='preview')
        args.update(kw)
        return reconstruct(**args)

    def test_T01_disabled_exact_baseline_even_unknown_space(self):
        result = self.run_core(options=MetalOptions(), r_contract=ColorContract('BC4', 'reflectance_scalar'))
        np.testing.assert_array_equal(result.diffuse, self.d)
        np.testing.assert_array_equal(result.specular_strength, self.s)
        np.testing.assert_array_equal(result.smoothness, self.g)
        self.assertFalse(result.report['applied'])

    def test_T02_dark_plastic_high_smoothness_is_not_metal(self):
        result = self.run_core(reflectance_source=np.full_like(self.r, .03), smoothness_source=np.ones_like(self.l))
        np.testing.assert_array_equal(result.diffuse, self.d)
        self.assertFalse(np.any(result.final_mask))

    def test_T03_synthetic_approval_gate_and_controlled_formula(self):
        # Test-only synthetic evidence: never ship as a real approved profile.
        evidence = dict(user_confirmed=True,target_game='FO4', game_version='TEST FIXTURE',
                        observed_by='unit test',sample_id='synthetic T03')
        profile = replace(self.profile, approved=True, approval_evidence=evidence,
                          variation_strength=0, tint_retention=0, target_mid_work=(.2, .21, .22))
        mask = np.zeros((16, 16)); mask[4:12, 4:12] = 1
        result = self.run_core(profile=profile, options=replace(self.opts, mode='manual'),
                               manual_mask=mask, intent='export')
        np.testing.assert_allclose(result.diffuse[mask == 1, :3], np.tile([.2,.21,.22], (64,1)), atol=1e-7)
        np.testing.assert_array_equal(result.diffuse[mask == 0], self.d[mask == 0])
        self.assertTrue(result.report['applied'])
        self.assertFalse(result.report['game_verified'])

    def test_T04_mixed_surface_protection_exact(self):
        d = self.d.copy(); r = self.r.copy()
        r[:8, :8] = .03  # rubber, even with high gloss
        d[:8, 8:, :3] = [.4, .08, .015]  # rust
        d[8:, :8, :3] = [1, .4, .3]  # colored markings
        protect = np.zeros((16,16)); protect[10:14, 10:14] = 1  # dark text/paint user protection
        result = self.run_core(diffuse_source=d, diffuse_base=d, reflectance_source=r, protect_mask=protect)
        allowed = np.zeros((16,16),bool);allowed[8:,8:] = True;allowed[protect == 1] = False
        changed = np.any(result.diffuse != d,axis=2)
        self.assertFalse(np.any(changed & ~allowed))
        self.assertGreater(np.count_nonzero(changed), 0)
        np.testing.assert_array_equal(result.specular_strength[protect == 1], self.s[protect == 1])
        np.testing.assert_array_equal(result.smoothness[protect == 1], self.g[protect == 1])

    def test_T05_missing_reflectance_keeps_black(self):
        result = self.run_core(reflectance_source=None)
        np.testing.assert_array_equal(result.diffuse,self.d)
        self.assertIn('MISSING_REFLECTANCE',result.report['diagnostic_codes'])

    def test_T06_constant_4x4_adds_no_texture_detail(self):
        result = self.run_core(reflectance_source=np.full((4,4,3),.6,dtype=np.float32))
        np.testing.assert_array_equal(result.diffuse[...,:3],np.broadcast_to(result.diffuse[0,0,:3],(16,16,3)))
        self.assertTrue(result.report['reflectance_resampling']['constant'])
        self.assertIn('no detail recovered', ' '.join(result.report['warnings']))

    def test_T07_srgb_decode_once_and_roundtrip(self):
        contract = replace(self.dcontract, transfer_function='srgb')
        values=np.array([0,.0031308,.04045,.18,.5,1.0])
        work=decode_target(values,contract)
        self.assertAlmostEqual(work[4],.21404114048223255,places=10)
        np.testing.assert_allclose(encode_target(work,contract),values,atol=3e-8)
        linear_decoder=replace(contract,decoder_output_space='linear')
        np.testing.assert_array_equal(decode_target(work,linear_decoder),work)
        np.testing.assert_array_equal(encode_target(work,linear_decoder),work)

    def test_T07_scalar_r_rejects_implicit_color_gamma(self):
        contract=replace(self.rcontract, semantic='reflectance_scalar',transfer_function='srgb')
        result=self.run_core(reflectance_source=np.ones((16,16),dtype=float),r_contract=contract)
        self.assertIn('POSSIBLE_COLORSPACE_ERROR',result.report['diagnostic_codes'])
        np.testing.assert_array_equal(result.diffuse,self.d)

    def test_T07_unknown_colorspace_and_conflict_fail_closed(self):
        result=self.run_core(d_contract=replace(self.dcontract,transfer_function='unknown'))
        self.assertIn('POSSIBLE_COLORSPACE_ERROR',result.report['diagnostic_codes'])
        self.assertFalse(result.report['applied'])
        result=self.run_core(diagnostics=['MATERIAL_REFERENCE_ERROR'])
        self.assertFalse(result.report['applied'])

    def test_T09_specular_channels_isolated_and_not_inverted(self):
        packed=pack_specular(self.s,self.l)
        np.testing.assert_array_equal(packed[...,0],self.s)
        np.testing.assert_array_equal(packed[...,1],self.l)
        r=self.run_core(options=replace(self.opts,color_strength=0,smoothness_strength=0))
        np.testing.assert_array_equal(r.diffuse,self.d)
        np.testing.assert_array_equal(r.smoothness,self.g)
        self.assertTrue(np.any(r.specular_strength != self.s))
        r=self.run_core(options=replace(self.opts,color_strength=0,specular_strength=0))
        np.testing.assert_array_equal(r.diffuse,self.d)
        np.testing.assert_array_equal(r.specular_strength,self.s)
        np.testing.assert_allclose(r.smoothness,self.l)

    def test_T10_zero_and_gray_mask_alpha_exact(self):
        manual=replace(self.opts,mode='manual')
        zero=self.run_core(options=manual,manual_mask=np.zeros((16,16)))
        np.testing.assert_array_equal(zero.diffuse,self.d)
        full=self.run_core(options=manual,manual_mask=np.ones((16,16)))
        gray=self.run_core(options=manual,manual_mask=np.full((16,16),.5))
        np.testing.assert_allclose(gray.diffuse[...,:3],(full.diffuse[...,:3]+self.d[...,:3])*.5,atol=2e-8)
        np.testing.assert_array_equal(full.diffuse[...,3],self.d[...,3])
        np.testing.assert_array_equal(gray.diffuse[...,3],self.d[...,3])

    def test_T10_protection_beats_manual_add(self):
        protect=np.ones((16,16));protect[4:12,4:12]=0
        result=self.run_core(options=replace(self.opts,mode='manual'),manual_mask=np.ones((16,16)),protect_mask=protect)
        np.testing.assert_array_equal(result.diffuse[protect == 1],self.d[protect == 1])
        np.testing.assert_array_equal(result.specular_strength[protect == 1],self.s[protect == 1])
        np.testing.assert_array_equal(result.smoothness[protect == 1],self.g[protect == 1])
        self.assertFalse(np.any(result.final_mask[protect == 1]))

    def test_T10_srgb_mask_zero_no_roundtrip_change(self):
        rng=np.random.default_rng(3);base=rng.random((16,16,4),dtype=np.float32)
        mask=np.zeros((16,16));mask[8:,:]=1
        result=self.run_core(diffuse_base=base,target_contract=replace(self.dcontract,transfer_function='srgb'),
                             options=replace(self.opts,mode='manual'),manual_mask=mask)
        np.testing.assert_array_equal(result.diffuse[:8],base[:8])
        np.testing.assert_array_equal(result.diffuse[...,3],base[...,3])

    def test_T11_inward_feather_cannot_expand(self):
        mask=np.zeros((16,16));mask[3:13,3:13]=1
        out=inward_boundary(mask,1,3,'clamp')
        self.assertFalse(np.any(out[mask == 0]))
        self.assertEqual(out[3,8],0)
        self.assertGreater(out[6,8],out[4,8])
        np.testing.assert_array_equal(inward_boundary(np.ones((16,16)),0,3,'wrap'),np.ones((16,16)))

    def test_T11_unknown_addressing_is_not_guessed(self):
        result=self.run_core(options=replace(self.opts,erode_px=1,addressing='unknown'))
        self.assertFalse(result.report['applied'])
        self.assertIn('NEEDS_MANUAL_REVIEW',result.report['diagnostic_codes'])

    def test_T12_hash_determinism_and_mask_variants(self):
        a=configuration_digest(self.profile,self.opts,np.zeros((16,16)))
        self.assertEqual(a,configuration_digest(self.profile,self.opts,np.zeros((16,16))))
        self.assertNotEqual(a,configuration_digest(self.profile,self.opts,np.ones((16,16))))
        self.assertNotEqual(a,configuration_digest(self.profile,replace(self.opts,color_strength=.1),np.zeros((16,16))))

    def test_T13_inputs_are_not_mutated_and_results_repeat(self):
        inputs=[self.d,self.r,self.l,self.s,self.g];copies=[x.copy() for x in inputs]
        a=self.run_core();b=self.run_core()
        for value,copy in zip(inputs,copies):np.testing.assert_array_equal(value,copy)
        np.testing.assert_array_equal(a.diffuse,b.diffuse)
        np.testing.assert_array_equal(a.specular_strength,b.specular_strength)

    def test_T15_colored_metal_excluded_from_auto_neutral(self):
        r=np.broadcast_to(np.array([.9,.5,.2]),(16,16,3)).copy()
        result=self.run_core(reflectance_source=r)
        np.testing.assert_array_equal(result.diffuse,self.d)
        self.assertIn('NEEDS_MANUAL_REVIEW',result.report['diagnostic_codes'])

    def test_T15_special_shader_gate_skips_even_manual(self):
        result=self.run_core(options=replace(self.opts,mode='manual',user_confirmed_task=True),
                             manual_mask=np.ones((16,16)),diagnostics=['UNSUPPORTED_SHADER'],intent='export')
        np.testing.assert_array_equal(result.diffuse,self.d)

    def test_experimental_preview_but_not_unattended_export(self):
        preview=self.run_core();export=self.run_core(intent='export')
        self.assertTrue(preview.report['applied']);self.assertFalse(export.report['applied'])
        np.testing.assert_array_equal(export.diffuse,self.d)
        confirmed=self.run_core(intent='export',options=replace(self.opts,user_confirmed_task=True))
        self.assertTrue(confirmed.report['applied'])
        self.assertEqual(confirmed.report['profile_status'],'experimental')
        self.assertFalse(self.profile.approved)

    def test_approved_boolean_without_game_evidence_not_enough(self):
        result=self.run_core(profile=replace(self.profile,approved=True),intent='export')
        self.assertFalse(result.report['applied'])
        self.assertEqual(result.report['profile_status'],'experimental')

    def test_monotonic_specular_and_smoothness_curves(self):
        ramp=np.linspace(.45,1,16);r=np.broadcast_to(ramp[None,:,None],(16,16,3)).copy()
        l=np.broadcast_to(np.linspace(0,1,16)[None,:],(16,16)).copy()
        result=self.run_core(reflectance_source=r,smoothness_source=l,
                             profile=replace(self.profile,specular_gamma=1.3,smoothness_gamma=.7))
        self.assertTrue(np.all(np.diff(result.specular_strength,axis=1)>=0))
        self.assertTrue(np.all(np.diff(result.smoothness,axis=1)>=0))

    def test_missing_smoothness_keeps_baseline(self):
        result=self.run_core(smoothness_source=None)
        np.testing.assert_array_equal(result.smoothness,self.g)
        self.assertTrue(result.report['applied'])

    def test_dimensions_nan_integer_and_radius_rejected(self):
        with self.assertRaisesRegex(ValueError,'size mismatch'):
            self.run_core(protect_mask=np.ones((4,4)))
        with self.assertRaisesRegex(ValueError,'size mismatch'):
            self.run_core(options=replace(self.opts,mode='manual'),manual_mask=np.ones((4,4)))
        with self.assertRaisesRegex(ValueError,'floating point'):
            self.run_core(diffuse_base=np.zeros((16,16,4),dtype=np.uint8))
        with self.assertRaisesRegex(ValueError,'source pixels'):
            self.run_core(options=replace(self.opts,erode_px=33))
        d=self.d.copy();d[0,0,0]=np.nan
        with self.assertRaisesRegex(ValueError,'non-finite'):
            self.run_core(diffuse_source=d)

    def test_numeric_resampling_clamp_and_wrap(self):
        source=np.array([[0.,1.],[0.,1.]])
        clamp=align_numeric(source,(4,4),'clamp')
        wrap=align_numeric(source,(4,4),'wrap')
        self.assertEqual(clamp[0,0],0);self.assertEqual(clamp[0,-1],1)
        self.assertAlmostEqual(wrap[0,0],.25);self.assertAlmostEqual(wrap[0,-1],.75)
        with self.assertRaises(ValueError):align_numeric(source,(4,4),'unknown')


if __name__ == '__main__':
    unittest.main()
