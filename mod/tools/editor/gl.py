"""moderngl renderer for the editor viewport: zone meshes (base texture x vertex colour x lightmap), placed editor
meshes (props, parametric pieces, imports) with a model matrix and a selection tint, the collision overlay, lines.

Renders into an offscreen framebuffer that the Viewport window shows with imgui.image.  The GL context is the one
hello_imgui made current (moderngl.create_context() attaches to it).

Mesh dicts (zonecache.ZoneMesh has the same fields as attributes):
    pos (N,3) f32, nrm (N,3) f32, col (N,4) f32 0..1 (optional), uv (N,2), uv_lm (N,2) (optional),
    idx (M*3,) u32, parts [{first, count, tex, lm, alpha}], textures {key: RGBA uint8 (h, w, 4)}
"""
import math

import moderngl
import numpy as np

MESH_VS = '''#version 330
uniform mat4 mvp; uniform mat4 model;
in vec3 pos; in vec3 nrm; in vec4 col; in vec2 uv; in vec2 uvl;
out vec3 n; out vec4 c; out vec2 t; out vec2 tl;
void main(){ n = mat3(model) * nrm; c = col; t = uv; tl = uvl; gl_Position = mvp * vec4(pos, 1.0); }'''
MESH_FS = '''#version 330
uniform sampler2D tex; uniform sampler2D lmap; uniform int has_tex; uniform int has_lm; uniform float alpha_cut;
uniform vec4 tint; uniform int shading; uniform float lm_scale;
in vec3 n; in vec4 c; in vec2 t; in vec2 tl; out vec4 o;
void main(){
  vec4 b = has_tex == 1 ? texture(tex, t) : vec4(0.72, 0.72, 0.74, 1.0);
  if (b.a < alpha_cut) discard;
  vec3 col;
  if (shading == 1 || has_lm == 0) {
    float d = 0.5 + 0.5 * max(dot(normalize(n), normalize(vec3(0.35, 0.85, 0.4))), 0.0);
    col = b.rgb * d * (shading == 1 ? vec3(1.0) : c.rgb);
  } else {
    col = b.rgb * c.rgb * 2.0 * texture(lmap, tl).rgb * lm_scale;
  }
  o = vec4(mix(col, tint.rgb, tint.a), 1.0);
}'''
FLAT_VS = '''#version 330
uniform mat4 mvp; in vec3 pos; in vec4 col; out vec4 c;
void main(){ c = col; gl_Position = mvp * vec4(pos, 1.0); }'''
FLAT_FS = '''#version 330
in vec4 c; out vec4 o; void main(){ o = c; }'''


def perspective(fovy_deg, aspect, near, far):
    f = 1.0 / math.tan(math.radians(fovy_deg) / 2)
    m = np.zeros((4, 4), np.float32)
    m[0, 0] = f / aspect
    m[1, 1] = f
    m[2, 2] = (far + near) / (near - far)
    m[2, 3] = 2 * far * near / (near - far)
    m[3, 2] = -1
    return m


class Camera:
    """Fly camera: position + yaw/pitch (degrees). yaw 0 looks along +Z, positive yaw turns towards +X (modkit)."""

    def __init__(self, pos=(0, 5, -10), yaw=0.0, pitch=-15.0, fov=60.0):
        self.pos = np.array(pos, np.float64)
        self.yaw, self.pitch, self.fov = yaw, pitch, fov
        self.near, self.far = 0.05, 3000.0

    def forward(self):
        y, p = math.radians(self.yaw), math.radians(self.pitch)
        return np.array([math.sin(y) * math.cos(p), math.sin(p), math.cos(y) * math.cos(p)])

    def right(self):
        """Screen right.  Looking along +Z (yaw 0) in a right-handed Y-up world, screen right is -X."""
        y = math.radians(self.yaw)
        return np.array([-math.cos(y), 0.0, math.sin(y)])

    def view(self):
        f = self.forward()
        s = np.cross(f, [0, 1, 0])
        s /= max(np.linalg.norm(s), 1e-9)
        u = np.cross(s, f)
        m = np.eye(4)
        m[0, :3], m[1, :3], m[2, :3] = s, u, -f
        m[:3, 3] = -m[:3, :3] @ self.pos
        return m.astype(np.float32)

    def proj(self, aspect):
        return perspective(self.fov, aspect, self.near, self.far)


def _get(m, k, default=None):
    if isinstance(m, dict):
        return m.get(k, default)
    return getattr(m, k, default)


class MeshDraw:
    """GPU copy of a mesh dict; textures come from the renderer's shared cache."""

    def __init__(self, r, m):
        self.r, self.m = r, m
        pos = np.asarray(_get(m, 'pos'), np.float32)
        n = len(pos)
        nrm = _get(m, 'nrm')
        nrm = np.asarray(nrm, np.float32) if nrm is not None else np.tile(np.float32([0, 1, 0]), (n, 1))
        col = _get(m, 'col')
        col = np.asarray(col, np.float32) if col is not None else np.ones((n, 4), np.float32)
        uv = _get(m, 'uv')
        uv = np.asarray(uv, np.float32) if uv is not None else np.zeros((n, 2), np.float32)
        ul = _get(m, 'uv_lm')
        ul = np.asarray(ul, np.float32) if ul is not None else np.zeros((n, 2), np.float32)
        v = np.hstack([pos, nrm, col[:, :4], uv, ul]).astype(np.float32)
        idx = np.asarray(_get(m, 'idx'), np.uint32).ravel()
        self.vbo = r.ctx.buffer(v.tobytes())
        self.ibo = r.ctx.buffer(idx.tobytes())
        self.vao = r.ctx.vertex_array(r.mesh_prog, [(self.vbo, '3f 3f 4f 2f 2f', 'pos', 'nrm', 'col', 'uv', 'uvl')],
                                      self.ibo, index_element_size=4)
        self.parts = _get(m, 'parts') or [dict(first=0, count=len(idx), tex=None, lm=None, alpha=False)]
        for k, im in (_get(m, 'textures') or {}).items():
            r.texture(k, im)
        self.hidden = set()
        self.ntris = len(idx) // 3

    def release(self):
        for o in (self.vao, self.vbo, self.ibo):
            o.release()

    def draw(self, VP, model=None, tint=(0, 0, 0, 0), lightmaps=True, shading=False, lm_scale=2.0):
        p = self.r.mesh_prog
        M = np.eye(4, dtype=np.float32) if model is None else np.asarray(model, np.float32)
        p['mvp'].write((VP @ M).T.astype(np.float32).tobytes())
        p['model'].write(M.T.astype(np.float32).tobytes())
        p['tex'].value, p['lmap'].value = 0, 1
        p['shading'].value = 1 if shading else 0
        p['tint'].value = tuple(tint)
        p['lm_scale'].value = lm_scale
        for i, part in enumerate(self.parts):
            if i in self.hidden:
                continue
            t = self.r.textures.get(part.get('tex'))
            lm = self.r.textures.get(part.get('lm')) if lightmaps else None
            p['has_tex'].value = 1 if t else 0
            p['has_lm'].value = 1 if lm else 0
            p['alpha_cut'].value = 0.3 if part.get('alpha') else -1.0
            if t:
                t.use(0)
            if lm:
                lm.use(1)
            self.vao.render(moderngl.TRIANGLES, vertices=part['count'], first=part['first'])


class Renderer:
    def __init__(self, ctx=None):
        self.ctx = ctx or moderngl.create_context()
        self.mesh_prog = self.ctx.program(vertex_shader=MESH_VS, fragment_shader=MESH_FS)
        self.flat_prog = self.ctx.program(vertex_shader=FLAT_VS, fragment_shader=FLAT_FS)
        self.fbo = None
        self.size = (0, 0)
        self.zone = None
        self.textures = {}
        self.meshes = {}         # key -> MeshDraw (placed editor meshes, cached by geometry key)
        self.collision = None    # (vao, vbo, count)
        self.sky = None          # MeshDraw of the level's sky dome, drawn around the camera
        self.bg = (0.07, 0.06, 0.1)

    # ------------------------------------------------------------------ resources
    def texture(self, key, im):
        if key in self.textures or im is None:
            return self.textures.get(key)
        im = np.ascontiguousarray(np.asarray(im, np.uint8))
        if im.ndim == 2:
            im = np.dstack([im, im, im, np.full_like(im, 255)])
        if im.shape[2] == 3:
            im = np.dstack([im, np.full(im.shape[:2], 255, np.uint8)])
        t = self.ctx.texture((im.shape[1], im.shape[0]), 4, im.tobytes())
        t.build_mipmaps()
        t.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
        t.anisotropy = 8.0
        self.textures[key] = t
        return t

    def replace_texture(self, key, im):
        old = self.textures.pop(key, None)
        if old is not None:
            old.release()
        return self.texture(key, im)

    def set_zone(self, zm):
        if self.zone is not None:
            self.zone.release()
        self.zone = MeshDraw(self, zm) if zm is not None else None
        self.set_collision(zm)

    def set_sky(self, zm):
        if self.sky is not None:
            self.sky.release()
        self.sky = MeshDraw(self, zm) if zm is not None else None

    def mesh(self, key, build):
        """Cached MeshDraw for `key`; build() -> mesh dict is called on a miss."""
        d = self.meshes.get(key)
        if d is None:
            m = build()
            if m is None:
                return None
            d = self.meshes[key] = MeshDraw(self, m)
        return d

    def drop_mesh(self, key):
        d = self.meshes.pop(key, None)
        if d is not None:
            d.release()

    def set_collision(self, zm, colors=None):
        if self.collision is not None:
            self.collision[0].release()
            self.collision[1].release()
            self.collision = None
        if zm is None or getattr(zm, 'col_tris', None) is None:
            return
        P = zm.col_pos[zm.col_tris.astype(np.int64)].reshape(-1, 3)
        C = np.repeat(collision_colors(zm.col_mat) if colors is None else colors, 3, 0)
        v = np.hstack([P, C]).astype(np.float32)
        vbo = self.ctx.buffer(v.tobytes())
        vao = self.ctx.vertex_array(self.flat_prog, [(vbo, '3f 4f', 'pos', 'col')])
        self.collision = (vao, vbo, len(P))

    def ensure(self, w, h):
        if self.size != (w, h):
            if self.fbo:
                self.fbo.release()
                self.color.release()
                self.depth.release()
            self.color = self.ctx.texture((w, h), 4)
            self.depth = self.ctx.depth_renderbuffer((w, h))
            self.fbo = self.ctx.framebuffer([self.color], self.depth)
            self.size = (w, h)

    @staticmethod
    def line_batch(segs):
        """segs: list of (Nx3 points, rgba, closed) -> vertex array for one GL_LINES draw."""
        out = []
        for pts, rgba, closed in segs:
            pts = np.asarray(pts, np.float32).reshape(-1, 3)
            if len(pts) < 2:
                continue
            a, b = pts[:-1], pts[1:]
            if closed:
                a = np.vstack([a, pts[-1:]])
                b = np.vstack([b, pts[:1]])
            c = np.tile(np.float32(rgba), (len(a), 1))
            out.append(np.hstack([np.stack([a, b], 1).reshape(-1, 3), np.repeat(c, 2, 0)]))
        return np.vstack(out).astype(np.float32) if out else None

    def _flat(self, data, mode, VP):
        if data is None or not len(data):
            return
        vbo = self.ctx.buffer(np.ascontiguousarray(data, np.float32).tobytes())
        vao = self.ctx.vertex_array(self.flat_prog, [(vbo, '3f 4f', 'pos', 'col')])
        self.flat_prog['mvp'].write(VP.T.astype(np.float32).tobytes())
        vao.render(mode)
        vao.release()
        vbo.release()

    # ------------------------------------------------------------------ frame
    def render(self, cam, w, h, draws=(), lines=(), xray_lines=(), tris=None, show_zone=True, show_collision=False,
               lightmaps=True, shading=False, lm_scale=2.0):
        """draws: [(MeshDraw, model 4x4, tint)];  lines: depth-tested segments; xray_lines: drawn on top;
        tris: (N*3, 7) pos+rgba translucent triangles (gizmo previews, volumes)."""
        self.ensure(w, h)
        ctx = self.ctx
        self.fbo.use()
        ctx.viewport = (0, 0, w, h)
        ctx.enable(moderngl.DEPTH_TEST)
        ctx.disable(moderngl.CULL_FACE)
        ctx.disable(moderngl.BLEND)
        ctx.clear(*self.bg, 1.0, depth=1.0)
        V, P = cam.view(), cam.proj(w / max(h, 1))
        VP = P @ V
        if self.sky is not None and show_zone:
            S = np.eye(4, dtype=np.float32)
            S[:3, :3] *= 0.5 * cam.far / 1500.0
            S[:3, 3] = cam.pos
            ctx.disable(moderngl.DEPTH_TEST)
            self.sky.draw(VP, S, (0, 0, 0, 0), lightmaps=False, shading=False)
            ctx.enable(moderngl.DEPTH_TEST)
        if self.zone and show_zone:
            self.zone.draw(VP, None, (0, 0, 0, 0), lightmaps, shading, lm_scale)
        for d, M, tint in draws:
            d.draw(VP, M, tint, lightmaps=False, shading=False)
        ctx.enable(moderngl.BLEND)
        ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA
        if show_collision and self.collision is not None and not getattr(self, 'hide_collision', False):
            ctx.polygon_offset = (-1.0, -1.0)
            self.flat_prog['mvp'].write(VP.T.astype(np.float32).tobytes())
            self.collision[0].render(moderngl.TRIANGLES)
            ctx.polygon_offset = (0.0, 0.0)
        if tris is not None:
            self._flat(tris, moderngl.TRIANGLES, VP)
        self._flat(self.line_batch(lines), moderngl.LINES, VP)
        if xray_lines:
            ctx.disable(moderngl.DEPTH_TEST)
            self._flat(self.line_batch(xray_lines), moderngl.LINES, VP)
            ctx.enable(moderngl.DEPTH_TEST)
        ctx.disable(moderngl.BLEND)
        ctx.screen.use()
        return V, P

    def read_rgb(self):
        """The last frame as an (h, w, 3) uint8 array (top row first)."""
        w, h = self.size
        a = np.frombuffer(self.fbo.read(components=3), np.uint8).reshape(h, w, 3)
        return a[::-1].copy()


# face flags (thp8hkc.FACE_FLAGS)
F_WALLRIDE, F_VERT, F_NONCOL, F_TRIGGER, F_INVISIBLE, F_NOTSKATE = 0x4, 0x8, 0x10, 0x40, 0x1000, 0x2


def collision_colors(mat, alpha=0.38):
    """Per-triangle RGBA from material words: vert orange, wallride blue, not skatable red, triggers / invisible
    grey, other surfaces green tinted by terrain."""
    mat = np.asarray(mat, np.uint32)
    fl = mat & 0xFFFF
    ter = (mat >> 16).astype(np.float32)
    c = np.zeros((len(mat), 4), np.float32)
    c[:, 0] = 0.25 + 0.25 * np.sin(ter * 1.7) ** 2
    c[:, 1] = 0.75
    c[:, 2] = 0.35 + 0.3 * np.cos(ter * 0.9) ** 2
    c[:, 3] = alpha
    c[(fl & F_WALLRIDE) != 0] = (0.25, 0.55, 1.0, alpha)
    c[(fl & F_VERT) != 0] = (1.0, 0.55, 0.15, alpha)
    c[(fl & F_NOTSKATE) != 0] = (1.0, 0.25, 0.25, alpha)
    c[(fl & (F_TRIGGER | F_INVISIBLE | F_NONCOL)) != 0] = (0.6, 0.6, 0.6, alpha * 0.4)
    return c
