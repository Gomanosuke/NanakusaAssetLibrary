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

from . import pbr

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

USD_LAYERS = ('.usd', '.usdc', '.usda')
GENERATE_TMP = '.nanakusa_generate_tmp'   # name part of a generation job's temporary output (never an asset)

def package_entries(folder):
    """The USD assets a folder under USD/ is made of: its entry file named after the folder (the
    whole folder is one asset), otherwise every USD layer directly in it - e.g. Big and Small
    versions sharing one ./textures folder. [] for a plain folder (standalone .usdz files in a
    plain folder are self-contained assets and do not make it one of these)."""
    entry = package_entry(folder)
    if entry:
        return [entry]
    try:
        return sorted(p for p in Path(folder).iterdir() if p.suffix.lower() in USD_LAYERS and p.is_file())
    except OSError:
        return []

def is_usd_package(row):
    """True for the entry file of a package folder (named after its folder): the folder is the asset.
    USD layers sharing a folder without such an entry are assets of their own, but still depend on
    the folder's other files (see is_shared_usd_layer)."""
    path=Path(row['relpath'])
    return row['kind']=='usd' and len(path.parts)>2 and path.stem.lower()==path.parent.name.lower()

def is_shared_usd_layer(row):
    """A USD layer listed next to other files it needs (textures...) in a folder without an entry file:
    moving it alone would break its relative paths, so it moves only with its folder."""
    path=Path(row['relpath'])
    return row['kind']=='usd' and path.suffix.lower() in USD_LAYERS and not is_usd_package(row)

def visible_folders(base, cancel=lambda: False):
    """Only the three genres; USD package contents are opaque."""
    base = Path(base)
    result = []
    for genre in GENRES:
        result.append(genre)
        start = base / genre
        if not start.is_dir():
            continue
        for folder, dirs, _ in os.walk(start, followlinks=False):
            if cancel():
                return result
            dirs[:] = sorted(d for d in dirs if not d.startswith('.') and not (Path(folder)/d).is_symlink())
            if Path(folder) != start:
                result.append(Path(folder).relative_to(base).as_posix())
            if genre == 'USD' and Path(folder) != start and package_entries(folder):
                dirs[:] = []
    return result

def is_lod_set(name):
    """A variant set that holds levels of detail ("LOD" from lod_gen.py, or a source's own "LOD",
    "lods", "LOD_levels"...)."""
    return name.lower().startswith('lod')

AUTO_TAGS = ('lod', 'variant', 'proxy', 'anim')   # set from the files themselves (variant_scan.py)

def sync_auto_tags(tags, set_names, has_proxy=None, has_anim=None):
    """`tags` with the automatic ones matching the asset: "lod" for a level-of-detail variant set on
    its top prim, "variant" for any other set there (element switch, a source's own variants),
    "proxy" when it has purpose=proxy geometry, "anim" when it is animated (a value that changes
    over time: joint rotations, points, transforms). has_proxy / has_anim None: leave that tag as it
    is. Other words and their order are kept."""
    words = tags.split()
    wanted_tags = [('lod', any(is_lod_set(n) for n in set_names)),
                   ('variant', any(not is_lod_set(n) for n in set_names))]
    if has_proxy is not None:
        wanted_tags.append(('proxy', bool(has_proxy)))
    if has_anim is not None:
        wanted_tags.append(('anim', bool(has_anim)))
    for tag, wanted in wanted_tags:
        if wanted and tag not in words:
            words.append(tag)
        elif not wanted and tag in words:
            words = [w for w in words if w != tag]
    return ' '.join(words)

def is_texture_cache(name):
    """A texture Houdini converted from a source image and saved beside it (photo.hdr.rat,
    photo.exr.tx: two image extensions). Not an asset of its own; a .rat / .tx saved on purpose
    has a single extension and is kept."""
    stem, ext = os.path.splitext(name.lower())
    return ext in ('.rat', '.tx') and os.path.splitext(stem)[1] in IMAGES

def classify(path):
    p = Path(path)
    name = p.name.lower()
    if is_texture_cache(name):
        return None
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
              CREATE TABLE IF NOT EXISTS info (
                id TEXT PRIMARY KEY, stamp TEXT NOT NULL, data TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS folders (
                root_id TEXT NOT NULL, relpath TEXT NOT NULL, PRIMARY KEY(root_id, relpath));
            ''')
            self._migrate(db)
            try:
                # Readers (the panel) keep working while a scan writes; safe on a local disk.
                db.execute('PRAGMA journal_mode=WAL')
            except sqlite3.DatabaseError:
                pass

    # Columns derived from the path, so the panel can list, page and stack with plain SQL:
    # folder = the folder that lists the asset (a USD package is listed by its parent folder),
    # pkg = the package folder of a USD package, stack / channel = PBR set key and channel of an
    # image that has partners in its folder, gkey = one stack's identity (root, folder, key).
    VERSION = 6

    def _migrate(self, db):
        version = db.execute('PRAGMA user_version').fetchone()[0]
        if version >= self.VERSION:
            return
        if version < 5:
            # Houdini's texture caches (photo.hdr.rat ...) were indexed as textures before 0.18.1.
            # Back the index up first (before any other change here), then drop them and their info.
            caches = [r[0] for r in db.execute(
                "SELECT id, relpath FROM assets WHERE lower(relpath) LIKE '%.rat' OR lower(relpath) LIKE '%.tx'")
                if is_texture_cache(r[1].rpartition('/')[2])]
            if caches:
                from datetime import datetime
                folder = self.data_dir / 'backups' / 'index'
                folder.mkdir(parents=True, exist_ok=True)
                target = sqlite3.connect(str(folder / ('index_backup_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '_before_v5.sqlite3')))
                try:
                    db.backup(target)
                finally:
                    target.close()
                for start in range(0, len(caches), 500):
                    chunk = caches[start:start + 500]
                    marks = ','.join('?' * len(chunk))
                    db.execute('DELETE FROM assets WHERE id IN (%s)' % marks, chunk)
                    db.execute('DELETE FROM info WHERE id IN (%s)' % marks, chunk)
                if version >= 3:   # PBR stacks may have counted a cache as a member: derive them again
                    rows = db.execute('SELECT id, root_id, relpath, kind FROM assets').fetchall()
                    derived = self.derive([(r['root_id'], r['relpath'], r['kind']) for r in rows])
                    db.executemany('UPDATE assets SET folder=?, pkg=?, stack=?, channel=?, gkey=? WHERE id=?',
                                   [d + (r['id'],) for d, r in zip(derived, rows)])
        if version < 3:
            have = {r[1] for r in db.execute('PRAGMA table_info(assets)')}
            for name in ('folder', 'pkg', 'stack', 'channel', 'gkey'):
                if name not in have:
                    db.execute('ALTER TABLE assets ADD COLUMN %s TEXT' % name)
            db.execute('CREATE INDEX IF NOT EXISTS asset_gkey ON assets(gkey)')
            rows = db.execute('SELECT id, root_id, relpath, kind FROM assets').fetchall()
            derived = self.derive([(r['root_id'], r['relpath'], r['kind']) for r in rows])
            db.executemany('UPDATE assets SET folder=?, pkg=?, stack=?, channel=?, gkey=? WHERE id=?',
                           [d + (r['id'],) for d, r in zip(derived, rows)])
        if version < 6:
            # vstamp: the file state ("mtime:size") whose variant sets last set the automatic
            # "lod" / "variant" tags (variant_scan.py), so only new or changed USD files are opened.
            if 'vstamp' not in {r[1] for r in db.execute('PRAGMA table_info(assets)')}:
                db.execute('ALTER TABLE assets ADD COLUMN vstamp TEXT')
        # The list order, as one index the panel can page through with a key (see EntryStream).
        db.execute('DROP INDEX IF EXISTS asset_order')
        db.execute('CREATE INDEX asset_order ON assets(label COLLATE NOCASE, relpath, root_id)')
        db.execute('PRAGMA user_version=%d' % self.VERSION)

    @staticmethod
    def derive(items):
        """[(folder, pkg, stack, channel, gkey)] for [(root_id, relpath, kind)].

        Images of one PBR set stack only when there are two or more and no channel repeats.
        """
        result, groups = [], {}
        for index, (root_id, relpath, kind) in enumerate(items):
            parent = relpath.rpartition('/')[0]
            pkg = parent if is_usd_package({'relpath': relpath, 'kind': kind}) else ''
            result.append([parent.rpartition('/')[0] if pkg else parent, pkg, None, None, None])
            info = pbr.stack_info(relpath) if kind == 'texture' else None
            if info:
                groups.setdefault((root_id, info[0], info[1]), []).append((index, info[3]))
        for (root_id, folder, key), members in groups.items():
            channels = [c for _, c in members]
            if len(members) >= 2 and len(set(channels)) == len(channels):
                for index, channel in members:
                    result[index][2:] = [key, channel, root_id + '|' + folder + '|' + key]
        return [tuple(r) for r in result]

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
            db.execute("DELETE FROM info WHERE id NOT IN (SELECT id FROM assets)")
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
                    # Several USD layers without an entry file named after the folder (e.g. Big /
                    # Small sharing ./textures): each is an asset, and like a package the folder's
                    # subfolders are its files, not library folders. Loose layers right under USD/
                    # stay unlisted, as before.
                    layers = [name for name in names if name.lower().endswith(USD_LAYERS)] if len(parts) > 1 else []
                    if layers:
                        dirs[:] = []
                    names = [name for name in names if name.lower().endswith('.usdz')] + layers
                # A generation job's output before it replaces its source (ui._MeshGenerateJob).
                names = [name for name in names if GENERATE_TMP not in name.lower()]
            for name in names:
                if cancel():
                    return {"cancelled": True, "count": 0, "errors": errors}
                lowered = name.lower()
                ext = os.path.splitext(lowered)[1]
                kind = ('usd' if genre == 'USD' else
                        'texture' if genre == 'Texture' and ext in IMAGES and not is_texture_cache(lowered) else
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
            derived = self.derive([(r[1], r[2], r[4]) for r in rows])
            db.executemany('''INSERT INTO assets (id,root_id,relpath,label,kind,size,mtime,folder,pkg,stack,channel,gkey)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(root_id,relpath) DO UPDATE SET
                kind=excluded.kind,override_kind=NULL,size=excluded.size,mtime=excluded.mtime,present=1,
                folder=excluded.folder,pkg=excluded.pkg,stack=excluded.stack,channel=excluded.channel,gkey=excluded.gkey''',
                [r + d for r, d in zip(rows, derived)])
        return {"cancelled": False, "count": len(rows), "errors": errors}

    def info(self, aid, stamp):
        """Statistics read earlier for this asset (resolution, polygons...), if the file is unchanged."""
        with self.connect() as db:
            row = db.execute("SELECT data FROM info WHERE id=? AND stamp=?", (aid, stamp)).fetchone()
        return json.loads(row[0]) if row else None

    def save_info(self, aid, stamp, data):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO info VALUES (?,?,?)", (aid, stamp, json.dumps(data)))

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
                row = db.execute("SELECT kind, stack FROM assets WHERE id=?", (change['id'],)).fetchone()
                if row:
                    folder, pkg = self.derive([(root_id, change['relpath'], row['kind'])])[0][:2]
                    gkey = root_id + '|' + folder.lower() + '|' + row['stack'] if row['stack'] else None
                    db.execute("UPDATE assets SET folder=?, pkg=?, gkey=? WHERE id=?", (folder, pkg, gkey, change['id']))
            for old, new in folder_moves:
                db.execute("UPDATE OR REPLACE folders SET relpath=? || substr(relpath, ?) WHERE root_id=? AND (relpath=? OR substr(relpath, 1, ?)=?)",
                           (new, len(old) + 1, root_id, old, len(old) + 1, old + '/'))

    def _where(self, search="", root_id=None, kind=None, favorite=False, missing=False, folder=None,
               recursive=True, listed_only=False):
        clauses, args = [], []
        if folder:
            if recursive:
                # Everything below a folder, as an index range on (root_id, relpath): '/' + 1 is '0'.
                clauses.append("a.relpath >= ? AND a.relpath < ?"); args.extend([folder.rstrip('/') + '/', folder.rstrip('/') + '0'])
            else:
                clauses.append("(a.folder=? OR a.pkg=?)"); args.extend([folder.rstrip('/')] * 2)
        if not missing:
            clauses.append("a.present=1")
        if root_id:
            clauses.append("a.root_id=?"); args.append(root_id)
        if kind:
            clauses.append("a.kind=?"); args.append(kind)
        elif listed_only:
            clauses.append("a.kind IN ('usd','model','texture')")   # older indexes may hold retired kinds
        if favorite:
            clauses.append("a.favorite=1")
        for word in search.split():
            clauses.append("(a.label LIKE ? ESCAPE '\\' OR a.relpath LIKE ? ESCAPE '\\' OR a.tags LIKE ? ESCAPE '\\')")
            word = word.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            args.extend(['%' + word + '%'] * 3)
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", args

    def assets(self, search="", root_id=None, kind=None, favorite=False, missing=False, folder=None):
        where, args = self._where(search, root_id, kind, favorite, missing, folder)
        sql = '''SELECT a.*, r.path AS root_path, r.label AS root_label,
                 a.kind AS effective_kind
                 FROM assets a JOIN roots r ON r.id=a.root_id''' + where + " ORDER BY a.label COLLATE NOCASE, a.relpath"
        with self.connect() as db:
            return [dict(r) for r in db.execute(sql, args)]

    def count(self, **filters):
        """Number of assets the panel lists for these filters (same keywords as stream)."""
        where, args = self._where(listed_only=True, **filters)
        with self.connect() as db:
            return db.execute("SELECT COUNT(*) FROM assets a" + where, args).fetchone()[0]

    def stream(self, stacked=True, **filters):
        """Lazy, ordered list entries for the panel; see EntryStream."""
        return EntryStream(self, stacked, filters)

    def iter_rows(self, batch=500, **filters):
        """Every listed asset for the filters, in slices, on its own connection (no stacking)."""
        stream = EntryStream(self, False, filters)
        try:
            while True:
                entries = stream.next(batch)
                if not entries:
                    return
                yield [e['rep'] for e in entries]
        finally:
            stream.close()

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

    # Bumped when variant_scan.py starts reading something new (p1: purpose=proxy geometry, p2:
    # animation), so every USD is checked once more.
    STAMP_VERSION = 'p2'

    @staticmethod
    def file_stamp(row):
        return '%s:%r:%d' % (Library.STAMP_VERSION, float(row['mtime'] or 0), int(row['size'] or 0))

    def variant_check_rows(self):
        """Present USD assets whose file changed since its variant sets last set the automatic tags."""
        with self.connect() as db:
            rows = db.execute('''SELECT a.id, a.relpath, a.mtime, a.size, a.vstamp, r.path AS root_path
                FROM assets a JOIN roots r ON r.id=a.root_id WHERE a.kind='usd' AND a.present=1''').fetchall()
        return [dict(r) for r in rows if r['vstamp'] != self.file_stamp(r)]

    def save_variant_tags(self, updates):
        """Apply [(id, variant set names or None, stamp[, has proxy[, has anim]])] from variant_scan.py: the
        automatic tags follow the file (None: it could not be read - only the stamp is kept, so it
        is not retried until it changes). Tags are read and written in one transaction, so a tag
        the user edits meanwhile is kept. Returns how many assets' tags changed."""
        changed = 0
        with self.connect() as db:
            for aid, names, stamp, *rest in updates:
                row = db.execute('SELECT tags FROM assets WHERE id=?', (aid,)).fetchone()
                if row is None:
                    continue
                tags = row['tags'] if names is None else sync_auto_tags(row['tags'], names, *rest[:2])
                changed += tags != row['tags']
                db.execute('UPDATE assets SET tags=?, vstamp=? WHERE id=?', (tags, stamp, aid))
        return changed

    def backup_index(self):
        from datetime import datetime
        folder = self.data_dir / 'backups' / 'index'
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
        if p.is_file() and p.suffix.lower() in IMAGES and not is_texture_cache(p.name):
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


class EntryStream:
    """Ordered list entries read on demand, so a huge result costs only what is shown.

    Entries follow the label index. Each read asks for the next batch after the last key
    (label, relpath, root) on a short-lived connection, so nothing stays open between reads: the
    panel always sees current data, scans and moves are never blocked, and the database file is
    never held. The first member of a PBR stack pulls in the rest of that stack (with the same
    filters); the stream skips them later.
    """
    BATCH = 128
    ORDER = " ORDER BY a.label COLLATE NOCASE, a.relpath, a.root_id"

    def __init__(self, library, stacked, filters):
        self.library = library
        self.stacked = stacked
        self.roots = {r['id']: r for r in library.roots()}
        filters = dict(filters)
        if len(self.roots) < 2:
            filters['root_id'] = None   # one library: no filter, so the label index can stream the order
        self.where, self.args = library._where(listed_only=True, **filters)
        self.after = None
        self.taken = set()
        self.pending = []
        self.done = False

    def _row(self, raw):
        row = dict(raw)
        root = self.roots.get(row['root_id'])
        row['root_path'], row['root_label'] = (root['path'], root['label']) if root else ('', '')
        row['effective_kind'] = row['kind']
        return row

    def _batch(self, db):
        where, args = self.where, list(self.args)
        if self.after is not None:
            where += (" AND " if where else " WHERE ") + "(a.label COLLATE NOCASE, a.relpath, a.root_id) > (?, ?, ?)"
            args += list(self.after)
        return db.execute("SELECT a.* FROM assets a" + where + self.ORDER + " LIMIT %d" % self.BATCH, args).fetchall()

    def _members(self, db, gkeys):
        """Members of every stack in `gkeys` (same filters), in one query for the whole batch.

        A query (and connection) per stack used to be most of the cost of a stacked refresh."""
        grouped = {key: [] for key in gkeys}
        if not gkeys:
            return grouped
        where = (self.where + " AND " if self.where else " WHERE ") + "a.gkey IN (%s)" % ','.join('?' * len(gkeys))
        for r in db.execute("SELECT a.* FROM assets a" + where + self.ORDER, self.args + list(gkeys)):
            grouped[r['gkey']].append(self._row(r))
        return grouped

    def next(self, count):
        """Exactly `count` more entries (a stack counts as one), fewer at the end of the result."""
        entries, self.pending = self.pending[:count], self.pending[count:]
        if len(entries) < count and not self.done:
            with self.library.connect() as db:   # one short-lived connection per read
                self._fill(db, entries, count)
        return entries

    def _fill(self, db, entries, count):
        while len(entries) < count and not self.done:
            raw = self._batch(db)
            if not raw:
                self.done = True
                break
            self.after = (raw[-1]['label'], raw[-1]['relpath'], raw[-1]['root_id'])
            if len(raw) < self.BATCH:
                self.done = True
            found = []
            members_of = self._members(db, list(dict.fromkeys(
                r['gkey'] for r in raw if self.stacked and r['gkey'] and r['id'] not in self.taken)))
            for record in raw:
                if record['id'] in self.taken:
                    continue
                row = self._row(record)
                if self.stacked and row['gkey']:
                    members = members_of[row['gkey']]
                    self.taken.update(m['id'] for m in members)
                    found.extend(pbr.group_entries(members))
                else:
                    found.append({'rows': [row], 'rep': row, 'label': row['label'], 'channels': []})
            room = count - len(entries)
            entries.extend(found[:room])
            self.pending.extend(found[room:])

    @property
    def finished(self):
        return self.done and not self.pending

    def close(self):
        self.done = True
        self.pending = []
