"""Sixth DISP page, using native information, histogram and horizon widgets.

State 5 is stored through the native DISP property service. Only presentation
readers translate it; snapshot comparisons retain the distinct state so entry
and exit still invalidate the native widgets. No snapshot is temporarily changed.
"""
from ..assembly import assemble, jump
from ..patch import Edit
from ..platform.z30_120 import BASE
from .liveview_full_info_windows import WINDOWS, PREFIXES

FULL_INFO_DISP = 5
# The histogram widget's first visibility mask (liveview_dual.HISTOGRAM + 4, RAM). Stock it
# is never 0 (0x444, 0xCCC with liveview-dual); portrait-ui clears all nine while it draws its
# own upright histogram. The extra page-5 bit below would otherwise show the stock one too
# (camera, portrait v25: two histograms on the full-information page).
HISTOGRAM_MASK = 0xC1826718
CYCLE_SITE = 0x10372536
CYCLE_EXPECTED = bytes.fromhex('90f89601083d2c5c022c')


def add(stock, tail, edits):
    hooks = []

    def hook(site, expected, source, name):
        if stock.read(site, len(expected)) != expected:
            raise ValueError(f'Unexpected full-info DISP window at {site:#x}')
        entry = BASE + len(stock.data) - 2 + len(tail)
        code = assemble(source, entry, 256)
        tail.extend(code)
        tail.extend(bytes((-len(code)) % 4))
        edits.append(Edit(site - BASE, expected, jump(site, entry, len(expected)), name))
        hooks.append(dict(site=hex(site), entry=hex(entry | 1), size=len(code),
                          resume=hex(site + len(expected)), name=name))

    # Insert after horizon, before the native next-page/eligibility logic.
    # For state 5 use table[3], never read beyond the five-entry stock table.
    hook(CYCLE_SITE, CYCLE_EXPECTED, '''
        ldrb.w r0, [r0, #0x196]; subs r5, #8;
        cmp r0, #3; beq full_info;
        cmp r0, #5; bne native;
        movs r0, #3;
      native:
        ldrb r4, [r5, r0]; cmp r4, #2;
        ldr.w pc, =0x10372541;
      full_info:
        movs r4, #5;
        ldr.w pc, =0x10372565;
    ''', 'Insert full-information DISP page')

    for site, expected_hex, dest, mode, classifier, replay in WINDOWS:
        expected = bytes.fromhex(expected_hex)
        load_offset, prefix = PREFIXES.get(site, (0, ''))
        # Preserve all flags and scratch registers: some displaced windows
        # consume a CMP from before the load (notably 103258AA).
        source = f'''
            {prefix};
            .byte {', '.join(str(x) for x in expected[load_offset:load_offset + 4])};
            push {{r12}}; mrs r12, APSR; push {{r12}};
            cmp {dest}, #5; bne unchanged;
            mov.w {dest}, #{mode};
        '''
        if classifier is not None:
            # Only the histogram needs an extra descriptor bit. Other native
            # widgets use their normal information-page masks, not a union of
            # unrelated output/recording masks. r4 is local to the classifier.
            source += f'''
                cmp r{classifier}, #31; bne unchanged;
                movw r12, #{HISTOGRAM_MASK & 0xFFFF}; movt r12, #{HISTOGRAM_MASK >> 16}; ldr r12, [r12];
                cmp r12, #0; beq unchanged;
                movw r12, #0x111; orr.w r4, r4, r12;
            '''
        source += f'''
          unchanged:
            pop {{r12}}; msr APSR_nzcvq, r12; pop {{r12}};
            {replay};
            ldr.w pc, ={site + len(expected) | 1};
        '''
        hook(site, expected, source, 'Full-information native display reader')
    return hooks
