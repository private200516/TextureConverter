"""Data-only processing options; executable selection is never a JSON setting."""
from collections.abc import Mapping

PROCESSING_OPTION_KEYS = frozenset({
    'mode', 'metal_mode', 'diffuse_fmt', 'normal_fmt', 'specular_fmt', 'glow_fmt',
    'generate_mipmap', 'invert_green', 'generate_s', 'ao_mode', 'ao_strength',
    'metal_compensation', 'metal_strength', 'glow_from_l_alpha', 'preset',
    'reconstruction_strength', 'specular_strength', 'smoothness_strength',
    'tint_retention', 'allow_experimental', 'boundary', 'erode_px', 'feather_px',
    'addressing', 'color_contract_override', 'source_d_transfer',
    'source_r_transfer', 'target_d_transfer', 'reviewed_source_hashes',
    'edit_mask_path', 'protect_mask_path', 'manual_mask', 'protect_mask',
})


def processing_options(value, label='processing options'):
    """Reject internal controls/paths while keeping the existing pixel options."""
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError(label + ' must be an object')
    result = {}
    for key, item in value.items():
        # Retain the old explicit fallback switch, never an executable path.
        if key == 'texconv_path':
            if item is False:
                result[key] = False
            elif item is not None:
                raise ValueError('EXECUTABLE_OPTION_FORBIDDEN: texconv_path cannot select a program')
        elif key in PROCESSING_OPTION_KEYS:
            result[key] = item
        else:
            raise ValueError('UNSUPPORTED_PROCESSING_OPTION: ' + str(key))
    return result


def job_options(value):
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValueError('job_overrides must be an object')
    result = {}
    for key, options in value.items():
        if not isinstance(key, str):
            raise ValueError('job_overrides IDs must be strings')
        result[key] = processing_options(options, 'job_overrides[' + key + ']')
    return result
