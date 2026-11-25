"""Experimental Z30 C1.20 two-core cache adapter with registry preflight.

This synchronous native service was camera-tested in the working configuration.
An unresponsive registered peer can still make it wait indefinitely; registration
is not a liveness check, and timeout/error behavior was not validated.
"""
from ...thumb import Thumb


def sync_shared_code(address, max_code=16384):
    a=Thumb(address)
    a.emit('f0b5 83b0 0446 0d46')
    a.emit('002c');a.branch(0,'reject')
    a.emit('002d');a.branch(0,'reject')
    a.load(0,'max_code',max_code)
    a.emit('8542');a.branch(8,'reject')
    a.emit('6019');a.branch(2,'reject')
    a.load(6,'smp_registry',0xc2abc8b8)
    a.emit('3068 0128');a.branch(1,'reject')
    a.load(7,'kernel_processor_count',0xc18eb038)
    a.emit('3868 0228');a.branch(1,'reject')
    a.emit('7069 0328');a.branch(1,'reject')  # all two configured cores registered.
    a.emit('7068 0128');a.branch(1,'reject')  # stock IPI IRQ identifier 1.
    a.load(7,'ipi_slot',0xc2a1c7f0)
    a.load(1,'ipi_handler',0x100527a8)
    a.emit('3868 8842');a.branch(1,'reject')
    a.emit('3f20 2146 8143 601a 2d18 0c46')
    a.emit('2046 2946 0122 0223')  # shared D clean (no local-only bit).
    a.load(7,'range_service',0x10035aac)
    a.emit('b847 0028');a.branch(13,'reject')
    a.emit('bff34f8f')
    a.emit('2046 2946 0222 0123 b847 0028');a.branch(13,'reject')
    a.emit('bff34f8f bff36f8f 0120');a.branch(14,'done')
    a.label('reject');a.emit('0020')
    a.label('done');a.emit('03b0 f0bd')
    return a.finish()
