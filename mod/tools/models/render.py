"""Offscreen GPU mesh renderer (moderngl standalone context, OpenGL 3.3) for previews / before-after shots.

    from render import render, grid
    img = render(parts, yaw=30, pitch=20, size=(800, 800), wire=True)      # -> PIL.Image (RGB)
    grid([img1, img2], cols=2).save('x.png')

parts: list of dicts (one per draw / material group):
    pos  (N,3) float          required
    idx  (M,3) int            required (triangle list; convert strips first)
    nrm  (N,3) float          optional (else flat normals computed per face)
    col  (N,4) float 0..1 or uint8   optional vertex colour (multiplied)
    uv   (N,2) float          optional, with tex (H,W,4) uint8 RGBA -> textured
    tint (r,g,b)              optional solid colour (default light grey)
Camera auto-fits the union bounding sphere (or pass center=/radius=). Y is up (the game is Y-up).
Options: wire=True overlays the triangle wireframe (geometry shader, screen-space constant width),
         flat=True forces faceted shading, cull=False (default draws both sides), bg=(r,g,b).
CLI:  python render.py mesh.obj out.png [--yaw 30 --pitch 20 --wire]
"""
import math
import sys

import numpy as np
from PIL import Image

_CTX = None
_PROG = {}

VS = """
#version 330
uniform mat4 mvp; uniform mat4 mv;
in vec3 in_pos; in vec3 in_nrm; in vec4 in_col; in vec2 in_uv;
out vec3 v_nrm; out vec4 v_col; out vec2 v_uv; out vec3 v_vpos;
void main(){ gl_Position = mvp*vec4(in_pos,1.0); v_nrm = mat3(mv)*in_nrm; v_col=in_col; v_uv=in_uv;
             v_vpos = (mv*vec4(in_pos,1.0)).xyz; }
"""
GS = """
#version 330
layout(triangles) in; layout(triangle_strip, max_vertices=3) out;
uniform vec2 vp;
in vec3 v_nrm[]; in vec4 v_col[]; in vec2 v_uv[]; in vec3 v_vpos[];
out vec3 g_nrm; out vec4 g_col; out vec2 g_uv; out vec3 g_dist; out vec3 g_vpos;
void main(){
  vec2 p0 = vp*gl_in[0].gl_Position.xy/gl_in[0].gl_Position.w;
  vec2 p1 = vp*gl_in[1].gl_Position.xy/gl_in[1].gl_Position.w;
  vec2 p2 = vp*gl_in[2].gl_Position.xy/gl_in[2].gl_Position.w;
  vec2 e0=p2-p1, e1=p2-p0, e2=p1-p0; float area=abs(e1.x*e2.y-e1.y*e2.x);
  float h0=area/max(length(e0),1e-6), h1=area/max(length(e1),1e-6), h2=area/max(length(e2),1e-6);
  for(int i=0;i<3;i++){ gl_Position=gl_in[i].gl_Position; g_nrm=v_nrm[i]; g_col=v_col[i]; g_uv=v_uv[i]; g_vpos=v_vpos[i];
    g_dist = i==0? vec3(h0,0,0) : (i==1? vec3(0,h1,0) : vec3(0,0,h2)); EmitVertex(); }
  EndPrimitive();
}
"""
FS = """
#version 330
uniform sampler2D tex; uniform int use_tex; uniform int wire; uniform int flat_shade; uniform vec3 tint;
in vec3 g_nrm; in vec4 g_col; in vec2 g_uv; in vec3 g_dist; in vec3 g_vpos;
out vec4 f;
void main(){
  vec3 n = flat_shade==1 ? normalize(cross(dFdx(g_vpos), dFdy(g_vpos))) : normalize(g_nrm);
  vec3 l1 = normalize(vec3(0.35,0.6,0.75)); vec3 l2 = normalize(vec3(-0.6,0.2,0.3));
  float d = abs(dot(n,l1))*0.75 + abs(dot(n,l2))*0.25;
  vec4 base = vec4(tint,1.0)*g_col;
  if(use_tex==1){ vec4 t = texture(tex, g_uv); if(t.a<0.3) discard; base *= vec4(t.rgb,1.0); }
  vec3 c = base.rgb*(0.25+0.85*d);
  if(wire==1){ float m=min(g_dist.x,min(g_dist.y,g_dist.z)); float w=1.0-smoothstep(0.4,1.2,m); c=mix(c, vec3(0.05,0.05,0.08), w*0.85); }
  f = vec4(c,1.0);
}
"""


def _ctx():
    global _CTX
    if _CTX is None:
        import moderngl
        _CTX = moderngl.create_standalone_context()
        _PROG['p'] = _CTX.program(vertex_shader=VS, geometry_shader=GS, fragment_shader=FS)
    return _CTX, _PROG['p']


def _persp(fovy, aspect, n, f):
    t = 1 / math.tan(math.radians(fovy) / 2)
    return np.array([[t / aspect, 0, 0, 0], [0, t, 0, 0], [0, 0, (f + n) / (n - f), 2 * f * n / (n - f)], [0, 0, -1, 0]], np.float32)


def _lookat(eye, tgt, up=(0, 1, 0)):
    eye, tgt, up = (np.asarray(x, np.float64) for x in (eye, tgt, up))
    f = tgt - eye
    f /= np.linalg.norm(f)
    s = np.cross(f, up)
    if np.linalg.norm(s) < 1e-6:
        s = np.cross(f, (0, 0, 1))
    s /= np.linalg.norm(s)
    u = np.cross(s, f)
    m = np.eye(4)
    m[0, :3], m[1, :3], m[2, :3] = s, u, -f
    m[:3, 3] = -m[:3, :3] @ eye
    return m.astype(np.float32)


def face_normals_expand(pos, idx):
    """Unshare vertices so each face gets its own normal (for flat shading without nrm)."""
    p = pos[idx.reshape(-1)]
    fn = np.cross(p[1::3] - p[0::3], p[2::3] - p[0::3])
    fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-12)
    return p, np.repeat(fn, 3, 0), np.arange(len(p)).reshape(-1, 3)


def smooth_normals(pos, idx):
    n = np.zeros_like(pos, dtype=np.float64)
    p = pos[idx]
    fn = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
    for k in range(3):
        np.add.at(n, idx[:, k], fn)
    return n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)


def render(parts, yaw=30.0, pitch=15.0, size=(800, 800), wire=False, flat=False, fov=35.0, center=None, radius=None,
           bg=(0.93, 0.93, 0.95), dist_scale=1.0, eye=None, target=None, ss=2):
    ctx, prog = _ctx()
    W, H = size[0] * ss, size[1] * ss
    allp = np.concatenate([np.asarray(p['pos'], np.float64).reshape(-1, 3) for p in parts if len(p['pos'])])
    if center is None:
        lo, hi = allp.min(0), allp.max(0)
        center = (lo + hi) / 2
    if radius is None:
        radius = float(np.linalg.norm(allp - center, axis=1).max()) or 1.0
    center = np.asarray(center, np.float64)
    d = radius / math.sin(math.radians(fov) / 2) * dist_scale
    cy, sy, cp, sp = math.cos(math.radians(yaw)), math.sin(math.radians(yaw)), math.cos(math.radians(pitch)), math.sin(math.radians(pitch))
    if eye is None:
        eye = center + d * np.array([sy * cp, sp, cy * cp])
    if target is None:
        target = center
    mv = _lookat(eye, target)
    dd = float(np.linalg.norm(np.asarray(eye) - np.asarray(target)))
    proj = _persp(fov, W / H, max(dd - radius * 1.5, dd * 1e-3, 1e-3), dd + radius * 2)
    fbo = ctx.simple_framebuffer((W, H), components=4)
    fbo.use()
    import moderngl
    ctx.enable(moderngl.DEPTH_TEST)
    ctx.disable(moderngl.CULL_FACE)
    fbo.clear(*bg, 1.0, depth=1.0)
    prog['mvp'].write((proj @ mv).T.astype(np.float32).tobytes())
    prog['mv'].write(mv.T.astype(np.float32).tobytes())
    prog['vp'].value = (W / 2, H / 2)
    prog['wire'].value = int(wire)
    prog['flat_shade'].value = int(flat)
    for p in parts:
        pos = np.asarray(p['pos'], np.float32).reshape(-1, 3)
        idx = np.asarray(p['idx'], np.int64).reshape(-1, 3)
        if not len(idx):
            continue
        nrm = p.get('nrm')
        nrm = smooth_normals(pos, idx) if nrm is None else np.asarray(nrm, np.float32)
        col = p.get('col')
        if col is None:
            col = np.ones((len(pos), 4), np.float32)
        else:
            col = np.asarray(col)
            col = (col.astype(np.float32) / 255.0) if col.dtype == np.uint8 else col.astype(np.float32)
            if col.shape[1] == 3:
                col = np.concatenate([col, np.ones((len(col), 1), np.float32)], 1)
        uv = p.get('uv')
        uv = np.zeros((len(pos), 2), np.float32) if uv is None else np.asarray(uv, np.float32)
        data = np.concatenate([pos, nrm.astype(np.float32), col, uv], 1).astype('f4')
        vbo = ctx.buffer(data.tobytes())
        ibo = ctx.buffer(idx.astype('i4').tobytes())
        vao = ctx.vertex_array(prog, [(vbo, '3f 3f 4f 2f', 'in_pos', 'in_nrm', 'in_col', 'in_uv')], ibo)
        tex = p.get('tex')
        prog['tint'].value = tuple(p.get('tint', (0.82, 0.82, 0.82) if tex is None else (1, 1, 1)))
        t = None
        if tex is not None:
            tex = np.ascontiguousarray(np.asarray(tex, np.uint8))
            t = ctx.texture((tex.shape[1], tex.shape[0]), 4, tex.tobytes())
            t.build_mipmaps()
            t.repeat_x = t.repeat_y = True
            t.use(0)
            prog['tex'].value = 0
        prog['use_tex'].value = int(tex is not None)
        vao.render(moderngl.TRIANGLES)
        vao.release(); vbo.release(); ibo.release()
        if t is not None:
            t.release()
    img = Image.frombytes('RGBA', (W, H), fbo.read(components=4)).transpose(Image.FLIP_TOP_BOTTOM).convert('RGB')
    fbo.release()
    if ss > 1:
        img = img.resize(size, Image.LANCZOS)
    return img


def grid(imgs, cols=2, labels=None, pad=4, bg=(255, 255, 255)):
    from PIL import ImageDraw
    w = max(i.width for i in imgs)
    h = max(i.height for i in imgs)
    rows = (len(imgs) + cols - 1) // cols
    out = Image.new('RGB', (cols * w + (cols + 1) * pad, rows * h + (rows + 1) * pad), bg)
    dr = ImageDraw.Draw(out)
    for k, im in enumerate(imgs):
        x, y = pad + (k % cols) * (w + pad), pad + (k // cols) * (h + pad)
        out.paste(im, (x, y))
        if labels:
            dr.rectangle([x, y, x + 8 + 7 * len(labels[k]), y + 16], fill=(255, 255, 255))
            dr.text((x + 4, y + 2), labels[k], fill=(0, 0, 0))
    return out


def load_obj(path):
    vs, fs = [], []
    for ln in open(path):
        if ln.startswith('v '):
            vs.append([float(x) for x in ln.split()[1:4]])
        elif ln.startswith('f '):
            ids = [int(t.split('/')[0]) - 1 for t in ln.split()[1:]]
            for i in range(1, len(ids) - 1):
                fs.append([ids[0], ids[i], ids[i + 1]])
    return np.array(vs), np.array(fs)


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('mesh')
    ap.add_argument('out')
    ap.add_argument('--yaw', type=float, default=30)
    ap.add_argument('--pitch', type=float, default=15)
    ap.add_argument('--wire', action='store_true')
    a = ap.parse_args()
    if a.mesh == 'test':                         # UV sphere (numpy only)
        th, ph = np.meshgrid(np.linspace(0, np.pi, 13), np.linspace(0, 2 * np.pi, 25), indexing='ij')
        pos = np.stack([np.sin(th) * np.cos(ph), np.cos(th), np.sin(th) * np.sin(ph)], -1).reshape(-1, 3)
        g = np.arange(13 * 25).reshape(13, 25)
        a0, b0, c0, d0 = g[:-1, :-1].ravel(), g[:-1, 1:].ravel(), g[1:, :-1].ravel(), g[1:, 1:].ravel()
        idx = np.concatenate([np.stack([a0, c0, b0], 1), np.stack([b0, c0, d0], 1)])
    else:
        pos, idx = load_obj(a.mesh)
    render([dict(pos=pos, idx=idx)], a.yaw, a.pitch, wire=a.wire).save(a.out)
    print('wrote', a.out)
