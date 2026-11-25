"""Reach an extension page through the root's categories (menu_framework `menu_paths`)."""


def wait_built(s, page, row=0):
    """Wait until `page` is the active page and its `row` widget exists (QEMU display_session `s`).

    A focus request sent while a page is still building is dropped, and a touch
    then selects whatever row the focus was on; the CJK pages build slowest.
    """
    for _ in range(40):
        active = int(s.input(0x1046109a, [])['registers']['r0'], 16)
        registry = int.from_bytes(s.remote.memory(page + 36, 4), 'little')
        if active == page and registry and int(s.input(0x1045745e, [registry, 1, row])['registers']['r0'], 16):
            break
        s.run_for(.25)
    s.run_for(1.5)


def enter_parent(menu, key, state, activate, s=None):
    """Open every page on the way to `key` except the last (its category page, if any).

    `activate(page_object, control_id, row)` opens a row; with the session `s`
    each opened page is waited for (wait_built). Returns the object of the page
    that lists `key` (the root, or its category page) and the control id and row
    that open `key` there. A confirmed choice and the back button on the page
    return to that object.
    """
    path = menu['menu_paths'][key]
    for step in path[:-1]:
        activate(state + menu['page_offsets'][str(step['page'])], step['control_id'], step['row'])
        if s is not None:
            wait_built(s, state + menu['page_offsets'][str(step['opens'])], path[-1]['row'])
    last = path[-1]
    return state + menu['page_offsets'][str(last['page'])], last['control_id'], last['row']
