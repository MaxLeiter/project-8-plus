"""RSX vertex data shared by THP8's static (.geom/.mdl: thp8geom) and skinned (.skin: thp8skin) meshes.

    vertex_layout(key)          vertex-format word (mesh +0x18) -> vertex declaration; port of the EBOOT's sub_005F57A0
    cmp_decode / cmp_encode     RSX CMP vectors (packed signed-normalised 11:11:10, X in the low bits)
    strip_to_tris(idx)          triangle strip(s) with 0x7FFF primitive restart -> (M,3) triangles

The declaration builder was checked against the EBOOT code: with bit 0x80 clear it emits weights (S1 x2, attr 1) AND
bone indices (UB x4, attr 7), then colour; with 0x80 set, colour and UVs go to stream 1 (offset restarts) and bits 1/2
are ignored. The extra-vector attribute tables are the bytes at 0x1005D0B0 (01 07 00) and 0x1005D0A8 (04 05 00).
"""
import numpy as np

RESTART = 0x7FFF              # primitive-restart index of every THP8 triangle strip
# RSX vertex attribute types (NV4097_SET_VERTEX_DATA_ARRAY_FORMAT): 1 S1 (s16n), 2 F, 3 SF (half), 4 UB, 5 S32K, 6 CMP, 7 UB256
RSX_TYPE = {1: 'S1', 2: 'F', 3: 'SF', 4: 'UB', 5: 'S32K', 6: 'CMP', 7: 'UB256'}
TSIZE = {1: 2, 2: 4, 3: 2, 4: 1, 5: 2, 6: 4, 7: 1}
TAN_ATTRS = (1, 7, 0)         # table @0x1005D0B0: (key>>10)&3 extra CMP vectors (tangent, binormal)
EXT_ATTRS = (4, 5, 0)         # table @0x1005D0A8: (key>> 8)&3 extra CMP vectors


def vertex_layout(key):
    """Vertex declaration of format word `key` (mesh+0x18) -> (elements, strides [stream 0, stream 1]); elements =
    list of dict(name, type, comps, size, offset, attr, stream).
      bit 0      positions float4 (else float3);  bits 12-13: number of position elements - 1 (2 = billboard: corner+pivot)
      bit 3      normal (attr 2): CMP 11:11:10 when bit 5 is also set, else float3
      bits 10-11 CMP vectors at attrs 1,7,0 (tangent/binormal);  bits 8-9 CMP vectors at attrs 4,5,0
      bit 7      colour + UVs go to stream 1 (offset restarts); else bit 1 = 2xS1 weights (attr 1), bit 2 = 4xUB indices (attr 7)
      bit 4      colour UB x4 (attr 3)
      bit 6      UVs as half floats (code 1,2 -> half2, 3 -> half4) else float (code n -> float n)
      bits 16-31 2-bit UV code per texcoord slot (attr 8 + k)
    """
    els = []
    off = [0, 0]

    def add(name, typ, comps, attr, stream=0):
        size = TSIZE[typ] * comps
        els.append(dict(name=name, type=typ, comps=comps, size=size, offset=off[stream], attr=attr, stream=stream))
        off[stream] += size

    npos = ((key >> 12) & 3) + 1
    for i in range(npos):
        add('pos' if i == 0 else f'pos{i}', 2, 4 if key & 1 else 3, 0)
    if key & 8:
        if (key & 0x28) == 0x28:
            add('nrm', 6, 1, 2)
        else:
            add('nrm', 2, 3, 2)
    for i in range((key >> 10) & 3):
        add(f'tan{i}', 6, 1, TAN_ATTRS[i])
    for i in range((key >> 8) & 3):
        add(f'ext{i}', 6, 1, EXT_ATTRS[i])
    stream = 0
    if key & 0x80:
        stream = 1
    else:
        if key & 2:
            add('weights', 1, 2, 1)
        if key & 4:
            add('bones', 4, 4, 7)
    if key & 0x10:
        add('col', 4, 4, 3, stream)
    uvk = key >> 16
    attr = 8
    while uvk:
        c = uvk & 3
        if c:
            if key & 0x40:
                add(f'uv{attr - 8}', 3, 4 if c == 3 else 2, attr, stream)
            else:
                add(f'uv{attr - 8}', 2, c, attr, stream)
        uvk >>= 2
        attr += 1
    return els, off


def cmp_decode(u):
    """RSX CMP (packed signed normalised 11:11:10, X in the low bits) -> (..., 3) float32."""
    u = np.asarray(u, np.uint32)
    x = (u & 0x7FF).astype(np.int32)
    y = ((u >> 11) & 0x7FF).astype(np.int32)
    z = ((u >> 22) & 0x3FF).astype(np.int32)
    x = np.where(x >= 0x400, x - 0x800, x)
    y = np.where(y >= 0x400, y - 0x800, y)
    z = np.where(z >= 0x200, z - 0x400, z)
    return np.stack([x / 1023.0, y / 1023.0, z / 511.0], -1).astype(np.float32)


def cmp_encode(v):
    v = np.clip(np.asarray(v, np.float64), -1, 1)
    x = np.round(v[..., 0] * 1023).astype(np.int64) & 0x7FF
    y = np.round(v[..., 1] * 1023).astype(np.int64) & 0x7FF
    z = np.round(v[..., 2] * 511).astype(np.int64) & 0x3FF
    return (x | (y << 11) | (z << 22)).astype(np.uint32)


def strip_to_tris(strip, restart_index=RESTART, drop_degenerate=True):
    """Triangle strip (with primitive-restart index; None = a single strip) -> (M,3) int64 triangles with the strip
    winding rule (odd triangles of each run swapped; front = CCW, matches the stored normals). Degenerate (join)
    triangles are dropped unless drop_degenerate=False."""
    s = np.asarray(strip, np.int64).reshape(-1)
    runs = np.split(s, np.nonzero(s == restart_index)[0]) if restart_index is not None else [s]
    out = []
    for r in runs:
        r = r[r != restart_index] if restart_index is not None else r
        if len(r) < 3:
            continue
        a, b, c = r[:-2].copy(), r[1:-1].copy(), r[2:]
        odd = np.arange(len(a)) % 2 == 1
        a[odd], b[odd] = r[1:-1][odd], r[:-2][odd]
        tri = np.stack([a, b, c], 1)
        if drop_degenerate:
            tri = tri[(tri[:, 0] != tri[:, 1]) & (tri[:, 1] != tri[:, 2]) & (tri[:, 0] != tri[:, 2])]
        out.append(tri)
    return np.concatenate(out) if out else np.zeros((0, 3), np.int64)
