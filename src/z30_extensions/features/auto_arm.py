"""Research-only: the auto_arm module built in (tracking box up by default).

The module (modules/auto_arm, built with -DREMAP -DDIRECT -DBUILTIN) is copied out of the
EG image and started by evr_builtin's autostart in the IMU task on the first good
accelerometer read after power-on (imu_hook boot=True). It reads the photo
shooting menu choice (auto_arm_ui, Enable / Disable, Enable without state) through
the get_property shim with KEY about once a second, and only acts on the photo
shooting screen in Auto-area AF: it calls the stock enter-tracking-mode function
0x1045A1AE there (camera-tested as an SD module, 2026-11-17). Shares the IMU hook
and the built-in cell with gyro-evr, so the two never build together.
"""
from . import evr_builtin, imu_hook

KEY = 0x41524D31   # 'ARM1': private property key for the menu choice


def add(plan, code):
    choice = getattr(plan, 'autoarm_choice', None)
    if choice is None:
        raise ValueError('auto-arm needs its photo shooting menu row (shutter-sound autoarm=True)')
    started = evr_builtin.add(
        plan, code, choice=choice, key=KEY, gate=False, runs=1, name='auto_arm_builtin',
        trigger='IMU task, first good accelerometer read after power-on',
        escape='Acts only on the photo shooting screen in Auto-area AF; menus stay reachable; '
               'photo shooting menu > Auto tracking box > Disable turns it off within a second')
    imu_hook.add(plan, autostart=started['autostart'], boot=True)
    plan.features['auto_arm'] = dict(code_bytes=len(code), property_key=hex(KEY),
                                     source='modules/auto_arm (-DREMAP -DDIRECT -DBUILTIN)')
    return started
