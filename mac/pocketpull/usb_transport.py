"""AOA discovery, opt-in mode switch, and bulk transport via libusb."""
import math
from pathlib import Path
import sys
import time

AOA_VENDOR = 0x18D1
AOA_PRODUCTS = (0x2D00, 0x2D01, 0x2D04, 0x2D05)
# Legacy manufacturer/model keep existing Android installations discoverable.
IDENTITY = ("PocketPull", "PocketPull", "Pocket Dimension USB file transfer", "1", "", "PocketPull-Host")


def modules():
    try:
        import usb.core
        import usb.util
        return usb.core, usb.util
    except ImportError as exc:
        raise RuntimeError("Install the CLI first: python -m pip install ./mac") from exc


def devices():
    core, _ = modules()
    try:
        if getattr(sys, "frozen", False):
            from usb.backend import libusb1
            library = Path(sys._MEIPASS) / "libusb-1.0.0.dylib"
            backend = libusb1.get_backend(find_library=lambda _: str(library))
            if backend is None:
                raise RuntimeError("Could not load bundled libusb")
            return list(core.find(find_all=True, backend=backend))
        return list(core.find(find_all=True))
    except core.NoBackendError as exc:
        raise RuntimeError("libusb is missing. On macOS run: brew install libusb") from exc


def is_accessory(device):
    return device.idVendor == AOA_VENDOR and device.idProduct in AOA_PRODUCTS


def choose(candidates):
    if not candidates:
        raise RuntimeError("No matching phone. Unlock it, use a data cable, and select File transfer in USB settings. Run pocket-dimension devices --all for unrecognized phones; override with --vid and --pid.")
    if len(candidates) != 1:
        raise RuntimeError("Multiple matching devices; select one with --bus and --address.")
    return candidates[0]


def switch(device, timeout=15):
    """Probe only the explicitly selected VID/PID, then wait on its physical port."""
    core, util = modules()
    bus, ports = device.bus, device.port_numbers
    existing = {(d.bus, d.address) for d in devices() if is_accessory(d)}
    try:
        version = bytes(device.ctrl_transfer(0xC0, 51, 0, 0, 2, timeout=2000))
        if len(version) != 2 or int.from_bytes(version, "little") not in (1, 2):
            raise RuntimeError("Phone did not report AOA protocol 1 or 2")
        for index, value in enumerate(IDENTITY):
            device.ctrl_transfer(0x40, 52, 0, index, value.encode() + b"\0", timeout=2000)
        try:
            device.ctrl_transfer(0x40, 53, 0, 0, b"", timeout=2000)
        except core.USBError as exc:
            # Some phones disconnect before the control request completes.
            if exc.errno != 19:
                raise
    finally:
        util.dispose_resources(device)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        candidates = [d for d in devices() if is_accessory(d)
                      and ((d.bus == bus and d.port_numbers == ports) if ports
                           else (d.bus, d.address) not in existing)]
        if candidates:
            return choose(candidates)
        time.sleep(0.25)
    raise RuntimeError("AOA switch timed out. Unlock the phone, approve the accessory, and check device support.")


class USBTransport:
    def __init__(self, device, timeout=30000):
        core, self.util = modules()
        self.device = device
        self.timeout = timeout
        self.interface = None
        try:
            try:
                config = device.get_active_configuration()
            except core.USBError:
                device.set_configuration()
                config = device.get_active_configuration()
            # AOA data is interface 0. Never claim the separate ADB interface.
            interface = config[(0, 0)]
            endpoints = [e for e in interface if self.util.endpoint_type(e.bmAttributes) == 2]
            self.input = next(e for e in endpoints if self.util.endpoint_direction(e.bEndpointAddress) == 0x80)
            self.output = next(e for e in endpoints if self.util.endpoint_direction(e.bEndpointAddress) == 0)
            self.util.claim_interface(device, 0)
            self.interface = 0
        except Exception:
            self.close()
            raise

    def read(self):
        # A successful zero-byte bulk transfer is a USB packet boundary, not EOF.
        # In particular, a ZLP may follow an Android write of exactly 16384 bytes.
        # Keep one deadline so repeated ZLPs cannot extend the inactivity timeout.
        deadline = time.monotonic() + self.timeout / 1000
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("USB read timed out waiting for data")
            data = bytes(self.input.read(16384, timeout=max(1, math.ceil(remaining * 1000))))
            if data:
                return data

    def write(self, data):
        # One complete frame per write, <=16384 bytes.
        if self.output.write(data, timeout=self.timeout) != len(data):
            raise RuntimeError("Incomplete USB write; reconnect and retry")

    def close(self):
        try:
            if self.interface is not None:
                self.util.release_interface(self.device, self.interface)
                self.interface = None
        finally:
            self.util.dispose_resources(self.device)
