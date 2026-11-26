"""Interactive USB bulk serial terminal for Nikon Z30 Linux (g_serial.use_acm=0).

Uses Windows' built-in winusb.dll + setupapi.dll via ctypes (no pip packages
or external DLLs needed). Works once USB\\VID_0525&PID_A4A6 is bound to the
built-in Windows "WinUSB Device" driver (via Device Manager or Zadig).

Usage:
  python tools/z30_usb_term.py                 # interactive terminal (Ctrl+] to quit)
  python tools/z30_usb_term.py --cmd "uname -a" # run a command and print output
  python tools/z30_usb_term.py --push LOCAL REMOTE  # verified file push (auto remounts /mnt/sd rw->ro)
"""
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import msvcrt
from pathlib import Path
import sys
import threading
import time

setupapi = ctypes.windll.setupapi
kernel32 = ctypes.windll.kernel32
winusb = ctypes.windll.winusb

DIGCF_PRESENT = 0x02
DIGCF_DEVICEINTERFACE = 0x10
DIGCF_ALLCLASSES = 0x04

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 1
FILE_SHARE_WRITE = 2
OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x80
FILE_FLAG_OVERLAPPED = 0x40000000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

PIPE_TRANSFER_TIMEOUT = 0x03
USB_ENDPOINT_TYPE_BULK = 2


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def parse_guid(s):
    s = s.strip().strip("{}")
    parts = s.split("-")
    d1 = int(parts[0], 16)
    d2 = int(parts[1], 16)
    d3 = int(parts[2], 16)
    d4_bytes = bytes.fromhex(parts[3] + parts[4])
    return GUID(d1, d2, d3, (ctypes.c_ubyte * 8)(*d4_bytes))


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("InterfaceClassGuid", GUID),
        ("Flags", wintypes.DWORD),
        ("Reserved", ctypes.POINTER(ctypes.c_ulong)),
    ]


class SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("ClassGuid", GUID),
        ("DevInst", wintypes.DWORD),
        ("Reserved", ctypes.POINTER(ctypes.c_ulong)),
    ]


class USB_INTERFACE_DESCRIPTOR(ctypes.Structure):
    _fields_ = [
        ("bLength", ctypes.c_ubyte),
        ("bDescriptorType", ctypes.c_ubyte),
        ("bInterfaceNumber", ctypes.c_ubyte),
        ("bAlternateSetting", ctypes.c_ubyte),
        ("bNumEndpoints", ctypes.c_ubyte),
        ("bInterfaceClass", ctypes.c_ubyte),
        ("bInterfaceSubClass", ctypes.c_ubyte),
        ("bInterfaceProtocol", ctypes.c_ubyte),
        ("iInterface", ctypes.c_ubyte),
    ]


class WINUSB_PIPE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PipeType", ctypes.c_int),
        ("PipeId", ctypes.c_ubyte),
        ("MaximumPacketSize", ctypes.c_ushort),
        ("Interval", ctypes.c_ubyte),
    ]


setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD]
setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(GUID), wintypes.DWORD, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)
]
setupapi.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA), ctypes.c_void_p,
    wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(SP_DEVINFO_DATA)
]
setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]
setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL

kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
    wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p
]
kernel32.CreateFileW.restype = ctypes.c_void_p
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
kernel32.CloseHandle.restype = wintypes.BOOL

winusb.WinUsb_Initialize.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
winusb.WinUsb_Initialize.restype = wintypes.BOOL
winusb.WinUsb_Free.argtypes = [ctypes.c_void_p]
winusb.WinUsb_Free.restype = wintypes.BOOL
winusb.WinUsb_QueryInterfaceSettings.argtypes = [ctypes.c_void_p, ctypes.c_ubyte, ctypes.POINTER(USB_INTERFACE_DESCRIPTOR)]
winusb.WinUsb_QueryInterfaceSettings.restype = wintypes.BOOL
winusb.WinUsb_QueryPipe.argtypes = [ctypes.c_void_p, ctypes.c_ubyte, ctypes.c_ubyte, ctypes.POINTER(WINUSB_PIPE_INFORMATION)]
winusb.WinUsb_QueryPipe.restype = wintypes.BOOL
winusb.WinUsb_SetPipePolicy.argtypes = [ctypes.c_void_p, ctypes.c_ubyte, wintypes.ULONG, wintypes.ULONG, ctypes.c_void_p]
winusb.WinUsb_SetPipePolicy.restype = wintypes.BOOL
winusb.WinUsb_ReadPipe.argtypes = [
    ctypes.c_void_p, ctypes.c_ubyte, ctypes.c_void_p, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG), ctypes.c_void_p
]
winusb.WinUsb_ReadPipe.restype = wintypes.BOOL
winusb.WinUsb_WritePipe.argtypes = [
    ctypes.c_void_p, ctypes.c_ubyte, ctypes.c_void_p, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG), ctypes.c_void_p
]
winusb.WinUsb_WritePipe.restype = wintypes.BOOL


def find_device_paths(vid_pid="vid_0525&pid_a4a6"):
    """Find matching USB/WinUSB device interface paths."""
    guids = [
        "{A5DCBF10-6530-11D2-901F-00C04FB951ED}",  # GUID_DEVINTERFACE_USB_DEVICE
        "{DEE824EF-729B-4A0E-9C14-B7117D33A817}",  # WinUSB default
        "{88BAE032-5A81-49F0-BC3D-A4FF138216D6}",  # USBDevice class
    ]
    # Also check registry DeviceInterfaceGUIDs for any matching USB instance.
    try:
        import winreg
        base = r"SYSTEM\CurrentControlSet\Enum\USB\VID_0525&PID_A4A6"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as k:
            for i in range(16):
                try:
                    inst = winreg.EnumKey(k, i)
                except OSError:
                    break
                try:
                    with winreg.OpenKey(k, inst + r"\Device Parameters") as dp:
                        for vname in ("DeviceInterfaceGUIDs", "DeviceInterfaceGUID"):
                            try:
                                val, _ = winreg.QueryValueEx(dp, vname)
                                if isinstance(val, list):
                                    guids = val + guids
                                elif isinstance(val, str):
                                    guids.insert(0, val)
                            except OSError:
                                pass
                except OSError:
                    pass
    except Exception:
        pass

    found = []
    seen = set()
    for gstr in guids:
        if gstr.upper() in seen:
            continue
        seen.add(gstr.upper())
        try:
            g = parse_guid(gstr)
        except Exception:
            continue
        hinfo = setupapi.SetupDiGetClassDevsW(ctypes.byref(g), None, None, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
        if hinfo == INVALID_HANDLE_VALUE:
            continue
        try:
            idx = 0
            while True:
                idata = SP_DEVICE_INTERFACE_DATA()
                idata.cbSize = ctypes.sizeof(idata)
                if not setupapi.SetupDiEnumDeviceInterfaces(hinfo, None, ctypes.byref(g), idx, ctypes.byref(idata)):
                    break
                idx += 1
                req = wintypes.DWORD(0)
                setupapi.SetupDiGetDeviceInterfaceDetailW(hinfo, ctypes.byref(idata), None, 0, ctypes.byref(req), None)
                if req.value == 0:
                    continue
                buf = ctypes.create_string_buffer(req.value)
                cb = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
                ctypes.memmove(buf, ctypes.byref(wintypes.DWORD(cb)), 4)
                if setupapi.SetupDiGetDeviceInterfaceDetailW(hinfo, ctypes.byref(idata), buf, req.value, None, None):
                    path = ctypes.wstring_at(ctypes.addressof(buf) + 4)
                    if vid_pid.lower() in path.lower() and path not in found:
                        found.append(path)
        finally:
            setupapi.SetupDiDestroyDeviceInfoList(hinfo)
    return found


class WinUSBSerial:
    def __init__(self, vid_pid="vid_0525&pid_a4a6"):
        paths = find_device_paths(vid_pid)
        if not paths:
            raise RuntimeError(
                f"No WinUSB device matching {vid_pid} found.\n"
                "In Device Manager, right-click 'Gadget Serial v2.4' -> Update driver ->\n"
                "Browse my computer -> Let me pick -> Universal Serial Bus devices -> WinUSB Device."
            )
        self.hdev = INVALID_HANDLE_VALUE
        self.husb = ctypes.c_void_p()
        last_err = None
        for p in paths:
            h = kernel32.CreateFileW(
                p, GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE,
                None, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OVERLAPPED, None
            )
            if h == INVALID_HANDLE_VALUE:
                last_err = ctypes.GetLastError()
                continue
            husb = ctypes.c_void_p()
            if winusb.WinUsb_Initialize(h, ctypes.byref(husb)):
                self.hdev = h
                self.husb = husb
                self.path = p
                break
            last_err = ctypes.GetLastError()
            kernel32.CloseHandle(h)
        if self.hdev == INVALID_HANDLE_VALUE:
            raise RuntimeError(f"Found {paths} but WinUsb_Initialize failed (Win32 error {last_err}).")

        iface = USB_INTERFACE_DESCRIPTOR()
        if not winusb.WinUsb_QueryInterfaceSettings(self.husb, 0, ctypes.byref(iface)):
            raise RuntimeError("WinUsb_QueryInterfaceSettings failed")

        self.ep_in = None
        self.ep_out = None
        for i in range(iface.bNumEndpoints):
            pipe = WINUSB_PIPE_INFORMATION()
            if winusb.WinUsb_QueryPipe(self.husb, 0, i, ctypes.byref(pipe)):
                if pipe.PipeType == USB_ENDPOINT_TYPE_BULK:
                    if pipe.PipeId & 0x80:
                        self.ep_in = pipe.PipeId
                    else:
                        self.ep_out = pipe.PipeId
        if self.ep_in is None or self.ep_out is None:
            raise RuntimeError(f"Could not find bulk IN/OUT pipes (in={self.ep_in}, out={self.ep_out})")

        timeout = wintypes.ULONG(100)
        winusb.WinUsb_SetPipePolicy(self.husb, self.ep_in, PIPE_TRANSFER_TIMEOUT, 4, ctypes.byref(timeout))
        winusb.WinUsb_SetPipePolicy(self.husb, self.ep_out, PIPE_TRANSFER_TIMEOUT, 4, ctypes.byref(timeout))

    def read(self, size=4096):
        buf = ctypes.create_string_buffer(size)
        got = wintypes.ULONG(0)
        ok = winusb.WinUsb_ReadPipe(self.husb, self.ep_in, buf, size, ctypes.byref(got), None)
        if not ok:
            err = ctypes.GetLastError()
            if err == 121:  # ERROR_SEM_TIMEOUT
                return b""
            raise OSError(f"WinUsb_ReadPipe failed: {err}")
        return buf.raw[:got.value]

    def write(self, data: bytes):
        if not data:
            return 0
        sent = wintypes.ULONG(0)
        buf = ctypes.create_string_buffer(data, len(data))
        ok = winusb.WinUsb_WritePipe(self.husb, self.ep_out, buf, len(data), ctypes.byref(sent), None)
        if not ok:
            raise OSError(f"WinUsb_WritePipe failed: {ctypes.GetLastError()}")
        return sent.value

    def set_write_timeout(self, ms: int):
        timeout = wintypes.ULONG(ms)
        winusb.WinUsb_SetPipePolicy(self.husb, self.ep_out, PIPE_TRANSFER_TIMEOUT, 4, ctypes.byref(timeout))

    def close(self):
        if self.husb:
            winusb.WinUsb_Free(self.husb)
            self.husb = None
        if self.hdev != INVALID_HANDLE_VALUE:
            kernel32.CloseHandle(self.hdev)
            self.hdev = INVALID_HANDLE_VALUE


def _drain(dev: WinUSBSerial, idle_s=0.15):
    deadline = time.time() + idle_s
    while time.time() < deadline:
        if dev.read():
            deadline = time.time() + idle_s


def _read_until(dev: WinUSBSerial, marker: bytes, timeout_s=15.0) -> bytes:
    buf = bytearray()
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        chunk = dev.read()
        if chunk:
            buf += chunk
            if marker in buf:
                return bytes(buf)
    raise TimeoutError(f"Timed out waiting for {marker!r}; got tail: {bytes(buf[-200:])!r}")


def push_file(dev: WinUSBSerial, local_path: str, remote_path: str):
    data = Path(local_path).read_bytes()
    size = len(data)
    local_sha = hashlib.sha256(data).hexdigest()
    print(f"Pushing {local_path} ({size} bytes, sha256={local_sha[:12]}...) -> {remote_path}")

    # Sync prompt
    dev.write(b"\x03\n")
    _drain(dev, 0.2)

    # Switch ttyGS0 to raw -echo, signal ready, read exact byte count into /tmp/push.bin, restore sane
    cmd = (
        f"stty raw -echo; printf 'PUSH''_READY\\n'; "
        f"head -c {size} > /tmp/push.bin; "
        f"stty sane; "
        f"printf 'PUSH''_DONE:%s\\n' \"$(sha256sum /tmp/push.bin | awk '{{print $1}}')\"\n"
    )
    dev.write(cmd.encode())
    _read_until(dev, b"PUSH_READY\n", timeout_s=5.0)

    # Stream raw binary payload over USB bulk OUT
    dev.set_write_timeout(5000)
    t0 = time.time()
    sent = 0
    chunk_size = 4096
    while sent < size:
        n = dev.write(data[sent:sent + chunk_size])
        sent += n
        if sent == size or (sent % (256 * 1024) == 0):
            dt = max(time.time() - t0, 1e-3)
            print(f"  {sent}/{size} bytes ({sent / dt / 1024:.0f} KB/s)", flush=True)
    dev.set_write_timeout(100)

    resp = _read_until(dev, b"PUSH_DONE:", timeout_s=15.0)
    # Collect the full hash line
    tail = bytearray(resp[resp.rfind(b"PUSH_DONE:"):])
    deadline = time.time() + 5.0
    while b"\n" not in tail and time.time() < deadline:
        tail += dev.read()
    remote_sha = tail.split(b"\n", 1)[0].split(b":", 1)[1].strip().decode("ascii", errors="replace")
    if remote_sha != local_sha:
        raise RuntimeError(f"Transfer SHA-256 mismatch: local={local_sha} remote={remote_sha} (SD untouched)")
    print(f"  /tmp/push.bin verified (sha256={remote_sha[:12]}...)")

    # Commit to destination (safely remounting /mnt/sd rw -> ro)
    if remote_path.startswith("/mnt/sd/"):
        commit = (
            f"mount -o remount,rw /mnt/sd && "
            f"cp /tmp/push.bin '{remote_path}' && rm -f /tmp/push.bin && sync && "
            f"S=$(sha256sum '{remote_path}' | awk '{{print $1}}') && "
            f"mount -o remount,ro /mnt/sd && "
            f"printf 'COMMIT''_OK:%s\\n' \"$S\" || printf 'COMMIT''_FAIL\\n'\n"
        )
    else:
        commit = (
            f"mv /tmp/push.bin '{remote_path}' && sync && "
            f"S=$(sha256sum '{remote_path}' | awk '{{print $1}}') && "
            f"printf 'COMMIT''_OK:%s\\n' \"$S\" || printf 'COMMIT''_FAIL\\n'\n"
        )
    _drain(dev, 0.1)
    dev.write(commit.encode())
    cresp = _read_until(dev, b"COMMIT_", timeout_s=20.0)
    ctail = bytearray(cresp[cresp.rfind(b"COMMIT_"):])
    deadline = time.time() + 5.0
    while b"\n" not in ctail and time.time() < deadline:
        ctail += dev.read()
    line = ctail.split(b"\n", 1)[0].strip().decode("ascii", errors="replace")
    if not line.startswith("COMMIT_OK:") or line.split(":", 1)[1].strip() != local_sha:
        raise RuntimeError(f"Commit failed or SHA-256 mismatch: {line}")
    print(f"  Committed & verified on camera: {remote_path} (SD remounted ro)")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--vid-pid", default="vid_0525&pid_a4a6")
    ap.add_argument("--cmd", help="Run a single command and print output")
    ap.add_argument("--push", nargs=2, action="append", metavar=("LOCAL", "REMOTE"),
                    help="Push LOCAL file to REMOTE path with SHA-256 verification (repeatable)")
    ap.add_argument("--reset", action="store_true", help="Send quote/EOF reset sequence before --cmd")
    ap.add_argument("--wait", type=float, default=1.0, help="Seconds to collect output for --cmd")
    args = ap.parse_args()

    dev = WinUSBSerial(args.vid_pid)
    try:
        if args.push:
            for local_p, remote_p in args.push:
                push_file(dev, local_p, remote_p)
            return
        if args.cmd:
            if args.reset:
                for seq in (b'"\r', b"'\r", b"done\r", b"\x03\r", b"\x04\r"):
                    dev.write(seq)
                    time.sleep(0.12)
                    dev.read()
                time.sleep(0.6)
            # Clear any partial line, drain banner, send command, collect output
            dev.write(b"\x03\n")
            time.sleep(0.2)
            while dev.read():
                pass
            dev.write((args.cmd + "\n").encode())
            deadline = time.time() + args.wait
            out = bytearray()
            while time.time() < deadline:
                chunk = dev.read()
                if chunk:
                    out += chunk
            sys.stdout.buffer.write(out)
            sys.stdout.buffer.flush()
            return

        print(f"Connected to Z30 USB Serial ({dev.path}, IN=0x{dev.ep_in:02x}, OUT=0x{dev.ep_out:02x})")
        print("Press Enter for shell prompt, Ctrl+] to exit.\n")
        stop = threading.Event()

        def reader():
            while not stop.is_set():
                try:
                    data = dev.read()
                    if data:
                        sys.stdout.buffer.write(data)
                        sys.stdout.buffer.flush()
                except OSError as e:
                    if not stop.is_set():
                        print(f"\n[disconnected: {e}]")
                        stop.set()
                    break

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        dev.write(b"\r")

        while not stop.is_set():
            if msvcrt.kbhit():
                ch = msvcrt.getch()
                if ch == b"\x1d":  # Ctrl+]
                    break
                if ch in (b"\x00", b"\xe0") and msvcrt.kbhit():
                    code = msvcrt.getch()
                    arrows = {b"H": b"\x1b[A", b"P": b"\x1b[B", b"M": b"\x1b[C", b"K": b"\x1b[D"}
                    dev.write(arrows.get(code, b""))
                else:
                    dev.write(ch)
            else:
                time.sleep(0.01)
        stop.set()
        t.join(timeout=0.5)
    finally:
        dev.close()


if __name__ == "__main__":
    main()
