# NanakusaAssetLibrary 開発指示

## 作業範囲

- Houdini 22 / Solaris向けのPython Panel。材質はKarma XPU向けMaterialXを基本とする。
- 利用方法と現行仕様はREADME.mdを参照する。名称はNanakusaAssetLibrary、Pythonモジュール名はnanakusa_asset_library。
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
- 不足分の一括生成は検索条件に一致する全ページが対象。既存画像をスキップし、中止操作を維持する。

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

## サムネイルのライティング

- サムネイルのDome Lightはresources/meadow_2_8k.exr（Exposure -0.5、上軸まわり-30度）、Distant LightはExposure 1。値の変更はthumbnail_scene.pyの定数で行う。
- HDRIはライブラリーのassetではなく本体側のresourcesへコピーして使う（ユーザーの指示: 移動してもサムネイルが作れなくならないように）。容量が大きいためGitへは入れない（*.exrを除外）。resources/README.mdに手順を残す。HDRIがない場合は白いDome Lightへ戻す。

## 一覧のアイコンサイズ

- Ctrl+中ボタンドラッグの処理はdragdrop.AssetListに置く。範囲は64〜512px、サイズはsettings.jsonのicon_sizeへ保存する。256pxを超える時だけ、大きい元画像（icon_edge）で一覧を作り直す。

## PBRスタックとマテリアル配置

- スタックの判定はpbr.stack_entriesに集約する。一覧の項目はSTACK_ROLEに所属する素材IDを持ち、選択・D&D・メタデータ保存は必ず全画像へ展開する。self.rowsは全画像のまま保ち、ページ分割だけをスタック単位で行う。
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
- READMEの保存先、UI名、対応動作を変更内容と揃える。個別PCの導入記録はリポジトリ外へ保存する。
- 報告は変更点、検証結果、残る制限を簡潔に述べる。実行していない検証を完了扱いにしない。
