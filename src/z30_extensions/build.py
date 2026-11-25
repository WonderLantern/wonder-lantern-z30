"""Compose stock-relative firmware features."""
from .plan import Plan
from .patch import Edit
from .features.liveview.dual import build as build_dual, VIDEO_STOCK_WINDOWS
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
# Features with a page in the extension menu. Any of them (or the shutter sound)
# brings the menu framework; none of them brings the shutter sound any more.
MENU_FEATURES = ('bitrate-slowmo-uncap', 'aspect-profiles', 'photo-zebra', 'date-stamp', 'portrait-ui', 'nef-menu',
                 'gyro-evr', '3d-tracking', 'af-frame-colour')


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
    return list(dict.fromkeys(selected)), [f for f in FEATURE_ORDER if f in features]


def build(source, preset='full', *, aspect_trace=False, aspect_border=False,
          aspect_border_diagnostic=False, aspect_border_lease=False,
          aspect_mask=None, aspect_mask_trial=False, module_host=False, ptp_read=False,
          evr_hook=False, imu_hook=False, face_hook=False, tail_base=None, card=False, resident=None,
          build_tag='dev', resident_id=None, ready_banner=True, banner_debug=False,
          menu_tab=False):
    """card=True returns (plan, report) without writing an image: card.py turns
    the plan's edits and tail (assembled at tail_base) into an SD-card patch."""
    # module_host is a research option only: the SD module host is no longer a
    # patcher preset; its tools still build it as a page of the menu framework.
    selected, features = resolve_presets(preset)
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
        from .features.gyro.evr import layout
        plan.evr_builtin_layout = layout()
    if 'liveview-dual' in features:
        dual, report = build_dual(source, plan.tail_base)
        plan.tail.extend(dual[len(source) - 2:-2])
        plan.edits.extend(Edit(int(e['offset'], 16), bytes.fromhex(e['expected_hex']),
                              bytes.fromhex(e['replacement_hex']), e['name']) for e in report['edits'])
        plan.features['liveview_dual'] = {key: report[key] for key in
                                        ('hooks', 'mask_rom_address', 'full_info_disp', 'full_info_hooks')}
    if 'm900' in features:
        from .features.photo.long_exposure import add
        add(plan)
    # The menu framework keeps the place the shutter sound had in FEATURE_ORDER.
    from .features.card.ready_banner import REPAINT_TRIGGER
    if card and ready_banner and REPAINT_TRIGGER and 'portrait-ui' in features:
        # The banner's repaint trigger runs from portrait-ui's UI hooks (built later).
        from .features.card.ready_banner import prepare_hooks
        plan.banner_hooks = prepare_hooks(plan)
    if card and ready_banner and banner_debug:
        # Diagnostic readout of the ready banner in the status page's version row.
        from .features.card.ready_banner import prepare_debug
        plan.banner_debug = prepare_debug(plan)
    if module_host or 'shutter-sound' in features or any(name in features for name in MENU_FEATURES):
        from .features.menu.framework import add
        add(plan, sound='shutter-sound' in features, menu_tab=menu_tab, modules=module_host, bitrate='bitrate-slowmo-uncap' in features,
            zebra='photo-zebra' in features, stamp='date-stamp' in features, portrait='portrait-ui' in features,
            nef='nef-menu' in features,
            gyro='gyro-evr' in features, trk3d='3d-tracking' in features, afcol='af-frame-colour' in features,
            aspect='aspect-profiles' in features, aspect_trace=aspect_trace, aspect_border=aspect_border,
            aspect_border_diagnostic=aspect_border_diagnostic,aspect_border_lease=aspect_border_lease,
            aspect_mask=aspect_mask, aspect_mask_trial=aspect_mask_trial,
            build_tag=build_tag, resident_id=resident_id, feature_count=len(features))
    if 'mtp-fast' in features:
        from .features.connect.mtp_fast import add
        add(plan)
    if 'video-rot' in features:
        from .features.video.rotation import add
        add(plan)
    if 'bitrate-slowmo-uncap' in features:
        from .features.video.bitrate import add
        add(plan)
    if 'photo-zebra' in features:
        from .features.photo.zebra import add
        add(plan)
    if 'wifi-5ghz' in features:
        from .features.connect.wifi_5ghz import add
        add(plan)
    if 'date-stamp' in features:
        from .features.photo.date_stamp import add
        add(plan)
    if 'portrait-ui' in features:
        from .features.portrait.layout import add
        add(plan)
    for name in NEF_FIXED_FEATURES:
        if name in features:
            from .features.nef.compression import add
            add(plan, name)
    if 'nef-menu' in features:
        from .features.nef.menu import add
        add(plan)
    if 'gyro-evr' in features:
        from .features.gyro.evr import add
        add(plan)
    if '3d-tracking' in features:
        from .features.tracking3d.tracking import add
        add(plan)
    if 'af-frame-colour' in features:
        from .features.photo.af_frame_colour import add
        add(plan)
    if 'focal-length' in features:
        from .features.photo.focal_length import add
        add(plan)
    if 'battery-percent' in features:
        from .features.photo.battery_percent import add
        add(plan)
    if (card and ready_banner) or ready_banner == 'qemu-test':
        # Card builds: "Wonder Lantern ready · N features" on the live view after loading.
        from .features.card.ready_banner import add
        add(plan, len(features), qemu_test=not card, debug=getattr(plan, 'banner_debug', None))
    if ptp_read:
        # Research only: read-only USB PTP 0x9FF1 ReadMemory for live readouts.
        from .features.research.ptp_read import add
        add(plan)
    if evr_hook:
        from .features.modules.evr_hook import add
        add(plan)
    if imu_hook:
        from .features.modules.imu_hook import add
        add(plan)
    if face_hook:
        from .features.modules.face_hook import add
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
