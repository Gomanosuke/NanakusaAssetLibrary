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
from . import core, houdini_ops as ops, dragdrop, storage, organize, pbr, embedded, proxy_gen, element_gen, lod_gen
from . import reveal as file_browser

ROLE = QtCore.Qt.ItemDataRole.UserRole
STACK_ROLE = dragdrop.STACK_ROLE   # ids of the assets a list item stands for
LAZY_ROLE = QtCore.Qt.ItemDataRole.UserRole + 2   # folder item whose children are not created yet
KINDS = {'': 'All Types', 'usd': 'USD', 'model': '3DModel', 'texture': 'Texture'}
KIND_COLORS = {'usd':'#3b6677','model':'#466c58','hdri':'#796a39','material':'#665488','pbr':'#665488','decal':'#805457','texture':'#496878'}
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
_session_scanned = False   # the first panel of a Houdini session rescans (LibraryWidget.AUTO_SCAN)

def plain_python_env(hfs):
    """Environment for Houdini's plain Python (asset_info.py / thumbnail_scene.py 'plain' mode): its
    DLLs on PATH, and none of Houdini's own USD plugin paths (loading those starts the whole Houdini
    engine, which is what makes hython take seconds to start)."""
    env=dict(os.environ);env['HFS']=hfs
    env['PATH']=str(Path(hfs)/'bin')+os.pathsep+env.get('PATH','')
    env.pop('PXR_PLUGINPATH_NAME',None)
    return env

class AssetInfoJob(QtCore.QThread):
    done=QtCore.Signal(object,object)

    def __init__(self,key,path,kind):
        super().__init__();self.key,self.path,self.kind=key,path,kind
        self.hfs=hou.getenv('HFS')   # hou is only used on the main thread
        self.python=ScanJob.find_python(self.hfs) if kind in ('usd','texture') else None

    def call(self,command,env=None):
        process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,**background())
        started=time.monotonic()
        try:
            while True:
                if self.isInterruptionRequested():return None
                if time.monotonic()-started>60:raise RuntimeError('Information read timed out')
                try:
                    out,err=process.communicate(timeout=.05);break
                except subprocess.TimeoutExpired:pass
            lines=[line[9:] for line in out.decode('utf-8',errors='replace').splitlines() if line.startswith('NAL_INFO:')]
            if not lines:raise RuntimeError(err.decode(errors='replace')[-500:] or 'Could not read asset information')
            return json.loads(lines[-1])
        finally:
            if process.poll() is None:process.kill();process.communicate()

    def run(self):
        script=str(Path(__file__).with_name('asset_info.py'))
        try:
            result={'retry':''}
            if self.python is not None:   # fast path (see asset_info.py); falls through to hython on 'retry'
                try:result=self.call([str(self.python),script,str(self.path),self.kind,'plain'],plain_python_env(self.hfs))
                except Exception as exc:result={'retry':str(exc)}
            if result is not None and 'retry' in result:
                executable=Path(self.hfs)/'bin'/('hython.exe' if os.name=='nt' else 'hython')
                result=self.call([str(executable),script,str(self.path),self.kind])
            if result is None:return   # interrupted
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

def fitted_image(path, edge):
    """Fit without stretching, cropping or baking black bars into the image. Safe off the UI
    thread (QImage only), so list icons are decoded in IconDecode workers."""
    reader=QtGui.QImageReader(str(path))
    size=reader.size()
    if size.isValid():
        reader.setScaledSize(size.scaled(edge,edge,QtCore.Qt.AspectRatioMode.KeepAspectRatio))
    image=reader.read()
    if image.isNull():return None
    image=image.scaled(edge,edge,QtCore.Qt.AspectRatioMode.KeepAspectRatio,QtCore.Qt.TransformationMode.SmoothTransformation)
    out=QtGui.QImage(edge,edge,QtGui.QImage.Format.Format_ARGB32_Premultiplied);out.fill(QtCore.Qt.GlobalColor.transparent)
    painter=QtGui.QPainter(out)
    painter.drawImage((edge-image.width())//2,(edge-image.height())//2,image);painter.end()
    return out

def square_preview(path, edge):
    image=fitted_image(path,edge)
    return None if image is None else QtGui.QPixmap.fromImage(image)

class IconSink(QtCore.QObject):
    """Lives on the UI thread; IconDecode workers report through it (queued across threads)."""
    decoded=QtCore.Signal(object,object)

class IconDecode(QtCore.QRunnable):
    def __init__(self,path,edge,key,sink):
        super().__init__()
        self.path,self.edge,self.key,self.sink=path,edge,key,sink   # holds the sink alive while running
    def run(self):
        try:image=fitted_image(self.path,self.edge)
        except Exception:image=None
        try:self.sink.decoded.emit(self.key,image)
        except RuntimeError:pass   # the panel was closed meanwhile

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

class VariantTagJob(QtCore.QThread):
    """Sync the automatic "lod" / "variant" tags with the variant sets USD files really have
    (variant_scan.py, Houdini's plain Python, only new or changed files)."""
    done = QtCore.Signal(object)
    def __init__(self, library):
        super().__init__()
        self.library = library
        self.hfs = hou.getenv('HFS')
        self.python = ScanJob.find_python(self.hfs)

    def run(self):
        result = {'checked': 0, 'changed': 0, 'errors': []}
        try:
            process = subprocess.Popen([str(self.python), str(Path(__file__).with_name('variant_scan.py')), str(self.library.data_dir)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=plain_python_env(self.hfs), **background())
            try:
                while True:
                    if self.isInterruptionRequested():
                        return
                    try:
                        out, err = process.communicate(timeout=.2); break
                    except subprocess.TimeoutExpired:
                        pass
            finally:
                if process.poll() is None:
                    process.kill(); process.communicate()
            lines = [line[9:] for line in out.decode('utf-8', errors='replace').splitlines() if line.startswith('NAL_TAGS:')]
            if not lines:
                raise RuntimeError(err.decode(errors='replace')[-500:] or 'The variant check failed')
            result = json.loads(lines[-1])
        except Exception as exc:
            result['errors'].append(str(exc))
        self.done.emit(result)

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
        self.hfs = hou.getenv('HFS'); self.bin = Path(self.hfs)/'bin'
        self.python = ScanJob.find_python(self.hfs) if kind == 'usd' else None

    def execute(self, args, log, env=None):
        with log.open('wb') as stream:
            process = subprocess.Popen(args, stdout=stream, stderr=stream, env=env, **background())
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
                prepare = [str(Path(__file__).with_name('thumbnail_scene.py')), self.source, self.kind, str(base/'scene.usda'), str(base/'camera.json')]
                try:
                    if self.python is None: raise RuntimeError('no plain Python')
                    # USD is framed by plain Python first (seconds faster); hython if that cannot compose it.
                    self.execute([str(self.python)] + prepare + ['plain'], base/'prepare.log', plain_python_env(self.hfs))
                except RuntimeError:
                    if self.isInterruptionRequested(): raise
                    (base/'scene.usda').unlink(missing_ok=True)
                    self.execute([str(self.bin/('hython'+suffix))] + prepare, base/'prepare.log')
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
        temp_usd = source.with_name(source.stem+core.GENERATE_TMP+source.suffix)
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
        if 'restyled' in result:
            return f"Already has a proxy; proxy material fixed ({len(result['restyled'])} mesh(es))"
        return f"Proxy added ({len(result['proxied'])} mesh(es))"


class ElementJob(_MeshGenerateJob):
    """Add an "element" variant set to a multi-object USD pack so only one shows (element_gen.py)."""
    script, label = 'element_gen.py', 'Element switch generation'
    def __init__(self, asset_id, source, backup_dir):
        super().__init__(asset_id, source, backup_dir)
    def summary(self, result):
        switch = result['element_switch']
        moved = ', moved to the top prim' if switch.get('moved_from') else ''
        return f"Element switch added ({len(switch['variants'])} elements{moved})"


class ElementDeleteJob(_MeshGenerateJob):
    """Undo ElementJob: remove a previously-added "element" variant set (element_gen.py remove mode)."""
    script, label = 'element_gen.py', 'Element switch removal'
    def __init__(self, asset_id, source, backup_dir):
        super().__init__(asset_id, source, backup_dir, ('remove',))
    def summary(self, result):
        return f"Element switch removed ({len(result['removed_element_switch'])} prim(s))"


class LodJob(_MeshGenerateJob):
    """Add a "LOD" variant set (LOD_1 = as authored, then reduced copies) for Auto Select LOD / Stage Manager (lod_gen.py)."""
    script, label = 'lod_gen.py', 'LOD generation'
    def __init__(self, asset_id, source, backup_dir, levels, keep):
        super().__init__(asset_id, source, backup_dir, ('add', levels, keep))
    def summary(self, result):
        lods = result['lods']
        return f"LODs added ({len(lods['variants'])} levels, {' / '.join(f'{t:,}' for t in lods['triangles'])} triangles)"


class LodDeleteJob(_MeshGenerateJob):
    """Undo LodJob: remove the "LOD" variant set and everything it defined (lod_gen.py remove mode)."""
    script, label = 'lod_gen.py', 'LOD removal'
    def __init__(self, asset_id, source, backup_dir):
        super().__init__(asset_id, source, backup_dir, ('remove',))
    def summary(self, result):
        return f"LODs removed ({len(result['removed_lods'])} prim(s))"


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
    ICON_THREADS = max(2, min(4, QtCore.QThread.idealThreadCount() - 1))
    AUTO_TAGS = True   # sync "lod" / "variant" / "proxy" tags from USD files after scans and on opening (tests switch it off)
    AUTO_SCAN = True   # Rescan when the first panel of a Houdini session opens (tests switch it off)
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
        self.lod_queue = deque()
        self.lod_pending = set()
        self.lod_failed = set()
        self.lod_job = None
        self.missing_lod_scan = None
        self.lod_delete_queue = deque()
        self.lod_delete_pending = set()
        self.lod_delete_failed = set()
        self.lod_delete_job = None
        self.info_job=None;self.info_pending=None;self.info_key=None;self.info_cache={};self.info_ids={}
        self.metadata_id=None;self.metadata_ids=[];self.pending_tags=None;self.folder_items={};self.folder_kids={};self.folder_roots={};self.folder_migrations=set();self.folder_migration_jobs={};self.row_index={};self.entry_of={};self.item_index={};self.member_index={};self.stream=None;self.total=0;self.icon_cache={};self.no_embedded=set();self.preview_cache=OrderedDict();self.placeholders={}
        self.icon_todo=deque();self.icons_loaded=set();self.page_entries=[];self.scroll_timer=QtCore.QTimer(self);self.scroll_timer.setSingleShot(True);self.scroll_timer.setInterval(30);self.scroll_timer.timeout.connect(self.scrolled);self.icon_timer=QtCore.QTimer(self);self.icon_timer.setSingleShot(True);self.icon_timer.timeout.connect(self.load_icons)
        # Pictures are decoded on a few worker threads; icon_waiting maps a decode in flight to the items waiting for it.
        self.icon_pool=QtCore.QThreadPool(self);self.icon_pool.setMaxThreadCount(self.ICON_THREADS)
        self.icon_sink=IconSink();self.icon_sink.decoded.connect(self.icon_decoded);self.icon_waiting={}
        self.info_wanted=None;self.info_timer=QtCore.QTimer(self);self.info_timer.setSingleShot(True);self.info_timer.setInterval(250);self.info_timer.timeout.connect(self.start_info)
        self.missing_scan=None
        self.tag_job=None;self.tag_again=False
        self.tag_timer=QtCore.QTimer(self);self.tag_timer.setSingleShot(True);self.tag_timer.timeout.connect(self.start_variant_tags)
        dragdrop.install()
        self._setup()
        self.rebuild_tree()
        self.refresh()
        if self.AUTO_SCAN and not _session_scanned and self.settings.get('rescan_on_first_open',True) and self.library.roots():
            # First panel of this Houdini session: pick up what changed on disk while Houdini was
            # closed. The scan runs in its own process; its end also starts the tag check.
            QtCore.QTimer.singleShot(800,self,self.first_open_scan)   # bound to this panel: dropped if it closes first
        elif self.AUTO_TAGS:self.tag_timer.start(1500)   # after the panel has drawn: new / changed files only

    def _button(self, text, callback, layout):
        button = QtWidgets.QPushButton(text)
        button.clicked.connect(lambda checked=False: self.safe(callback))
        layout.addWidget(button)
        return button

    def closeEvent(self,event):
        for timer in (self.scroll_timer,self.icon_timer,self.info_timer,self.folder_timer,self.search_timer,self.jobs_timer,self.tag_timer):timer.stop()
        self.missing_scan=None;self.close_stream()
        self.icon_todo.clear();self.icon_pool.clear();self.icon_pool.waitForDone(2000)
        # A folder migration only reads/writes the index; stop it and wait briefly so it never
        # outlives a data directory that is about to be moved or removed (as in tests' temp dirs).
        for job in list(self.folder_migration_jobs.values()):job.requestInterruption();job.wait(2000)
        super().closeEvent(event)

    def event(self,event):
        # Houdini's Python Panel can retain keyboard focus on the root widget.
        if event.type()==QtCore.QEvent.Type.ShortcutOverride and (event.matches(QtGui.QKeySequence.StandardKey.SelectAll) or event.matches(QtGui.QKeySequence.StandardKey.Find)):
            event.accept();return True
        return super().event(event)

    def keyPressEvent(self,event):
        if event.matches(QtGui.QKeySequence.StandardKey.SelectAll):
            self.items.selectAll();event.accept();return
        if event.matches(QtGui.QKeySequence.StandardKey.Find):   # a key press, not a QShortcut: never Houdini-wide
            self.search.setFocus();self.search.selectAll();event.accept();return
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
        # One toolbar row (search, filters, options, rescan): the list gets the height a title row used to take.
        filters = QtWidgets.QHBoxLayout()
        self.search = QtWidgets.QLineEdit(); self.search.setPlaceholderText('Search names, paths, tags...  (Ctrl+F)')
        self.search.setClearButtonEnabled(True)
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
        self.more=QtWidgets.QToolButton(); self.more.setText('Options'); self.more.setCheckable(True); filters.addWidget(self.more)
        self.more.setToolTip('Import target, USD reference / sublayer, material prim pattern, subfolders')
        self.scan_button = self._button('Rescan', self.scan, filters)
        self.scan_button.setToolTip('Re-index the selected library (all libraries when "All Libraries" is selected). Source files are only read.')
        self.cancel_button = self._button('Cancel Scan', self.cancel_scan, filters); self.cancel_button.setVisible(False)
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
        # Background work (thumbnails, proxies, element switches) at a glance, with one way to stop it all.
        self.jobs_bar=QtWidgets.QWidget(); jobs=QtWidgets.QHBoxLayout(self.jobs_bar); jobs.setContentsMargins(0,0,0,0)
        self.jobs_label=QtWidgets.QLabel(); jobs.addWidget(self.jobs_label,1)
        self.jobs_cancel=self._button('Cancel All',self.cancel_all_jobs,jobs)
        self.jobs_cancel.setToolTip('Drop every queued thumbnail / proxy / element switch job. Jobs already running finish first.')
        outer.addWidget(self.jobs_bar); self.jobs_bar.setVisible(False)
        self.jobs_timer=QtCore.QTimer(self); self.jobs_timer.setInterval(400); self.jobs_timer.timeout.connect(self.update_jobs_bar); self.jobs_timer.start()
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
        if self.lod_pending or self.missing_lod_scan is not None:raise ValueError('Wait for LOD generation to finish (or use Libraries... > Cancel LODs) before moving assets.')
        if self.lod_delete_pending:raise ValueError('Wait for LOD removal to finish before moving assets.')
        self.save_metadata()
        root_id,dest_rel,_=target;select=None
        if 'assets' in payload:
            result=organize.move_assets(self.library,self.dragged_rows(payload['assets']),dest_rel,self.data_dir)
            message=f"Moved {result['moved']} asset(s) to {dest_rel}"
            if result.get('companions'):
                # USD files that share their folder's textures move as that whole folder.
                folders=', '.join(Path(f).name for f in result['folders'])
                message+=f" / Moved together (same folder {folders}): {', '.join(result['companions'])}"
        else:
            result=organize.move_folders(self.library,root_id,[f['rel'] for f in payload['folders']],dest_rel,self.data_dir)
            message=f"Moved {result['folders']} folder(s) ({result['moved']} assets) to {dest_rel}";select=(root_id,result['new_rel'],target[2])
        try:
            relinked=ops.relink_catalog_paths([self.catalogs.itemData(i) for i in range(self.catalogs.count())],result['catalog'],self.data_dir/'backups'/'catalog') if result['catalog'] else 0
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
            pix=QtGui.QPixmap(144,144);pix.fill(QtGui.QColor(KIND_COLORS.get(kind,'#555555')))
            icon=self.placeholders[kind]=QtGui.QIcon(pix)
        return icon

    def load_icons(self):
        """Hand the queued items' pictures to the decode workers, a few at a time and nearest first
        (schedule_icons keeps icon_todo ordered by distance to the view), so the UI thread only
        looks files up and sets icons. Cached and missing pictures are shown at once."""
        deadline=time.monotonic()+0.012
        while self.icon_todo and len(self.icon_waiting)<2*self.ICON_THREADS and time.monotonic()<deadline:
            index=self.icon_todo.popleft()
            if index in self.icons_loaded or index>=self.items.count() or index>=len(self.page_entries):continue
            row=self.page_entries[index]['rep']
            path,key=self.icon_source(row)
            if key is None or key in self.icon_cache:
                self.show_icon(index,self.missing_icon(row) if key is None else self.icon_cache[key]);continue
            waiting=self.icon_waiting.setdefault(key,[])
            if not waiting:self.icon_pool.start(IconDecode(path,key[2],key,self.icon_sink))
            waiting.append((index,row['id']));self.icons_loaded.add(index)
        if self.icon_todo and len(self.icon_waiting)<2*self.ICON_THREADS:self.icon_timer.start(0)

    def icon_decoded(self,key,image):
        waiting=self.icon_waiting.pop(key,[])
        icon=None
        if image is not None and not image.isNull():
            icon=QtGui.QIcon(QtGui.QPixmap.fromImage(image))
            if len(self.icon_cache)>=300*256*256//(key[2]*key[2]):self.icon_cache.pop(next(iter(self.icon_cache)))
            self.icon_cache[key]=icon
        for index,aid in waiting:
            # Skip items scrolled far away (their icon was dropped) or replaced by a refresh meanwhile.
            if index not in self.icons_loaded or index>=len(self.page_entries) or self.page_entries[index]['rep']['id']!=aid:continue
            row=self.page_entries[index]['rep']
            self.show_icon(index,icon if icon is not None else self.missing_icon(row))
        if self.icon_todo:self.icon_timer.start(0)

    def icons_pending(self):
        """True while pictures are still queued or being decoded (tests and benchmarks wait on it)."""
        return bool(self.icon_todo or self.icon_waiting or self.icon_timer.isActive())

    def show_icon(self,index,icon):
        entry=self.page_entries[index]
        if len(entry['rows'])>1:icon=self.stacked_icon(icon,len(entry['rows']))
        else:
            words=entry['rep']['tags'].split()
            labels=[label for tag,label in (('variant','VARIANT'),('lod','LOD'),('proxy','PROXY')) if tag in words]
            if labels:icon=self.variant_badge(icon,labels)
        self.items.item(index).setIcon(icon);self.icons_loaded.add(index)

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

    def variant_badge(self,icon,labels=('VARIANT',)):
        """Small corner markers for a USD carrying the automatic "variant" / "lod" / "proxy" tags,
        so what it offers is recognisable without opening its info panel."""
        edge=self.icon_edge();out=QtGui.QPixmap(edge,edge);out.fill(QtCore.Qt.GlobalColor.transparent)
        painter=QtGui.QPainter(out);painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing);painter.scale(edge/256,edge/256)
        painter.drawPixmap(QtCore.QRect(0,0,256,256),icon.pixmap(edge,edge))
        font=painter.font();font.setBold(True);font.setPixelSize(14);painter.setFont(font)
        x=6
        for label in labels:
            width={'VARIANT':96,'LOD':52,'PROXY':82}.get(label,96)
            color={'VARIANT':(45,95,180),'LOD':(150,95,30),'PROXY':(40,130,70)}.get(label,(90,90,90))
            painter.setPen(QtCore.Qt.PenStyle.NoPen);painter.setBrush(QtGui.QColor(*color))
            painter.drawRoundedRect(x,6,width,28,6,6)
            painter.setPen(QtGui.QColor('#ffffff'));painter.drawText(QtCore.QRect(x,6,width,28),QtCore.Qt.AlignmentFlag.AlignCenter,label)
            x+=width+6
        painter.end()
        return QtGui.QIcon(out)

    def icon_edge(self):
        return 512 if self.items.iconSize().width()>256 else 256

    def icon_size_finished(self,size):
        # Larger icons need larger source pictures; rebuild the list only when that changes.
        self.settings['icon_size']=size;self.safe(self.save_settings)
        edge=self.icon_edge()
        if edge!=getattr(self,'icons_built_at',256):self.icon_cache.clear();self.refresh()
        self.status.setText(f'Icon size: {size}px (Ctrl + mouse wheel or Ctrl + middle-drag to change)')

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
            if message and (message.startswith('Proxy added') or 'already has a proxy' in message.lower()):
                self.set_tag(aid,'proxy',True)
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

    def queue_lod(self,row,levels,keep):
        aid=row['id']
        if row['kind']!='usd' or aid in self.lod_pending or aid in self.lod_failed:return
        self.lod_pending.add(aid);self.lod_queue.append((row,levels,keep))
        QtCore.QTimer.singleShot(0,self.next_lod)

    def next_lod(self):
        if self.lod_job is not None or not self.lod_queue:return
        row,levels,keep=self.lod_queue.popleft()
        source=Path(row['root_path'])/row['relpath']
        self.lod_job=LodJob(row['id'],source,self.data_dir/'backups'/'lod',levels,keep)
        self.lod_job.done.connect(self.lod_done)
        self.lod_job.finished.connect(self.lod_finished)
        _keep_job(self.lod_job)

    def lod_finished(self):
        self.lod_job=None;self.next_lod()

    def lod_done(self,aid,message,error):
        self.lod_pending.discard(aid)
        if error:
            self.lod_failed.add(aid);self.status.setText('LOD generation failed: '+error)
        else:
            if message and message.startswith('LODs added'):self.set_tag(aid,'lod',True)
            self.status.setText((message or 'Already has LODs')+f' ({len(self.lod_queue)} remaining)')

    def queue_lod_delete(self,row):
        aid=row['id']
        if row['kind']!='usd' or aid in self.lod_delete_pending or aid in self.lod_delete_failed:return
        self.lod_delete_pending.add(aid);self.lod_delete_queue.append(row)
        QtCore.QTimer.singleShot(0,self.next_lod_delete)

    def next_lod_delete(self):
        if self.lod_delete_job is not None or not self.lod_delete_queue:return
        row=self.lod_delete_queue.popleft()
        source=Path(row['root_path'])/row['relpath']
        self.lod_delete_job=LodDeleteJob(row['id'],source,self.data_dir/'backups'/'lod')
        self.lod_delete_job.done.connect(self.lod_delete_done)
        self.lod_delete_job.finished.connect(self.lod_delete_finished)
        _keep_job(self.lod_delete_job)

    def lod_delete_finished(self):
        self.lod_delete_job=None;self.next_lod_delete()

    def lod_delete_done(self,aid,message,error):
        self.lod_delete_pending.discard(aid)
        if error:
            self.lod_delete_failed.add(aid);self.status.setText('LOD removal failed: '+error)
        else:
            if message and message.startswith('LODs removed'):self.set_tag(aid,'lod',False)
            self.status.setText((message or 'No LODs to remove')+f' ({len(self.lod_delete_queue)} remaining)')

    def set_variant_tag(self,aid,present):
        self.set_tag(aid,'variant',present)

    def set_tag(self,aid,tag,present):
        """Keep an automatic tag ("variant" for an element switch, "lod" for LODs) and the thumbnail
        badge it drives in sync with what a job just added or removed, without opening the file
        again. Assets not loaded in the list (a bulk queue) are updated in the index directly."""
        row=self.row_index.get(aid)
        if row is None:
            found=self.library.assets_by_ids([aid])
            if not found:return
            row=found[0]
        words=row['tags'].split()
        if present:
            if tag in words:return
            words.append(tag)
        else:
            if tag not in words:return
            words=[w for w in words if w!=tag]
        tags=' '.join(words)
        self.library.update(aid,tags=tags)
        if aid in self.row_index:self.update_metadata_rows(aid,tags=tags)
        index=self.item_index.get(aid)
        if index is not None:self.icons_loaded.discard(index);self.icon_todo.appendleft(index);self.icon_timer.start(0)
        current=self.items.currentItem()
        if current is not None and current.data(ROLE)==aid:self.selection_changed()

    def icon_source(self,row):
        """(picture path, cache key) of a row's list icon; key is None when there is no picture."""
        path=self.thumbnail_path(row)
        if path:
            try:return path,(str(path),path.stat().st_mtime_ns,self.icon_edge())
            except OSError:pass
        return path,None

    def icon_for(self,row):
        """The row's icon, decoded on the calling thread (load_icons uses the workers instead)."""
        path,key=self.icon_source(row)
        if key in self.icon_cache:return self.icon_cache[key]
        if path:
            pix=square_preview(path,self.icon_edge())
            if pix is not None:
                icon=QtGui.QIcon(pix)
                if key is not None:
                    if len(self.icon_cache)>=300*256*256//(key[2]*key[2]):self.icon_cache.pop(next(iter(self.icon_cache)))
                    self.icon_cache[key]=icon
                return icon
        return self.missing_icon(row)

    def missing_icon(self,row):
        """Kind-coloured card while a thumbnail does not exist yet (and ask for one)."""
        self.queue_thumbnail(row)
        kind=row['effective_kind'];icon=self.placeholders.get(('labelled',kind))
        if icon is None:
            pix=QtGui.QPixmap(144,144);pix.fill(QtGui.QColor(KIND_COLORS.get(kind,'#555555')))
            painter=QtGui.QPainter(pix);painter.setPen(QtGui.QColor('#eeeeee'));painter.drawText(pix.rect(),QtCore.Qt.AlignmentFlag.AlignCenter,kind.upper());painter.end()
            icon=self.placeholders[('labelled',kind)]=QtGui.QIcon(pix)
        return icon

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
        rows=self.selected_rows();menu=QtWidgets.QMenu(self);menu.setToolTipsVisible(True)
        usd=bool(rows) and all(row['kind']=='usd' for row in rows)
        def action(label,callback,tip=''):
            item=menu.addAction(label,lambda:self.safe(callback))
            if tip:item.setToolTip(tip)
        menu.addSection(f'{len(rows)} selected' if len(rows)!=1 else rows[0]['label'])
        action('Import Selected',self.import_selected,'Same as double-click: into the Import Target set in Options.');action('Copy Paths',self.copy_path)
        action('Show in Explorer',self.reveal)
        if usd:action('Add Catalog',self.add_catalog)
        menu.addSection('Thumbnail')
        action('Generate Selected Thumbnails',self.generate_thumbnail)
        if len(rows)==1:action('Choose Thumbnail...',self.choose_thumbnail)
        if usd:
            menu.addSection('USD (edits the file; a backup is kept)')
            action('Generate Selected Proxies...',self.generate_proxy,'Adds a decimated purpose=proxy copy of each mesh.')
            action('Generate Selected Element Switch...',self.generate_element,'Only for packs of alternate objects: adds an "element" variant set.')
            action('Delete Element Switch',self.generate_element_delete,'Undo the element switch (assets without one are skipped).')
            action('Generate Selected LODs...',self.generate_lods,'Adds a "LOD" variant set (LOD_1 = as is, then reduced copies) for Auto Select LOD, Stage Manager and Set Variant.')
            action('Delete LODs',self.generate_lod_delete,'Undo the LODs, restoring the file as it was (assets without LODs are skipped).')
        if len(rows)==1:
            menu.addSeparator()
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
        if self.AUTO_TAGS:self.start_variant_tags()

    def first_open_scan(self):
        global _session_scanned
        if _session_scanned:return   # another panel opened at the same time got there first
        _session_scanned=True
        self.scan()
        self.status.setText('Rescanning after Houdini started (turn off with "rescan_on_first_open": false in settings.json)... '+self.status.text())

    def start_variant_tags(self):
        """Open new / changed USD files in the background and set their "lod" / "variant" tags."""
        if self.tag_job is not None:
            self.tag_again=True;return   # a scan finished while checking: check its files too
        job=VariantTagJob(self.library)
        if job.python is None:return
        self.tag_job=job;job.done.connect(self.variant_tags_done)
        _keep_job(job)

    def variant_tags_done(self,result):
        self.tag_job=None
        if result.get('changed'):
            self.refresh()   # the list holds the old tags; refresh keeps the scroll position
            self.status.setText(f"Tagged from USD variant sets: {result['changed']} asset(s) got or lost the lod / variant tag"
                                +(' ('+'; '.join(result['errors'][:2])+')' if result.get('errors') else ''))
        if self.tag_again:
            self.tag_again=False;self.start_variant_tags()

    def new_folder(self):
        folder=self.current_folder()
        if not folder:raise ValueError('Select the parent folder on the left.')
        if not folder[1]:raise ValueError('Choose a folder under USD, Texture or 3DModel. These top-level categories are fixed.')
        if folder[1].startswith('USD/') and core.package_entries(core.inside(folder[2],folder[1])):
            raise ValueError('Cannot create folders inside a USD package (or a folder of USD files sharing its textures).')
        name,ok=QtWidgets.QInputDialog.getText(self,'New Folder','Name (use / for nested folders)')
        if ok and name.strip():
            path=core.inside(core.inside(folder[2],folder[1]),name.strip()); path.mkdir(parents=True,exist_ok=True)
            self.library.add_folder(folder[0],path.relative_to(Path(folder[2])).as_posix())
            self.rebuild_tree(); self.status.setText('Created: '+str(path))

    def job_counts(self):
        """Background work still to do per kind: (label, queued + running, cancel callback)."""
        def count(queue,job):return len(queue)+(1 if job is not None else 0)
        return [('Thumbnails',count(self.thumb_queue,self.thumb_job),self.cancel_thumbnails),
                ('Proxies',count(self.proxy_queue,self.proxy_job),self.cancel_proxies),
                ('Element switches',count(self.element_queue,self.element_job),self.cancel_elements),
                ('Element switch removals',count(self.element_delete_queue,self.element_delete_job),self.cancel_element_deletes),
                ('LODs',count(self.lod_queue,self.lod_job),self.cancel_lods),
                ('LOD removals',count(self.lod_delete_queue,self.lod_delete_job),self.cancel_lod_deletes)]

    def update_jobs_bar(self):
        parts=[f'{label} {n}' for label,n,_ in self.job_counts() if n]
        if self.missing_scan is not None:parts.append('checking thumbnails')
        if self.missing_proxy_scan is not None:parts.append('checking USD for proxies')
        if self.missing_lod_scan is not None:parts.append('checking USD for LODs')
        if self.tag_job is not None:parts.append('reading USD variant sets for tags')
        if parts:self.jobs_label.setText('Background: '+'  ·  '.join(parts)+' remaining')
        self.jobs_bar.setVisible(bool(parts))

    def cancel_all_jobs(self):
        for _,n,cancel in self.job_counts():cancel()
        self.missing_scan=None;self.missing_proxy_scan=None;self.missing_lod_scan=None
        self.update_jobs_bar()
        self.status.setText('Cancelled every queued background job. Jobs already running finish first.')

    def build_root_menu(self):
        menu=QtWidgets.QMenu(self)
        def action(label,callback,enabled=True,tip=''):
            item=menu.addAction(label,lambda:self.safe(callback));item.setEnabled(enabled)
            if tip:item.setToolTip(tip)
            return item
        menu.setToolTipsVisible(True)
        menu.addSection('Library')
        action('Add Library...',self.add_root)
        action('New Folder...',self.new_folder)
        action('Show in Explorer',self.reveal)
        action('Relink Library...',self.relink,tip='Point the selected library at its new location after moving it on disk.')
        action('Use Library for USD / Catalog Output',self.set_publish_root)
        action('Remove Library Registration...',self.remove_root,tip='Unregister only; source files are kept.')
        action('Back Up Index',lambda:self.status.setText(str(self.library.backup_index())))
        menu.addSection('Generate (search and folder filters apply)')
        action('Generate Missing Thumbnails',self.generate_missing_thumbnails,self.missing_scan is None)
        action('Generate Missing Proxies...',self.generate_missing_proxies,self.missing_proxy_scan is None)
        action('Generate Missing LODs...',self.generate_missing_lods,self.missing_lod_scan is None,
               tip='LOD variant set on every matching USD that has none yet (for Auto Select LOD / Stage Manager).')
        menu.addSection('Cancel Background Jobs')
        counts=self.job_counts()
        scanning={'Cancel Thumbnails':self.missing_scan,'Cancel Proxies':self.missing_proxy_scan,'Cancel LODs':self.missing_lod_scan}
        for (label,n,cancel),name in zip(counts,('Cancel Thumbnails','Cancel Proxies','Cancel Element Switches','Cancel Element Switch Removals','Cancel LODs','Cancel LOD Removals')):
            action(name+(f' ({n})' if n else ''),cancel,bool(n) or scanning.get(name) is not None)
        action('Cancel All',self.cancel_all_jobs,any(n for _,n,_ in counts) or any(s is not None for s in scanning.values()))
        menu.addSection('Catalog')
        action('Open Catalog',self.open_catalog)
        action('Select Catalog...',self.select_catalog)
        action('New Catalog...',self.new_catalog)
        return menu

    def root_menu(self):
        menu=self.build_root_menu()
        try:menu.exec(QtGui.QCursor.pos())
        finally:menu.deleteLater()

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
        open_folder=lambda path:QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path)))
        if self.items.selectedItems():
            # The assets' own files, selected in their folder (one window per folder), so a folder
            # holding hundreds of assets does not have to be searched again.
            files=[self.library.resolve(row) for row in self.selected_rows()]
            file_browser.select_in_file_browser(files,open_folder)
            self.status.setText(f'Shown in Explorer: {len(files)} file(s)')
            return
        folder=self.current_folder()
        if not folder:raise ValueError('Select a folder.')
        open_folder(core.inside(folder[2],folder[1]))

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

    def ask_lod_settings(self):
        """(levels, keep percent) from a small dialog that remembers the last values, or None."""
        dialog=QtWidgets.QDialog(self);dialog.setWindowTitle('Generate LODs')
        form=QtWidgets.QFormLayout(dialog)
        levels=QtWidgets.QSpinBox();levels.setRange(2,lod_gen.MAX_LEVELS);levels.setValue(int(self.settings.get('lod_levels',lod_gen.DEFAULT_LEVELS)))
        levels.setToolTip('Variants LOD_1 (the asset as it is) .. LOD_N in the "LOD" variant set.')
        keep=QtWidgets.QDoubleSpinBox();keep.setRange(1.0,99.0);keep.setDecimals(1);keep.setSuffix(' %')
        keep.setValue(float(self.settings.get('lod_keep',lod_gen.DEFAULT_KEEP)))
        keep.setToolTip('Triangles kept at each step: 50 % gives LOD_2 = 1/2, LOD_3 = 1/4, LOD_4 = 1/8 of the original.')
        form.addRow('Levels',levels);form.addRow('Keep per level',keep)
        form.addRow(QtWidgets.QLabel('Works with Auto Select LOD (Variant Set "LOD"), Stage Manager\'s Inspector and Set Variant.'))
        buttons=QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok|QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);form.addRow(buttons)
        if dialog.exec()!=QtWidgets.QDialog.DialogCode.Accepted:return None
        self.settings['lod_levels']=levels.value();self.settings['lod_keep']=keep.value();self.save_settings()
        return levels.value(),keep.value()

    def generate_lods(self):
        rows=[r for r in self.selected_rows() if r['kind']=='usd']
        if not rows:raise ValueError('Select one or more USD assets.')
        settings=self.ask_lod_settings()
        if settings is None:return
        for row in rows:
            self.library.resolve(row)
            self.lod_failed.discard(row['id'])
            self.queue_lod(row,*settings)
        self.status.setText(f'Queued {len(rows)} LOD job(s) ({settings[0]} levels, keep {settings[1]:g} % per level). Each USD file is overwritten in place (a backup is kept in data/backups/lod).')

    def generate_missing_lods(self):
        if self.missing_lod_scan is not None:return
        settings=self.ask_lod_settings()
        if settings is None:return
        # Like proxies: whether a USD already has LODs is only known by opening it, so each job checks its own file.
        filters=dict(self.filters());filters['kind']='usd'
        self.missing_lod_scan={'batches':self.library.iter_rows(**filters),'batch':[],'index':0,'count':0,'seen':0,'settings':settings}
        QtCore.QTimer.singleShot(0,self.missing_lod_step)

    def missing_lod_step(self):
        scan=self.missing_lod_scan
        if scan is None:return
        deadline=time.monotonic()+0.015
        while time.monotonic()<deadline:
            if scan['index']>=len(scan['batch']):
                scan['batch']=next(scan['batches'],None)
                if scan['batch'] is None:
                    self.missing_lod_scan=None
                    self.status.setText(f"Queued {scan['count']} LOD jobs. Everything matching the search and folder filters is included; assets that already have LODs are skipped.")
                    return
                scan['index']=0
            row=scan['batch'][scan['index']];scan['index']+=1;scan['seen']+=1
            if row['id'] not in self.lod_pending and 'lod' not in row['tags'].split():
                self.lod_failed.discard(row['id']);self.queue_lod(row,*scan['settings']);scan['count']+=1
        self.status.setText(f"Checking USD assets... {scan['seen']} / {self.total}")
        QtCore.QTimer.singleShot(0,self.missing_lod_step)

    def cancel_lods(self):
        self.missing_lod_scan=None
        for row,_,_ in self.lod_queue:self.lod_pending.discard(row['id'])
        self.lod_queue.clear()
        if self.lod_job:self.lod_job.requestInterruption()
        self.status.setText('Cancelled. An active LOD generation will finish first.')

    def generate_lod_delete(self):
        rows=[r for r in self.selected_rows() if r['kind']=='usd']
        if not rows:raise ValueError('Select one or more USD assets.')
        for row in rows:
            self.library.resolve(row)
            self.lod_delete_failed.discard(row['id'])
            self.queue_lod_delete(row)
        self.status.setText(f'Queued {len(rows)} LOD removal job(s). Assets without LODs are skipped untouched. Each USD file is overwritten in place (a backup is kept in data/backups/lod).')

    def cancel_lod_deletes(self):
        for row in self.lod_delete_queue:self.lod_delete_pending.discard(row['id'])
        self.lod_delete_queue.clear()
        if self.lod_delete_job:self.lod_delete_job.requestInterruption()
        self.status.setText('Cancelled. An active LOD removal will finish first.')

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
        node=ops.import_asset(source,row['effective_kind'],row['label'],self.target.text(),upstream,'sublayer' if self.usdmode.currentIndex() else 'reference',self.assign.text(),add_variant_switch='variant' in row['tags'].split(),add_lod_select='lod' in row['tags'].split())
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
            item,added=ops.register_catalog(path,self.catalog_path(),row['label'],row['tags'],str(thumb) if thumb else '',backup_dir=self.data_dir/'backups'/'catalog')
            items.append(item)
        self.refresh_catalogs()
        self.status.setText(f'Catalog ready: {len(items)} USD assets (existing entries kept).')
        return items

    def open_catalog(self):
        path=self.catalog_path()
        if not path.exists():raise ValueError('Add a USD asset to the Catalog first.')
        # hou.ui.setSharedLayoutDataSource(hou.AssetGalleryDataSource(...)) is the exact call
        # SideFX's own Asset Gallery menu uses to point the built-in 'asset_gallery' Python Panel
        # interface (labelled "Asset Catalog") at a database - see
        # $HFS/houdini/AssetGallerySourceMenu.xml and python3.13libs/layout/assetgallery.py, which
        # confirm setSharedAssetGalleryDataSource(source, gallery_name) is a different, unrelated
        # API (it only feeds the 'layout'/'material' node-parameter browsers, not this panel; an
        # earlier version of this method called it, which was wrong and just raised a TypeError).
        #
        # setSharedLayoutDataSource is correct, but live-testing (many isolated before/after
        # comparisons in a disposable Houdini 22.0.447 instance, restarted between each) showed
        # that call alone - with no pane ever created - reliably leaves every native Houdini
        # menu/popup in the session unresponsive until Houdini is restarted (this panel's own Qt
        # menus keep working; every native one does not). Creating the interface's pane, whether
        # floating (createFloatingPaneTab) or docked (Pane.createTab), does not avoid it either.
        # This looks like a genuine Houdini 22.0.447 engine bug in this exact call, not something
        # fixable from a Python Panel, so this no longer attempts it - the same result is one
        # native, unscripted UI action away.
        hou.ui.displayMessage(
            'Houdini 22.0.447 has a bug: calling hou.ui.setSharedLayoutDataSource() from a script '
            '(what "Open Catalog" used to do) freezes every native menu in the session until '
            'Houdini is restarted. Open the same Asset Catalog by hand instead - this uses only '
            "Houdini's own UI and does not hit the bug:\n\n"
            '1. Click the "+" on any pane tab row, then Python Panel > Asset Catalog.\n'
            '2. In that panel, use its own menu (top right) > Open Asset Database File...\n'
            '3. Choose this file:\n' + str(path),
            title='Open Catalog')

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
