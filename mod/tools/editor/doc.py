"""The editor document: a modkit mod folder (modkit.json + assets/) with editor metadata in x_editor keys.

docs/editor/contract.md section 1.  Every change goes through Doc methods so that undo/redo sees it:

    doc = Doc.load(folder)            # or Doc.new(folder, id=..., name=..., author=...)
    with doc.undo('Move kicker'):
        doc.update_op(uid, {'position': [1, 2, 3]})
    doc.undo_last(); doc.redo()
    doc.save()                        # ops sorted by phase, generated assets written by the caller first
"""
import copy
import json
import os
import secrets
import tempfile
from contextlib import contextmanager
from pathlib import Path

PHASES = {'texture': 10, 'static': 20, 'collision': 30, 'nodes': 40, 'rails': 45, 'peds': 50, 'goals': 60,
          'story': 70, 'script': 80, 'skin': 15, 'cas': 16}


def clears_first(op):
    """Order inside a phase for the ops that wipe stock content: rails.remove all runs before the rails.add ops;
    static.clear / collision.clear run after the adds (the adds clone the zone's own materials / bodies as templates,
    and the clears keep what the build added)."""
    n = str(op.get('op', ''))
    if n == 'rails.remove' and op.get('all'):
        return 0
    if n in ('static.clear', 'collision.clear'):
        return 2
    return 1
DOC_FORMAT = 1


def new_uid():
    return secrets.token_hex(4)


def op_area(op):
    return str(op.get('op', '')).split('.', 1)[0]


def meta(op):
    """The op's x_editor dict (created on demand)."""
    return op.setdefault('x_editor', {})


class Doc:
    def __init__(self, folder, data):
        self.folder = Path(folder)
        self.data = data
        self.data.setdefault('ops', [])
        top = self.data.setdefault('x_editor', {})
        top.setdefault('format', DOC_FORMAT)
        for op in self.data['ops']:
            m = op.setdefault('x_editor', {})       # every op gets an id (the build strips x_ keys)
            if 'uid' not in m:
                m['uid'] = new_uid()
        self._undo, self._redo = [], []
        self._txn = None
        self.dirty = False
        self.revision = 0            # bumped on every change (views poll it)
        self.phases = dict(PHASES)   # extended by ops modules' PHASE

    # ------------------------------------------------------------------ files
    @classmethod
    def load(cls, folder):
        folder = Path(folder)
        data = json.loads((folder / 'modkit.json').read_text(encoding='utf-8'))
        return cls(folder, data)

    @classmethod
    def new(cls, folder, id, name, author, zone=None, description=''):
        folder = Path(folder)
        (folder / 'assets').mkdir(parents=True, exist_ok=True)
        data = {'format': 1, 'id': id, 'name': name, 'version': '0.1.0', 'author': author,
                'description': description, 'game': {'title_id': 'BLUS30011', 'disc_version': '01.02'},
                'priority': 50, 'requires': [], 'conflicts': [], 'ops': [],
                'x_editor': {'format': DOC_FORMAT, 'zone': zone}}
        d = cls(folder, data)
        d.save()
        return d

    def sorted_ops(self):
        def key(io):
            i, op = io
            return (self.phases.get(op_area(op), 90), clears_first(op), i)
        return [op for _, op in sorted(enumerate(self.data['ops']), key=key)]

    def save(self):
        self.data['ops'] = self.sorted_ops()
        txt = json.dumps(self.data, indent=2, ensure_ascii=False) + '\n'
        self.folder.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.folder, prefix='.modkit.', suffix='.json')
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
            f.write(txt)
        os.replace(tmp, self.folder / 'modkit.json')
        self.dirty = False

    def asset_path(self, rel):
        return self.folder / rel

    # ------------------------------------------------------------------ queries
    @property
    def ops(self):
        return self.data['ops']

    @property
    def top(self):
        return self.data['x_editor']

    def find(self, uid):
        for op in self.data['ops']:
            if op.get('x_editor', {}).get('uid') == uid:
                return op
        return None

    def entity_ops(self, entity):
        return [op for op in self.data['ops'] if op.get('x_editor', {}).get('entity') == entity]

    def entities(self):
        """[(entity id, [ops])] in document order; raw ops (no x_editor) are their own entity 'raw:<index>'."""
        out, seen = [], {}
        for i, op in enumerate(self.data['ops']):
            m = op.get('x_editor')
            ent = (m.get('entity') or m.get('uid')) if m else f'raw:{i}'
            if ent not in seen:
                seen[ent] = []
                out.append((ent, seen[ent]))
            seen[ent].append(op)
        return out

    # ------------------------------------------------------------------ edits (undoable)
    def _snapshot(self):
        return copy.deepcopy(self.data)

    @contextmanager
    def undo(self, label):
        """Group the changes made inside the block into one undo step."""
        if self._txn is not None:
            yield
            return
        before = self._snapshot()
        self._txn = label
        try:
            yield
        except Exception:
            self.data = before
            raise
        finally:
            self._txn = None
        if before != self.data:
            self._undo.append((label, before))
            self._undo = self._undo[-200:]
            self._redo.clear()
            self._changed()

    def _edit(self, label, fn):
        with self.undo(label):
            return fn()

    def _changed(self):
        self.dirty = True
        self.revision += 1

    def add_op(self, op, entity=None, kind=None, label=None, params=None, index=None):
        op = copy.deepcopy(op)
        m = meta(op)
        m.setdefault('uid', new_uid())
        m.setdefault('entity', entity or m['uid'])
        if kind:
            m['kind'] = kind
        if label:
            m['label'] = label
        if params is not None:
            m['params'] = params

        def f():
            if index is None:
                self.data['ops'].append(op)
            else:
                self.data['ops'].insert(index, op)
            return op
        return self._edit(f'Add {label or op.get("op")}', f)

    def update_op(self, uid, changes, label=None):
        def f():
            op = self.find(uid)
            if op is None:
                raise KeyError(uid)
            for k, v in changes.items():
                if v is None:
                    op.pop(k, None)
                else:
                    op[k] = copy.deepcopy(v)
            return op
        return self._edit(label or 'Edit', f)

    def set_meta(self, uid, **kw):
        def f():
            m = meta(self.find(uid))
            m.update(kw)
        return self._edit('Edit', f)

    def remove_entity(self, entity):
        def f():
            self.data['ops'] = [op for op in self.data['ops']
                                if op.get('x_editor', {}).get('entity', op.get('x_editor', {}).get('uid')) != entity]
        return self._edit('Delete', f)

    def replace_entity(self, entity, ops, label='Edit'):
        """Regenerate an entity: its ops are replaced by `ops` at the position of the first old one."""
        def f():
            old = self.data['ops']
            pos = next((i for i, op in enumerate(old) if op.get('x_editor', {}).get('entity') == entity), len(old))
            keep = [op for op in old if op.get('x_editor', {}).get('entity') != entity]
            # the regenerated ops keep the old ones' uids (by position): regenerating an unchanged entity (every save /
            # build does) then leaves the document identical, so no undo step and no revision bump (Live update would
            # otherwise rebuild after every build, forever)
            old_uids = [op.get('x_editor', {}).get('uid') for op in old
                        if op.get('x_editor', {}).get('entity') == entity]
            new = []
            for i, op in enumerate(ops):
                op = copy.deepcopy(op)
                m = meta(op)
                if 'uid' not in m:
                    m['uid'] = old_uids[i] if i < len(old_uids) and old_uids[i] else new_uid()
                m['entity'] = entity
                new.append(op)
            before = sum(1 for op in old[:pos] if op.get('x_editor', {}).get('entity') != entity)
            self.data['ops'] = keep[:before] + new + keep[before:]
        return self._edit(label, f)

    def set_top(self, **kw):
        return self._edit('Edit project', lambda: self.data['x_editor'].update(kw))

    def set_field(self, key, value):
        return self._edit(f'Edit {key}', lambda: self.data.__setitem__(key, value))

    # ------------------------------------------------------------------ undo / redo
    def can_undo(self):
        return bool(self._undo)

    def can_redo(self):
        return bool(self._redo)

    def undo_last(self):
        if not self._undo:
            return None
        label, before = self._undo.pop()
        self._redo.append((label, self._snapshot()))
        self.data = before
        self._changed()
        return label

    def redo(self):
        if not self._redo:
            return None
        label, after = self._redo.pop()
        self._undo.append((label, self._snapshot()))
        self.data = after
        self._changed()
        return label


class Drag:
    """Live edits spread over several frames (a gizmo drag): changes are applied directly, then committed as ONE undo
    step by end().  doc.begin_drag() -> Drag; drag.end(label)."""

    def __init__(self, doc):
        self.doc = doc
        self.before = doc._snapshot()

    def end(self, label):
        d = self.doc
        if self.before != d.data:
            d._undo.append((label, self.before))
            d._redo.clear()
            d._changed()


def _begin_drag(self):
    return Drag(self)


def _live_update(self, uid, changes):
    """Change an op without an undo step (inside a Drag)."""
    op = self.find(uid)
    if op is None:
        return
    for k, v in changes.items():
        op[k] = copy.deepcopy(v)
    self.revision += 1


Doc.begin_drag = _begin_drag
Doc.live_update = _live_update
