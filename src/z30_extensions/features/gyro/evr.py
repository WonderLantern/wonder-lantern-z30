"""Gyro e-VR: in-body gyroscope stabilization for movie mode, built in.

What it installs (all dormant at boot, every cell is zero in stock data):
  * evr_hook (request=True): the e-VR result hooks on both VrMx paths. With no
    module running, the path-1 hook asks once for the built-in copy.
  * evr_builtin: the module code (modules/gyro_evr, EVR_PRODUCT build), its
    autostart routine and the get_property shim for the menu choice.
  * imu_hook (autostart): after a good stock accelerometer read with the request
    pending, starts the built-in copy in the IMU task (the task that owns I2C).
The Gyro e-VR menu page itself is features/gyro/ui.py (in the extension menu).

Behaviour on the camera (menu: Standard / Enhanced / Native crop / OFF)
  * Electronic VR on: gyro stabilization with the chosen crop: yaw/pitch/roll,
    stock IMU period 10 ms, lens-VR cooperation (lens VR on -> the lens keeps
    yaw/pitch, gyro keeps roll).
  * Electronic VR off: the module stays idle (stock registers and period).
  * OFF: no gyro reads, IMU registers and period back to stock, stock e-VR.
  * Escape hatch: a camera that boots in photo mode never starts it, so Setup -> Firmware version stays reachable.
  * Power off resets every hook and the IMU period.
"""
from . import builtin as evr_builtin
from ..modules import evr_hook, imu_hook
from .payload import CODE, SHA256, SIZE


def layout():
    return evr_builtin.layout(len(CODE))


def add(plan):
    if len(CODE) != SIZE:
        raise ValueError('gyro e-VR payload is truncated')
    evr_hook.add(plan, request=True)
    started = evr_builtin.add(plan, CODE, choice=getattr(plan, 'gyro_choice', None))
    imu_hook.add(plan, autostart=started['autostart'])
    plan.features['gyro_evr'] = dict(payload_sha256=SHA256, payload_bytes=SIZE,
                                     source='modules/gyro_evr/gyro_evr.c (-DEVR_PRODUCT)')
    return started
