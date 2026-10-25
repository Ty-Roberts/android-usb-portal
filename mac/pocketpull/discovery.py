"""Conservative Android USB identities; no probing unrelated USB peripherals.

VID/PID facts cross-checked against libmtp's device registry:
https://github.com/libmtp/libmtp/blob/master/src/music-players.h
An identity match is a discovery hint, not proof of AOA compatibility.
"""
from .usb_transport import is_accessory

KNOWN_PHONES = {
    (0x04E8, 0x6860): "Samsung Galaxy (MTP)",
    (0x04E8, 0x685C): "Samsung Galaxy (MTP + ADB)",
    (0x18D1, 0x4EE1): "Google Nexus / Pixel (MTP)",
    (0x18D1, 0x4EE2): "Google Nexus / Pixel (MTP + ADB)",
    (0x18D1, 0x4EE5): "Google Nexus / Pixel (PTP)",
    (0x18D1, 0x4EE6): "Google Nexus / Pixel (PTP + ADB)",
    (0x18D1, 0x7169): "OnePlus 6T (MTP)",
    (0x2717, 0xFF40): "Xiaomi (MTP)",
    (0x2717, 0xFF48): "Xiaomi (MTP)",
    (0x2717, 0xFF60): "Xiaomi / Redmi (MTP)",
    (0x2717, 0xFF68): "Xiaomi / Redmi (MTP)",
}


def label(device):
    if is_accessory(device):
        return "Android accessory (AOA)"
    return KNOWN_PHONES.get((device.idVendor, device.idProduct))


def describe(device):
    name = label(device) or "Unrecognized USB device"
    return (f"{device.idVendor:04x}:{device.idProduct:04x}  "
            f"bus={device.bus} address={device.address}  {name}")


def candidates(found, vid=None, pid=None, bus=None, address=None):
    return [device for device in found
            if ((label(device) is not None) if vid is None
                else (device.idVendor == vid and device.idProduct == pid))
            and (bus is None or device.bus == bus)
            and (address is None or device.address == address)]


def clean_text(value):
    if not isinstance(value, str): return None
    value = ''.join(c for c in value if c.isprintable()).strip()[:128]
    return value or None


def descriptor(device, field):
    try: return clean_text(getattr(device, field, None))
    except Exception: return None  # Some phones deny USB string-descriptor reads.


def device_record(device, identity=None):
    accessory = is_accessory(device)
    # AOA string descriptors describe the host accessory, not the Android phone.
    # In particular, our AOA serial is the same host-supplied string on all phones.
    identity = identity or {}
    model = clean_text(identity.get('model'))
    manufacturer = clean_text(identity.get('manufacturer'))
    if manufacturer and manufacturer.islower(): manufacturer = manufacturer.capitalize()
    name = label(device) or 'Unrecognized USB device'
    if model:
        name = model if not manufacturer or model.casefold().startswith(manufacturer.casefold()) else manufacturer + ' ' + model
    elif not accessory:
        name = descriptor(device, 'product') or name
    return {'vid': device.idVendor, 'pid': device.idProduct, 'bus': device.bus,
            'address': device.address, 'name': name, 'model': model, 'manufacturer': manufacturer,
            'serial': None if accessory else descriptor(device, 'serial_number'),
            'device_id': clean_text(identity.get('device_id'))}


def identify_accessory(device):
    """Read app identity only; never switch USB modes or access shared files."""
    if not is_accessory(device): return {}
    from .protocol import Connection
    from .usb_lock import USBLease
    from .usb_transport import USBTransport
    lease = transport = None
    try:
        lease = USBLease(device, timeout=0)
        transport = USBTransport(device, 1000)
        connection = Connection(transport); connection.hello(public_only=True)
        return {**connection.device_info, 'device_id': connection.device_id}
    except Exception:
        return {}  # Busy, older, or unapproved app: still show its USB location.
    finally:
        if transport:
            try: transport.close()
            except Exception: pass
        if lease: lease.close()


def record_label(record):
    identifier = ('Serial ' + record['serial']) if record.get('serial') else ('Phone ID ' + record['device_id'][-12:]) if record.get('device_id') else None
    return record['name'] + (' · ' + identifier if identifier else '')
