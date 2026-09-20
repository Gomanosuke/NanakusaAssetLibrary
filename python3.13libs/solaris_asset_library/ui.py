"""Dockable Python Panel and floating window for Houdini 22 / PySide6."""
from pathlib import Path
import json
import os
import subprocess
import traceback
from hutil.PySide import QtCore, QtGui, QtWidgets
import hou
from . import core, houdini_ops as ops

ROLE = QtCore.Qt.ItemDataRole.UserRole
KINDS = {'': 'すべての種類', 'usd': 'USD', 'model': 'モデル', 'material': 'MaterialX',
         'pbr': 'PBRマテリアル', 'texture': 'テクスチャ', 'hdri': 'HDRI', 'decal': 'デカール'}
_windows = []
_jobs = set()  # Keep background workers alive when a pane is closed mid-scan.

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
        self.exe = str(Path(hou.getenv('HFS')) / 'bin' / ('iconvert.exe' if os.name == 'nt' else 'iconvert'))

    def run(self):
        try:
            # Run image decoding outside Houdini's UI thread; no shell interpolation.
            result = subprocess.run([self.exe, '-d', '8', '-g', 'auto', self.source, self.dest],
                capture_output=True, timeout=120, creationflags=(subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0))
            if result.returncode:
                raise RuntimeError(result.stderr.decode(errors='replace')[-1500:])
            image = QtGui.QImage(self.dest)
            if image.isNull():
                raise RuntimeError('Could not decode thumbnail')
            image = image.scaled(512, 320, QtCore.Qt.AspectRatioMode.KeepAspectRatio, QtCore.Qt.TransformationMode.SmoothTransformation)
            image.save(self.dest)
            self.done.emit(self.asset_id, self.dest, '')
        except Exception as exc:
            self.done.emit(self.asset_id, '', str(exc))

def _keep_job(job):
    _jobs.add(job)
    job.finished.connect(lambda: _jobs.discard(job))
    job.start()

class ManifestDialog(QtWidgets.QDialog):
    def __init__(self, folder, parent=None):
        super().__init__(parent)
        self.setWindowTitle('PBR / デカールセットを登録')
        self.resize(700, 440)
        layout = QtWidgets.QVBoxLayout(self)
        note = QtWidgets.QLabel('候補を確認して必要なマップを指定してください。元画像は変更しません。\nNormalはOpenGL形式。Packed ORMやDirectX Normalは事前に変換してください。')
        note.setWordWrap(True); layout.addWidget(note)
        form = QtWidgets.QFormLayout(); layout.addLayout(form)
        self.name = QtWidgets.QLineEdit(Path(folder).name)
        form.addRow('セット名', self.name)
        self.kind = QtWidgets.QComboBox(); self.kind.addItems(['PBR', 'Decal'])
        form.addRow('種類', self.kind)
        self.color = QtWidgets.QComboBox(); self.color.addItems(['srgb_texture', 'ACEScg', 'lin_rec709', 'Raw'])
        form.addRow('カラー画像の色空間', self.color)
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
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, '画像を選択', str(folder))
        if path:
            field.setText(path)

class LibraryWidget(QtWidgets.QWidget):
    PAGE_SIZE = 200
    def __init__(self, parent=None, data_dir=None, initial_root=None):
        super().__init__(parent)
        self.setObjectName('SolarisAssetLibrary')
        self.data_dir = Path(data_dir or os.environ.get('SAL_DATA_DIR') or (Path(hou.getenv('HOUDINI_USER_PREF_DIR'))/'solaris_asset_library'))
        self.library = core.Library(self.data_dir)
        config_path = self.data_dir / 'settings.json'
        self.settings = json.loads(config_path.read_text(encoding='utf-8-sig')) if config_path.exists() else {}
        self.default_root = initial_root or os.environ.get('SAL_ASSET_ROOT') or self.settings.get('publish_root', '')
        if self.default_root and not self.library.roots() and Path(self.default_root).is_dir():
            self.library.add_root(self.default_root)
        self.job = None
        self.page = 0
        self.rows = []
        self._setup()
        self.rebuild_tree()
        self.refresh()

    def _button(self, text, callback, layout):
        button = QtWidgets.QPushButton(text)
        button.clicked.connect(lambda checked=False: self.safe(callback))
        layout.addWidget(button)
        return button

    def safe(self, callback):
        try:
            return callback()
        except Exception as exc:
            self.status.setText('エラー: ' + str(exc))
            dialog = QtWidgets.QMessageBox(self)
            dialog.setWindowTitle('Asset Library'); dialog.setText(str(exc))
            dialog.setDetailedText(traceback.format_exc()); dialog.exec()

    def _setup(self):
        outer = QtWidgets.QVBoxLayout(self)
        heading = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel('ASSET LIBRARY  /  Solaris · Karma XPU')
        title.setStyleSheet('font-size: 17px; font-weight: bold; padding: 6px;')
        heading.addWidget(title); heading.addStretch()
        self._button('ライブラリー追加', self.add_root, heading)
        self.scan_button = self._button('再スキャン', self.scan, heading)
        self.cancel_button = self._button('中止', self.cancel_scan, heading); self.cancel_button.setEnabled(False)
        outer.addLayout(heading)
        filters = QtWidgets.QHBoxLayout()
        self.search = QtWidgets.QLineEdit(); self.search.setPlaceholderText('名前・パス・タグを検索…')
        self.search_timer = QtCore.QTimer(self); self.search_timer.setSingleShot(True); self.search_timer.setInterval(180)
        self.search.textChanged.connect(lambda: self.search_timer.start())
        self.search_timer.timeout.connect(self.reset_page)
        self.kind = QtWidgets.QComboBox()
        for key, label in KINDS.items(): self.kind.addItem(label,key)
        self.kind.currentIndexChanged.connect(self.reset_page)
        self.favorite = QtWidgets.QCheckBox('★ お気に入り'); self.favorite.toggled.connect(self.reset_page)
        self.recursive = QtWidgets.QCheckBox('下位フォルダーを含む'); self.recursive.setChecked(True); self.recursive.toggled.connect(self.reset_page)
        filters.addWidget(self.search,1); filters.addWidget(self.kind); filters.addWidget(self.favorite); filters.addWidget(self.recursive)
        outer.addLayout(filters)
        split = QtWidgets.QSplitter(); outer.addWidget(split,1)
        left = QtWidgets.QWidget(); leftbox=QtWidgets.QVBoxLayout(left); leftbox.setContentsMargins(0,0,0,0)
        self.tree = QtWidgets.QTreeWidget(); self.tree.setHeaderLabel('ライブラリー / フォルダー')
        self.tree.currentItemChanged.connect(self.reset_page); leftbox.addWidget(self.tree,1)
        folderbuttons=QtWidgets.QHBoxLayout()
        self._button('新規フォルダー', self.new_folder, folderbuttons)
        self._button('設定', self.root_menu, folderbuttons); leftbox.addLayout(folderbuttons)
        split.addWidget(left)
        center=QtWidgets.QWidget(); centerbox=QtWidgets.QVBoxLayout(center); centerbox.setContentsMargins(0,0,0,0)
        self.items = QtWidgets.QListWidget(); self.items.setViewMode(QtWidgets.QListView.ViewMode.IconMode)
        self.items.setResizeMode(QtWidgets.QListView.ResizeMode.Adjust)
        self.items.setMovement(QtWidgets.QListView.Movement.Static)
        self.items.setIconSize(QtCore.QSize(144,100)); self.items.setGridSize(QtCore.QSize(168,148))
        self.items.setWordWrap(True); self.items.setSpacing(5)
        self.items.currentItemChanged.connect(self.selection_changed)
        self.items.itemDoubleClicked.connect(lambda item: self.safe(self.import_selected))
        centerbox.addWidget(self.items,1)
        nav=QtWidgets.QHBoxLayout(); self._button('前へ',lambda:self.turn_page(-1),nav)
        self.page_label=QtWidgets.QLabel(); nav.addWidget(self.page_label,1)
        self._button('次へ',lambda:self.turn_page(1),nav); centerbox.addLayout(nav)
        split.addWidget(center)
        details=QtWidgets.QWidget(); details.setMinimumWidth(235)
        db=QtWidgets.QVBoxLayout(details)
        self.preview=QtWidgets.QLabel('素材を選択'); self.preview.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter); self.preview.setMinimumHeight(150)
        db.addWidget(self.preview)
        self.info=QtWidgets.QLabel(); self.info.setWordWrap(True); self.info.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse); db.addWidget(self.info)
        form=QtWidgets.QFormLayout(); db.addLayout(form)
        self.label=QtWidgets.QLineEdit(); form.addRow('表示名',self.label)
        self.tags=QtWidgets.QLineEdit(); self.tags.setPlaceholderText('wood outdoor red'); form.addRow('タグ',self.tags)
        self.override=QtWidgets.QComboBox(); self.override.addItem('自動判定',None)
        for k,v in KINDS.items():
            if k:self.override.addItem(v,k)
        form.addRow('種類',self.override)
        self.star=QtWidgets.QCheckBox('お気に入り'); form.addRow('',self.star)
        self._button('メタデータ保存',self.save_metadata,db)
        self._button('サムネイル指定',self.choose_thumbnail,db)
        self._button('画像からサムネイル生成',self.generate_thumbnail,db)
        self._button('フォルダーを開く',self.reveal,db)
        db.addStretch(); split.addWidget(details); split.setSizes([230,600,270])
        destrow=QtWidgets.QHBoxLayout()
        destrow.addWidget(QtWidgets.QLabel('読み込み先'))
        self.target=QtWidgets.QLineEdit('/stage'); destrow.addWidget(self.target,1)
        self.connect_input=QtWidgets.QCheckBox('選択LOPの後に接続'); self.connect_input.setChecked(True); destrow.addWidget(self.connect_input)
        self.usdmode=QtWidgets.QComboBox(); self.usdmode.addItems(['USD Reference','USD Sublayer']); destrow.addWidget(self.usdmode)
        outer.addLayout(destrow)
        assignrow=QtWidgets.QHBoxLayout(); assignrow.addWidget(QtWidgets.QLabel('材質の割当先Prim（任意）'))
        self.assign=QtWidgets.QLineEdit(); self.assign.setPlaceholderText('/assets/chair/**'); assignrow.addWidget(self.assign,1); outer.addLayout(assignrow)
        actions=QtWidgets.QHBoxLayout()
        self._button('Solarisへ読み込む',self.import_selected,actions)
        self._button('PBR / Decalセット作成',self.create_manifest,actions)
        self._button('USDをCatalogへ登録',self.add_catalog,actions)
        self._button('素材を静的USD化',self.publish_asset,actions)
        self._button('Catalogを開く',self.open_catalog,actions)
        outer.addLayout(actions)
        self.status=QtWidgets.QLabel('ライブラリーを追加し「再スキャン」で一覧を更新します。'); self.status.setWordWrap(True); outer.addWidget(self.status)

    def current_folder(self):
        item=self.tree.currentItem()
        return item.data(0,ROLE) if item else None

    def rebuild_tree(self):
        selected=self.current_folder()
        self.tree.blockSignals(True); self.tree.clear()
        allitem=QtWidgets.QTreeWidgetItem(['すべてのライブラリー']); self.tree.addTopLevelItem(allitem)
        chosen=allitem
        assets=self.library.assets(missing=True)
        for root in self.library.roots():
            top=QtWidgets.QTreeWidgetItem([root['label']]); data=(root['id'],'',root['path']); top.setData(0,ROLE,data); top.setToolTip(0,root['path']); self.tree.addTopLevelItem(top)
            folders={'' :top}; rels=set()
            for a in assets:
                if a['root_id']==root['id']:
                    parent=Path(a['relpath']).parent
                    while str(parent)!='.':
                        rels.add(parent.as_posix()); parent=parent.parent
            try:
                for path,dirs,_ in os.walk(root['path']):
                    dirs[:]=[d for d in dirs if not d.startswith('.') and not (Path(path)/d).is_symlink()]
                    for d in dirs: rels.add((Path(path)/d).relative_to(root['path']).as_posix())
                    # Index supplies deep folders; keep empty-folder discovery bounded.
                    if len(Path(path).relative_to(root['path']).parts)>=2: dirs[:]=[]
            except OSError: pass
            for rel in sorted(rels, key=lambda x:(len(Path(x).parts),x.lower())):
                parent=Path(rel).parent.as_posix(); parent='' if parent=='.' else parent
                item=QtWidgets.QTreeWidgetItem([Path(rel).name]); value=(root['id'],rel,root['path']); item.setData(0,ROLE,value)
                folders.get(parent,top).addChild(item); folders[rel]=item
                if value==selected:chosen=item
            if data==selected:chosen=top
            top.setExpanded(True)
        self.tree.setCurrentItem(chosen); self.tree.blockSignals(False)

    def reset_page(self,*args):
        self.page=0; self.refresh()

    def refresh(self):
        folder=self.current_folder()
        rows=self.library.assets(self.search.text(),folder[0] if folder else None,self.kind.currentData() or None,self.favorite.isChecked())
        if folder:
            rel=folder[1]
            if self.recursive.isChecked(): rows=[r for r in rows if not rel or r['relpath'].startswith(rel+'/')]
            else: rows=[r for r in rows if (Path(r['relpath']).parent.as_posix().replace('.', '',1) if Path(r['relpath']).parent.as_posix()=='.' else Path(r['relpath']).parent.as_posix())==rel]
        self.rows=rows
        pages=max(1,(len(rows)+self.PAGE_SIZE-1)//self.PAGE_SIZE); self.page=min(self.page,pages-1)
        self.items.clear()
        for row in rows[self.page*self.PAGE_SIZE:(self.page+1)*self.PAGE_SIZE]:
            icon=self.icon_for(row)
            item=QtWidgets.QListWidgetItem(icon, ('★ ' if row['favorite'] else '')+row['label']+'\n'+KINDS[row['effective_kind']])
            item.setData(ROLE,row); item.setToolTip(row['relpath']); self.items.addItem(item)
        self.page_label.setText(f'{len(rows)} assets  ·  {self.page+1} / {pages}')

    def turn_page(self,delta):
        self.page=max(0,self.page+delta); self.refresh()

    def thumbnail_path(self,row):
        p=Path(row['root_path'])/row['relpath']
        candidates=[Path(row['thumbnail'])] if row['thumbnail'] else []
        candidates += [p.with_suffix('.preview.jpg'),p.parent/'thumbnail.jpg',p.parent/'thumbnail.png',p.parent/'preview.jpg']
        if p.suffix.lower() in {'.png','.jpg','.jpeg','.bmp','.tga'}:candidates.append(p)
        return next((x for x in candidates if x.is_file()),None)

    def icon_for(self,row):
        path=self.thumbnail_path(row)
        if path:
            reader=QtGui.QImageReader(str(path)); reader.setScaledSize(QtCore.QSize(144,100)); image=reader.read()
            if not image.isNull():return QtGui.QIcon(QtGui.QPixmap.fromImage(image))
        pix=QtGui.QPixmap(144,100); pix.fill(QtGui.QColor({'usd':'#3b6677','model':'#466c58','hdri':'#796a39','material':'#665488','pbr':'#665488','decal':'#805457','texture':'#496878'}[row['effective_kind']]))
        painter=QtGui.QPainter(pix); painter.setPen(QtGui.QColor('#eeeeee')); painter.drawText(pix.rect(),QtCore.Qt.AlignmentFlag.AlignCenter,row['effective_kind'].upper()); painter.end()
        return QtGui.QIcon(pix)

    def selected(self):
        item=self.items.currentItem()
        if not item:raise ValueError('素材を選択してください')
        return item.data(ROLE)

    def selection_changed(self,*args):
        if not self.items.currentItem():
            self.preview.clear(); self.info.clear(); return
        row=self.selected()
        self.preview.setPixmap(self.icon_for(row).pixmap(240,150))
        self.info.setText(f"{row['root_label']} / {row['relpath']}\n{row['size']/1048576:.2f} MB")
        self.label.setText(row['label']); self.tags.setText(row['tags']); self.star.setChecked(bool(row['favorite']))
        self.override.setCurrentIndex(max(0,self.override.findData(row['override_kind'])))

    def save_metadata(self):
        row=self.selected(); self.library.update(row['id'],label=self.label.text().strip() or row['label'],tags=self.tags.text(),favorite=int(self.star.isChecked()),override_kind=self.override.currentData())
        self.refresh(); self.status.setText('メタデータを保存しました。元ファイルは変更していません。')

    def add_root(self):
        path=QtWidgets.QFileDialog.getExistingDirectory(self,'ライブラリーのルートフォルダー',self.default_root)
        if path:
            source_root=Path(__file__).resolve().parents[2]
            candidate=Path(path).resolve()
            if candidate.is_relative_to(source_root) or source_root.is_relative_to(candidate):
                raise ValueError('コードフォルダーとライブラリーは独立した場所を指定してください')
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
        self.scan_button.setEnabled(False); self.cancel_button.setEnabled(True); self.status.setText('スキャン中… 元ファイルの読み取りのみ。キャンセルできます。')
        _keep_job(self.job)

    def cancel_scan(self):
        if self.job:self.job.requestInterruption()

    def scan_done(self,results):
        self.scan_button.setEnabled(True); self.cancel_button.setEnabled(False)
        self.rebuild_tree(); self.refresh()
        messages=[]
        for name,result in results:
            messages.append(name+': '+('中止' if result.get('cancelled') else str(result['count'])+' 件')+(' / '+ '; '.join(result['errors'][:3]) if result.get('errors') else ''))
        self.status.setText(' | '.join(messages) or 'スキャン対象なし')

    def new_folder(self):
        folder=self.current_folder()
        if not folder:raise ValueError('親フォルダーを左側で選択してください')
        name,ok=QtWidgets.QInputDialog.getText(self,'新規フォルダー','名前（/ 区切りで階層も作成できます）')
        if ok and name.strip():
            path=core.inside(folder[2],str(Path(folder[1])/name.strip())); path.mkdir(parents=True,exist_ok=True)
            self.rebuild_tree(); self.status.setText('作成: '+str(path))

    def root_menu(self):
        menu=QtWidgets.QMenu(self)
        menu.addAction('フォルダーを開く',lambda:self.safe(self.reveal))
        menu.addAction('ルートの場所を変更',lambda:self.safe(self.relink))
        menu.addAction('このライブラリーをUSD / Catalogの保存先にする',lambda:self.safe(self.set_publish_root))
        menu.addAction('ライブラリー登録を解除（ファイルは保持）',lambda:self.safe(self.remove_root))
        menu.addAction('インデックスをバックアップ',lambda:self.status.setText(str(self.library.backup_index())))
        menu.exec(QtGui.QCursor.pos())

    def set_publish_root(self):
        folder=self.current_folder()
        if not folder:raise ValueError('ライブラリーを選択してください')
        self.default_root=folder[2]; self.settings['publish_root']=self.default_root; self.save_settings()
        self.status.setText('USD / Catalog保存先: '+self.default_root)

    def relink(self):
        folder=self.current_folder()
        if not folder:raise ValueError('ライブラリーを選択してください')
        path=QtWidgets.QFileDialog.getExistingDirectory(self,'移動後のルートフォルダー',folder[2])
        if path:
            source_root=Path(__file__).resolve().parents[2]
            candidate=Path(path).resolve()
            if candidate.is_relative_to(source_root) or source_root.is_relative_to(candidate):
                raise ValueError('コードフォルダーとライブラリーは独立した場所を指定してください')
            self.library.backup_index(); self.library.relink_root(folder[0],path)
            if self.default_root and core.path_key(self.default_root)==core.path_key(folder[2]):
                self.default_root=path; self.settings['publish_root']=path; self.save_settings()
            self.rebuild_tree(); self.scan()

    def remove_root(self):
        folder=self.current_folder()
        if not folder:raise ValueError('ライブラリーを選択してください')
        result=QtWidgets.QMessageBox.question(self,'登録解除','このライブラリーの登録とタグ情報を解除しますか？\n元ファイルは保持し、インデックスをバックアップします。')
        if result==QtWidgets.QMessageBox.StandardButton.Yes:
            self.library.backup_index(); self.library.remove_root(folder[0]); self.rebuild_tree(); self.refresh()

    def reveal(self):
        if self.items.currentItem():path=self.library.resolve(self.selected()).parent
        else:
            folder=self.current_folder()
            if not folder:raise ValueError('フォルダーを選択してください')
            path=core.inside(folder[2],folder[1])
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path)))

    def choose_thumbnail(self):
        row=self.selected(); path,_=QtWidgets.QFileDialog.getOpenFileName(self,'サムネイル画像',str(self.library.resolve(row).parent),'Images (*.png *.jpg *.jpeg)')
        if path:self.library.update(row['id'],thumbnail=path); self.refresh()

    def generate_thumbnail(self):
        row=self.selected(); path=self.library.resolve(row)
        if path.suffix.lower() not in core.IMAGES:raise ValueError('画像素材用です。モデルには「サムネイル指定」を使用してください。')
        cache=self.data_dir/'thumbnails'; cache.mkdir(exist_ok=True)
        job=ThumbnailJob(row['id'],path,cache/(row['id']+'.png'))
        job.done.connect(self.thumbnail_done)
        _keep_job(job); self.status.setText('画像サムネイルを生成中…（表示用の近似色）')

    def thumbnail_done(self,aid,path,error):
        if error:self.status.setText('サムネイル生成失敗: '+error)
        else:self.library.update(aid,thumbnail=path); self.refresh(); self.status.setText('サムネイルを生成しました')

    def import_selected(self):
        row=self.selected(); source=self.library.resolve(row)
        upstream=None
        if self.connect_input.isChecked():
            nodes=[n for n in hou.selectedNodes() if n.parent().path()==self.target.text() and n.type().category()==hou.lopNodeTypeCategory()]
            if len(nodes)>1:raise ValueError('接続元LOPは1つだけ選択してください')
            if nodes:upstream=nodes[0]
        node=ops.import_asset(source,row['effective_kind'],row['label'],self.target.text(),upstream,'sublayer' if self.usdmode.currentIndex() else 'reference',self.assign.text())
        node.setSelected(True,clear_all_selected=True); node.setDisplayFlag(True)
        self.status.setText('読み込み完了: '+node.path()+('（FBX/glTFの元マテリアルは自動再構築しません）' if source.suffix.lower() in {'.fbx','.gltf','.glb'} else ''))
        return node

    def create_manifest(self):
        folder=self.current_folder()
        base=core.inside(folder[2],folder[1]) if folder else Path(self.default_root)
        if self.items.currentItem():base=self.library.resolve(self.selected()).parent
        dialog=ManifestDialog(base,self)
        if dialog.exec()!=QtWidgets.QDialog.DialogCode.Accepted:return
        name=dialog.name.text().strip()
        if not name or any(c in name for c in '/\\:*?"<>|'):raise ValueError('有効なセット名を入力してください')
        maps={key:field.text().strip() for key,field in dialog.maps.items() if field.text().strip()}
        if not maps:raise ValueError('マップを1枚以上指定してください')
        dest=base/(name+('.decal.json' if dialog.kind.currentIndex() else '.pbr.json'))
        resolved={}
        for key,value in maps.items():
            p=Path(hou.expandString(value)).resolve()
            if not p.is_file() and '<UDIM>' not in str(p):raise FileNotFoundError(str(p))
            try:resolved[key]=Path(os.path.relpath(p,base)).as_posix()
            except ValueError:resolved[key]=p.as_posix()
        data={'schema':1,'maps':resolved,'color_space':dialog.color.currentText(),'displacement_scale':dialog.scale.value()}
        with dest.open('x',encoding='utf-8') as stream:json.dump(data,stream,ensure_ascii=False,indent=2)
        self.scan(); self.status.setText('セットを保存: '+str(dest))

    def catalog_path(self):
        if not self.default_root:raise ValueError('ライブラリーを追加し、設定からUSD / Catalog保存先を指定してください')
        return Path(self.default_root)/'_catalog'/'solaris_assets.db'

    def add_catalog(self):
        row=self.selected(); path=self.library.resolve(row)
        thumb=self.thumbnail_path(row)
        item,added=ops.register_catalog(path,self.catalog_path(),row['label'],row['tags'],str(thumb) if thumb else '')
        self.status.setText(('Catalogに登録: ' if added else '登録済み（重複なし）: ')+row['label'])
        return item

    def open_catalog(self):
        path=self.catalog_path()
        if not path.exists():raise ValueError('先にUSDをCatalogへ登録してください')
        source=hou.AssetGalleryDataSource(path.as_posix())
        hou.ui.setSharedLayoutDataSource(source)
        pane=hou.ui.curDesktop().createFloatingPaneTab(hou.paneTabType.PythonPanel)
        pane.setActiveInterface(hou.pypanel.interfaceByName('asset_gallery'))

    def publish_asset(self):
        row=self.selected(); path=self.library.resolve(row)
        if not self.default_root:raise ValueError('USD保存先のライブラリーを設定してください')
        base=Path(self.default_root)/'USD'/ops.safe_name(row['label'])
        dest,_=QtWidgets.QFileDialog.getSaveFileName(self,'現在フレームの素材単体をUSD化（画像は元ファイルを参照）',str(base/(ops.safe_name(row['label'])+'.usd')),'USD (*.usd *.usda *.usdc)')
        if not dest:return
        if Path(dest).exists():raise FileExistsError('別バージョンの名前を指定してください。既存USDは上書きしません。')
        # Isolated temporary network: never export the user's entire shot.
        network=hou.node('/obj').createNode('lopnet','sal_publish')
        try:
            node=ops.import_asset(path,row['effective_kind'],row['label'],network.path())
            ops.export_stage(node,dest)
        finally:network.destroy()
        self.scan(); self.status.setText('USDを書き出しました: '+dest+' / 再スキャン後にCatalog登録できます')

def show_window():
    dialog=QtWidgets.QDialog(hou.qt.mainWindow())
    dialog.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
    dialog.setWindowTitle('Solaris Asset Library'); dialog.resize(1250,820)
    layout=QtWidgets.QVBoxLayout(dialog); panel=LibraryWidget(); layout.addWidget(panel)
    _windows.append(dialog)
    dialog.destroyed.connect(lambda: _windows.remove(dialog) if dialog in _windows else None)
    dialog.show()
    return panel
