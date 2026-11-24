"""Compose feature edits against one stock source and a shared aligned tail."""
from .patch import Edit, apply_patch
from .platform.z30_120 import BASE, CAPACITY, STOCK_SHA256, StockImage


class Plan:
    """Edits plus an owned tail.

    tail_base moves the tail away from the end of the stock image: card builds
    (card.py) assemble it at two far bases to derive relocations, and the
    card loader copies it into RAM. card=True also makes features keep their
    edits out of the EG range the camera cannot write (docs/card-loader-probe.md);
    resident names the addresses the resident firmware already provides.
    """

    def __init__(self, source, *, tail_base=None, card=False, resident=None):
        self.image = StockImage(source)
        self.source = self.image.data
        self.stock_end = BASE + len(self.source) - 2
        self.tail_base = self.stock_end if tail_base is None else tail_base
        if self.tail_base % 4:
            raise ValueError('Tail base must be aligned to four bytes')
        self.card = card
        self.resident = dict(resident or {})
        self.tail = bytearray()
        self.edits = []
        self.features = {}

    @property
    def address(self):
        return self.tail_base + len(self.tail)

    def alloc(self, data):
        address = self.address
        self.tail.extend(data)
        self.tail.extend(bytes((-len(data)) % 4))
        return address

    def put(self, address, data):
        offset = address - self.tail_base
        if offset < 0 or offset + len(data) > len(self.tail):
            raise ValueError('Write outside the owned code/data tail')
        self.tail[offset:offset + len(data)] = data

    def edit(self, address, replacement, name, expected=None):
        replacement = bytes(replacement)
        actual = self.image.read(address, len(replacement))
        if expected is not None and actual != expected:
            raise ValueError(f'Unexpected source instruction/data at {address:#x}')
        self.edits.append(Edit(self.image.rom(address, len(replacement)) - BASE,
                               actual, replacement, name))

    def copy_table(self, address, size, patches=()):
        """Copy stock data into the tail with (offset, bytes, expected) patches applied."""
        data = bytearray(self.image.read(address, size))
        for offset, replacement, expected in patches:
            if expected is not None and data[offset:offset + len(replacement)] != expected:
                raise ValueError(f'Unexpected table data at {address + offset:#x}')
            data[offset:offset + len(replacement)] = replacement
        return self.alloc(bytes(data))

    def finish(self):
        if self.tail_base != self.stock_end:
            raise ValueError('A relocated tail cannot be written into the EG image')
        output, report = apply_patch(self.source, self.edits, self.tail,
                                     expected_sha256=STOCK_SHA256, base=BASE, capacity=CAPACITY)
        report.update(model='Nikon Z30', firmware='C 1.20', features=self.features,
                      camera_verified=False, status_page_included=False,
                      limitations=['This exact composed image has not been physically tested.',
                                   'CRC and offline instruction execution do not prove boot, display, audio or exposure.'])
        return output, report
