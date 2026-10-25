"""Okumura LZSS as used by THP8 QB scripts: N=4096, F=18, THRESHOLD=2, ring pre-filled with 0x20 (' '), r=N-F.

Token stream: flag byte (LSB first, 1 = literal), match = 2 bytes: lo=pos&0xFF, hi=((pos>>4)&0xF0)|(len-3).

The ring buffer MUST be pre-filled with spaces (0x20): the game's decoder sub_1ECA48 fills it with
0x20202020 (and the eboot also contains the matching Okumura encoder, sub_1EE5xx).  A zero-filled
decoder (tools/qb.py) corrupts 251 of 7628 scripts in the main qb pak: matches that reach back into
the initial window turn 0x20 bytes into 0x00 (`begin` tokens -> 0x00, names 0x20xxxxxx -> 0x00xxxxxx).

`compress` is a faithful port of Okumura's 1989 LZSS.C encoder (binary search tree).
"""
N = 4096
F = 18
THRESHOLD = 2
NIL = N
FILL = 0x20


def decompress(src: bytes, out_len: int) -> bytes:
    ring = bytearray([FILL]) * N
    r = N - F
    out = bytearray()
    i = 0
    n = len(src)
    flags = 0
    while len(out) < out_len and i < n:
        flags >>= 1
        if not flags & 0x100:
            flags = src[i] | 0xFF00
            i += 1
            if i >= n:
                break
        if flags & 1:
            c = src[i]
            i += 1
            out.append(c)
            ring[r] = c
            r = (r + 1) & (N - 1)
        else:
            if i + 1 >= n:
                break
            lo, hi = src[i], src[i + 1]
            i += 2
            pos = lo | ((hi & 0xF0) << 4)
            ln = (hi & 0x0F) + THRESHOLD + 1
            for k in range(ln):
                c = ring[(pos + k) & (N - 1)]
                out.append(c)
                ring[r] = c
                r = (r + 1) & (N - 1)
    return bytes(out[:out_len])


def compress(data: bytes) -> bytes:
    text_buf = bytearray(N + F - 1)
    text_buf[:N - F] = bytes([FILL]) * (N - F)
    lson = [NIL] * (N + 1)
    rson = [NIL] * (N + 257)
    dad = [NIL] * (N + 1)
    mlen = 0
    mpos = 0

    def insert_node(r):
        nonlocal mlen, mpos
        cmp = 1
        p = N + 1 + text_buf[r]
        rson[r] = lson[r] = NIL
        mlen = 0
        tb = text_buf
        while True:
            if cmp >= 0:
                if rson[p] != NIL:
                    p = rson[p]
                else:
                    rson[p] = r
                    dad[r] = p
                    return
            else:
                if lson[p] != NIL:
                    p = lson[p]
                else:
                    lson[p] = r
                    dad[r] = p
                    return
            i = 1
            cmp = 0
            while i < F:
                cmp = tb[r + i] - tb[p + i]
                if cmp != 0:
                    break
                i += 1
            if i > mlen:
                mpos = p
                mlen = i
                if mlen >= F:
                    break
        dad[r] = dad[p]
        lson[r] = lson[p]
        rson[r] = rson[p]
        dad[lson[p]] = r
        dad[rson[p]] = r
        if rson[dad[p]] == p:
            rson[dad[p]] = r
        else:
            lson[dad[p]] = r
        dad[p] = NIL

    def delete_node(p):
        if dad[p] == NIL:
            return
        if rson[p] == NIL:
            q = lson[p]
        elif lson[p] == NIL:
            q = rson[p]
        else:
            q = lson[p]
            if rson[q] != NIL:
                while rson[q] != NIL:
                    q = rson[q]
                rson[dad[q]] = lson[q]
                dad[lson[q]] = dad[q]
                lson[q] = lson[p]
                dad[lson[p]] = q
            rson[q] = rson[p]
            dad[rson[p]] = q
        dad[q] = dad[p]
        if rson[dad[p]] == p:
            rson[dad[p]] = q
        else:
            lson[dad[p]] = q
        dad[p] = NIL

    out = bytearray()
    code_buf = bytearray(17)
    code_buf[0] = 0
    cbp = 1
    mask = 1
    s = 0
    r = N - F
    src = data
    n = len(src)
    ip = 0
    ln = 0
    while ln < F and ip < n:
        text_buf[r + ln] = src[ip]
        ip += 1
        ln += 1
    if ln == 0:
        return b''
    for i in range(1, F + 1):
        insert_node(r - i)
    insert_node(r)
    while True:
        if mlen > ln:
            mlen = ln
        if mlen <= THRESHOLD:
            mlen = 1
            code_buf[0] |= mask
            code_buf[cbp] = text_buf[r]
            cbp += 1
        else:
            code_buf[cbp] = mpos & 0xFF
            code_buf[cbp + 1] = ((mpos >> 4) & 0xF0) | (mlen - (THRESHOLD + 1))
            cbp += 2
        mask = (mask << 1) & 0xFF
        if mask == 0:
            out += code_buf[:cbp]
            code_buf[0] = 0
            cbp = 1
            mask = 1
        last = mlen
        i = 0
        while i < last and ip < n:
            c = src[ip]
            ip += 1
            delete_node(s)
            text_buf[s] = c
            if s < F - 1:
                text_buf[s + N] = c
            s = (s + 1) & (N - 1)
            r = (r + 1) & (N - 1)
            insert_node(r)
            i += 1
        while i < last:
            i += 1
            delete_node(s)
            s = (s + 1) & (N - 1)
            r = (r + 1) & (N - 1)
            ln -= 1
            if ln:
                insert_node(r)
        if ln <= 0:
            break
    if cbp > 1:
        out += code_buf[:cbp]
    return bytes(out)
