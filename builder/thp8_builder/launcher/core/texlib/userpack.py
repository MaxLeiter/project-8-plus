"""A user texture pack on disk: <pack>\\textures\\<key>.dds, textures\\index.json (what each file replaces) and the
optional mod.json the mod manager shows. Writes are atomic per file."""
import json
import os
import time

INDEX = "index.json"


def textures_dir(pack_dir):
    return os.path.join(pack_dir, "textures")


def _write_atomic(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def read_index(pack_dir):
    try:
        with open(os.path.join(textures_dir(pack_dir), INDEX), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_index(pack_dir, index):
    _write_atomic(os.path.join(textures_dir(pack_dir), INDEX),
                  json.dumps(index, indent=1, sort_keys=True).encode("utf-8"))


def save_replacement(pack_dir, rec, dds_bytes, k, how="replace"):
    """Store the DDS for texture `rec` (its key names the file) and record it in index.json. Returns the file path."""
    key = rec.get("key")
    if not key:
        raise ValueError("this texture has no key (it cannot be replaced)")
    path = os.path.join(textures_dir(pack_dir), f"{key}.dds")
    _write_atomic(path, dds_bytes)
    index = read_index(pack_dir)
    index[key] = dict(file=f"{key}.dds", name=rec.get("name") or rec["id"], id=rec["id"], w=rec["w"] * k,
                      h=rec["h"] * k, k=k, mips=rec["mips"], format=rec["fmt"], how=how,
                      saved=time.strftime("%Y-%m-%d %H:%M:%S"))
    _write_index(pack_dir, index)
    return path


def remove_replacement(pack_dir, key):
    try:
        os.remove(os.path.join(textures_dir(pack_dir), f"{key}.dds"))
    except FileNotFoundError:
        pass
    index = read_index(pack_dir)
    if index.pop(key, None) is not None:
        _write_index(pack_dir, index)


def replaced_keys(pack_dir):
    """Keys that have a DDS in the pack (the folder is the truth, like the runtime's scan)."""
    d = textures_dir(pack_dir)
    try:
        return sorted(f[:16].lower() for f in os.listdir(d) if len(f) == 20 and f.lower().endswith(".dds"))
    except OSError:
        return []
