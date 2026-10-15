"""Offline, deterministic FO76 metal adaptation in explicit numeric spaces.

This module is an artistic adaptation, not a Bethesda conversion formula or a
material classifier. No IO, texture compression, network, AO, normal-map or AI
operations occur here. Every built-in profile remains experimental.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any
import hashlib
import json
import numpy as np

ALGORITHM_VERSION = 'metal-reconstruction-1.0-alpha'
READY = 'READY_FOR_METAL_ANALYSIS'


@dataclass(frozen=True)
class ColorContract:
    storage_format: str
    semantic: str
    transfer_function: str = 'unknown'
    decoder_output_space: str = 'stored'
    explicit_override: str | None = None
    provenance: str = ''

    def effective_transfer(self) -> str:
        transfer = self.transfer_function
        if transfer == 'explicit_override':
            transfer = self.explicit_override
        if transfer not in ('srgb', 'linear'):
            raise ValueError('Unresolved transfer function; explicit evidence or override required')
        if self.decoder_output_space not in ('stored', 'linear'):
            raise ValueError('Unresolved decoder output space')
        if not self.storage_format or not self.provenance:
            raise ValueError('Storage format and colorspace provenance are required')
        # A numeric BC4 reflectance channel must never inherit RGB gamma by accident.
        if self.semantic in ('smoothness', 'ao', 'mask', 'normal') and transfer != 'linear':
            raise ValueError('Numeric channel cannot use color transfer')
        if self.semantic == 'reflectance_scalar' and transfer != 'linear':
            raise ValueError('Scalar reflectance requires a linear numeric contract')
        return transfer


@dataclass(frozen=True)
class TargetProfile:
    id: str = 'neutral_steel_experimental_v1'
    approved: bool = False
    approval_evidence: dict[str, Any] | None = None
    target_mid_work: tuple[float, float, float] = (0.22, 0.22, 0.22)
    reflectance_reference: float | None = 0.5
    variation_strength: float = 0.55
    tint_retention: float = 0.25
    variation_min: float = 0.55
    variation_max: float = 1.5
    tint_min: float = 0.5
    tint_max: float = 1.5
    specular_scale: float = 0.8
    specular_gamma: float = 1.0
    smoothness_scale: float = 1.0
    smoothness_gamma: float = 1.0
    neutral_tolerance: float = 0.35

    def is_approved(self) -> bool:
        evidence = self.approval_evidence or {}
        return bool(self.approved and evidence.get('user_confirmed') is True
                    and evidence.get('target_game') == 'FO4'
                    and evidence.get('game_version') and evidence.get('observed_by')
                    and evidence.get('sample_id'))


@dataclass(frozen=True)
class MetalOptions:
    mode: str = 'off'
    color_strength: float = 0.65
    specular_strength: float = 0.5
    smoothness_strength: float = 0.0
    user_confirmed_task: bool = False
    d_low: float = 0.01
    d_high: float = 0.08
    r_low: float = 0.15
    r_high: float = 0.45
    candidate_threshold: float = 0.9
    erode_px: int = 0
    feather_px: int = 0
    addressing: str = 'unknown'


@dataclass
class MetalResult:
    diffuse: np.ndarray
    specular_strength: np.ndarray
    smoothness: np.ndarray
    candidate_mask: np.ndarray
    protect_mask: np.ndarray
    final_mask: np.ndarray
    report: dict[str, Any]


EXPERIMENTAL_PROFILES = {'neutral_steel_experimental_v1': TargetProfile()}


def _array(value, label, channels=None):
    array = np.asarray(value)
    if array.dtype.kind != 'f':
        raise ValueError(label + ' must contain normalized floating point values')
    if array.ndim not in (2, 3) or min(array.shape[:2]) < 1:
        raise ValueError(label + ' must be a nonempty HxW or HxWxC array')
    if channels is not None and (array.ndim != 3 or array.shape[2] not in channels):
        raise ValueError(label + ' has an unsupported channel count')
    if not np.all(np.isfinite(array)) or np.any(array < 0) or np.any(array > 1):
        raise ValueError(label + ' contains out-of-range or non-finite data')
    return array


def srgb_to_linear(value):
    value = np.asarray(value, dtype=np.float64)
    return np.where(value <= 0.04045, value / 12.92,
                    np.power((value + 0.055) / 1.055, 2.4))


def linear_to_srgb(value):
    value = np.asarray(value, dtype=np.float64)
    return np.where(value <= 0.0031308, 12.92 * value,
                    1.055 * np.power(np.maximum(value, 0), 1 / 2.4) - 0.055)


def decode_target(value, contract: ColorContract):
    """Decode only RGB/numeric data; callers preserve Alpha independently."""
    transfer = contract.effective_transfer()
    value = np.asarray(value, dtype=np.float64)
    if contract.decoder_output_space == 'linear' or transfer == 'linear':
        return value.copy()
    return srgb_to_linear(value)


def encode_target(value, contract: ColorContract):
    """Encode to the same value space as the target baseline array."""
    transfer = contract.effective_transfer()
    value = np.asarray(value, dtype=np.float64)
    if contract.decoder_output_space == 'linear' or transfer == 'linear':
        return value.copy()
    return linear_to_srgb(value)


def luminance(rgb):
    rgb = np.asarray(rgb)
    if rgb.ndim == 2:
        return rgb.astype(np.float64)
    if rgb.shape[2] == 1:
        return rgb[..., 0].astype(np.float64)
    return np.asarray(rgb[..., :3], dtype=np.float64) @ np.array([0.2126, 0.7152, 0.0722])


def _smoothstep(low, high, value):
    t = np.clip((value - low) / (high - low), 0, 1)
    return t * t * (3 - 2 * t)


def align_numeric(array, shape, addressing='clamp'):
    """Pixel-centre bilinear sampling. This adds no recoverable detail.

    Unknown addressing is accepted only for matching grids or constant inputs.
    The exported diffuse grid is never resized.
    """
    array = np.asarray(array, dtype=np.float64)
    height, width = shape
    if array.shape[:2] == (height, width):
        return array.copy()
    if np.all(array == array[0, 0]):
        return np.broadcast_to(array[0, 0], (height, width) + array.shape[2:]).copy()
    if addressing not in ('clamp', 'wrap'):
        raise ValueError('Nonconstant resampling requires explicit clamp/wrap addressing')
    sy = (np.arange(height) + .5) * array.shape[0] / height - .5
    sx = (np.arange(width) + .5) * array.shape[1] / width - .5
    y0, x0 = np.floor(sy).astype(int), np.floor(sx).astype(int)
    fy, fx = sy - y0, sx - x0
    y1, x1 = y0 + 1, x0 + 1
    if addressing == 'wrap':
        y0 %= array.shape[0]; y1 %= array.shape[0]
        x0 %= array.shape[1]; x1 %= array.shape[1]
    else:
        y0 = np.clip(y0, 0, array.shape[0] - 1); y1 = np.clip(y1, 0, array.shape[0] - 1)
        x0 = np.clip(x0, 0, array.shape[1] - 1); x1 = np.clip(x1, 0, array.shape[1] - 1)
    fy = fy.reshape((height, 1) + (1,) * (array.ndim - 2))
    fx = fx.reshape((1, width) + (1,) * (array.ndim - 2))
    top = array[y0[:, None], x0[None, :]] * (1-fx) + array[y0[:, None], x1[None, :]] * fx
    bottom = array[y1[:, None], x0[None, :]] * (1-fx) + array[y1[:, None], x1[None, :]] * fx
    return top * (1-fy) + bottom * fy


def _erode_one(mask, addressing):
    if addressing == 'wrap':
        return np.minimum.reduce([mask, np.roll(mask, 1, 0), np.roll(mask, -1, 0),
                                  np.roll(mask, 1, 1), np.roll(mask, -1, 1)])
    padded = np.pad(mask, 1, mode='constant', constant_values=0)
    return np.minimum.reduce([mask, padded[:-2, 1:-1], padded[2:, 1:-1],
                              padded[1:-1, :-2], padded[1:-1, 2:]])


def inward_boundary(mask, erode_px=0, feather_px=0, addressing='unknown'):
    """Four-neighbour inward erosion/feathering, never selecting outside input."""
    if not erode_px and not feather_px:
        return mask.copy()
    if addressing not in ('clamp', 'wrap'):
        raise ValueError('Boundary processing requires explicit addressing')
    result = mask.copy()
    for _ in range(erode_px):
        result = _erode_one(result, addressing)
    if feather_px:
        inside = (result > 0).astype(np.float64)
        depth = np.zeros_like(result)
        for _ in range(feather_px):
            depth += inside
            inside = _erode_one(inside, addressing)
        result *= depth / feather_px
    return np.minimum(result, mask)


def _validate_config(profile, options):
    if options.mode not in ('off', 'auto_conservative', 'manual'):
        raise ValueError('Unknown metal mode')
    for name in ('color_strength', 'specular_strength', 'smoothness_strength', 'candidate_threshold'):
        if not 0 <= getattr(options, name) <= 1:
            raise ValueError(name + ' must be between zero and one')
    if not 0 <= options.d_low < options.d_high <= 1 or not 0 <= options.r_low < options.r_high <= 1:
        raise ValueError('Invalid diagnostic thresholds')
    for radius in (options.erode_px, options.feather_px):
        if not isinstance(radius, int) or isinstance(radius, bool) or not 0 <= radius <= 32:
            raise ValueError('Boundary radius must be an integer in source pixels, 0..32')
    if options.addressing not in ('unknown', 'clamp', 'wrap'):
        raise ValueError('Unknown addressing mode')
    numeric = np.array([*profile.target_mid_work, profile.variation_strength, profile.tint_retention,
                        profile.variation_min, profile.variation_max, profile.tint_min, profile.tint_max,
                        profile.specular_scale, profile.specular_gamma, profile.smoothness_scale,
                        profile.smoothness_gamma, profile.neutral_tolerance], dtype=float)
    if not np.all(np.isfinite(numeric)) or len(profile.target_mid_work) != 3:
        raise ValueError('Invalid profile values')
    if any(v < 0 or v > 1 for v in profile.target_mid_work):
        raise ValueError('Target color must be normalized linear RGB')
    if not 0 <= profile.variation_strength <= 1 or not 0 <= profile.tint_retention <= 1:
        raise ValueError('Profile blend strengths must be normalized')
    if not 0 < profile.variation_min <= profile.variation_max or not 0 < profile.tint_min <= profile.tint_max:
        raise ValueError('Invalid bounded variation/tint')
    if min(profile.specular_scale, profile.smoothness_scale, profile.neutral_tolerance) < 0 or min(profile.specular_gamma, profile.smoothness_gamma) <= 0:
        raise ValueError('Curves must be monotonic increasing')
    if profile.reflectance_reference is not None and (not np.isfinite(profile.reflectance_reference) or profile.reflectance_reference <= 0):
        raise ValueError('Reflectance reference must be positive or absent')


def configuration_digest(profile, options, manual_mask=None, protect_mask=None):
    """Stable algorithm settings/mask hash; job adds source, codec and output hashes."""
    h = hashlib.sha256(json.dumps({'algorithm': ALGORITHM_VERSION, 'profile': asdict(profile),
                                  'options': asdict(options)}, sort_keys=True, allow_nan=False).encode('utf8'))
    for mask in (manual_mask, protect_mask):
        if mask is None:
            h.update(b'none')
        else:
            arr = np.asarray(mask, dtype='<f4')
            h.update(str(arr.shape).encode('ascii')); h.update(arr.tobytes())
    return h.hexdigest()


def reconstruct(diffuse_source, reflectance_source, smoothness_source,
                diffuse_base, specular_base, smoothness_base, *,
                d_contract: ColorContract, r_contract: ColorContract,
                target_contract: ColorContract, profile: TargetProfile | None = None,
                options: MetalOptions | None = None, manual_mask=None, protect_mask=None,
                diagnostics=None, intent='export') -> MetalResult:
    profile = profile or TargetProfile()
    options = options or MetalOptions()
    _validate_config(profile, options)
    if intent not in ('export', 'preview'):
        raise ValueError('intent must be export or preview')
    base = _array(diffuse_base, 'Diffuse baseline', (3, 4))
    sb = _array(specular_base, 'Specular baseline')
    gb = _array(smoothness_base, 'Smoothness baseline')
    grid = base.shape[:2]
    if sb.shape != grid or gb.shape != grid:
        raise ValueError('Baseline numeric channels must exactly match diffuse dimensions')
    zero = np.zeros(grid, dtype=np.float64)
    protection = zero.copy() if protect_mask is None else _array(protect_mask, 'Protection mask').astype(np.float64)
    if protection.shape != grid:
        raise ValueError('Protection mask size mismatch; caller must explicitly resolve it')
    report = dict(algorithm=ALGORITHM_VERSION, mode=options.mode, intent=intent,
                  profile=asdict(profile), profile_status='approved' if profile.is_approved() else 'experimental',
                  options=asdict(options), colorspaces={'source_d': asdict(d_contract),
                  'source_r': asdict(r_contract), 'target': asdict(target_contract)},
                  diagnostic_codes=[], warnings=[], applied=False, game_verified=False,
                  protection_coverage=float(np.mean(protection > 0)), edited_coverage=0.0,
                  input_dimensions=list(grid), precompression_protection_exact=True,
                  alpha_preserved=True, score_interpretation='heuristic score, not probability')
    result = MetalResult(base.copy(), sb.copy(), gb.copy(), zero.copy(), protection.copy(), zero.copy(), report)

    def stop(code, message):
        if code not in report['diagnostic_codes']:
            report['diagnostic_codes'].append(code)
        report['warnings'].append(message)
        return result

    if options.mode == 'off':
        report['diagnostic_codes'].append('DISABLED_LEGACY_BASELINE')
        return result
    hard_gates = [code for code in (diagnostics or []) if code != READY]
    if hard_gates:
        report['diagnostic_codes'].extend(hard_gates)
        return stop('NEEDS_MANUAL_REVIEW', 'Material diagnostics prevent reconstruction; baseline retained')
    if reflectance_source is None:
        return stop('MISSING_REFLECTANCE', 'No reflectance data; cannot infer steel from a black diffuse')
    try:
        if d_contract.semantic != 'color' or target_contract.semantic != 'color':
            raise ValueError('Diffuse contracts must have color semantics')
        if r_contract.semantic not in ('reflectance_rgb', 'reflectance_scalar'):
            raise ValueError('Reflectance contract has wrong semantics')
        d_contract.effective_transfer(); r_contract.effective_transfer(); target_contract.effective_transfer()
    except ValueError as exc:
        return stop('POSSIBLE_COLORSPACE_ERROR', str(exc))
    source = _array(diffuse_source, 'Source diffuse', (3, 4))
    reflected = _array(reflectance_source, 'Source reflectance')
    if reflected.ndim == 3 and reflected.shape[2] not in (1, 3, 4):
        raise ValueError('Reflectance must be scalar or RGB(A)')
    scalar = reflected.ndim == 2 or reflected.shape[-1] == 1
    if scalar != (r_contract.semantic == 'reflectance_scalar'):
        return stop('POSSIBLE_COLORSPACE_ERROR', 'Reflectance scalar/RGB contract does not match input data')
    try:
        source_linear = align_numeric(decode_target(source[..., :3], d_contract), grid, options.addressing)
        raw_r = reflected if scalar else reflected[..., :3]
        reflection = align_numeric(decode_target(raw_r, r_contract), grid, options.addressing)
    except ValueError as exc:
        return stop('NEEDS_MANUAL_REVIEW', str(exc))
    if reflected.shape[:2] != grid:
        report['warnings'].append('Reflectance resampled on internal grid; no detail recovered')
        report['reflectance_resampling'] = {'source': list(reflected.shape[:2]), 'target': list(grid),
                                            'addressing': options.addressing,
                                            'constant': bool(np.all(reflected == reflected[0, 0]))}
    yd = luminance(source_linear)
    yr = luminance(reflection)
    candidate = (1-_smoothstep(options.d_low, options.d_high, yd)) * _smoothstep(options.r_low, options.r_high, yr)
    result.candidate_mask = candidate
    if scalar:
        rgb_r = np.repeat(yr[..., None], 3, axis=2)
        colored = np.zeros(grid, dtype=bool)
    else:
        rgb_r = reflection
        colored = np.ptp(rgb_r, axis=2) / np.maximum(yr, 1e-8) > profile.neutral_tolerance
    report['colored_reflectance_candidate_coverage'] = float(np.mean(colored & (candidate >= options.candidate_threshold)))
    if np.any(colored & (candidate >= options.candidate_threshold)):
        report['diagnostic_codes'].append('NEEDS_MANUAL_REVIEW')
        report['warnings'].append('Colored reflectance is excluded from neutral-steel automatic selection')
    if options.mode == 'manual':
        if manual_mask is None:
            return stop('NEEDS_MANUAL_REVIEW', 'Manual mode requires an explicit edit mask')
        selection = _array(manual_mask, 'Manual mask').astype(np.float64)
        if selection.shape != grid:
            raise ValueError('Manual mask size mismatch; implicit stretching is forbidden')
    else:
        selection = np.where((candidate >= options.candidate_threshold) & ~colored, candidate, 0)
    try:
        selection = inward_boundary(selection * (1-protection), options.erode_px,
                                    options.feather_px, options.addressing)
    except ValueError as exc:
        return stop('NEEDS_MANUAL_REVIEW', str(exc))
    selection[protection >= 1] = 0
    result.final_mask = selection
    report['candidate_coverage'] = float(np.mean(candidate >= options.candidate_threshold))
    report['selected_coverage'] = float(np.mean(selection > 0))
    report['configuration_digest'] = configuration_digest(profile, options, manual_mask, protect_mask)
    if intent == 'export' and not profile.is_approved() and not options.user_confirmed_task:
        return stop('NEEDS_MANUAL_REVIEW', 'Experimental profile: preview only until this task is explicitly confirmed')
    if not np.any(selection):
        return stop('NO_ELIGIBLE_METAL_REGION', 'No eligible pixels; baseline retained')
    reference = profile.reflectance_reference
    if reference is None:
        reference = float(np.median(yr[selection > 0]))
        report['warnings'].append('Experimental reflectance reference estimated from selected pixels')
        report['reference_fallback'] = 'selected-region median'
    reference = max(reference, 1e-8)
    report['reflectance_reference_used'] = reference
    variation = np.clip(yr/reference, profile.variation_min, profile.variation_max)
    tint = np.clip(rgb_r/np.maximum(yr[..., None], 1e-8), profile.tint_min, profile.tint_max)
    metal = np.asarray(profile.target_mid_work)[None, None, :] * (1+profile.variation_strength*(variation-1))[..., None]
    metal = metal * (1+profile.tint_retention*(tint-1))
    report['candidate_clipped_values'] = int(np.count_nonzero((metal < 0) | (metal > 1)))
    metal = np.clip(metal, 0, 1)
    base_work = decode_target(base[..., :3], target_contract)
    wc = selection * options.color_strength
    rebuilt = encode_target(base_work + (metal-base_work)*wc[..., None], target_contract)
    # Assign edited pixels only: no decode/encode roundtrip touches M=0 or Alpha.
    change_color = wc > 0
    result.diffuse[..., :3][change_color] = np.clip(rebuilt[change_color], 0, 1).astype(base.dtype)
    spec_candidate = np.clip(profile.specular_scale * np.power(yr, profile.specular_gamma), 0, 1)
    ws = selection * options.specular_strength
    change_spec = ws > 0
    new_spec = sb + (spec_candidate-sb)*ws
    result.specular_strength[change_spec] = new_spec[change_spec].astype(sb.dtype)
    if smoothness_source is not None:
        smooth = _array(smoothness_source, 'Source smoothness')
        if smooth.ndim != 2:
            raise ValueError('Pass the semantic smoothness channel only, not packed lighting RGB')
        try:
            smooth = align_numeric(smooth, grid, options.addressing)
        except ValueError as exc:
            report['warnings'].append('Smoothness unchanged: '+str(exc))
            smooth = None
        if smooth is not None:
            smooth_candidate = np.clip(profile.smoothness_scale * np.power(smooth, profile.smoothness_gamma), 0, 1)
            wg = selection * options.smoothness_strength
            change_smooth = wg > 0
            new_smooth = gb + (smooth_candidate-gb)*wg
            result.smoothness[change_smooth] = new_smooth[change_smooth].astype(gb.dtype)
    else:
        report['warnings'].append('Smoothness missing: existing baseline retained; no inferred detail')
    outside = (selection == 0) | (protection >= 1)
    result.diffuse[outside] = base[outside]
    result.specular_strength[outside] = sb[outside]
    result.smoothness[outside] = gb[outside]
    if base.shape[-1] == 4:
        result.diffuse[..., 3] = base[..., 3]
    changed = np.any(result.diffuse != base, axis=2) | (result.specular_strength != sb) | (result.smoothness != gb)
    report['edited_coverage'] = float(np.mean(changed))
    report['applied'] = bool(np.any(changed))
    report['diagnostic_codes'].append(READY)
    report['warnings'].append('Engineering adaptation; FO4 appearance, compression and mipmap boundaries require separate validation')
    return result


def pack_specular(specular_strength, smoothness):
    """Standard FO4 numeric _s: R=specular strength, G=smoothness (no inversion)."""
    s = _array(specular_strength, 'Specular strength')
    g = _array(smoothness, 'Smoothness')
    if s.ndim != 2 or s.shape != g.shape:
        raise ValueError('Specular and smoothness must be matching scalar planes')
    return np.stack((s, g), axis=-1)
