"""Compose stock-relative firmware features."""
from .plan import Plan
from .patch import Edit
from .features.liveview_dual import build as build_dual, VIDEO_STOCK_WINDOWS
from .platform.z30_120 import BASE


PRESETS = ('full', 'liveview-dual', 'shutter-sound', 'm900', 'mtp-fast', 'video-rot',
           'bitrate-slowmo-uncap', 'aspect-profiles', 'photo-zebra', 'wifi-5ghz', 'date-stamp', 'portrait-ui',
           'gyro-evr', '3d-tracking', 'af-frame-colour', 'focal-length', 'nef-menu', 'nef-uncompressed', 'nef-compressed',
           'battery-percent')
LEGACY_PRESETS = ('modular',)
FEATURE_ORDER = ('liveview-dual', 'm900', 'shutter-sound', 'mtp-fast', 'video-rot',
                 'bitrate-slowmo-uncap', 'aspect-profiles', 'photo-zebra', 'wifi-5ghz', 'date-stamp', 'portrait-ui',
                 'gyro-evr', '3d-tracking', 'af-frame-colour', 'focal-length', 'nef-menu', 'nef-uncompressed', 'nef-compressed',
                 'battery-percent')
EXPERIMENTAL_FEATURES = ('battery-percent',)  # opt-in builds, not part of full
NEF_FIXED_FEATURES = ('nef-uncompressed', 'nef-compressed')
NEF_COMPRESSION_FEATURES = ('nef-menu',) + NEF_FIXED_FEATURES
FULL_FEATURES = tuple(name for name in FEATURE_ORDER
                      if name not in NEF_FIXED_FEATURES + EXPERIMENTAL_FEATURES)
MODULAR_FEATURES = ('liveview-dual', 'm900', 'shutter-sound')


def resolve_presets(preset='full'):
    """Expand aliases once, independent of selection order or duplicates."""
    selected = [preset] if isinstance(preset, str) else list(preset)
    if not selected:
        raise ValueError('Select at least one feature preset')
    features = set()
    for name in selected:
        if name not in PRESETS + LEGACY_PRESETS:
            raise ValueError(f'Unknown feature preset: {name}')
        if name == 'full':
            features.update(FULL_FEATURES)
        elif name == 'modular':
            features.update(MODULAR_FEATURES)
        else:
            features.add(name)
    # A fixed NEF preset chosen with full replaces the menu that full brings.
    if 'nef-menu' not in selected and any(name in features for name in NEF_FIXED_FEATURES):
        features.discard('nef-menu')
    if sum(name in features for name in NEF_COMPRESSION_FEATURES) > 1:
        raise ValueError('Select only one NEF compression preset')
    # These pages share the sound menu/state framework.
    if any(f in features for f in ('bitrate-slowmo-uncap', 'aspect-profiles', 'photo-zebra', 'date-stamp', 'portrait-ui', 'nef-menu',
                                   'gyro-evr', '3d-tracking', 'af-frame-colour')):
        features.add('shutter-sound')
    return list(dict.fromkeys(selected)), [f for f in FEATURE_ORDER if f in features]


def build(source, preset='full', *, aspect_trace=False, aspect_border=False,
          aspect_border_diagnostic=False, aspect_border_lease=False,
          aspect_mask=None, aspect_mask_trial=False, module_host=False, ptp_read=False,
          evr_hook=False, imu_hook=False, face_hook=False, tail_base=None, card=False, resident=None):
    """card=True returns (plan, report) without writing an image: card.py turns
    the plan's edits and tail (assembled at tail_base) into an SD-card patch."""
    # module_host is a research option only: the SD module host is no longer a
    # patcher preset, but its tools still build it on top of the sound framework.
    selected, features = resolve_presets(preset)
    if module_host and 'shutter-sound' not in features:
        raise ValueError('The module host requires the shutter-sound framework')
    # evr_hook is research-only: a dormant e-VR result hook that only SD modules
    # can arm, so it is never built without the module host.
    if evr_hook and not module_host:
        raise ValueError('The e-VR hook requires the module host')
    if imu_hook and not module_host:
        raise ValueError('The IMU hook requires the module host')
    if face_hook and not module_host:
        raise ValueError('The detection result hook requires the module host')
    # 3d-tracking runs on the same stock site as the research detection hook.
    if face_hook and '3d-tracking' in features:
        raise ValueError('3d-tracking already hooks the detection result writer')
    # gyro-evr installs the e-VR and IMU hooks itself (with its built-in autostart).
    if 'gyro-evr' in features and (evr_hook or imu_hook):
        raise ValueError('gyro-evr already installs the e-VR and IMU hooks')
    # Photo aspect and its live-view mask are one public preset. Historical
    # border/static-mask experiments keep their own explicitly selected path;
    # researchers may still pass False for a no-mask comparison build.
    if aspect_mask is None:
        aspect_mask = ('aspect-profiles' in features and
                       not (aspect_border or aspect_mask_trial))
    if aspect_trace and 'aspect-profiles' not in features:
        raise ValueError('Aspect trace requires aspect-profiles')
    if aspect_border and 'aspect-profiles' not in features:
        raise ValueError('Aspect border requires aspect-profiles')
    if aspect_border_diagnostic and not (aspect_trace and aspect_border):
        raise ValueError('Border diagnostic requires aspect trace and border trial')
    if aspect_border_lease and not aspect_border:
        raise ValueError('Border channel lease requires border trial')
    if (aspect_mask or aspect_mask_trial) and 'aspect-profiles' not in features:
        raise ValueError('Soft aspect mask requires aspect-profiles')
    if aspect_mask and aspect_mask_trial:
        raise ValueError('Soft aspect mask dynamic and trial modes are mutually exclusive')
    if card and (module_host or ptp_read or evr_hook or imu_hook or face_hook):
        raise ValueError('Research hooks are not available in card builds')
    if features == ['liveview-dual'] and not card:
        output, report = build_dual(source)
        report.update(preset=selected[0] if len(selected) == 1 else selected,
                      selected_presets=selected, enabled_features=features)
        return output, report
    plan = Plan(source, tail_base=tail_base, card=card, resident=resident)
    if 'gyro-evr' in features:
        # The SD host (research builds) drives the built-in copy from its Run row.
        from .features.gyro_evr import layout
        plan.evr_builtin_layout = layout()
    if 'liveview-dual' in features:
        dual, report = build_dual(source, plan.tail_base)
        plan.tail.extend(dual[len(source) - 2:-2])
        plan.edits.extend(Edit(int(e['offset'], 16), bytes.fromhex(e['expected_hex']),
                              bytes.fromhex(e['replacement_hex']), e['name']) for e in report['edits'])
        plan.features['liveview_dual'] = {key: report[key] for key in
                                        ('hooks', 'mask_rom_address', 'full_info_disp', 'full_info_hooks')}
    if 'm900' in features:
        from .features.long_exposure import add
        add(plan)
    if 'shutter-sound' in features:
        from .features.shutter_sound import add
        add(plan, modules=module_host, bitrate='bitrate-slowmo-uncap' in features,
            zebra='photo-zebra' in features, stamp='date-stamp' in features, portrait='portrait-ui' in features,
            nef='nef-menu' in features,
            gyro='gyro-evr' in features, trk3d='3d-tracking' in features, afcol='af-frame-colour' in features,
            aspect='aspect-profiles' in features, aspect_trace=aspect_trace, aspect_border=aspect_border,
            aspect_border_diagnostic=aspect_border_diagnostic,aspect_border_lease=aspect_border_lease,
            aspect_mask=aspect_mask, aspect_mask_trial=aspect_mask_trial)
    if 'mtp-fast' in features:
        from .features.mtp_fast import add
        add(plan)
    if 'video-rot' in features:
        from .features.video_rot import add
        add(plan)
    if 'bitrate-slowmo-uncap' in features:
        from .features.bitrate_slowmo_uncap import add
        add(plan)
    if 'photo-zebra' in features:
        from .features.photo_zebra import add
        add(plan)
    if 'wifi-5ghz' in features:
        from .features.wifi_5ghz import add
        add(plan)
    if 'date-stamp' in features:
        from .features.date_stamp import add
        add(plan)
    if 'portrait-ui' in features:
        from .features.portrait_ui import add
        add(plan)
    for name in NEF_FIXED_FEATURES:
        if name in features:
            from .features.nef_compression import add
            add(plan, name)
    if 'nef-menu' in features:
        from .features.nef_menu import add
        add(plan)
    if 'gyro-evr' in features:
        from .features.gyro_evr import add
        add(plan)
    if '3d-tracking' in features:
        from .features.tracking3d import add
        add(plan)
    if 'af-frame-colour' in features:
        from .features.af_frame_colour import add
        add(plan)
    if 'focal-length' in features:
        from .features.focal_length import add
        add(plan)
    if 'battery-percent' in features:
        from .features.battery_percent import add
        add(plan)
    if ptp_read:
        # Research only: read-only USB PTP 0x9FF1 ReadMemory for live readouts.
        from .features.ptp_read import add
        add(plan)
    if evr_hook:
        from .features.evr_hook import add
        add(plan)
    if imu_hook:
        from .features.imu_hook import add
        add(plan)
    if face_hook:
        from .features.face_hook import add
        add(plan)
    if card:
        for address, expected in VIDEO_STOCK_WINDOWS:
            for edit in plan.edits:
                if edit.offset < address - BASE + len(expected) and address - BASE < edit.offset + len(edit.expected):
                    raise ValueError('Video shutter differs from stock')
        return plan, dict(preset=selected[0] if len(selected) == 1 else selected,
                          selected_presets=selected, enabled_features=features, features=plan.features)
    output, report = plan.finish()
    for address, expected in VIDEO_STOCK_WINDOWS:
        if output[address - BASE:address - BASE + len(expected)] != expected:
            raise ValueError('Video shutter differs from stock')
    report['preset'] = selected[0] if len(selected) == 1 else selected
    report['selected_presets'] = selected
    report['enabled_features'] = (features + (['module-host'] if module_host else [])
                                  + (['ptp-read-memory'] if ptp_read else [])
                                  + (['evr-hook'] if evr_hook else [])
                                  + (['imu-hook'] if imu_hook else [])
                                  + (['face-hook'] if face_hook else []))
    report['video_shutter_stock'] = True
    return output, report
