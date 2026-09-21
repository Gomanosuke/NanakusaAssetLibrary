"""Dockable Python Panel and floating window for Houdini 22 / PySide6."""
from collections import OrderedDict, deque
from pathlib import Path
import json
import os
import subprocess
import tempfile
import time
import traceback
from hutil.PySide import QtCore, QtGui, QtWidgets
import hou
from . import core, houdini_ops as ops, dragdrop, storage, organize, pbr, embedded

ROLE = QtCore.Qt.ItemDataRole.UserRole
STACK_ROLE = dragdrop.STACK_ROLE   # ids of the assets a list item stands for
KINDS = {'': 'All Types', 'usd': 'USD', 'model': '3DModel', 'texture': 'Texture'}
_windows = []
_jobs = set()  # Keep background workers alive when a pane is closed mid-scan.

class AssetInfoJob(QtCore.QThread):
    done=QtCore.Signal(object,object)

    def __init__(self,key,path,kind):
        super().__init__();self.key,self.path,self.kind=key,path,kind

    def run(self):
        result={}
        try:
            executable=Path(hou.getenv('HFS'))/'bin'/('hython.exe' if os.name=='nt' else 'hython')
            process=subprocess.Popen([str(executable),str(Path(__file__).with_name('asset_info.py')),str(self.path),self.kind],
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
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

    def run(self):
        results = []
        for root in self.roots:
            if self.isInterruptionRequested():
                break
            try:
                result = self.library.scan(root['id'], self.isInterruptionRequested)
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
                capture_output=True, timeout=120, creationflags=(subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0))
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
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        with log.open('wb') as stream:
            process = subprocess.Popen(args, stdout=stream, stderr=stream, creationflags=flags)
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
    PAGE_SIZE = 200
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
        self.page = 0
        self.rows = []
        self.thumb_queue = deque()
        self.thumb_pending = set()
        self.thumb_failed = set()
        self.thumb_job = None
        self.info_job=None;self.info_pending=None;self.info_key=None;self.info_cache={}
        self.metadata_id=None;self.metadata_ids=[];self.pending_tags=None;self.row_index={};self.entries=[];self.icon_cache={};self.no_embedded=set();self.preview_cache=OrderedDict();self.placeholders={}
        self.icon_todo=deque();self.page_entries=[];self.icon_timer=QtCore.QTimer(self);self.icon_timer.setSingleShot(True);self.icon_timer.timeout.connect(self.load_icons)
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
        nav=QtWidgets.QHBoxLayout(); self._button('Previous',lambda:self.turn_page(-1),nav)
        self.page_label=QtWidgets.QLabel(); nav.addWidget(self.page_label,1)
        self._button('Next',lambda:self.turn_page(1),nav); centerbox.addLayout(nav)
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
        self.refresh_catalogs()
        selected=self.current_folder()
        it=QtWidgets.QTreeWidgetItemIterator(self.tree); expanded=set()
        while it.value():
            if it.value().isExpanded() and it.value().data(0,ROLE):expanded.add(it.value().data(0,ROLE))
            it+=1
        self.tree.blockSignals(True); self.tree.clear()
        allitem=QtWidgets.QTreeWidgetItem(['All Libraries']); self.tree.addTopLevelItem(allitem)
        chosen=allitem
        for root in self.library.roots():
            top=QtWidgets.QTreeWidgetItem([root['label']]); data=(root['id'],'',root['path']); top.setData(0,ROLE,data); top.setToolTip(0,root['path']); self.tree.addTopLevelItem(top)
            folders={'' :top}; rels=self.folder_paths(root)
            for rel in sorted(rels, key=lambda x:(x.count('/'),x.lower())):
                parent,_,name=rel.rpartition('/')
                item=QtWidgets.QTreeWidgetItem([name]); value=(root['id'],rel,root['path']); item.setData(0,ROLE,value)
                folders.get(parent,top).addChild(item); folders[rel]=item
                item.setExpanded(value in expanded)
                if value==selected:chosen=item
            if data==selected:chosen=top
            top.setExpanded(True)
        self.tree.setCurrentItem(chosen); self.tree.blockSignals(False)

    def folder_paths(self,root):
        """Folders of a library from the index; walk the disk only once for an index made by an older version."""
        rels=set(self.library.folders(root['id']))
        if not rels and Path(root['path']).is_dir():
            rels=set(core.visible_folders(root['path']));self.library.set_folders(root['id'],rels)
        return rels|set(core.GENRES)

    def select_folder(self,value):
        it=QtWidgets.QTreeWidgetItemIterator(self.tree)
        while it.value():
            if it.value().data(0,ROLE)==value:
                parent=it.value().parent()
                while parent is not None:parent.setExpanded(True);parent=parent.parent()
                self.tree.setCurrentItem(it.value());self.tree.scrollToItem(it.value());return
            it+=1

    def dragged_rows(self,ids):
        by_id={r['id']:r for r in self.rows}
        return [by_id[i] for i in ids if i in by_id]

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
        self.page=0; self.refresh()

    def refresh(self):
        self.icons_built_at=self.icon_edge()
        folder=self.current_folder()
        rows=self.library.assets(self.search.text(),folder[0] if folder else None,self.kind.currentData() or None,self.favorite.isChecked(),folder=folder[1] if folder and folder[1] else None)
        # Older indexes may still contain retired kinds until the next scan.
        rows=[r for r in rows if r['effective_kind'] in KINDS]
        if folder:
            rel=folder[1]
            if self.recursive.isChecked(): rows=[r for r in rows if not rel or r['relpath'].startswith(rel+'/')]
            else:
                rows=[r for r in rows if (Path(r['relpath']).parent.parent.as_posix() if core.is_usd_package(r) else Path(r['relpath']).parent.as_posix())==rel or (core.is_usd_package(r) and Path(r['relpath']).parent.as_posix()==rel)]
        self.rows=rows;self.row_index={r['id']:r for r in rows}
        self.entries=pbr.stack_entries(rows,self.stack.isChecked())
        pages=max(1,(len(self.entries)+self.PAGE_SIZE-1)//self.PAGE_SIZE); self.page=min(self.page,pages-1)
        self.items.clear();self.icon_todo.clear();self.icon_timer.stop()
        self.page_entries=self.entries[self.page*self.PAGE_SIZE:(self.page+1)*self.PAGE_SIZE]
        for index,entry in enumerate(self.page_entries):
            row=entry['rep'];members=entry['rows']
            # Items appear at once with a flat placeholder; pictures are read a few at a time so a big
            # folder never freezes the panel.
            item=QtWidgets.QListWidgetItem(self.placeholder(row['effective_kind']),self.item_text(members,row,entry['label']))
            self.icon_todo.append(index)
            item.setData(ROLE,row);item.setData(STACK_ROLE,[m['id'] for m in members])
            item.setToolTip(row['relpath'] if len(members)==1 else f"{entry['label']}: {', '.join(entry['channels'])}")
            self.items.addItem(item)
        self.icon_timer.start(0)
        stacked=len(self.entries)!=len(rows)
        self.page_label.setText((f'{len(self.entries)} items ({len(rows)} assets)' if stacked else f'{len(rows)} assets')+f'  ·  {self.page+1} / {pages}')

    def placeholder(self,kind):
        icon=self.placeholders.get(kind)
        if icon is None:
            pix=QtGui.QPixmap(144,144);pix.fill(QtGui.QColor({'usd':'#3b6677','model':'#466c58','hdri':'#796a39','material':'#665488','pbr':'#665488','decal':'#805457','texture':'#496878'}.get(kind,'#555555')))
            icon=self.placeholders[kind]=QtGui.QIcon(pix)
        return icon

    def load_icons(self):
        """Read the pictures of the visible page in small slices (about 12 ms each) between events."""
        deadline=time.monotonic()+0.012
        while self.icon_todo and time.monotonic()<deadline:
            index=self.icon_todo.popleft()
            if index>=self.items.count() or index>=len(self.page_entries):continue
            entry=self.page_entries[index]
            icon=self.icon_for(entry['rep'])
            if len(entry['rows'])>1:icon=self.stacked_icon(icon,len(entry['rows']))
            self.items.item(index).setIcon(icon)
        if self.icon_todo:self.icon_timer.start(0)

    def item_text(self,members,rep,label=None):
        favorite=any(m['favorite'] for m in members)
        if len(members)>1:return ('★ ' if favorite else '')+(label or rep['label'])+f'\nPBR set · {len(members)} images'
        return ('★ ' if favorite else '')+rep['label']+'\n'+KINDS[rep['effective_kind']]

    def item_rows(self,item):
        """The assets a list item stands for: one row, or every image of a stack."""
        rows=[self.row_index[i] for i in (item.data(STACK_ROLE) or []) if i in self.row_index]
        return rows or [item.data(ROLE)]

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

    def turn_page(self,delta):
        self.page=max(0,self.page+delta); self.refresh()

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
        return item.data(ROLE)

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
            entry=next((e for e in self.entries if e['rep']['id']==row['id']),None)
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
        for i in range(self.items.count()):
            item=self.items.item(i);ids=item.data(STACK_ROLE) or []
            if aid in ids:
                rep=item.data(ROLE)
                if rep['id']==aid:rep.update(changes);item.setData(ROLE,rep)
                members=self.item_rows(item)
                entry=next((e for e in self.entries if e['rep']['id']==rep['id']),None)
                item.setText(self.item_text(members,rep,entry['label'] if entry else None))

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

    def generate_missing_thumbnails(self):
        if self.missing_scan is not None:return
        # Checking every asset touches the disk; do it in slices so the panel stays responsive.
        self.missing_scan={'rows':list(self.rows),'index':0,'count':0}
        QtCore.QTimer.singleShot(0,self.missing_step)

    def missing_step(self):
        scan=self.missing_scan
        if scan is None:return
        rows=scan['rows'];deadline=time.monotonic()+0.015
        while scan['index']<len(rows) and time.monotonic()<deadline:
            row=rows[scan['index']];scan['index']+=1
            if row['id'] not in self.thumb_pending and not self.thumbnail_path(row):
                self.thumb_failed.discard(row['id'])
                self.queue_thumbnail(row,geometry=True);scan['count']+=1
        if scan['index']<len(rows):
            self.status.setText(f"Checking thumbnails... {scan['index']} / {len(rows)}")
            QtCore.QTimer.singleShot(0,self.missing_step);return
        self.missing_scan=None
        self.status.setText(f"Queued {scan['count']} missing thumbnails. All pages matching the search and folder filters are included.")

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
            for i in range(self.items.count()):
                item=self.items.item(i);row=item.data(ROLE)
                if row['id']==aid:item.setIcon(self.icon_for(row))
            self.selection_changed()
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
            payloads=[{'path':self.library.resolve(row),'kind':row['effective_kind'],'label':row['label']} for row in rows]
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
        node=ops.import_asset(source,row['effective_kind'],row['label'],self.target.text(),upstream,'sublayer' if self.usdmode.currentIndex() else 'reference',self.assign.text())
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
