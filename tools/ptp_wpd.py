"""Send raw PTP operations to a camera through Windows Portable Devices (WPD).

Uses the MTP extension commands of the inbox WPD driver, so no driver change
is needed. Only operations with no data phase or a device-to-host data phase
are offered: this tool never sends data to the camera.

Usage (Windows, camera connected over USB):
  python tools/ptp_wpd.py --list
  python tools/ptp_wpd.py --op 0x1001                 # GetDeviceInfo, prints the dataset
  python tools/ptp_wpd.py --op 0x9FF1 --params 0x10020000 64
"""
import argparse
import ctypes
import sys
from ctypes import c_ubyte, c_ulong, c_ulonglong, c_ushort, POINTER, byref, Structure, Union, c_void_p, c_wchar_p

import comtypes
import comtypes.client
from comtypes import GUID

comtypes.client.GetModule('portabledeviceapi.dll')
comtypes.client.GetModule('portabledevicetypes.dll')
from comtypes.gen import PortableDeviceApiLib as api  # noqa: E402
from comtypes.gen import PortableDeviceTypesLib as types  # noqa: E402

MTP_EXT = GUID('{4D545058-1A2E-4106-A357-771E0819FC56}')
COMMON = GUID('{F0422A9C-5DC8-4440-B5BD-5DF28835658A}')
CMD_WITHOUT_DATA, CMD_WITH_DATA_TO_READ, CMD_READ_DATA, CMD_END_DATA = 12, 13, 15, 17
P_OPCODE, P_PARAMS, P_RESPONSE_CODE, P_RESPONSE_PARAMS = 1001, 1002, 1003, 1004
P_CONTEXT, P_TOTAL, P_TO_READ, P_READ, P_DATA, P_OPTIMAL = 1006, 1007, 1008, 1009, 1012, 1013
P_CATEGORY, P_COMMAND_ID, P_HRESULT = 1001, 1002, 1003
CLIENT_INFO = GUID('{204D9F0C-2292-4080-9F42-40664E70F859}')


def get(values, method, fmtid, pid, *args):
    """Call an IPortableDeviceValues method with the property key type its typelib expects."""
    for module in (types, api):
        value = module._tagpropertykey()
        value.fmtid, value.pid = fmtid, pid
        try:
            return getattr(values, method)(byref(value), *args)
        except ctypes.ArgumentError:
            continue
    raise TypeError(f'No property key type accepted by {method}')


class Camera:
    def __init__(self, match='Z 30'):
        manager = comtypes.client.CreateObject(api.PortableDeviceManager, interface=api.IPortableDeviceManager)
        count = manager.GetDevices(POINTER(c_wchar_p)(), 0)[1]
        ids = (c_wchar_p * count)()
        manager.GetDevices(ctypes.cast(ids, POINTER(c_wchar_p)), count)
        self.device_id = None
        for device_id in ids[:count]:
            length = manager.GetDeviceFriendlyName(device_id, POINTER(c_ushort)(), 0)[1]
            name = ctypes.create_unicode_buffer(length)
            manager.GetDeviceFriendlyName(device_id, ctypes.cast(name, POINTER(c_ushort)), length)
            if match in name.value:
                self.device_id, self.name = device_id, name.value
                break
        if self.device_id is None:
            raise SystemExit(f'No WPD device named like {match!r}')
        self.device = comtypes.client.CreateObject(api.PortableDevice, interface=api.IPortableDevice)
        client = comtypes.client.CreateObject(types.PortableDeviceValues, interface=types.IPortableDeviceValues)
        self.device.Open(self.device_id, client)

    def values(self):
        return comtypes.client.CreateObject(types.PortableDeviceValues, interface=types.IPortableDeviceValues)

    def command(self, command_id, opcode=None, params=(), extra=None):
        values = self.values()
        get(values, 'SetGuidValue', COMMON, P_CATEGORY, MTP_EXT)
        get(values, 'SetUnsignedIntegerValue', COMMON, P_COMMAND_ID, command_id)
        if opcode is not None:
            get(values, 'SetUnsignedIntegerValue', MTP_EXT, P_OPCODE, opcode)
            collection = comtypes.client.CreateObject(types.PortableDevicePropVariantCollection,
                                                      interface=types.IPortableDevicePropVariantCollection)
            for param in params:
                variant = types.tag_inner_PROPVARIANT()
                variant.vt = 19  # VT_UI4
                getattr(variant, variant._fields_[-1][0]).ulVal = param & 0xFFFFFFFF
                collection.Add(variant)
            get(values, 'SetIPortableDevicePropVariantCollectionValue', MTP_EXT, P_PARAMS, collection)
        if extra:
            extra(values)
        result = self.device.SendCommand(0, values)
        status = get(result, 'GetErrorValue', COMMON, P_HRESULT)
        if status:
            raise OSError(f'WPD command {command_id} failed: HRESULT {status & 0xFFFFFFFF:#010x}')
        return result

    @staticmethod
    def response(result):
        code = get(result, 'GetUnsignedIntegerValue', MTP_EXT, P_RESPONSE_CODE)
        return code

    def run(self, opcode, params=(), read=True):
        """Return (response code, data bytes or None)."""
        if not read:
            result = self.command(CMD_WITHOUT_DATA, opcode, params)
            return self.response(result), None
        result = self.command(CMD_WITH_DATA_TO_READ, opcode, params)
        context = get(result, 'GetStringValue', MTP_EXT, P_CONTEXT)
        total = get(result, 'GetUnsignedLargeIntegerValue', MTP_EXT, P_TOTAL)
        data = bytearray()
        while len(data) < total:
            want = min(total - len(data), 0x40000)
            def setup(values, want=want):
                get(values, 'SetStringValue', MTP_EXT, P_CONTEXT, context)
                get(values, 'SetUnsignedIntegerValue', MTP_EXT, P_TO_READ, want)
                get(values, 'SetBufferValue', MTP_EXT, P_DATA, (c_ubyte * want)(), want)
            chunk = self.command(CMD_READ_DATA, extra=setup)
            got = get(chunk, 'GetUnsignedIntegerValue', MTP_EXT, P_READ)
            buffer, size = get(chunk, 'GetBufferValue', MTP_EXT, P_DATA)
            data.extend(bytes(buffer[:got]))
            ctypes.windll.ole32.CoTaskMemFree(buffer)
            if got == 0:
                break
        def finish(values):
            get(values, 'SetStringValue', MTP_EXT, P_CONTEXT, context)
        end = self.command(CMD_END_DATA, extra=finish)
        return self.response(end), bytes(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--op', type=lambda x: int(x, 0))
    parser.add_argument('--params', type=lambda x: int(x, 0), nargs='*', default=[])
    parser.add_argument('--no-data', action='store_true')
    parser.add_argument('--out')
    args = parser.parse_args()
    camera = Camera()
    print(f'Opened {camera.name}')
    if args.op is None:
        return
    code, data = camera.run(args.op, args.params, read=not args.no_data)
    print(f'response {code:#06x}, data {len(data) if data is not None else "-"} bytes')
    if data:
        if args.out:
            open(args.out, 'wb').write(data)
        print(data[:256].hex(' '))


if __name__ == '__main__':
    main()
