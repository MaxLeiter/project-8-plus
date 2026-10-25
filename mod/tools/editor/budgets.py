"""Live budget estimate for the Budgets window (the exact numbers come from a dry-run build's report).

Video memory: every zone's _vram pak must fit the four shared zone link slots, which are as big as the largest zone
_vram pak (docs/modding/limits.md): free = 58.86 MiB - 4 x (slot - stock slot); the build fails under 8 MiB free.
"""
import os

from . import entities as E

STOCK_SLOT = 14786560
FREE_STOCK = int(58.86 * 2 ** 20)
RESERVE = 8 * 2 ** 20


def estimate(ed):
    """[(label, used, limit, unit, note)]: added video memory from the preview meshes (vertex ~36 B, ~2.2 B per
    strip index, generated textures at DXT1 + mips, shared per zone), the zone slot, the free video memory left."""
    if ed.doc is None or ed.zone_mesh is None:
        return []
    added = {}
    tex_seen = set()
    tris_col = 0
    for ent, ops in ed.doc.entities():
        m = ops[0].get('x_editor', {})
        k = E.get_kind(m.get('kind')) if m.get('primary') else None
        if k is None or not k.has_mesh or m.get('kind') == 'prop.gameobject':
            continue
        try:
            res = E.entity_mesh(ed, m)
        except Exception:  # noqa: BLE001
            continue
        if not res or res[1] is None:
            continue
        md = res[1]
        z = (m.get('zone') or ed.zone).lower()
        nv, nt = len(md['pos']), len(md['idx']) // 3
        b = nv * 36 + nt * 3 * 2.2
        for p in md.get('parts', []):
            key = (z, p.get('tex'))
            if p.get('tex') is not None and key not in tex_seen:
                tex_seen.add(key)
                im = (md.get('textures') or {}).get(p.get('tex'))
                b += int(im.shape[0] * im.shape[1] * 0.5 * 1.34) if im is not None else 44000
        added[z] = added.get(z, 0) + b
        if any(op.get('op') == 'collision.add_mesh' for op in ops):
            tris_col = max(tris_col, nt)
    zone = ed.zone.lower()
    try:
        vrel = ed.zone_mesh.meta.get('vram')
        base = os.path.getsize(ed.data_dir / vrel) if vrel else STOCK_SLOT
    except OSError:
        base = STOCK_SLOT
    size = base + added.get(zone, 0)
    slot = max(STOCK_SLOT, -(-int(size) // 0x800) * 0x800)
    free = FREE_STOCK - 4 * (slot - STOCK_SLOT)
    hard = STOCK_SLOT + (FREE_STOCK - RESERVE) // 4
    rows = [("This level's video memory", int(size), hard, 'B',
             f'stock slot {STOCK_SLOT / 2 ** 20:.2f} MiB: up to it this zone is free, above it every byte costs 4 '
             f'(all four zone slots grow); the hard limit keeps the 8 MiB video memory reserve'),
            ("All levels' video memory", int(FREE_STOCK - free), FREE_STOCK - RESERVE, 'B',
             f'predicted free video memory {free / 2 ** 20:.2f} MiB; the build fails under the 8 MiB reserve')]
    if tris_col:
        rows.append(('Collision triangles (largest piece)', tris_col, 65535, '', 'collision.add_mesh limit per op'))
    return rows


SHORT = {"This level's video memory": 'Level video memory', "All levels' video memory": 'All levels',
         'Collision triangles (largest piece)': 'Collision', 'Grind rails (all levels)': 'Rails', 'Goals': 'Goals',
         'People in this level': 'People', 'Script space': 'Scripts', 'Level data (main memory)': 'Level data'}


def friendly(key):
    """A build report budget key -> a name for people."""
    k = key.split(':')[0]
    return {'RailNodeHeap (global_rails.pak)': 'Grind rails (all levels)', 'goals.count': 'Goals',
            'peds.placed': 'People in this level', 'qb_pak.added': 'Script space', 'zone_header': 'Level data (main memory)',
            'zone_vram_slot': 'Zone video memory slot', 'zone_vram': "This level's video memory",
            'heap_global_pak.main': 'Global data', 'heap_global_pak.vram': 'Global video memory',
            'mission_pak': 'Mission data'}.get(k, key.replace('_', ' ').replace('.', ' '))


def from_report(budgets, bar=False):
    """The exact budgets of the last build -> [(label, used, limit, unit, note)] (those with a limit)."""
    out = []
    for k, b in sorted(budgets.items()):
        if b.get('limit') is None or b.get('value') is None:
            continue
        if bar and k.split(':')[0] in ('zone_vram', 'zone_vram_slot'):
            continue                      # the bar shows the live video memory estimate instead
        out.append((friendly(k), b['value'], b['limit'], b.get('unit', 'B'), b.get('note') or k))
    return out


def scale(unit, limit):
    """-> (factor, unit label, format) for showing a budget."""
    if unit == 'B':
        if (limit or 0) >= 2 ** 20:
            return 1 / 2 ** 20, 'MB', '{:.2f}'
        return 1 / 1024, 'KB', '{:.0f}'
    return 1, unit or '', '{:.0f}'
