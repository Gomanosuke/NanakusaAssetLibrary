"""Filesystem indexing; deliberately independent of Houdini and Qt.

Sources are read-only. SQLite contains only an index and user metadata.
Connections are short-lived and thread-local. The index belongs on local disk.
"""
from contextlib import contextmanager
from pathlib import Path
import hashlib
import json
import os
import re
from stat import S_ISLNK
import sqlite3
import uuid

KINDS = ("usd", "model", "material", "pbr", "texture", "hdri", "decal")
IMAGES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".exr", ".hdr", ".rat", ".pic", ".tx", ".bmp", ".tga"}
MODELS = {".obj", ".bgeo", ".geo", ".abc", ".fbx", ".glb", ".stl", ".ply", ".vdb"}
USD = {".usd", ".usda", ".usdc", ".usdz"}
GENRES = ('USD', 'Texture', '3DModel')

def package_entry(folder):
    folder = Path(folder)
    for ext in ('.usd', '.usdc', '.usda', '.usdz'):
        p = folder / (folder.name + ext)
        if p.is_file():
            return p
    return None

def is_usd_package(row):
    path=Path(row['relpath'])
    return row['kind']=='usd' and not (path.suffix.lower()=='.usdz' and
        (len(path.parts)==2 or path.stem.lower()!=path.parent.name.lower()))

def visible_folders(base):
    """Only the three genres; USD package contents are opaque."""
    base = Path(base)
    result = []
    for genre in GENRES:
        result.append(genre)
        start = base / genre
        if not start.is_dir():
            continue
        for folder, dirs, _ in os.walk(start, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not d.startswith('.') and not (Path(folder)/d).is_symlink())
            if Path(folder) != start:
                result.append(Path(folder).relative_to(base).as_posix())
            if genre == 'USD' and package_entry(folder):
                dirs[:] = []
    return result

def classify(path):
    p = Path(path)
    name = p.name.lower()
    if name.endswith(".pbr.json"):
        return "pbr"
    if name.endswith(".decal.json"):
        return "decal"
    if p.suffix.lower() in USD:
        return "usd"
    if p.suffix.lower() == ".mtlx":
        return "material"
    if p.suffix.lower() in MODELS or name.endswith((".bgeo.sc", ".geo.sc")):
        return "model"
    if p.suffix.lower() in IMAGES:
        words = re.split(r"[/\\_\- .]+", str(p).lower())
        if "decal" in words or "decals" in words:
            return "decal"
        if p.suffix.lower() == ".hdr" or "hdri" in words or "hdris" in words:
            return "hdri"
        return "texture"  # EXR may be a data map; do not guess HDRI from EXR alone.
    return None

def path_key(path):
    return os.path.normcase(str(Path(path).resolve()))

def inside(root, relative):
    root = Path(root).resolve()
    dest = (root / relative).resolve()
    if not dest.is_relative_to(root):
        raise ValueError("Path escapes the registered library root")
    return dest

class Library:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db = self.data_dir / "library.sqlite3"
        with self.connect() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS roots (
                id TEXT PRIMARY KEY, path TEXT NOT NULL UNIQUE, label TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS assets (
                id TEXT PRIMARY KEY, root_id TEXT NOT NULL, relpath TEXT NOT NULL,
                label TEXT NOT NULL, kind TEXT NOT NULL, override_kind TEXT,
                size INTEGER, mtime REAL, present INTEGER NOT NULL DEFAULT 1,
                favorite INTEGER NOT NULL DEFAULT 0, tags TEXT NOT NULL DEFAULT '',
                thumbnail TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
                UNIQUE(root_id, relpath));
              CREATE INDEX IF NOT EXISTS asset_root ON assets(root_id);
              CREATE TABLE IF NOT EXISTS folders (
                root_id TEXT NOT NULL, relpath TEXT NOT NULL, PRIMARY KEY(root_id, relpath));
            ''')
            try:
                # Readers (the panel) keep working while a scan writes; safe on a local disk.
                db.execute('PRAGMA journal_mode=WAL')
            except sqlite3.DatabaseError:
                pass

    @contextmanager
    def connect(self):
        db = sqlite3.connect(str(self.db), timeout=30)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA synchronous=NORMAL')
            with db:
                yield db
        finally:
            db.close()

    def roots(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM roots ORDER BY label")]

    def add_root(self, path, label=None):
        p = Path(path).resolve()
        if not p.is_dir():
            raise ValueError("Library folder does not exist: " + str(p))
        key = path_key(p)
        with self.connect() as db:
            row = db.execute("SELECT id FROM roots WHERE path=?", (key,)).fetchone()
            if row:
                return row[0]
            # Overlapping roots create duplicate entries and ambiguous ownership.
            for r in db.execute("SELECT path FROM roots"):
                other = Path(r[0])
                if p.is_relative_to(other) or other.is_relative_to(p):
                    raise ValueError("This folder overlaps an existing library root")
            rid = uuid.uuid4().hex
            db.execute("INSERT INTO roots VALUES (?,?,?)", (rid, key, label or p.name))
            return rid

    def remove_root(self, rid):
        """Forget index entries only; never delete source assets."""
        with self.connect() as db:
            db.execute("DELETE FROM assets WHERE root_id=?", (rid,))
            db.execute("DELETE FROM folders WHERE root_id=?", (rid,))
            db.execute("DELETE FROM roots WHERE id=?", (rid,))

    def relink_root(self, rid, path):
        p = Path(path).resolve()
        if not p.is_dir():
            raise ValueError("Folder not found")
        for root in self.roots():
            if root['id'] != rid:
                other = Path(root['path'])
                if p.is_relative_to(other) or other.is_relative_to(p):
                    raise ValueError("This folder overlaps another library")
        with self.connect() as db:
            db.execute("UPDATE roots SET path=? WHERE id=?", (path_key(p), rid))

    def scan(self, rid, cancel=lambda: False):
        root = next(r for r in self.roots() if r['id'] == rid)
        base = Path(root['path'])
        if not base.is_dir():
            raise FileNotFoundError("Library is offline: " + str(base))
        rows = []
        errors = []
        folders = set()
        with self.connect() as db:
            known = {r[1]: r[0] for r in db.execute("SELECT id, relpath FROM assets WHERE root_id=?", (rid,))}
        taken = set(known.values())
        base_text = str(base)
        data_key = os.path.normcase(str(self.data_dir))
        for folder, dirs, names in os.walk(base_text, followlinks=False, onerror=lambda e: errors.append(str(e))):
            if cancel():
                return {"cancelled": True, "count": 0, "errors": errors}
            # Plain string paths: a library can hold hundreds of thousands of files and this
            # runs next to the UI thread, so every syscall and object counts.
            dirs[:] = [d for d in dirs if not d.startswith('.') and not os.path.islink(os.path.join(folder, d))
                       and os.path.normcase(os.path.abspath(os.path.join(folder, d))) != data_key]
            relative_folder = os.path.relpath(folder, base_text).replace(os.sep, '/')
            if relative_folder == '.':
                dirs[:] = [d for d in dirs if d in GENRES]
                continue
            parts = relative_folder.split('/')
            genre = parts[0]
            folders.add(relative_folder)
            entry = None
            if genre == 'USD':
                entry = package_entry(folder) if len(parts) > 1 else None
                if entry:
                    names = [entry.name]
                    dirs[:] = []
                else:
                    names = [name for name in names if name.lower().endswith('.usdz')]
            for name in names:
                if cancel():
                    return {"cancelled": True, "count": 0, "errors": errors}
                lowered = name.lower()
                ext = os.path.splitext(lowered)[1]
                kind = ('usd' if genre == 'USD' else
                        'texture' if genre == 'Texture' and ext in IMAGES else
                        'model' if genre == '3DModel' and (ext in MODELS or lowered.endswith(('.bgeo.sc', '.geo.sc'))) else None)
                if not kind:
                    continue
                try:
                    stat = os.lstat(os.path.join(folder, name))
                    if S_ISLNK(stat.st_mode):
                        continue
                    rel = relative_folder + '/' + name
                    # A moved asset keeps its original id so tags survive; another file
                    # later appearing at the old path must not reuse that id.
                    aid = known.get(rel)
                    if aid is None:
                        aid = hashlib.sha256((rid + '/' + rel).encode()).hexdigest()[:32]
                        if aid in taken:
                            aid = uuid.uuid4().hex
                        taken.add(aid)
                    label = parts[-1] if kind == 'usd' and entry else name[:len(name) - len(os.path.splitext(name)[1])]
                    rows.append((aid, rid, rel, label, kind, stat.st_size, stat.st_mtime))
                except OSError as exc:
                    errors.append(str(exc))
        # A failed or cancelled traversal must never invalidate a good index.
        with self.connect() as db:
            if not errors:
                db.execute("UPDATE assets SET present=0 WHERE root_id=?", (rid,))
                db.execute("DELETE FROM folders WHERE root_id=?", (rid,))
                db.executemany("INSERT OR IGNORE INTO folders VALUES (?,?)", [(rid, f) for f in folders])
            db.executemany('''INSERT INTO assets (id,root_id,relpath,label,kind,size,mtime)
                VALUES (?,?,?,?,?,?,?) ON CONFLICT(root_id,relpath) DO UPDATE SET
                kind=excluded.kind,override_kind=NULL,size=excluded.size,mtime=excluded.mtime,present=1''', rows)
        return {"cancelled": False, "count": len(rows), "errors": errors}

    def folders(self, rid):
        """Folder paths of a root as seen by the last scan (USD packages are leaves), sorted.

        Read from the index so the panel never walks the disk. Empty when the root was
        indexed by an older version and has not been scanned since; see set_folders.
        """
        with self.connect() as db:
            found = [r[0] for r in db.execute("SELECT relpath FROM folders WHERE root_id=?", (rid,))]
        return found

    def set_folders(self, rid, rels):
        with self.connect() as db:
            db.execute("DELETE FROM folders WHERE root_id=?", (rid,))
            db.executemany("INSERT OR IGNORE INTO folders VALUES (?,?)", [(rid, r) for r in rels])

    def add_folder(self, rid, rel):
        """Record a folder (and its parents) created by the panel."""
        parts = [p for p in rel.split('/') if p]
        with self.connect() as db:
            for i in range(1, len(parts) + 1):
                db.execute("INSERT OR IGNORE INTO folders VALUES (?,?)", (rid, '/'.join(parts[:i])))

    def relocate(self, root_id, changes, folder_moves=()):
        """Apply path changes from a completed move in one transaction.

        changes: dicts with id, relpath and thumbnail. Ids are kept so tags,
        favorites and notes follow the asset. Stale rows for files that no
        longer exist may hold a destination path; they are dropped.
        """
        with self.connect() as db:
            for change in changes:
                db.execute("DELETE FROM assets WHERE root_id=? AND relpath=? AND present=0 AND id<>?",
                           (root_id, change['relpath'], change['id']))
                db.execute("UPDATE assets SET relpath=?, thumbnail=? WHERE id=? AND root_id=?",
                           (change['relpath'], change['thumbnail'], change['id'], root_id))
            for old, new in folder_moves:
                db.execute("UPDATE OR REPLACE folders SET relpath=? || substr(relpath, ?) WHERE root_id=? AND (relpath=? OR substr(relpath, 1, ?)=?)",
                           (new, len(old) + 1, root_id, old, len(old) + 1, old + '/'))

    def assets(self, search="", root_id=None, kind=None, favorite=False, missing=False, folder=None):
        clauses, args = [], []
        if folder:
            # Everything below a folder, as an index range on (root_id, relpath): '/' + 1 is '0'.
            clauses.append("a.relpath >= ? AND a.relpath < ?"); args.extend([folder.rstrip('/') + '/', folder.rstrip('/') + '0'])
        if not missing:
            clauses.append("a.present=1")
        if root_id:
            clauses.append("a.root_id=?"); args.append(root_id)
        if kind:
            clauses.append("a.kind=?"); args.append(kind)
        if favorite:
            clauses.append("a.favorite=1")
        for word in search.split():
            clauses.append("(a.label LIKE ? ESCAPE '\\' OR a.relpath LIKE ? ESCAPE '\\' OR a.tags LIKE ? ESCAPE '\\')")
            word = word.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            args.extend(['%' + word + '%'] * 3)
        sql = '''SELECT a.*, r.path AS root_path, r.label AS root_label,
                 a.kind AS effective_kind
                 FROM assets a JOIN roots r ON r.id=a.root_id'''
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY a.label COLLATE NOCASE, a.relpath"
        with self.connect() as db:
            return [dict(r) for r in db.execute(sql, args)]

    def assets_by_ids(self, ids):
        rows = []
        ids = list(dict.fromkeys(ids))
        with self.connect() as db:
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                rows += [dict(r) for r in db.execute(
                    '''SELECT a.*, r.path AS root_path, r.label AS root_label, a.kind AS effective_kind
                       FROM assets a JOIN roots r ON r.id=a.root_id WHERE a.id IN (%s)''' % ','.join('?' * len(chunk)), chunk)]
        return rows

    def update(self, aid, **fields):
        allowed = {'label', 'override_kind', 'favorite', 'tags', 'thumbnail', 'notes'}
        if not fields or not set(fields).issubset(allowed):
            raise ValueError("Unsupported metadata fields")
        if fields.get('override_kind') not in (None, *KINDS):
            raise ValueError("Unknown kind")
        with self.connect() as db:
            db.execute("UPDATE assets SET " + ','.join(k+'=?' for k in fields) + " WHERE id=?", [*fields.values(), aid])

    def resolve(self, asset):
        p = inside(asset['root_path'], asset['relpath'])
        if not p.is_file():
            raise FileNotFoundError("Asset is missing or library is offline: " + str(p))
        return p

    def backup_index(self):
        from datetime import datetime
        folder = self.data_dir / 'backups'
        folder.mkdir(parents=True, exist_ok=True)
        p = folder / ('index_backup_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.sqlite3')
        with self.connect() as source:
            target = sqlite3.connect(str(p))
            try:
                source.backup(target)
            finally:
                target.close()
        return p

MAP_ALIASES = {
    'base_color': ('basecolor','base_color','albedo','diffuse','color'),
    'roughness': ('roughness','rough'), 'metalness': ('metalness','metallic','metal'),
    'normal': ('normal','nor_gl','normalgl','nrm'), 'opacity': ('opacity','alpha','mask'),
    'displacement': ('displacement','height','disp'), 'emission': ('emission','emissive'),
}

def suggest_maps(folder):
    """Suggestions only: ambiguous sets must be reviewed in the authoring dialog."""
    result = {}
    for p in sorted(Path(folder).iterdir()):
        if p.is_file() and p.suffix.lower() in IMAGES:
            name = p.stem.lower()
            for channel, aliases in MAP_ALIASES.items():
                if channel not in result and any(re.search(r'(^|[_\-. ])'+re.escape(a)+r'($|[_\-. \d])',name) for a in aliases):
                    result[channel] = p.as_posix()
    return result

def read_manifest(path):
    p = Path(path)
    data = json.loads(p.read_text(encoding='utf-8-sig'))
    if data.get('schema') != 1 or not isinstance(data.get('maps'), dict):
        raise ValueError("Expected schema=1 and a maps object")
    maps = {}
    for key, value in data['maps'].items():
        if key not in MAP_ALIASES or not isinstance(value, str):
            raise ValueError("Unsupported map entry: " + key)
        source = Path(value) if Path(value).is_absolute() else p.parent / value
        if not source.is_file() and '<UDIM>' not in str(source):
            raise FileNotFoundError(str(source))
        maps[key] = source.resolve().as_posix()
    return data, maps
