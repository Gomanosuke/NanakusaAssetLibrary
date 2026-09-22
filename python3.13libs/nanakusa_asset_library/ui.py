"""Dockable Python Panel and floating window for Houdini 22 / PySide6."""
from collections import OrderedDict, deque
from datetime import datetime
from pathlib import Path
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
import traceback
from hutil.PySide import QtCore, QtGui, QtWidgets
import hou
from . import core, houdini_ops as ops, dragdrop, storage, organize, pbr, embedded, proxy_gen, element_gen

ROLE = QtCore.Qt.ItemDataRole.UserRole
STACK_ROLE = dragdrop.STACK_ROLE   # ids of the assets a list item stands for
LAZY_ROLE = QtCore.Qt.ItemDataRole.UserRole + 2   # folder item whose children are not created yet
KINDS = {'': 'All Types', 'usd': 'USD', 'model': '3DModel', 'texture': 'Texture'}
_windows = []


# CREATE_NO_WINDOW's own priority flags (e.g. BELOW_NORMAL_PRIORITY_CLASS) only lower CPU
# scheduling; Windows' background process mode lowers CPU, disk I/O and memory priority together,
# so a long queue of proxy jobs or a scan does not make the panel's own reads compete for the
# disk (observed live: the panel froze for several seconds selecting an asset while a hundreds-
# strong proxy queue was running). Undocumented-but-tested: combining it with an explicit
# priority class did not error, but only PROCESS_MODE_BACKGROUND_BEGIN is Microsoft-documented
# as safe on its own, so that is all this passes.
PROCESS_MODE_BACKGROUND_BEGIN = 0x00100000

def background():
    """Popen / run keywords for helper processes: hidden, and at Windows' background priority so a
    scan, a thumbnail render, or a proxy queue never competes with the panel's own reads."""
    if os.name == 'nt':
        return {'creationflags': subprocess.CREATE_NO_WINDOW | PROCESS_MODE_BACKGROUND_BEGIN}
    return {'preexec_fn': lambda: os.nice(10)}
_jobs = set()  # Keep background workers alive when a pane is closed mid-scan.

class AssetInfoJob(QtCore.QThread):
    done=QtCore.Signal(object,object)

    def __init__(self,key,path,kind):
        super().__init__();self.key,self.path,self.kind=key,path,kind
        self.hfs=hou.getenv('HFS')   # hou is only used on the main thread

    def run(self):
        result={}
        try:
            executable=Path(self.hfs)/'bin'/('hython.exe' if os.name=='nt' else 'hython')
            process=subprocess.Popen([str(executable),str(Path(__file__).with_name('asset_info.py')),str(self.path),self.kind],
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,**background())
            started=time.monotonic()
            try:
                while True:
                    if self.isInterruptionRequested():return
                    if time.monotonic()-started>60:raise RuntimeError('Information read timed out')
                    try:
                        out,err=process.communicate(timeout=.1);break
                    except subprocess.TimeoutExpired:pass
                lines=[line[9:] for line in out.decode('utf-8',errors='replace').splitlines() if line.startswith('NAL_INFO:')]
                if not lines:raise RuntimeError(err.decode(errors='replace')[-500:] or 'Could not read asset information')
                result=json.loads(lines[-1])
            finally:
                if process.poll() is None:process.kill();process.communicate()
        except Exception as exc:result={'error':str(exc)}
        self.done.emit(self.key,result)


class LargePreview(QtWidgets.QWidget):
    def __init__(self,parent=None):
        super().__init__(parent);self.pixmap=None
        self.setMinimumSize(180,180)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,QtWidgets.QSizePolicy.Policy.Expanding)

    def sizeHint(self):return QtCore.QSize(400,400)

    def setPixmap(self,pixmap):self.pixmap=pixmap;self.update()

    def clear(self):self.pixmap=None;self.update()

    def paintEvent(self,event):
        painter=QtGui.QPainter(self)
        if self.pixmap is None:
            painter.drawText(self.rect(),QtCore.Qt.AlignmentFlag.AlignCenter,'Select an asset');return
        edge=min(self.width(),self.height())
        target=QtCore.QRect((self.width()-edge)//2,(self.height()-edge)//2,edge,edge)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawPixmap(target,self.pixmap)

def square_preview(path, edge):
    """Fit without stretching, cropping or baking black bars into the image."""
    reader=QtGui.QImageReader(str(path))
    size=reader.size()
    if size.isValid():
        reader.setScaledSize(size.scaled(edge,edge,QtCore.Qt.AspectRatioMode.KeepAspectRatio))
    image=reader.read()
    if image.isNull():return None
    image=image.scaled(edge,edge,QtCore.Qt.AspectRatioMode.KeepAspectRatio,QtCore.Qt.TransformationMode.SmoothTransformation)
    pix=QtGui.QPixmap(edge,edge);pix.fill(QtCore.Qt.GlobalColor.transparent)
    painter=QtGui.QPainter(pix)
    painter.drawImage((edge-image.width())//2,(edge-image.height())//2,image);painter.end()
    return pix

class ScanJob(QtCore.QThread):
    done = QtCore.Signal(object)
    def __init__(self, library, roots):
        super().__init__()
        self.library, self.roots = library, roots
        self.python = self.find_python(hou.getenv('HFS'))   # looked up here: hou is only used on the main thread

    @staticmethod
    def find_python(home):
        """The Python that ships with Houdini, for work that should not share the panel's interpreter."""
        home = home or ''
        for candidate in (Path(home)/'python313'/'python.exe', Path(home)/'python'/'bin'/'python3.13', Path(home)/'python'/'bin'/'python3'):
            if home and candidate.is_file():
                return candidate
        return None

    def scan_root(self, root):
        python = self.python
        if python is None:   # no separate interpreter available: scan in this thread
            return self.library.scan(root['id'], self.isInterruptionRequested)
        process = subprocess.Popen([str(python), str(Path(__file__).with_name('scan_worker.py')), str(self.library.data_dir), root['id']],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, **background())
        try:
            while True:
                if self.isInterruptionRequested():
                    return {'cancelled': True, 'count': 0, 'errors': []}
                try:
                    out, err = process.communicate(timeout=.1)
                    break
                except subprocess.TimeoutExpired:
                    pass
        finally:
            if process.poll() is None:
                process.kill(); process.communicate()
        lines = [line[9:] for line in out.decode('utf-8', errors='replace').splitlines() if line.startswith('NAL_SCAN:')]
        if not lines:
            raise RuntimeError(err.decode(errors='replace')[-500:] or 'The scan process failed')
        return json.loads(lines[-1])

    def run(self):
        results = []
        for root in self.roots:
            if self.isInterruptionRequested():
                break
            try:
                result = self.scan_root(root)
                results.append((root['label'], result))
            except Exception as exc:
                results.append((root['label'], {'count': 0, 'errors': [str(exc)]}))
        self.done.emit(results)

class ThumbnailJob(QtCore.QThread):
    done = QtCore.Signal(str, str, str)
    def __init__(self, asset_id, source, dest):
        super().__init__()
        self.asset_id = asset_id
        self.source, self.dest = str(source), str(dest)
        self.exe = str(Path(hou.getenv('HFS')) / 'bin' / ('hoiiotool.exe' if os.name == 'nt' else 'hoiiotool'))

    def run(self):
        try:
            # Run image decoding outside Houdini's UI thread; no shell interpolation.
            args=[self.exe,'--threads','4',self.source,'--fit:pad=0','512x512','--origin','+0+0','--fullpixels']
            if Path(self.source).suffix.lower() in ('.hdr','.exr'):
                args += ['--colorconvert','lin_rec709','srgb_texture']
            temporary = str(Path(self.dest).with_suffix('.pending.png'))
            Path(self.dest).parent.mkdir(parents=True, exist_ok=True)
            args += ['-d','uint8','-o',temporary]
            result = subprocess.run(args,
                capture_output=True, timeout=120, **background())
            if result.returncode:
                raise RuntimeError(result.stderr.decode(errors='replace')[-1500:])
            image = QtGui.QImage(temporary)
            if image.isNull():
                raise RuntimeError('Could not decode thumbnail')
            os.replace(temporary,self.dest)
            self.done.emit(self.asset_id, self.dest, '')
        except Exception as exc:
            self.done.emit(self.asset_id, '', str(exc))

class GeometryThumbnailJob(QtCore.QThread):
    done = QtCore.Signal(str, str, str)
    def __init__(self, asset_id, source, kind, dest):
        super().__init__()
        self.asset_id, self.source, self.kind, self.dest = asset_id, str(source), kind, Path(dest)
        self.bin = Path(hou.getenv('HFS'))/'bin'

    def execute(self, args, log):
        with log.open('wb') as stream:
            process = subprocess.Popen(args, stdout=stream, stderr=stream, **background())
            started = time.monotonic()
            try:
                while process.poll() is None:
                    if self.isInterruptionRequested():
                        raise RuntimeError('Generation cancelled')
                    if time.monotonic()-started > 240:
                        raise RuntimeError('Thumbnail generation timed out')
                    self.msleep(100)
            finally:
                if process.poll() is None:
                    process.terminate()
                    try: process.wait(timeout=5)
                    except subprocess.TimeoutExpired: process.kill(); process.wait()
        if process.returncode:
            raise RuntimeError(log.read_text(encoding='utf-8', errors='replace')[-2000:])

    def run(self):
        try:
            with tempfile.TemporaryDirectory(prefix='nanakusa_preview_') as folder:
                base = Path(folder)
                suffix = '.exe' if os.name == 'nt' else ''
                self.execute([str(self.bin/('hython'+suffix)), str(Path(__file__).with_name('thumbnail_scene.py')),
                    self.source, self.kind, str(base/'scene.usda'), str(base/'camera.json')], base/'prepare.log')
                info = json.loads((base/'camera.json').read_text(encoding='utf-8'))
                self.execute([str(self.bin/('husk'+suffix)), '--renderer', 'BRAY_HdKarma', '--engine', 'cpu',
                    '--threads', '4', '--pixel-samples', '16', '--res', '512', '512',
                    '--camera', info['camera'], '--frame', str(info['frame']), '--disable-motionblur',
                    '--disable-delegate-products', '--disable-slapcomp', '--timelimit', '180',
                    '--output', str(base/'render.exr'), str(base/'scene.usda')], base/'render.log')
                self.execute([str(self.bin/('hoiiotool'+suffix)), '--threads', '2', str(base/'render.exr'),
                    '--colorconvert', 'lin_rec709', 'srgb_texture', '-d', 'uint8', '-o', str(base/'preview.png')], base/'convert.log')
                if QtGui.QImage(str(base/'preview.png')).isNull():
                    raise RuntimeError('Could not decode the rendered image')
                temporary = self.dest.with_suffix('.tmp')
                temporary.write_bytes((base/'preview.png').read_bytes())
                os.replace(temporary, self.dest)
            self.done.emit(self.asset_id, str(self.dest), '')
        except Exception as exc:
            self.done.emit(self.asset_id, '', str(exc))

class _MeshGenerateJob(QtCore.QThread):
    """Shared plumbing for ProxyJob and ElementJob.

    Generation runs in a separate hython process (a worker script named by the `script` class
    attribute) and only ever writes to a temporary file next to the source; the source asset is
    backed up and swapped in here only after that succeeds, so a crash or a locked file never
    leaves the asset half-written. The swap uses os.replace, which Windows refuses across drives,
    so the temp file lives beside the source rather than in the system TEMP directory.
    """
    done = QtCore.Signal(str, str, str)   # asset_id, message (skip reason or summary), error
    script = ''         # set by the subclass: worker script filename, next to this file
    label = 'Generation'   # set by the subclass: used in cancelled/timeout/no-output messages

    def __init__(self, asset_id, source, backup_dir, extra_args=()):
        super().__init__()
        self.asset_id, self.source, self.backup_dir = asset_id, str(source), Path(backup_dir)
        self.extra_args = [str(a) for a in extra_args]
        self.bin = Path(hou.getenv('HFS'))/'bin'

    def summary(self, result):
        raise NotImplementedError

    def run(self):
        source = Path(self.source)
        temp_usd = source.with_name(source.stem+'.nanakusa_generate_tmp'+source.suffix)
        try:
            with tempfile.TemporaryDirectory(prefix='nanakusa_generate_') as folder:
                base = Path(folder)
                suffix = '.exe' if os.name == 'nt' else ''
                result_path = base/'result.json'
                log = base/'generate.log'
                with log.open('wb') as stream:
                    process = subprocess.Popen([str(self.bin/('hython'+suffix)), str(Path(__file__).with_name(self.script)),
                        self.source, str(temp_usd), str(result_path), *self.extra_args], stdout=stream, stderr=stream, **background())
                    started = time.monotonic()
                    try:
                        while process.poll() is None:
                            if self.isInterruptionRequested():
                                raise RuntimeError(self.label+' cancelled')
                            if time.monotonic()-started > 240:
                                raise RuntimeError(self.label+' timed out')
                            self.msleep(100)
                    finally:
                        if process.poll() is None:
                            process.terminate()
                            try: process.wait(timeout=5)
                            except subprocess.TimeoutExpired: process.kill(); process.wait()
                if process.returncode:
                    raise RuntimeError(log.read_text(encoding='utf-8', errors='replace')[-2000:])
                result = json.loads(result_path.read_text(encoding='utf-8'))
                if result.get('skipped'):
                    self.done.emit(self.asset_id, result['skipped'], '')
                    return
                if not temp_usd.is_file() or temp_usd.stat().st_size == 0:
                    raise RuntimeError(self.label+' produced no output')
                self.backup_dir.mkdir(parents=True, exist_ok=True)
                backup = self.backup_dir/(source.stem+'_backup_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+source.suffix)
                shutil.copy2(self.source, backup)
                os.replace(str(temp_usd), self.source)
            self.done.emit(self.asset_id, self.summary(result), '')
        except Exception as exc:
            self.done.emit(self.asset_id, '', str(exc))
        finally:
            if temp_usd.is_file():
                try: temp_usd.unlink()
                except OSError: pass


class ProxyJob(_MeshGenerateJob):
    """Add a decimated purpose=proxy sibling to each render mesh of a USD file (proxy_gen.py)."""
    script, label = 'proxy_gen.py', 'Proxy generation'
    def __init__(self, asset_id, source, backup_dir, target_triangles):
        super().__init__(asset_id, source, backup_dir, (target_triangles,))
    def summary(self, result):
        return f"Proxy added ({len(result['proxied'])} mesh(es))"


class ElementJob(_MeshGenerateJob):
    """Add an "element" variant set to a multi-object USD pack so only one shows (element_gen.py)."""
    script, label = 'element_gen.py', 'Element switch generation'
    def __init__(self, asset_id, source, backup_dir):
        super().__init__(asset_id, source, backup_dir)
    def summary(self, result):
        return f"Element switch added ({len(result['element_switch']['variants'])} elements)"


class ElementDeleteJob(_MeshGenerateJob):
    """Undo ElementJob: remove a previously-added "element" variant set (element_gen.py remove mode)."""
    script, label = 'element_gen.py', 'Element switch removal'
    def __init__(self, asset_id, source, backup_dir):
        super().__init__(asset_id, source, backup_dir, ('remove',))
    def summary(self, result):
        return f"Element switch removed ({len(result['removed_element_switch'])} prim(s))"


class FolderMigrationJob(QtCore.QThread):
    """One-time folder listing for an index made before folders were recorded by the scan.

    Walking a large asset tree can take a while; doing it here keeps that walk off the UI thread
    (see LibraryWidget.folder_paths, which used to do this walk inline and could freeze the panel).
    """
    done = QtCore.Signal(str)
    def __init__(self, library, root):
        super().__init__()
        self.library, self.root = library, root

    def run(self):
        # Always emit done, even on failure or cancellation, so the widget's bookkeeping
        # (folder_migrations) never gets stuck thinking a migration is still in flight.
        try:
            rels = core.visible_folders(self.root['path'], cancel=self.isInterruptionRequested)
            if not self.isInterruptionRequested():
                self.library.set_folders(self.root['id'], rels)
        except (OSError, sqlite3.Error):
            pass
        finally:
            self.done.emit(self.root['id'])


def _keep_job(job):
    _jobs.add(job)
    job.finished.connect(lambda: _jobs.discard(job))
    job.start()

class ManifestDialog(QtWidgets.QDialog):
    def __init__(self, folder, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Register PBR / Decal Set')
        self.resize(700, 440)
        layout = QtWidgets.QVBoxLayout(self)
        note = QtWidgets.QLabel('Review the suggested maps. Source images are unchanged.\nUse OpenGL normals. Convert packed ORM and DirectX normals first.')
        note.setWordWrap(True); layout.addWidget(note)
        form = QtWidgets.QFormLayout(); layout.addLayout(form)
        self.name = QtWidgets.QLineEdit(Path(folder).name)
        form.addRow('Set Name', self.name)
        self.kind = QtWidgets.QComboBox(); self.kind.addItems(['PBR', 'Decal'])
        form.addRow('Type', self.kind)
        self.color = QtWidgets.QComboBox(); self.color.addItems(['srgb_texture', 'ACEScg', 'lin_rec709', 'Raw'])
        form.addRow('Color Image Space', self.color)
        self.maps = {}
        suggestions = core.suggest_maps(folder)
        for channel in core.MAP_ALIASES:
            row = QtWidgets.QHBoxLayout()
            field = QtWidgets.QLineEdit(suggestions.get(channel, ''))
            button = QtWidgets.QPushButton('…'); button.setMaximumWidth(34)
            button.clicked.connect(lambda checked=False, f=field: self.browse(f, folder))
            row.addWidget(field); row.addWidget(button)
            form.addRow(channel, row); self.maps[channel] = field
        self.scale = QtWidgets.QDoubleSpinBox(); self.scale.setDecimals(5); self.scale.setRange(-100,100); self.scale.setValue(.01)
        form.addRow('Displacement scale', self.scale)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Save | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def browse(self, field, folder):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, 'Select Image', str(folder))
        if path:
            field.setText(path)

class LibraryWidget(QtWidgets.QWidget):
    CHUNK = 200   # items added each time the list is scrolled near its end
    def __init__(self, parent=None, data_dir=None, initial_root=None):
        super().__init__(parent)
        self.setObjectName('NanakusaAssetLibrary')
        self.data_dir = Path(data_dir or storage.default_data_dir())
        self.library = core.Library(self.data_dir)
        config_path = self.data_dir / 'settings.json'
        self.settings = json.loads(config_path.read_text(encoding='utf-8-sig')) if config_path.exists() else {}
        self.default_root = initial_root or os.environ.get('NAL_ASSET_ROOT') or self.settings.get('publish_root', '')
        if self.default_root and not self.library.roots() and Path(self.default_root).is_dir():
            self.library.add_root(self.default_root)
        self.job = None
        self.rows = []
        self.thumb_queue = deque()
        self.thumb_pending = set()
        self.thumb_failed = set()
        self.thumb_job = None
        self.proxy_queue = deque()
        self.proxy_pending = set()
        self.proxy_failed = set()
        self.proxy_job = None
        self.missing_proxy_scan = None
        self.element_queue = deque()
        self.element_pending = set()
        self.element_failed = set()
        self.element_job = None
        self.element_delete_queue = deque()
        self.element_delete_pending = set()
        self.element_delete_failed = set()
        self.element_delete_job = None
        self.info_job=None;self.info_pending=None;self.info_key=None;self.info_cache={};self.info_ids={}
        self.metadata_id=None;self.metadata_ids=[];self.pending_tags=None;self.folder_items={};self.folder_kids={};self.folder_roots={};self.folder_migrations=set();self.folder_migration_jobs={};self.row_index={};self.entry_of={};self.item_index={};self.member_index={};self.stream=None;self.total=0;self.icon_cache={};self.no_embedded=set();self.preview_cache=OrderedDict();self.placeholders={}
        self.icon_todo=deque();self.icons_loaded=set();self.page_entries=[];self.scroll_timer=QtCore.QTimer(self);self.scroll_timer.setSingleShot(True);self.scroll_timer.setInterval(30);self.scroll_timer.timeout.connect(self.scrolled);self.icon_timer=QtCore.QTimer(self);self.icon_timer.setSingleShot(True);self.icon_timer.timeout.connect(self.load_icons)
        self.info_wanted=None;self.info_timer=QtCore.QTimer(self);self.info_timer.setSingleShot(True);self.info_timer.setInterval(250);self.info_timer.timeout.connect(self.start_info)
        self.missing_scan=None
        dragdrop.install()
        self._setup()
        self.rebuild_tree()
        self.refresh()

    def _button(self, text, callback, layout):
        button = QtWidgets.QPushButton(text)
        button.clicked.connect(lambda checked=False: self.safe(callback))
        layout.addWidget(button)
        return button

    def closeEvent(self,event):
        for timer in (self.scroll_timer,self.icon_timer,self.info_timer,self.folder_timer,self.search_timer):timer.stop()
        self.missing_scan=None;self.close_stream()
        # A folder migration only reads/writes the index; stop it and wait briefly so it never
        # outlives a data directory that is about to be moved or removed (as in tests' temp dirs).
        for job in list(self.folder_migration_jobs.values()):job.requestInterruption();job.wait(2000)
        super().closeEvent(event)

    def event(self,event):
        # Houdini's Python Panel can retain keyboard focus on the root widget.
        if event.type()==QtCore.QEvent.Type.ShortcutOverride and event.matches(QtGui.QKeySequence.StandardKey.SelectAll):
            event.accept();return True
        return super().event(event)

    def keyPressEvent(self,event):
        if event.matches(QtGui.QKeySequence.StandardKey.SelectAll):
            self.items.selectAll();event.accept();return
        super().keyPressEvent(event)

    def safe(self, callback):
        try:
            return callback()
        except Exception as exc:
            self.status.setText('Error: ' + str(exc))
            dialog = QtWidgets.QMessageBox(self)
            dialog.setWindowTitle('Asset Library'); dialog.setText(str(exc))
            dialog.setDetailedText(traceback.format_exc()); dialog.exec()

    def _setup(self):
        outer = QtWidgets.QVBoxLayout(self)
        heading = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel('NanakusaAssetLibrary  /  Karma XPU')
        title.setStyleSheet('font-size: 17px; font-weight: bold; padding: 6px;')
        heading.addWidget(title); heading.addStretch()
        self.more=QtWidgets.QToolButton(); self.more.setText('Options'); self.more.setCheckable(True); heading.addWidget(self.more)
        self.scan_button = self._button('Rescan', self.scan, heading)
        self.cancel_button = self._button('Cancel', self.cancel_scan, heading); self.cancel_button.setVisible(False)
        outer.addLayout(heading)
        filters = QtWidgets.QHBoxLayout()
        self.search = QtWidgets.QLineEdit(); self.search.setPlaceholderText('Search names, paths, tags...')
        self.search_timer = QtCore.QTimer(self); self.search_timer.setSingleShot(True); self.search_timer.setInterval(180)
        self.search.textChanged.connect(lambda: self.search_timer.start())
        self.search_timer.timeout.connect(self.reset_page)
        self.kind = QtWidgets.QComboBox()
        for key, label in KINDS.items(): self.kind.addItem(label,key)
        self.kind.currentIndexChanged.connect(self.reset_page)
        self.favorite = QtWidgets.QCheckBox('★ Favorites'); self.favorite.toggled.connect(self.reset_page)
        self.recursive = QtWidgets.QCheckBox('Include Subfolders'); self.recursive.setChecked(True); self.recursive.toggled.connect(self.reset_page)
        self.stack = QtWidgets.QCheckBox('Stack PBR Sets'); self.stack.setChecked(bool(self.settings.get('stack_pbr',True)))
        self.stack.setToolTip('Show the images of one PBR set (albedo, roughness, normal...) as a single item. Dragging a stack drags all of its images.')
        self.stack.toggled.connect(self.stack_toggled)
        filters.addWidget(self.search,1); filters.addWidget(self.kind); filters.addWidget(self.stack); filters.addWidget(self.favorite)
        outer.addLayout(filters)
        split = QtWidgets.QSplitter(); outer.addWidget(split,1)
        left = QtWidgets.QWidget(); leftbox=QtWidgets.QVBoxLayout(left); leftbox.setContentsMargins(0,0,0,0)
        self.tree = dragdrop.FolderTree(self.can_drop); self.tree.setHeaderLabel('Libraries / Folders')
        self.tree.setToolTip('Drop assets or folders here to move them. Ctrl / Shift-click selects several folders; drag them onto another folder to nest them.')
        self.tree.dropped.connect(self.tree_dropped)
        self.tree.itemExpanded.connect(self.populate)
        # Listing a folder can take a while; doing it inside the mouse press would freeze a drag
        # that starts on the same folder. Wait briefly, and never while a drag is running.
        self.folder_timer=QtCore.QTimer(self); self.folder_timer.setSingleShot(True); self.folder_timer.setInterval(120)
        self.folder_timer.timeout.connect(self.reset_page); self.folder_dirty=False
        self.tree.currentItemChanged.connect(lambda *a:self.folder_timer.start())
        self.tree.dragStarted.connect(self.folder_drag_started); self.tree.dragFinished.connect(self.folder_drag_finished)
        leftbox.addWidget(self.tree,1)
        folderbuttons=QtWidgets.QHBoxLayout()
        self._button('Libraries...', self.root_menu, folderbuttons); leftbox.addLayout(folderbuttons)
        leftbox.addWidget(QtWidgets.QLabel('Catalog'))
        self.catalogs=QtWidgets.QComboBox()
        self.catalogs.setToolTip('USD registration target. Right-click to open, select or create a catalog.')
        self.catalogs.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.catalogs.customContextMenuRequested.connect(self.catalog_menu)
        self.catalogs.currentIndexChanged.connect(self.catalog_changed)
        leftbox.addWidget(self.catalogs)
        split.addWidget(left)
        center=QtWidgets.QWidget(); centerbox=QtWidgets.QVBoxLayout(center); centerbox.setContentsMargins(0,0,0,0)
        self.items = dragdrop.AssetList(self.library); self.items.expand=self.item_rows; self.items.folder_tree=self.tree; self.items.setViewMode(QtWidgets.QListView.ViewMode.IconMode)
        self.items.setResizeMode(QtWidgets.QListView.ResizeMode.Adjust)
        self.items.setMovement(QtWidgets.QListView.Movement.Static)
        self.items.setDragEnabled(True)
        self.items.setDragDropMode(QtWidgets.QAbstractItemView.DragDropMode.DragOnly)
        self.items.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.items.apply_icon_size(self.settings.get('icon_size',144)); self.items.iconSizeFinished.connect(self.icon_size_finished)
        self.items.setWordWrap(True); self.items.setSpacing(5)
        self.items.currentItemChanged.connect(self.selection_changed)
        self.items.itemSelectionChanged.connect(self.selection_changed)
        self.items.itemDoubleClicked.connect(lambda item: self.safe(self.import_selected))
        self.items.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.items.customContextMenuRequested.connect(self.asset_menu)
        centerbox.addWidget(self.items,1)
        # More items are added as the list is scrolled; there are no pages.
        self.page_label=QtWidgets.QLabel(); centerbox.addWidget(self.page_label)
        self.items.verticalScrollBar().valueChanged.connect(lambda *a:self.scroll_timer.start())
        self.items.resized.connect(lambda:self.scroll_timer.start())
        split.addWidget(center)
        details=QtWidgets.QWidget(); details.setMinimumWidth(250)
        db=QtWidgets.QVBoxLayout(details)
        self.preview=LargePreview();db.addWidget(self.preview,1)
        self.info=QtWidgets.QLabel(); self.info.setWordWrap(True); self.info.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse); db.addWidget(self.info)
        self.stats=QtWidgets.QLabel(); self.stats.setWordWrap(True);self.stats.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse);db.addWidget(self.stats)
        form=QtWidgets.QFormLayout(); db.addLayout(form)
        self.tags=QtWidgets.QLineEdit(); self.tags.setPlaceholderText('wood outdoor red'); form.addRow('Tags',self.tags)
        self.tags.setToolTip('Press Enter or leave the field to save tags for the active asset.')
        self.tags.textEdited.connect(self.tags_edited);self.tags.editingFinished.connect(lambda:self.safe(self.save_metadata))
        self.star=QtWidgets.QCheckBox('Favorite'); form.addRow('',self.star)
        self.star.toggled.connect(lambda checked:self.safe(lambda:self.save_favorite(checked)))
        self.tags.setEnabled(False);self.star.setEnabled(False)
        split.addWidget(details);split.setSizes([200,550,400]);split.setStretchFactor(1,2);split.setStretchFactor(2,1)
        self.advanced=QtWidgets.QWidget(); advanced=QtWidgets.QVBoxLayout(self.advanced); advanced.setContentsMargins(0,0,0,0)
        advanced.addWidget(self.recursive)
        destrow=QtWidgets.QHBoxLayout()
        destrow.addWidget(QtWidgets.QLabel('Import Target'))
        self.target=QtWidgets.QLineEdit('/stage'); destrow.addWidget(self.target,1)
        self.connect_input=QtWidgets.QCheckBox('Connect After Selected LOP'); self.connect_input.setChecked(True); destrow.addWidget(self.connect_input)
        self.usdmode=QtWidgets.QComboBox(); self.usdmode.addItems(['USD Reference','USD Sublayer']); destrow.addWidget(self.usdmode)
        advanced.addLayout(destrow)
        assignrow=QtWidgets.QHBoxLayout(); assignrow.addWidget(QtWidgets.QLabel('Material Prim Pattern (optional)'))
        self.assign=QtWidgets.QLineEdit(); self.assign.setPlaceholderText('/assets/chair/**'); assignrow.addWidget(self.assign,1); advanced.addLayout(assignrow)
        outer.addWidget(self.advanced); self.advanced.setVisible(False); self.more.toggled.connect(self.advanced.setVisible)
        self.status=QtWidgets.QLabel('Drop: Model / USD to a network | Texture to fields or material networks'); self.status.setWordWrap(True); outer.addWidget(self.status)

    def current_folder(self):
        item=self.tree.currentItem()
        return item.data(0,ROLE) if item else None

    def rebuild_tree(self):
        """Folder tree. Only the levels that are open exist as items; a closed folder holds a
        placeholder and gets its children when it is opened, so tens of thousands of folders cost nothing."""
        self.refresh_catalogs()
        selected=self.current_folder()
        it=QtWidgets.QTreeWidgetItemIterator(self.tree); expanded=set()
        while it.value():
            if it.value().isExpanded() and it.value().data(0,ROLE):expanded.add(it.value().data(0,ROLE))
            it+=1
        self.tree.blockSignals(True); self.tree.clear()
        self.folder_items={};self.folder_kids={};self.folder_roots={}
        allitem=QtWidgets.QTreeWidgetItem(['All Libraries']); self.tree.addTopLevelItem(allitem)
        for root in self.library.roots():
            kids={}
            for rel in sorted(self.folder_paths(root),key=str.lower):
                kids.setdefault(rel.rpartition('/')[0],[]).append(rel)
            self.folder_kids[root['id']]=kids;self.folder_roots[root['id']]=root['path']
            top=QtWidgets.QTreeWidgetItem([root['label']]); top.setData(0,ROLE,(root['id'],'',root['path'])); top.setToolTip(0,root['path'])
            self.folder_items[(root['id'],'')]=top; self.tree.addTopLevelItem(top)
            self.add_placeholder(top);self.populate(top);top.setExpanded(True)
        for value in sorted(expanded,key=lambda v:v[1].count('/')+(1 if v[1] else 0)):
            item=self.folder_items.get((value[0],value[1]))
            if item is not None:self.populate(item);item.setExpanded(True)
        chosen=allitem
        if selected:chosen=self.ensure_item(selected[0],selected[1]) or allitem
        self.tree.setCurrentItem(chosen); self.tree.blockSignals(False)

    def add_placeholder(self,item):
        item.addChild(QtWidgets.QTreeWidgetItem(['...']));item.setData(0,LAZY_ROLE,True)

    def populate(self,item):
        """Create the children of a folder the first time it is needed."""
        if not item.data(0,LAZY_ROLE):return
        item.setData(0,LAZY_ROLE,False);item.takeChildren()
        root_id,rel,_=item.data(0,ROLE)
        for child in self.folder_kids.get(root_id,{}).get(rel,[]):
            node=QtWidgets.QTreeWidgetItem([child.rpartition('/')[2]]);node.setData(0,ROLE,(root_id,child,self.folder_roots[root_id]))
            self.folder_items[(root_id,child)]=node;item.addChild(node)
            if self.folder_kids[root_id].get(child):self.add_placeholder(node)

    def ensure_item(self,root_id,rel):
        """The tree item of a folder, creating the levels above it if they are still closed."""
        item=self.folder_items.get((root_id,''))
        path=''
        for part in [p for p in rel.split('/') if p]:
            if item is None:return None
            self.populate(item)
            path=path+'/'+part if path else part
            item=self.folder_items.get((root_id,path))
        return item

    def folder_paths(self,root):
        """Folders of a library from the index; an index made by an older version has none yet.

        That first listing is filled in by a background walk (see start_folder_migration) instead
        of walking the disk here, so opening the tree never blocks on how large the library is.
        """
        rels=set(self.library.folders(root['id']))
        if not rels and Path(root['path']).is_dir():
            self.start_folder_migration(root)
        return rels|set(core.GENRES)

    def start_folder_migration(self,root):
        rid=root['id']
        if rid in self.folder_migrations:return
        self.folder_migrations.add(rid)
        job=FolderMigrationJob(self.library,root)
        job.done.connect(self.folder_migration_done)
        self.folder_migration_jobs[rid]=job
        _keep_job(job)

    def folder_migration_done(self,rid):
        self.folder_migrations.discard(rid)
        self.folder_migration_jobs.pop(rid,None)
        self.safe(self.rebuild_tree)

    def select_folder(self,value):
        item=self.ensure_item(value[0],value[1])
        if item is None:return
        parent=item.parent()
        while parent is not None:parent.setExpanded(True);parent=parent.parent()
        self.tree.setCurrentItem(item);self.tree.scrollToItem(item)

    def dragged_rows(self,ids):
        return [self.row_index[i] for i in ids if i in self.row_index]

    def can_drop(self,payload,target):
        try:
            if 'assets' in payload:organize.check_assets_target(self.dragged_rows(payload['assets']),target[0],target[1])
            else:
                folders=payload['folders']
                if any(f['root_id']!=target[0] for f in folders):return False
                for f in folders:organize.check_folder_target(target[2],f['rel'],target[1])
        except (organize.MoveError,KeyError,ValueError,OSError):return False
        return True

    def tree_dropped(self,payload,target):
        # The drag that started in the asset list is still unwinding; change the list afterwards.
        QtCore.QTimer.singleShot(0,lambda:self.safe(lambda:self.organize_drop(payload,target)))

    def organize_drop(self,payload,target):
        if self.job and self.job.isRunning():raise ValueError('Wait for the scan to finish before moving assets.')
        if self.thumb_pending:raise ValueError('Wait for thumbnail generation to finish (or use Libraries... > Cancel Thumbnails) before moving assets.')
        if self.proxy_pending:raise ValueError('Wait for proxy generation to finish (or use Libraries... > Cancel Proxies) before moving assets.')
        if self.element_pending:raise ValueError('Wait for element switch generation to finish (or use Libraries... > Cancel Element Switches) before moving assets.')
        if self.element_delete_pending:raise ValueError('Wait for element switch removal to finish before moving assets.')
        self.save_metadata()
        root_id,dest_rel,_=target;select=None
        if 'assets' in payload:
            result=organize.move_assets(self.library,self.dragged_rows(payload['assets']),dest_rel,self.data_dir)
            message=f"Moved {result['moved']} asset(s) to {dest_rel}"
        else:
            result=organize.move_folders(self.library,root_id,[f['rel'] for f in payload['folders']],dest_rel,self.data_dir)
            message=f"Moved {result['folders']} folder(s) ({result['moved']} assets) to {dest_rel}";select=(root_id,result['new_rel'],target[2])
        try:
            relinked=ops.relink_catalog_paths([self.catalogs.itemData(i) for i in range(self.catalogs.count())],result['catalog'],self.data_dir/'backups') if result['catalog'] else 0
            if relinked:message+=f' / {relinked} Catalog entries updated'
        except Exception as exc:message+=' / Catalog entries were not updated: '+str(exc)
        self.info_cache.clear();self.info_key=None
        self.rebuild_tree()
        if select:self.select_folder(select)
        self.refresh();self.status.setText(message+' (index backed up)')

    def folder_drag_started(self):
        self.folder_dirty=self.folder_timer.isActive();self.folder_timer.stop()

    def folder_drag_finished(self):
        if self.folder_dirty:self.folder_dirty=False;self.folder_timer.start(0)

    def reset_page(self,*args):
        self.refresh(reset=True)

    def filters(self):
        folder=self.current_folder()
        return dict(search=self.search.text(),root_id=folder[0] if folder else None,kind=self.kind.currentData() or None,
                    favorite=self.favorite.isChecked(),folder=folder[1] if folder and folder[1] else None,recursive=self.recursive.isChecked())

    def close_stream(self):
        if self.stream is not None:self.stream.close();self.stream=None

    def refresh(self,reset=False):
        """Start the list again. Entries are read from the index on demand (see Library.stream), so a
        result of any size opens instantly and only what has been scrolled to is ever loaded."""
        self.icons_built_at=self.icon_edge()
        bar=self.items.verticalScrollBar();position=bar.value();keep=0 if reset else len(self.page_entries)
        self.close_stream()
        filters=self.filters()
        self.stream=self.library.stream(stacked=self.stack.isChecked(),**filters)
        self.total=self.library.count(**{k:v for k,v in filters.items()})
        self.items.clear();self.icon_todo.clear();self.icons_loaded=set();self.icon_timer.stop()
        self.rows=[];self.row_index={};self.page_entries=[];self.entry_of={};self.item_index={};self.member_index={}
        if reset:bar.setValue(0)
        self.append_items(max(self.CHUNK,keep))
        if position and not reset:QtCore.QTimer.singleShot(0,lambda:bar.setValue(position))   # refreshing keeps the scroll position

    def append_items(self,count):
        """Add the next items of the current result to the list."""
        chunk=self.stream.next(count) if self.stream is not None else []
        if not chunk:
            self.update_count_label();return
        # Items appear at once with a flat placeholder; pictures are read a few at a time near the
        # visible area (schedule_icons), so a huge result never freezes the panel.
        for entry in chunk:
            row=entry['rep'];members=entry['rows']
            item=QtWidgets.QListWidgetItem(self.placeholder(row['effective_kind']),self.item_text(members,row,entry['label']))
            # Items hold only ids (a whole library can be scrolled through); rows live in row_index.
            item.setData(ROLE,row['id'])
            if len(members)>1:item.setData(STACK_ROLE,[m['id'] for m in members])
            item.setToolTip(row['relpath'] if len(members)==1 else f"{entry['label']}: {', '.join(entry['channels'])}")
            index=len(self.page_entries)
            self.items.addItem(item);self.page_entries.append(entry);self.entry_of[row['id']]=entry;self.item_index[row['id']]=index
            self.member_index.update({m['id']:index for m in members})
            self.rows.extend(members);self.row_index.update({m['id']:m for m in members})
        self.update_count_label();self.schedule_icons()

    def update_count_label(self):
        more=self.stream is not None and not self.stream.finished
        self.page_label.setText(f'{self.total} assets'+(f'  ·  showing {len(self.page_entries)} items, scroll for more' if more else ''))

    def capacity(self):
        """How many items fit in the visible area."""
        grid=self.items.gridSize();viewport=self.items.viewport()
        return max(1,viewport.height()//max(1,grid.height()))*max(1,viewport.width()//max(1,grid.width()))

    def maybe_load_more(self):
        if self.stream is None or self.stream.finished:return
        bar=self.items.verticalScrollBar()
        near_end=bar.maximum()>0 and bar.value()>=bar.maximum()-self.items.gridSize().height()*3
        if near_end or len(self.page_entries)<2*self.capacity():
            self.append_items(self.CHUNK)
            self.scroll_timer.start()   # the new items may still not fill the view

    def scrolled(self):
        self.maybe_load_more();self.schedule_icons()

    def visible_range(self):
        """(first, last) item index currently on screen, from hit tests on the first column."""
        count=self.items.count()
        if not count:return None
        viewport=self.items.viewport();grid=self.items.gridSize();x=self.items.spacing()+grid.width()//2
        per_row=1
        if count>1:
            a,b=self.items.visualItemRect(self.items.item(0)),self.items.visualItemRect(self.items.item(1))
            if a.top()==b.top() and b.left()!=a.left():per_row=max(1,viewport.width()//abs(b.left()-a.left()))
        def probe(top_down):
            for step in range(0,grid.height()+2*self.items.spacing()+12,6):
                y=step if top_down else viewport.height()-1-step
                index=self.items.indexAt(QtCore.QPoint(x,y))
                if index.isValid():return index.row()
            return None
        first,last=probe(True),probe(False)
        if first is None:first=0
        first-=first%per_row
        last=first+self.capacity()-1 if last is None else last-last%per_row+per_row-1
        return first,max(first,min(last,count-1))

    def schedule_icons(self):
        """Queue pictures for the visible items and one screen around them; drop the ones far away.

        Only a few screens of pictures are ever held, so scrolling through tens of thousands of assets
        keeps memory flat.
        """
        found=self.visible_range()
        if found is None:return
        first,last=found;span=max(self.capacity(),last-first+1);count=self.items.count()
        near=(max(0,first-span),min(count-1,last+span));far=(first-3*span,last+3*span)
        for index in [i for i in self.icons_loaded if i<far[0] or i>far[1]]:
            self.icons_loaded.discard(index)
            if index<len(self.page_entries):self.items.item(index).setIcon(self.placeholder(self.page_entries[index]['rep']['effective_kind']))
        wanted=[i for i in range(near[0],near[1]+1) if i not in self.icons_loaded]
        wanted.sort(key=lambda i:0 if first<=i<=last else min(abs(i-first),abs(i-last)))
        self.icon_todo=deque(wanted)
        if wanted:self.icon_timer.start(0)

    def placeholder(self,kind):
        icon=self.placeholders.get(kind)
        if icon is None:
            pix=QtGui.QPixmap(144,144);pix.fill(QtGui.QColor({'usd':'#3b6677','model':'#466c58','hdri':'#796a39','material':'#665488','pbr':'#665488','decal':'#805457','texture':'#496878'}.get(kind,'#555555')))
            icon=self.placeholders[kind]=QtGui.QIcon(pix)
        return icon

    def load_icons(self):
        """Read pictures in small slices (about 12 ms each) between events."""
        deadline=time.monotonic()+0.012
        while self.icon_todo and time.monotonic()<deadline:
            index=self.icon_todo.popleft()
            if index in self.icons_loaded or index>=self.items.count() or index>=len(self.page_entries):continue
            entry=self.page_entries[index]
            icon=self.icon_for(entry['rep'])
            if len(entry['rows'])>1:icon=self.stacked_icon(icon,len(entry['rows']))
            elif 'variant' in entry['rep']['tags'].split():icon=self.variant_badge(icon)
            self.items.item(index).setIcon(icon);self.icons_loaded.add(index)
        if self.icon_todo:self.icon_timer.start(0)

    def item_text(self,members,rep,label=None):
        favorite=any(m['favorite'] for m in members)
        if len(members)>1:return ('★ ' if favorite else '')+(label or rep['label'])+f'\nPBR set · {len(members)} images'
        return ('★ ' if favorite else '')+rep['label']+'\n'+KINDS[rep['effective_kind']]

    def item_rows(self,item):
        """The assets a list item stands for: one row, or every image of a stack."""
        rows=[self.row_index[i] for i in (item.data(STACK_ROLE) or [item.data(ROLE)]) if i in self.row_index]
        return rows

    def item_row(self,item):
        return self.row_index.get(item.data(ROLE))

    def stacked_icon(self,icon,count):
        """Card pile with a count badge, so a stack is recognisable at a glance."""
        edge=self.icon_edge();out=QtGui.QPixmap(edge,edge);out.fill(QtCore.Qt.GlobalColor.transparent)
        painter=QtGui.QPainter(out);painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing);painter.scale(edge/256,edge/256)
        for x,y in ((32,16),(16,32)):
            painter.setPen(QtGui.QPen(QtGui.QColor(160,160,160),2));painter.setBrush(QtGui.QColor(72,72,72))
            painter.drawRoundedRect(x,y,208,208,6,6)
        painter.drawPixmap(QtCore.QRect(0,48,208,208),icon.pixmap(edge,edge))
        painter.setPen(QtCore.Qt.PenStyle.NoPen);painter.setBrush(QtGui.QColor(30,120,90));painter.drawEllipse(166,198,50,50)
        font=painter.font();font.setBold(True);font.setPixelSize(24);painter.setFont(font)
        painter.setPen(QtGui.QColor('#ffffff'));painter.drawText(QtCore.QRect(166,198,50,50),QtCore.Qt.AlignmentFlag.AlignCenter,str(count));painter.end()
        return QtGui.QIcon(out)

    def variant_badge(self,icon):
        """Small corner marker for a USD carrying the "variant" tag (an element switch, or
        anything else tagged that way), so it is recognisable without opening its info panel."""
        edge=self.icon_edge();out=QtGui.QPixmap(edge,edge);out.fill(QtCore.Qt.GlobalColor.transparent)
        painter=QtGui.QPainter(out);painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing);painter.scale(edge/256,edge/256)
        painter.drawPixmap(QtCore.QRect(0,0,256,256),icon.pixmap(edge,edge))
        painter.setPen(QtCore.Qt.PenStyle.NoPen);painter.setBrush(QtGui.QColor(45,95,180));painter.drawRoundedRect(6,6,96,28,6,6)
        font=painter.font();font.setBold(True);font.setPixelSize(14);painter.setFont(font)
        painter.setPen(QtGui.QColor('#ffffff'));painter.drawText(QtCore.QRect(6,6,96,28),QtCore.Qt.AlignmentFlag.AlignCenter,'VARIANT');painter.end()
        return QtGui.QIcon(out)

    def icon_edge(self):
        return 512 if self.items.iconSize().width()>256 else 256

    def icon_size_finished(self,size):
        # Larger icons need larger source pictures; rebuild the list only when that changes.
        self.settings['icon_size']=size;self.safe(self.save_settings)
        edge=self.icon_edge()
        if edge!=getattr(self,'icons_built_at',256):self.icon_cache.clear();self.refresh()
        self.status.setText(f'Icon size: {size}px (Ctrl + middle-drag to change)')

    def stack_toggled(self,checked):
        self.settings['stack_pbr']=bool(checked)
        self.safe(self.save_settings);self.reset_page()

    def thumbnail_path(self,row):
        source=Path(row['root_path'])/row['relpath']
        # A large original image is not decoded on the UI thread; its small thumbnail is made in the background.
        found=next((x for x in storage.thumbnail_candidates(row,self.data_dir)
                    if x.is_file() and not (x==source and row['kind']=='texture' and row['size']>2*1024*1024)),None)
        if found is None and storage.is_usdz(row):
            # Take the preview stored inside the .usdz once and cache it; remember packages without one.
            key=(row['id'],row['mtime'],row['size'])
            if key not in self.no_embedded:
                found=embedded.extract(Path(row['root_path'])/row['relpath'],storage.embedded_cache_stem(row,self.data_dir))
                if found is None:self.no_embedded.add(key)
        return found

    def cache_path(self,row):
        return storage.thumbnail_destination(row,self.data_dir)

    def queue_thumbnail(self,row,geometry=False,force=False):
        aid=row['id']
        if (row['kind']!='texture' and not geometry) or aid in self.thumb_pending or aid in self.thumb_failed:return
        if not force and self.cache_path(row).is_file():return
        self.thumb_pending.add(aid);self.thumb_queue.append(row)
        QtCore.QTimer.singleShot(0,self.next_thumbnail)

    def next_thumbnail(self):
        if self.thumb_job is not None or not self.thumb_queue:return
        row=self.thumb_queue.popleft()
        source=Path(row['root_path'])/row['relpath']
        self.thumb_job=(ThumbnailJob(row['id'],source,self.cache_path(row)) if row['kind']=='texture' else
            GeometryThumbnailJob(row['id'],source,row['kind'],self.cache_path(row)))
        self.thumb_job.done.connect(self.thumbnail_done)
        self.thumb_job.finished.connect(self.thumbnail_finished)
        _keep_job(self.thumb_job)

    def thumbnail_finished(self):
        self.thumb_job=None;self.next_thumbnail()

    def queue_proxy(self,row,target_triangles):
        aid=row['id']
        if row['kind']!='usd' or aid in self.proxy_pending or aid in self.proxy_failed:return
        self.proxy_pending.add(aid);self.proxy_queue.append((row,target_triangles))
        QtCore.QTimer.singleShot(0,self.next_proxy)

    def next_proxy(self):
        if self.proxy_job is not None or not self.proxy_queue:return
        row,target_triangles=self.proxy_queue.popleft()
        source=Path(row['root_path'])/row['relpath']
        self.proxy_job=ProxyJob(row['id'],source,self.data_dir/'backups'/'proxy',target_triangles)
        self.proxy_job.done.connect(self.proxy_done)
        self.proxy_job.finished.connect(self.proxy_finished)
        _keep_job(self.proxy_job)

    def proxy_finished(self):
        self.proxy_job=None;self.next_proxy()

    def proxy_done(self,aid,message,error):
        self.proxy_pending.discard(aid)
        if error:
            self.proxy_failed.add(aid);self.status.setText('Proxy generation failed: '+error)
        else:
            self.status.setText((message or 'Already has a proxy')+f' ({len(self.proxy_queue)} remaining)')

    def queue_element(self,row):
        aid=row['id']
        if row['kind']!='usd' or aid in self.element_pending or aid in self.element_failed:return
        self.element_pending.add(aid);self.element_queue.append(row)
        QtCore.QTimer.singleShot(0,self.next_element)

    def next_element(self):
        if self.element_job is not None or not self.element_queue:return
        row=self.element_queue.popleft()
        source=Path(row['root_path'])/row['relpath']
        self.element_job=ElementJob(row['id'],source,self.data_dir/'backups'/'element')
        self.element_job.done.connect(self.element_done)
        self.element_job.finished.connect(self.element_finished)
        _keep_job(self.element_job)

    def element_finished(self):
        self.element_job=None;self.next_element()

    def element_done(self,aid,message,error):
        self.element_pending.discard(aid)
        if error:
            self.element_failed.add(aid);self.status.setText('Element switch generation failed: '+error)
        else:
            if message and message.startswith('Element switch added'):self.set_variant_tag(aid,True)
            self.status.setText((message or 'Already has an element switch')+f' ({len(self.element_queue)} remaining)')

    def queue_element_delete(self,row):
        aid=row['id']
        if row['kind']!='usd' or aid in self.element_delete_pending or aid in self.element_delete_failed:return
        self.element_delete_pending.add(aid);self.element_delete_queue.append(row)
        QtCore.QTimer.singleShot(0,self.next_element_delete)

    def next_element_delete(self):
        if self.element_delete_job is not None or not self.element_delete_queue:return
        row=self.element_delete_queue.popleft()
        source=Path(row['root_path'])/row['relpath']
        self.element_delete_job=ElementDeleteJob(row['id'],source,self.data_dir/'backups'/'element')
        self.element_delete_job.done.connect(self.element_delete_done)
        self.element_delete_job.finished.connect(self.element_delete_finished)
        _keep_job(self.element_delete_job)

    def element_delete_finished(self):
        self.element_delete_job=None;self.next_element_delete()

    def element_delete_done(self,aid,message,error):
        self.element_delete_pending.discard(aid)
        if error:
            self.element_delete_failed.add(aid);self.status.setText('Element switch removal failed: '+error)
        else:
            if message and message.startswith('Element switch removed'):self.set_variant_tag(aid,False)
            self.status.setText((message or 'No element switch to remove')+f' ({len(self.element_delete_queue)} remaining)')

    def set_variant_tag(self,aid,present):
        """Keep the auto "variant" tag (and the thumbnail badge it drives) in sync with whether
        this asset currently has an element switch, without opening the file again - the caller
        already knows, since it just added or removed one."""
        row=self.row_index.get(aid)
        if row is None:return
        words=row['tags'].split()
        if present:
            if 'variant' in words:return
            words.append('variant')
        else:
            if 'variant' not in words:return
            words=[w for w in words if w!='variant']
        tags=' '.join(words)
        self.library.update(aid,tags=tags);self.update_metadata_rows(aid,tags=tags)
        index=self.item_index.get(aid)
        if index is not None:self.icons_loaded.discard(index);self.icon_todo.appendleft(index);self.icon_timer.start(0)
        current=self.items.currentItem()
        if current is not None and current.data(ROLE)==aid:self.selection_changed()

    def icon_for(self,row):
        path=self.thumbnail_path(row)
        if path:
            edge=self.icon_edge()
            try:key=(str(path),path.stat().st_mtime_ns,edge)
            except OSError:key=None
            if key in self.icon_cache:return self.icon_cache[key]
            pix=square_preview(path,edge)
            if pix is not None:
                icon=QtGui.QIcon(pix)
                if key is not None:
                    if len(self.icon_cache)>=300*256*256//(edge*edge):self.icon_cache.pop(next(iter(self.icon_cache)))
                    self.icon_cache[key]=icon
                return icon
        self.queue_thumbnail(row)
        pix=QtGui.QPixmap(144,144); pix.fill(QtGui.QColor({'usd':'#3b6677','model':'#466c58','hdri':'#796a39','material':'#665488','pbr':'#665488','decal':'#805457','texture':'#496878'}[row['effective_kind']]))
        painter=QtGui.QPainter(pix); painter.setPen(QtGui.QColor('#eeeeee')); painter.drawText(pix.rect(),QtCore.Qt.AlignmentFlag.AlignCenter,row['effective_kind'].upper()); painter.end()
        return QtGui.QIcon(pix)

    def preview_pixmap(self,path):
        try:key=(str(path),Path(path).stat().st_mtime_ns)
        except OSError:return None
        if key in self.preview_cache:
            self.preview_cache.move_to_end(key);return self.preview_cache[key]
        pix=square_preview(path,1024)
        if pix is not None:
            self.preview_cache[key]=pix
            while len(self.preview_cache)>12:self.preview_cache.popitem(last=False)
        return pix

    def selected(self):
        item=self.items.currentItem()
        if not item:raise ValueError('Select an asset.')
        return self.item_row(item)

    def selected_rows(self):
        rows={}
        for item in self.items.selectedItems():
            for row in self.item_rows(item):rows[row['id']]=row
        if not rows:raise ValueError('Select one or more assets.')
        return list(rows.values())

    def selection_changed(self,*args):
        self.safe(self.save_metadata)
        if not self.items.currentItem():
            self.preview.clear();self.info.clear();self.stats.clear();self.metadata_id=None;self.metadata_ids=[]
            self.tags.clear();self.tags.setEnabled(False);self.star.setEnabled(False)
            self.info_key=None;self.info_pending=None;self.info_wanted=None;self.info_timer.stop()
            if self.info_job:self.info_job.requestInterruption()
            self.status.setText('Select assets. Ctrl / Shift: multi-select.'); return
        row=self.selected();members=self.item_rows(self.items.currentItem())
        thumb=self.thumbnail_path(row)
        pix=self.preview_pixmap(thumb) if thumb else None
        self.preview.setPixmap(pix if pix is not None else self.icon_for(row).pixmap(512,512))
        display_path=Path(row['relpath']).parent.as_posix() if core.is_usd_package(row) else row['relpath']
        if len(members)>1:
            entry=self.entry_of.get(row['id'])
            label=entry['label'] if entry else row['label'];channels=', '.join(entry['channels']) if entry else ''
            self.info.setText(f"{label}\nPBR set: {len(members)} images | {sum(m['size'] for m in members)/1048576:.2f} MB\n{channels}")
            self.info.setToolTip(f"{row['root_label']} / {Path(row['relpath']).parent.as_posix()}")
        else:
            self.info.setText(f"{row['label']}\n{KINDS[row['kind']]} | {row['size']/1048576:.2f} MB")
            self.info.setToolTip(f"{row['root_label']} / {display_path}")
        self.metadata_id=row['id'];self.metadata_ids=[m['id'] for m in members];self.tags.setEnabled(True);self.star.setEnabled(True)
        words=list(dict.fromkeys(w for m in members for w in m['tags'].split()))
        self.tags.setText(' '.join(words) if len(members)>1 else row['tags'])
        self.star.blockSignals(True);self.star.setChecked(all(m['favorite'] for m in members));self.star.blockSignals(False)
        self.request_info(row)
        count=len(self.items.selectedItems())
        self.status.setText(f'{count} selected | Ctrl / Shift: multi-select | Drag to a field or network')

    def save_metadata(self):
        if self.pending_tags is None:return
        ids,value=self.pending_tags
        for aid in ids:
            self.library.update(aid,tags=value);self.update_metadata_rows(aid,tags=value)
        self.pending_tags=None;self.status.setText('Tags saved.')

    def tags_edited(self,value):
        if self.metadata_ids:self.pending_tags=(list(self.metadata_ids),value)

    def save_favorite(self,checked):
        for aid in self.metadata_ids:
            self.library.update(aid,favorite=int(checked))
            self.update_metadata_rows(aid,favorite=int(checked))

    def update_metadata_rows(self,aid,**changes):
        if aid in self.row_index:self.row_index[aid].update(changes)
        index=self.member_index.get(aid)
        if index is not None and index<self.items.count():
            item=self.items.item(index);rep=self.item_row(item)   # row_index already holds the new values
            entry=self.entry_of.get(rep['id'])
            item.setText(self.item_text(self.item_rows(item),rep,entry['label'] if entry else None))

    def request_info(self,row):
        path=Path(row['root_path'])/row['relpath']
        try:
            stat=path.stat();key=(str(path),stat.st_mtime_ns,stat.st_size)
        except OSError:
            self.stats.setText('Source file unavailable');self.info_key=None;return
        if key==self.info_key:return
        self.info_key=key
        self.info_pending=None;self.info_wanted=None
        if self.info_job:self.info_job.requestInterruption()
        stamp=f'{key[1]}:{key[2]}';self.info_ids[key]=(row['id'],stamp)
        if key not in self.info_cache:
            stored=self.library.info(row['id'],stamp)   # read in an earlier session: no hython needed
            if stored:self.info_cache[key]=stored
        if key in self.info_cache:
            self.show_info(self.info_cache[key]);return
        self.stats.setText('Reading asset information...')
        # Starting hython costs seconds of CPU: wait until the selection settles.
        self.info_wanted=(key,path,row['kind']);self.info_timer.start()

    def start_info(self):
        if self.info_wanted:self.info_pending,self.info_wanted=self.info_wanted,None
        if not self.info_job:self.next_info()

    def next_info(self):
        if self.info_job or not self.info_pending:return
        self.info_job=AssetInfoJob(*self.info_pending);self.info_pending=None
        self.info_job.done.connect(self.info_done);self.info_job.finished.connect(self.info_finished)
        _keep_job(self.info_job)

    def info_finished(self):self.info_job=None;self.next_info()

    def info_done(self,key,result):
        self.info_cache[key]=result
        if 'info' in result and key in self.info_ids:self.library.save_info(*self.info_ids[key],result)   # errors are not kept
        if key==self.info_key:self.show_info(result)

    def show_info(self,result):
        self.stats.setText('Information unavailable: '+result['error'] if 'error' in result else '\n'.join(f'{name}: {value}' for name,value in result['info'].items()))

    def asset_menu(self,position):
        item=self.items.itemAt(position)
        if item is None:return
        if not item.isSelected():self.items.setCurrentItem(item,QtCore.QItemSelectionModel.SelectionFlag.ClearAndSelect)
        else:self.items.setCurrentItem(item,QtCore.QItemSelectionModel.SelectionFlag.NoUpdate)
        menu=self.build_asset_menu()
        try:menu.exec(self.items.viewport().mapToGlobal(position))
        finally:menu.deleteLater()

    def build_asset_menu(self):
        rows=self.selected_rows();menu=QtWidgets.QMenu(self)
        def action(label,callback):menu.addAction(label,lambda:self.safe(callback))
        action('Import Selected',self.import_selected);action('Copy Paths',self.copy_path)
        action('Show in Explorer',self.reveal)
        if all(row['kind']=='usd' for row in rows):action('Add Catalog',self.add_catalog)
        menu.addSeparator()
        action('Generate Selected Thumbnails',self.generate_thumbnail)
        if all(row['kind']=='usd' for row in rows):
            action('Generate Selected Proxies...',self.generate_proxy)
            action('Generate Selected Element Switch...',self.generate_element)
            action('Delete Element Switch',self.generate_element_delete)
        if len(rows)==1:
            action('Choose Thumbnail...',self.choose_thumbnail)
            action('Publish Static USD...',self.publish_asset)
        return menu

    def add_root(self):
        path=QtWidgets.QFileDialog.getExistingDirectory(self,'Library Root Folder',self.default_root)
        if path:
            source_root=Path(__file__).resolve().parents[2]
            candidate=Path(path).resolve()
            if candidate.is_relative_to(source_root) or source_root.is_relative_to(candidate):
                raise ValueError('Keep the code and asset library in separate folders.')
            self.library.add_root(path)
            if not self.default_root:
                self.default_root=path; self.settings['publish_root']=path; self.save_settings()
            self.rebuild_tree(); self.scan()

    def save_settings(self):
        target=self.data_dir/'settings.json'
        temp=target.with_suffix('.tmp')
        temp.write_text(json.dumps(self.settings,ensure_ascii=False,indent=2),encoding='utf-8')
        os.replace(temp,target)

    def scan(self):
        if self.job and self.job.isRunning():return
        roots=self.library.roots(); folder=self.current_folder()
        if folder:roots=[r for r in roots if r['id']==folder[0]]
        self.job=ScanJob(self.library,roots); self.job.done.connect(self.scan_done)
        self.scan_button.setEnabled(False); self.cancel_button.setVisible(True); self.status.setText('Scanning... Source files are read only. You can cancel.')
        _keep_job(self.job)

    def cancel_scan(self):
        if self.job:self.job.requestInterruption()

    def scan_done(self,results):
        self.scan_button.setEnabled(True); self.cancel_button.setVisible(False)
        self.info_cache.clear()
        self.rebuild_tree(); self.refresh()
        messages=[]
        for name,result in results:
            messages.append(name+': '+('Cancel' if result.get('cancelled') else str(result['count'])+' assets')+(' / '+ '; '.join(result['errors'][:3]) if result.get('errors') else ''))
        self.status.setText(' | '.join(messages) or 'No libraries to scan')

    def new_folder(self):
        folder=self.current_folder()
        if not folder:raise ValueError('Select the parent folder on the left.')
        if not folder[1]:raise ValueError('Choose a folder under USD, Texture or 3DModel. These top-level categories are fixed.')
        if folder[1].startswith('USD/') and core.package_entry(core.inside(folder[2],folder[1])):
            raise ValueError('Cannot create folders inside a USD package.')
        name,ok=QtWidgets.QInputDialog.getText(self,'New Folder','Name (use / for nested folders)')
        if ok and name.strip():
            path=core.inside(core.inside(folder[2],folder[1]),name.strip()); path.mkdir(parents=True,exist_ok=True)
            self.library.add_folder(folder[0],path.relative_to(Path(folder[2])).as_posix())
            self.rebuild_tree(); self.status.setText('Created: '+str(path))

    def root_menu(self):
        menu=QtWidgets.QMenu(self)
        menu.addAction('Add Library...',lambda:self.safe(self.add_root))
        menu.addAction('New Folder...',lambda:self.safe(self.new_folder))
        menu.addSeparator()
        menu.addAction('Show in Explorer',lambda:self.safe(self.reveal))
        menu.addAction('Relink Library...',lambda:self.safe(self.relink))
        menu.addAction('Use Library for USD / Catalog Output',lambda:self.safe(self.set_publish_root))
        menu.addAction('Remove Library Registration...',lambda:self.safe(self.remove_root))
        menu.addAction('Back Up Index',lambda:self.status.setText(str(self.library.backup_index())))
        menu.addSeparator()
        menu.addAction('Generate Missing Thumbnails',lambda:self.safe(self.generate_missing_thumbnails))
        menu.addAction('Cancel Thumbnails',lambda:self.safe(self.cancel_thumbnails))
        menu.addAction('Generate Missing Proxies...',lambda:self.safe(self.generate_missing_proxies))
        menu.addAction('Cancel Proxies',lambda:self.safe(self.cancel_proxies))
        menu.addAction('Cancel Element Switches',lambda:self.safe(self.cancel_elements))
        menu.addAction('Cancel Element Switch Removals',lambda:self.safe(self.cancel_element_deletes))
        menu.addAction('Open Catalog',lambda:self.safe(self.open_catalog))
        menu.addAction('Select Catalog...',lambda:self.safe(self.select_catalog))
        menu.addAction('New Catalog...',lambda:self.safe(self.new_catalog))
        menu.exec(QtGui.QCursor.pos())

    def set_publish_root(self):
        folder=self.current_folder()
        if not folder:raise ValueError('Select a library.')
        self.default_root=folder[2]; self.settings['publish_root']=self.default_root; self.save_settings(); self.refresh_catalogs()
        self.status.setText('USD / Catalog output: '+self.default_root)

    def relink(self):
        folder=self.current_folder()
        if not folder:raise ValueError('Select a library.')
        path=QtWidgets.QFileDialog.getExistingDirectory(self,'New Library Root Folder',folder[2])
        if path:
            source_root=Path(__file__).resolve().parents[2]
            candidate=Path(path).resolve()
            if candidate.is_relative_to(source_root) or source_root.is_relative_to(candidate):
                raise ValueError('Keep the code and asset library in separate folders.')
            self.library.backup_index(); self.library.relink_root(folder[0],path)
            if self.default_root and core.path_key(self.default_root)==core.path_key(folder[2]):
                self.default_root=path; self.settings['publish_root']=path; self.save_settings()
            self.rebuild_tree(); self.scan()

    def remove_root(self):
        folder=self.current_folder()
        if not folder:raise ValueError('Select a library.')
        result=QtWidgets.QMessageBox.question(self,'Remove Library','Remove this library registration and its tag metadata?\nSource files are kept. The index will be backed up.')
        if result==QtWidgets.QMessageBox.StandardButton.Yes:
            self.library.backup_index(); self.library.remove_root(folder[0]); self.rebuild_tree(); self.refresh()

    def reveal(self):
        if self.items.selectedItems():paths=list(dict.fromkeys(self.library.resolve(row).parent for row in self.selected_rows()))
        else:
            folder=self.current_folder()
            if not folder:raise ValueError('Select a folder.')
            paths=[core.inside(folder[2],folder[1])]
        for path in paths:QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path)))

    def choose_thumbnail(self):
        row=self.selected(); path,_=QtWidgets.QFileDialog.getOpenFileName(self,'Thumbnail Image',str(self.library.resolve(row).parent),'Images (*.png *.jpg *.jpeg)')
        if path:self.library.update(row['id'],thumbnail=path); self.refresh()

    def generate_thumbnail(self):
        rows=self.selected_rows()
        for row in rows:
            self.library.resolve(row)
            self.thumb_failed.discard(row['id'])
            self.queue_thumbnail(row,geometry=True,force=True)
        self.status.setText(f'Queued {len(rows)} thumbnails. Geometry renders in a separate Karma CPU process.')

    def ask_proxy_target(self):
        default=int(self.settings.get('proxy_target_triangles',proxy_gen.TARGET_TRIANGLES))
        value,ok=QtWidgets.QInputDialog.getInt(self,'Generate Proxy','Target triangles per mesh:',default,4,2000000)
        if not ok:return None
        self.settings['proxy_target_triangles']=value;self.save_settings()
        return value

    def generate_proxy(self):
        rows=[r for r in self.selected_rows() if r['kind']=='usd']
        if not rows:raise ValueError('Select one or more USD assets.')
        target=self.ask_proxy_target()
        if target is None:return
        for row in rows:
            self.library.resolve(row)
            self.proxy_failed.discard(row['id'])
            self.queue_proxy(row,target)
        self.status.setText(f'Queued {len(rows)} proxy job(s) (target {target} triangles). Each USD file is overwritten in place (a backup is kept in data/backups/proxy).')

    def generate_missing_proxies(self):
        if self.missing_proxy_scan is not None:return
        target=self.ask_proxy_target()
        if target is None:return
        # Whether a USD already has a proxy can only be known by opening it, which is too slow to
        # check here for every asset; each queued job checks its own file and skips quickly if so.
        filters=dict(self.filters());filters['kind']='usd'
        self.missing_proxy_scan={'batches':self.library.iter_rows(**filters),'batch':[],'index':0,'count':0,'seen':0,'target':target}
        QtCore.QTimer.singleShot(0,self.missing_proxy_step)

    def missing_proxy_step(self):
        scan=self.missing_proxy_scan
        if scan is None:return
        deadline=time.monotonic()+0.015
        while time.monotonic()<deadline:
            if scan['index']>=len(scan['batch']):
                scan['batch']=next(scan['batches'],None)
                if scan['batch'] is None:
                    self.missing_proxy_scan=None
                    self.status.setText(f"Queued {scan['count']} proxy jobs. Everything matching the search and folder filters is included.")
                    return
                scan['index']=0
            row=scan['batch'][scan['index']];scan['index']+=1;scan['seen']+=1
            if row['id'] not in self.proxy_pending:
                self.proxy_failed.discard(row['id']);self.queue_proxy(row,scan['target']);scan['count']+=1
        self.status.setText(f"Checking USD assets... {scan['seen']} / {self.total}")
        QtCore.QTimer.singleShot(0,self.missing_proxy_step)

    def cancel_proxies(self):
        self.missing_proxy_scan=None
        for row,_ in self.proxy_queue:self.proxy_pending.discard(row['id'])
        self.proxy_queue.clear()
        if self.proxy_job:self.proxy_job.requestInterruption()
        self.status.setText('Cancelled. An active proxy generation will finish first.')

    def generate_element(self):
        rows=[r for r in self.selected_rows() if r['kind']=='usd']
        if not rows:raise ValueError('Select one or more USD assets.')
        for row in rows:
            self.library.resolve(row)
            self.element_failed.discard(row['id'])
            self.queue_element(row)
        # Only for a pack of alternate top-level objects (all shown at once today, only one wanted).
        # A modular kit whose pieces are meant to all stay visible looks the same on disk and would
        # be broken the same way, so this is deliberately per-asset - check each one visually.
        self.status.setText(f'Queued {len(rows)} element switch job(s). Only use this on assets that bundle several alternate objects (only the first will show after generation) - a modular kit meant to display all its pieces together would be broken by it. Each USD file is overwritten in place (a backup is kept in data/backups/element).')

    def cancel_elements(self):
        for row in self.element_queue:self.element_pending.discard(row['id'])
        self.element_queue.clear()
        if self.element_job:self.element_job.requestInterruption()
        self.status.setText('Cancelled. An active element switch generation will finish first.')

    def generate_element_delete(self):
        rows=[r for r in self.selected_rows() if r['kind']=='usd']
        if not rows:raise ValueError('Select one or more USD assets.')
        for row in rows:
            self.library.resolve(row)
            self.element_delete_failed.discard(row['id'])
            self.queue_element_delete(row)
        self.status.setText(f'Queued {len(rows)} element switch removal job(s). Assets without one are skipped untouched. Each USD file is overwritten in place (a backup is kept in data/backups/element).')

    def cancel_element_deletes(self):
        for row in self.element_delete_queue:self.element_delete_pending.discard(row['id'])
        self.element_delete_queue.clear()
        if self.element_delete_job:self.element_delete_job.requestInterruption()
        self.status.setText('Cancelled. An active element switch removal will finish first.')

    def generate_missing_thumbnails(self):
        if self.missing_scan is not None:return
        # Checking every asset touches the disk; read the index and check the files in slices so the
        # panel stays responsive however many assets match.
        self.missing_scan={'batches':self.library.iter_rows(**self.filters()),'batch':[],'index':0,'count':0,'seen':0}
        QtCore.QTimer.singleShot(0,self.missing_step)

    def missing_step(self):
        scan=self.missing_scan
        if scan is None:return
        deadline=time.monotonic()+0.015
        while time.monotonic()<deadline:
            if scan['index']>=len(scan['batch']):
                scan['batch']=next(scan['batches'],None)
                if scan['batch'] is None:
                    self.missing_scan=None
                    self.status.setText(f"Queued {scan['count']} missing thumbnails. Everything matching the search and folder filters is included.")
                    return
                scan['index']=0
            row=scan['batch'][scan['index']];scan['index']+=1;scan['seen']+=1
            if row['id'] not in self.thumb_pending and not self.thumbnail_path(row):
                self.thumb_failed.discard(row['id'])
                self.queue_thumbnail(row,geometry=True);scan['count']+=1
        self.status.setText(f"Checking thumbnails... {scan['seen']} / {self.total}")
        QtCore.QTimer.singleShot(0,self.missing_step)

    def cancel_thumbnails(self):
        self.missing_scan=None
        for row in self.thumb_queue:self.thumb_pending.discard(row['id'])
        self.thumb_queue.clear()
        if self.thumb_job:self.thumb_job.requestInterruption()
        self.status.setText('Cancelled. An active image conversion will finish first.')

    def thumbnail_done(self,aid,path,error):
        self.thumb_pending.discard(aid)
        if error:
            self.thumb_failed.add(aid);self.status.setText('Thumbnail failed: '+error)
        else:
            index=self.item_index.get(aid)
            if index is not None:   # reload the picture through the normal (sliced) path
                self.icons_loaded.discard(index);self.icon_todo.appendleft(index);self.icon_timer.start(0)
            current=self.items.currentItem()
            if current is not None and current.data(ROLE)==aid:self.selection_changed()
            self.status.setText(f'Thumbnail complete ({len(self.thumb_queue)} remaining)')

    def copy_path(self):
        path=dragdrop.paths_text([self.library.resolve(row) for row in self.selected_rows()])
        QtWidgets.QApplication.clipboard().setText(path)
        self.status.setText('Copied paths: '+path)

    def import_selected(self):
        rows=self.selected_rows()
        if len(rows)>1:
            parent=hou.node(self.target.text())
            if parent is None:raise ValueError('Import target does not exist.')
            if all(row['kind']=='texture' for row in rows) and not dragdrop.is_material_context(parent):
                self.copy_path();return
            payloads=[{'path':self.library.resolve(row),'kind':row['effective_kind'],'label':row['label'],'tags':row['tags']} for row in rows]
            nodes=dragdrop.import_payloads(payloads,parent)
            self.status.setText(f'Imported {len(nodes)} assets into {parent.path()}')
            return nodes
        row=self.selected(); source=self.library.resolve(row)
        if row['kind']=='texture':
            self.copy_path();self.status.setText('Drop textures into a field for paths, or into a material network for MaterialX.');return
        upstream=None
        if self.connect_input.isChecked():
            nodes=[n for n in hou.selectedNodes() if n.parent().path()==self.target.text() and n.type().category()==hou.lopNodeTypeCategory()]
            if len(nodes)>1:raise ValueError('Select only one upstream LOP.')
            if nodes:upstream=nodes[0]
        node=ops.import_asset(source,row['effective_kind'],row['label'],self.target.text(),upstream,'sublayer' if self.usdmode.currentIndex() else 'reference',self.assign.text(),add_variant_switch='variant' in row['tags'].split())
        node.setSelected(True,clear_all_selected=True); node.setDisplayFlag(True)
        self.status.setText('Imported: '+node.path()+(' (FBX/glTF source materials are not rebuilt automatically)' if source.suffix.lower() in {'.fbx','.gltf','.glb'} else ''))
        return node

    def create_manifest(self):
        folder=self.current_folder()
        base=core.inside(folder[2],folder[1]) if folder else Path(self.default_root)
        if self.items.currentItem():base=self.library.resolve(self.selected()).parent
        dialog=ManifestDialog(base,self)
        if dialog.exec()!=QtWidgets.QDialog.DialogCode.Accepted:return
        name=dialog.name.text().strip()
        if not name or any(c in name for c in '/\\:*?"<>|'):raise ValueError('Enter a valid set name.')
        maps={key:field.text().strip() for key,field in dialog.maps.items() if field.text().strip()}
        if not maps:raise ValueError('Choose at least one map.')
        dest=base/(name+('.decal.json' if dialog.kind.currentIndex() else '.pbr.json'))
        resolved={}
        for key,value in maps.items():
            p=Path(hou.expandString(value)).resolve()
            if not p.is_file() and '<UDIM>' not in str(p):raise FileNotFoundError(str(p))
            try:resolved[key]=Path(os.path.relpath(p,base)).as_posix()
            except ValueError:resolved[key]=p.as_posix()
        data={'schema':1,'maps':resolved,'color_space':dialog.color.currentText(),'displacement_scale':dialog.scale.value()}
        with dest.open('x',encoding='utf-8') as stream:json.dump(data,stream,ensure_ascii=False,indent=2)
        self.scan(); self.status.setText('Set saved: '+str(dest))

    def catalog_directory(self):
        if not self.default_root:raise ValueError('Choose a library for USD / Catalog output in Libraries.')
        return Path(self.default_root)/'Catalog'

    def catalog_path(self):
        selected=self.settings.get('catalogs',{}).get(core.path_key(self.default_root))
        if selected:return core.inside(self.default_root,selected) if not Path(selected).is_absolute() else Path(selected)
        return self.catalog_directory()/'solaris_assets.db'

    def refresh_catalogs(self):
        if not hasattr(self,'catalogs'):return
        self.catalogs.blockSignals(True);self.catalogs.clear()
        if self.default_root:
            current=self.catalog_path()
            folder=self.catalog_directory()
            paths=sorted((p for p in folder.rglob('*') if p.is_file() and p.suffix.lower() in {'.db','.sqlite','.sqlite3'}),key=lambda p:str(p).lower()) if folder.exists() else []
            if current not in paths:paths.append(current)
            for path in paths:
                try:label=path.relative_to(folder).as_posix()
                except ValueError:label=str(path)
                self.catalogs.addItem(label+(' (new)' if not path.exists() else ''),str(path))
                self.catalogs.setItemData(self.catalogs.count()-1,str(path),QtCore.Qt.ItemDataRole.ToolTipRole)
            selected=next((i for i in range(self.catalogs.count()) if core.path_key(self.catalogs.itemData(i))==core.path_key(current)),-1)
            self.catalogs.setCurrentIndex(selected)
        self.catalogs.blockSignals(False)

    def use_catalog(self,path):
        path=Path(path).resolve()
        try:value=path.relative_to(Path(self.default_root).resolve()).as_posix()
        except ValueError:value=str(path)
        self.settings.setdefault('catalogs',{})[core.path_key(self.default_root)]=value
        self.save_settings();self.refresh_catalogs()
        self.status.setText('Catalog target: '+str(path))

    def catalog_changed(self,index):
        if index>=0:self.safe(lambda:self.use_catalog(self.catalogs.itemData(index)))

    def select_catalog(self):
        path,_=QtWidgets.QFileDialog.getOpenFileName(self,'Select Catalog',str(self.catalog_directory()),'Asset Catalog (*.db *.sqlite *.sqlite3)')
        if path:
            source=hou.AssetGalleryDataSource(path)
            if not source.isValid():raise ValueError('Invalid Asset Catalog')
            self.use_catalog(path)

    def new_catalog(self):
        path,_=QtWidgets.QFileDialog.getSaveFileName(self,'New Catalog',str(self.catalog_directory()/'NewCatalog.db'),'Asset Catalog (*.db)')
        if not path:return
        path=Path(path)
        if not path.suffix:path=path.with_suffix('.db')
        if path.exists():raise FileExistsError('Choose a new filename. Existing catalogs are never overwritten.')
        path.parent.mkdir(parents=True,exist_ok=True)
        source=hou.AssetGalleryDataSource(path.as_posix())
        if not source.isValid() or source.isReadOnly():raise ValueError('Could not create a writable Asset Catalog')
        source.startTransaction();source.endTransaction(True)
        self.use_catalog(path)

    def build_catalog_menu(self):
        menu=QtWidgets.QMenu(self)
        menu.addAction('Open Catalog',lambda:self.safe(self.open_catalog))
        menu.addAction('Select Catalog...',lambda:self.safe(self.select_catalog))
        menu.addAction('New Catalog...',lambda:self.safe(self.new_catalog))
        return menu

    def catalog_menu(self,position):
        menu=self.build_catalog_menu()
        try:menu.exec(self.catalogs.mapToGlobal(position))
        finally:menu.deleteLater()

    def add_catalog(self):
        rows=self.selected_rows()
        if any(row['kind']!='usd' for row in rows):raise ValueError('Select USD assets only.')
        items=[]
        for row in rows:
            path=self.library.resolve(row);thumb=self.thumbnail_path(row)
            item,added=ops.register_catalog(path,self.catalog_path(),row['label'],row['tags'],str(thumb) if thumb else '',backup_dir=self.data_dir/'backups')
            items.append(item)
        self.refresh_catalogs()
        self.status.setText(f'Catalog ready: {len(items)} USD assets (existing entries kept).')
        return items

    def open_catalog(self):
        path=self.catalog_path()
        if not path.exists():raise ValueError('Add a USD asset to the Catalog first.')
        source=hou.AssetGalleryDataSource(path.as_posix())
        hou.ui.setSharedLayoutDataSource(source)
        pane=hou.ui.curDesktop().createFloatingPaneTab(hou.paneTabType.PythonPanel)
        pane.setActiveInterface(hou.pypanel.interfaceByName('asset_gallery'))

    def publish_asset(self):
        row=self.selected(); path=self.library.resolve(row)
        if not self.default_root:raise ValueError('Choose a library for USD output.')
        base=Path(self.default_root)/'USD'/ops.safe_name(row['label'])
        dest,_=QtWidgets.QFileDialog.getSaveFileName(self,'Publish Asset at Current Frame (textures remain linked)',str(base/(ops.safe_name(row['label'])+'.usd')),'USD (*.usd *.usda *.usdc)')
        if not dest:return
        if Path(dest).exists():raise FileExistsError('Choose a new version name. Existing USD files will not be overwritten.')
        # Isolated temporary network: never export the user's entire shot.
        network=hou.node('/obj').createNode('lopnet','sal_publish')
        try:
            node=ops.import_asset(path,row['effective_kind'],row['label'],network.path())
            ops.export_stage(node,dest)
        finally:network.destroy()
        self.scan(); self.status.setText('USD exported: '+dest+' / Add to Catalog after rescanning')

def show_window():
    dialog=QtWidgets.QDialog(hou.qt.mainWindow())
    dialog.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
    dialog.setWindowTitle('NanakusaAssetLibrary'); dialog.resize(1250,820)
    layout=QtWidgets.QVBoxLayout(dialog); panel=LibraryWidget(); layout.addWidget(panel)
    _windows.append(dialog)
    dialog.destroyed.connect(lambda: _windows.remove(dialog) if dialog in _windows else None)
    dialog.show()
    return panel
