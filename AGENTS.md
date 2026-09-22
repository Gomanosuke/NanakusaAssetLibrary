# NanakusaAssetLibrary 開発指示

## 作業範囲

- Houdini 22 / Solaris向けのPython Panel。材質はKarma XPU向けMaterialXを基本とする。
- 現行仕様・実装の構成・検証方法はdocs/SPEC.md、利用者向けの説明はREADME.mdを参照する。README.mdは利用者向けに書き、実装の詳細・テスト・移行手順はdocs/SPEC.mdへ置く。名称はNanakusaAssetLibrary、Pythonモジュール名はnanakusa_asset_library。
- 作業開始時にgit statusと関連差分を確認し、ユーザーの未コミット変更を保持する。
- サブエージェントはユーザーが明示的に依頼した場合のみ使用する。
- 個人PCの絶対パス、実素材名、ダウンロード素材、設定、DB、画像、HIPをGitに入れない。例とテストには汎用名・合成データを使う。

## 保存先と固定仕様

- 本体、asset、dataを独立させる。保存先をコードにハードコードしない。
- インストーラーの--dataとpackage JSONのNAL_DATA_DIRでdataを設定する。通常はassetと同階層のdata。
- HoudiniのDocuments設定フォルダーへ画像キャッシュを戻さない。
- 設定、タグ、お気に入り、SQLiteインデックス、Textureの画像キャッシュはdataへ保存する。
- バックアップはdata/backupsへ統一する。assetやdataの親に新たなbackupsを作らない。
- asset直下の分類はUSD / Texture / 3DModelの3つで固定。CatalogはDB専用とし、素材分類として表示しない。
- USDはフォルダーと同名の入口USDを検出し、パッケージを1件として表示する。内部レイヤー・画像を列挙しない。
- 単体USDZはUSD直下・整理用フォルダー内でも検出し、ファイル名で表示する。既存USDパッケージの内部は列挙しない。単体USDZのサムネイルは<ファイル名>_thumbnail.pngとして衝突を防ぐ。
- 3DModelは単一ファイルで形状を読める形式を対象とし、外部材質の自動再構築は前提にしない。
- スキャンは元素材を変更しない。生成サムネイルを独立アセットとして混入させない。

## サムネイル

- 3DModel: 元ファイルの隣に<モデル名>_thumbnail.png。複合拡張子.bgeo.sc / .geo.scもモデル名から除く。
- USD: 入口USDの隣にthumbnail.png。既存thumbnail.jpgも認識する。Component Builderの配置規約であり、USD仕様の必須要件ではない。
- Texture: data/thumbnailsのみ。サムネイル保存先の判断はstorage.pyに集約する。
- 一覧・詳細表示と形状レンダーは正方形。縦横比を維持し、非正方形画像の余白は透明にする。
- 画像を引き延ばさず、黒帯を画素へ焼き込まない。OIIOのdata windowとdisplay windowの違いに注意する。
- 形状生成は別プロセスのhython / Karma CPUを使用し、ユーザーの作業HIPにレンダー用ノードを残さない。
- 再生成に失敗しても既存サムネイルを失わないよう、一時ファイルの成功確認後に置換する。
- 不足分の一括生成は検索条件に一致する全素材が対象（Library.iter_rowsで索引をスライス読み）。既存画像をスキップし、中止操作を維持する。

## D&DとUI

- GUIのラベル・メニュー・ステータスは英語。読み込み設定はOptions、管理操作はLibraries、素材操作は右クリックメニューにまとめる。
- 右側の大きなプレビューは常時表示し、編集項目はTagsとFavoriteだけにする。タグはEnterまたはフォーカス移動で保存する。
- 素材情報は別プロセスで取得し、選択変更後の古い結果を表示しない。形状の集計で作業HIPを変更しない。
- Catalog選択欄はasset/CatalogのDBを検出する。選択先はルートごとに保存し、Add Catalog / Open Catalogの双方で同じ選択を使う。新規作成で既存DBを上書きしない。
- Import Selected / Copy Pathsは右クリックに置き、Add CatalogはUSD選択時のみ表示する。
- Ctrl / Shiftの複数選択を保持してD&D・Copy Paths・サムネイル生成を行う。メタデータ編集はactive assetのみ。
- 全種類で文字列入力欄にはパスを渡す。Network ViewのPパラメーターは独立したネイティブ描画領域なので、グラフと誤判定してイベントを横取りしない。
- 一括読み込みは1つのUndoにまとめ、失敗時は今回生成したノードのみを取り除く。LOP / SOPの複数読み込みはMergeで全素材を表示する。
- PBR画像の命名判定はpbr.py。セットごとに共有texcoordを1つ作成し、データ画像はRaw。曖昧な同一チャンネル重複は拒否する。
- 既存Builderの出力接続とノード配置を保持する。moveToGoodPositionの既定は周囲のノードも移動するため注意する。
- モデル・USDはstage / obj / SOPの文脈に応じた読み込みノードを生成する。
- Textureは入力欄へパスだけを渡す。材質階層でのみUV付きMaterialXを生成し、既存の出力接続を勝手に差し替えない。
- ライブラリー独自MIMEのイベントだけを扱い、通常のHoudini D&Dを妨げない。
- QListViewの表示モード・Movement変更でドラッグ設定がリセットされる場合がある。設定完了後のdragEnabledを確認する。
- パネルの狭いドッキング状態でも操作できる配置を保つ。

## 素材・フォルダーの移動

- 移動処理はorganize.pyに集約する（HoudiniとQtに依存させない）。上書きせず、失敗時は移動済みファイルとインデックスを元に戻す。
- 素材IDは移動しても変えない。タグ・お気に入り・メモはIDで保持する。IDはrelpathのハッシュで作るため、scanは既存relpathのIDを再利用し、他の行のIDと衝突する場合は新しいIDにする。
- サムネイルの同梱ルールはstorage.thumbnail_candidatesと同じ隣接ファイルを対象とする。他の素材と共有するものはコピーする。
- フォルダーは複数選択でまとめて移動できる。全て成功するか、全て元に戻す。
- ツリーの選択変更で一覧をすぐ再構築しない（マウス押下中に重い処理を走らせるとドラッグが始まらない）。遅延させ、ドラッグ中は止める。遅い一覧更新は、refreshにsleepを入れて実機で再現できる。
- 素材・フォルダーのドラッグは、Qtのドロップが届かない場合に備え、終了時のカーソル位置でも移動を行う（FolderTree.end_tracking）。二重に実行しない。
- 移動先は同じ分類・同じライブラリーのみ。USDパッケージの中には置かない。
- USDの移動後はAsset CatalogのファイルパスをsetFilePathで更新する。
- ライブラリー独自MIMEのドロップは、パラメーター欄・文字列入力欄・Network Editor・ライブラリーのフォルダーツリー以外では受け取り、何もせず無視する（Houdiniにファイルを開かせない）。

## 大量素材での動作（性能）

- UIスレッドで、素材数に比例する処理・ディスクの走査・画像の一括デコードをしない。目安: 2.8万ファイルの合成ライブラリー（tests外のベンチ）でfolder切替が20ms台、初期表示が0.5秒以内。
- フォルダー一覧は索引のfoldersテーブルから読む（Library.folders）。スキャンが更新し、パネルでの作成・移動はadd_folder / relocate(folder_moves)で反映する。旧版の索引でテーブルが空の時だけ、一度ディスクをたどって保存する。
- 一覧はLibrary.stream（EntryStream）で、ラベル順の索引（asset_order）から必要な件数だけ読む。直前のキー（label, relpath, root_id）より後ろを短い接続で読むため、DB接続や読み取りトランザクションを開いたままにしない（開いたままだとWALが縮まず、閉じる時にUIが数秒止まる。Windowsではファイルも掴む）。ページ分けはせず、スクロールでappend_itemsが続きを足す（CHUNK件ずつ）。全件を読んでPythonで絞らない・数えない（総数はLibrary.count）。フォルダー指定はrelpathの前方一致（'/'の次の'0'が上限）か、非再帰はfolder/pkg列。移動処理も対象フォルダー分だけを索引から読む（assets_by_ids、assets(folder=)）。
- 列・索引の追加はLibrary._migrate（PRAGMA user_version）で行い、旧索引を一度だけ変換する。
- 画像は表示位置の前後1画面分だけ持ち（schedule_icons）、3画面より遠いものは捨てる。アイテムにはrowを持たせずid（ROLE）だけにする。
- フォルダーツリーは開いた階層だけアイテムを作る（populate / ensure_item。閉じたフォルダーはplaceholderの子を持つ）。
- スキャンは別プロセス（scan_worker.py、Houdini同梱のPython）。QThreadの中ではhouを呼ばない（ジョブのコンストラクターで必要な値を取っておく）。補助プロセスはui.background()で低優先度にする。素材情報はinfoテーブルにも保存し、同じ素材ではhythonを再起動しない。
- ui.background()（Windows）はPROCESS_MODE_BACKGROUND_BEGINを使う。BELOW_NORMAL_PRIORITY_CLASSだけではCPU優先度しか下がらずディスクI/O優先度は下がらないため、proxy/LODの大量生成のように補助プロセスが長時間ディスクを使い続けると、パネル自身のファイル読み込み（素材選択時のプレビュー・info取得など）が競合して固まって見える（実際に発生・調査済み）。PROCESS_MODE_BACKGROUND_BEGINは明示的な優先度クラスと同時指定しない（単独で使う）。
- 一覧の画像はload_iconsで12msずつ読み込む。refresh内で画像を読まない。ディスクを触る処理（サムネイルの存在確認など）は同様にスライスする。
- 判定の重い純Python処理（pbr._find / stack_info）はキャッシュする。正規表現の前に部分文字列で絞る。
- スキャンはos.walkの文字列パスとlstatで行う（Pathオブジェクトを大量に作らない）。SQLiteはWAL（読み手はスキャン中も止まらない。ローカルディスク前提）。
- hythonを起動する処理は選択が落ち着いてから始める（250ms）。

## USDZ内のプレビュー

- サムネイルの優先順位は、指定画像 > 素材の隣の生成画像 > USDZ内のプレビュー（data/thumbnails/usdz_*にキャッシュ）。USDZ自体には書き込まない。内部のテクスチャ画像をプレビューと誤認しない（embedded.pyの検出規則を広げる時は、テクスチャ名を拾わないことをテストで確認する）。
- キャッシュはIDとmtime・サイズで決まるため、素材を移動しても有効。中にプレビューがないパッケージは、メモリー内で記録して再走査しない。

## サムネイルのライティング

- サムネイルのDome Lightはresources/meadow_2_8k.exr（Exposure -0.5、上軸まわり-30度）、Distant LightはExposure 1。値の変更はthumbnail_scene.pyの定数で行う。
- HDRIはライブラリーのassetではなく本体側のresourcesへコピーして使う（ユーザーの指示: 移動してもサムネイルが作れなくならないように）。容量が大きいためGitへは入れない（*.exrを除外）。resources/README.mdに手順を残す。HDRIがない場合は白いDome Lightへ戻す。

## Proxy / LODの生成

- proxy_gen.py（purpose=proxy）とlod_gen.py（lod variant set）が別プロセス（hython）で追加する。対象資産自身の入口ファイルだけを編集し、参照・ペイロード先には触れない。ファイル入出力（プレーンUSD/`.usdz`の展開・再パッケージ）はproxy_gen.pyのgenerate_plain/generate_usdzをlod_gen.pyが再利用する。
- デシメート前に`fuse`（UV・材質境界の分離頂点を結合。結合しないと境界の断片が個別に潰れて形が崩れる）と`divide`（三角形化。しないとpolyreduceの目標数＝プリミティブ数になり、四角形主体のメッシュで指定数のおよそ2倍が残る）を必ず通す。値はどちらも`_decimate`内で計算・固定。数値を変える時はここを見る。
- proxyは対象メッシュの束縛材質からbase colorテクスチャを検出できれば、UVでサンプルした色を頂点カラー（primvars:displayColor、vertex補間）としてattribfrommap相当で焼き込む（0〜1にクランプ）。見つからなければ無地のまま。LODでは焼き込みをしない。
- lod_gen.pyはvariant追加前に対象メッシュの点・面カウント属性をClear()する。USDの合成順（Local > VariantSets > References）で、消さないとどのLODを選んでも元のローカル値が勝ってしまい切り替わらない。
- `.usdz`はUsdUtils.ExtractUsdzPackageで展開し、アーカイブ先頭エントリ（usdz仕様のルートレイヤー）を編集してからUsdUtils.CreateNewUsdzPackageで再パッケージする。手動でのzip操作はしない。
- ui.pyの`_MeshGenerateJob`（ProxyJob/LodJobの共通基底）が結果を検証してから、data/backups/proxy/またはdata/backups/lod/へ元ファイルをバックアップし、os.replaceで置き換える。失敗・スキップ時は元ファイルを一切変更しない（生成スクリプト単体はSave()せずExport/CreateNewUsdzPackageで新規ファイルに書き出すだけ）。
- **一時出力ファイルは元ファイルと同じフォルダーに書く**（tempfile.TemporaryDirectory()配下ではない）。os.replaceはWindowsで別ドライブ間の置き換えができない（WinError 17）。素材ライブラリーとシステムTEMPが別ドライブの構成で実際に踏んだ既知の不具合（バックアップだけ作られ元ファイルは変更されないまま失敗する）。
- 実行時にダイアログで値を聞く（target triangles / levels / reduction%）。値はsettings.jsonに保存し次回の初期値にする。ダイアログをテストする時はQtWidgets.QInputDialog.getInt/getDoubleをpatchする。

## 一覧のアイコンサイズ

- Ctrl+中ボタンドラッグの処理はdragdrop.AssetListに置く。範囲は64〜512px、サイズはsettings.jsonのicon_sizeへ保存する。256pxを超える時だけ、大きい元画像（icon_edge）で一覧を作り直す。

## PBRスタックとマテリアル配置

- スタックはスキャン時にassetsのstack/channel/gkey列へ決める（Library.derive。同じフォルダー・同じキー（解像度込み）で2枚以上かつチャンネルが重複しない場合のみ）。一覧はEntryStreamがgkeyでメンバーを引いてpbr.group_entriesでエントリーにする。一覧の項目はROLEに代表のid、スタックのみSTACK_ROLEに全idを持つ。選択・D&D・メタデータ保存は必ず全画像へ展開する（item_rows）。self.rows / row_indexは読み込み済みの分だけ。
- チャンネルはファイル名の最後のトークンで決める。identify（材質作成）とstack_info（表示）で同じ判定を使う。
- マテリアルのノード配置はhoudini_ops._layout_materialに集約する。Imageノードは同一x・等間隔、変換ノードは元のImageと同じ高さ。既存ノードは動かさない。

## テストのパス比較

- GitHub ActionsのWindowsではTEMPが短縮名（`RUNNER~1`）になる。パスの比較は`Path.samefile`か`resolve()`済みのパスで行い、`Path`の等号で比較しない。ローカルでは、長い名前のフォルダーの短縮名（`cmd /c "for %I in (<dir>) do @echo %~sI"`）をTEMPに指定して全テストを流すと再現できる。
- 移動時のカスタムサムネイル判定（organize._remap）は、表記の違いを吸収するためresolve()済みで比較する。

## 保全と検証

- ファイル変更や移行前に、対象コード・設定・インデックスを日時付きでバックアップし、存在と内容を確認する。
- ライブHoudini操作前は、未保存変更を含むシーン全体を別名HIPとして保存する。ノードのみのCPIOや自動保存で代替しない。
- 利用環境にHoudini用の追加指示がある場合は従う。ライブ接続がなければ、接続済み・実機確認済みと報告しない。
- 検証用ノードは独立したネットワークに置き、終了後は自分が作ったものだけを片付ける。
- coreとstorageは標準Pythonで検証可能。Houdini / Qt依存の全テストは新しいhythonプロセスで実行する。
- UIやD&Dを変えた場合は実際の操作経路も確認する。画像変更では正方形と横長画像、TIFF/HDR、モデル/USDの保存先を確認する。
- ドキュメントだけの変更では、差分・リンク・実装との整合を確認し、レンダーや全テストを不要に再実行しない。
- 必要な検証が通ったら、失敗や新しい変更がない限り同じ確認を繰り返さない。

## Gitと報告

- 共有するコードとドキュメントだけをcommitする。push前に差分と追跡対象を確認する。
- 本体の移動後はpackage JSONとGitHub Desktop等の参照先を更新する。データ移行と既存登録の再リンクを混同しない。
- pushした場合はremoteのブランチSHAとローカルHEADの一致を確認する。
- docs/SPEC.mdとREADME.md（利用者向けの表現）の保存先、UI名、対応動作を変更内容と揃える。個別PCの導入記録はリポジトリ外へ保存する。
- 報告は変更点、検証結果、残る制限を簡潔に述べる。実行していない検証を完了扱いにしない。
