# NanakusaAssetLibrary 仕様・実装メモ

開発者・エージェント向けの現行仕様です。利用者向けの説明は [README.md](../README.md)、作業ルールは [AGENTS.md](../AGENTS.md) を参照してください。
仕様を変更したら、このファイルと README.md（利用者向けの表現）の両方を実装と揃えます。

現在のバージョンは **0.22.4** です。

## コードとデータの分離

このGitリポジトリーはコード、ドキュメント、合成データを使うテストだけを含みます。
アセットは独立した任意のフォルダーに置き、画面またはインストーラーから指定します。
PC固有の設定、SQLiteインデックス、タグ、画像素材の縮小キャッシュは、指定した`data`フォルダーに保存します。
モデルとUSDのサムネイルは素材と一緒に共有できるよう、素材の隣に保存します。
`NAL_DATA_DIR` で保存先、`NAL_ASSET_ROOT` で初回のアセットルートを指定することもできます。
これらのデータと素材はGitに含めません。

推奨する配置例です。コードのclone先と素材の保存先は独立させてください。

```text
<code-location>/NanakusaAssetLibrary/    # Git管理する本体
<library-location>/
  asset/                              # USD / Texture / 3DModel
  data/                               # settings.json、library.sqlite3、thumbnails/
    backups/                          # インストール・DB更新・変更前の保全
```

`data`のSQLiteは各PCのローカルディスクで管理し、複数PCから同じDBへ同時に書き込まないでください。
素材と素材の隣のサムネイルは共有できます。

## インストール・別PCでの開発

1. このリポジトリーを任意の場所にcloneします。
2. Houdiniの作業を保存してから、Python 3.10以上またはHoudini 22のhythonで実行します。

```text
python install.py --prefs "<Houdiniのユーザー設定フォルダー>" --library "<アセットルート>" --data "<データフォルダー>"
```

3. Houdiniを再起動し、Python Panelのメニューから **NanakusaAssetLibrary** を開きます。
   Shelfのボタン、または以下でも開けます。

```python
import nanakusa_asset_library
nanakusa_asset_library.show()
```

`--data`を省略するとアセットルートと同じ階層の`data`を使用します。
`--library`を省略して画面から登録することもできます。その場合は`--data`を指定してください。
両方省略した場合はOSのユーザーアプリデータ領域を使用し、HoudiniのDocumentsフォルダーにはキャッシュを作りません。
インストーラーはコードの場所と`NAL_DATA_DIR`を指定する
`packages/nanakusa_asset_library.json` を作成します。既存設定は日時付きでバックアップします。
コードを移動した場合は再インストールしてください。
別PCではそのPCの素材ルートを指定します。同じ素材階層を共有しても、インデックスは各PCで保持できます。
コード変更はGitでcommit/pushし、別PCでpull後にHoudiniを再起動します。
旧版の `packages/solaris_asset_library.json` がある場合は、バックアップして無効化してください。

### 既存環境の移行

1. Houdiniを保存して終了し、コード・package JSON・既存dataをバックアップします。
2. 本体フォルダー名を変更する場合は`.git`を含むフォルダー全体を移動します。
3. dataを移す場合は、`settings.json`・`library.sqlite3`・キャッシュをまとめて移します。
   インストーラーは既存dataの自動移動を行いません。
4. 既存インデックスを引き継ぐ場合は、新しい本体から`--prefs`と移動先の`--data`だけを指定してインストーラーを再実行します。
   この段階では`--library`で移動先を追加登録しないでください。
5. Houdiniを起動し、素材ルートを移した場合は既存の登録を「Libraries... → Relink Library...」で再リンクします。
   同じ素材を追加登録するだけでは、旧登録のタグやお気に入りは引き継がれません。
6. 再スキャンして素材と保存先を確認します。GitHub Desktopで本体が見つからない場合はLocateで新しいclone先を指定します。

package JSONの`path`が本体、`env`の`NAL_DATA_DIR`がdataの指定です。
古い場所を参照していないことを確認してから、旧フォルダーを整理してください。

## 固定の分類とフォルダー

ルート直下のアセット分類は次の3つに固定です。それぞれの下に整理用フォルダーを作れます。

```text
<asset-root>/
  USD/
    Props/
      Chair/
        Chair.usd
        thumbnail.png
        payload.usdc
        textures/
  Texture/
    HDR/
    PBR/
    Decal/
  3DModel/
    Props/
      Mesh.fbx
      Mesh_thumbnail.png
```

- **USD**: フォルダーと同名の `.usd` / `.usdc` / `.usda` / `.usdz` を入口として検出し、
  パッケージの親フォルダーを1件表示します。内部のUSDレイヤーやテクスチャは表示しません。
  複数の入口がある場合は上記の拡張子順です。同名の入口がないフォルダーは整理用フォルダーとして走査します。
  例外として単体の`.usdz`はUSD直下や整理用フォルダー内からファイル名で表示します。展開は不要です。
  同名の入口がなく、直下に`.usd`/`.usdc`/`.usda`があるフォルダー（USD直下を除く。例: `…_Big_OL.usd`と`…_Small_OL.usd`が`textures/`を共有）は、
  各レイヤーをファイル名（拡張子なし）の素材としてそのフォルダー内に表示し、サブフォルダーはパッケージと同様に走査・表示しない（`core.package_entries`）。
  パッケージ判定（`is_usd_package`）は「フォルダーと同名の入口」だけで、これらは`is_shared_usd_layer`。サムネイルは`<名前>_thumbnail.png`（USDごと）。
  フォルダー内のファイルを参照しているため、素材としてドラッグしてもフォルダーごと移動する（`organize.move_assets`: 中の全素材のrelpathとfoldersを更新し、選んでいない素材を`companions`として返す。UIはステータス欄に「Moved together (same folder …): …」と表示）。同じフォルダーの複数を選んでも移動は1回。そのサブフォルダーは移動先・New Folderの対象にしない。
  生成ジョブの一時出力（`*.nanakusa_generate_tmp.*`、`core.GENERATE_TMP`）はスキャンで除外する。
- **3DModel**: `.obj`, `.fbx`, `.vdb`, `.bgeo`, `.bgeo.sc`, `.geo`, `.geo.sc`, `.abc`, `.glb`, `.stl`, `.ply`。
  形状を単一ファイルから読める形式が対象です。FBXの外部画像や元の材質は再構築しません。
- **Texture**: PNG、JPEG、TIFF、HDR、EXRなどの画像。HDRやデカールもこの分類に置きます。

素材を追加したら「Rescan」を押してください。スキャンは元ファイルの移動・改名・削除をしません。
USDのパッケージ内は「Libraries... → New Folder...」の対象外です。UDIM・シーケンスの自動集約はありません。
ルート移動後は「Libraries... → Relink Library...」で再リンクできます。HIP内の既存パスは書き換えません。

## フォルダー整理（移動）

左側のフォルダーツリーへドロップして、素材とフォルダーの階層を整理できます。
Explorerでの手動移動は不要です。

- **素材の移動**: 素材一覧から左のフォルダーへドラッグします。複数選択も対応です。
- **フォルダーの移動**: 左のフォルダーを別のフォルダーへドラッグすると、中の素材ごと入れ子にします。
  例: `Texture/PBR/Misc/<セット名>_1K` を `Texture/PBR/Masonry` へ。
  Ctrl / Shiftクリックで複数のフォルダーを選び、まとめて移動できます（全部成功するか、何も変わらないかのどちらかです）。
  選択したフォルダーの中にあるフォルダーは、親と一緒に移動します。深い階層は、ドラッグ中に重ねると自動で開きます。
- 移動できるのは同じ分類の中だけです（USDはUSD、3DModelは3DModel、TextureはTexture）。
  USD / Texture / 3DModelの各分類フォルダー自体は移動できません。別のライブラリーへの移動もできません。
- USDパッケージは、入口USDのあるフォルダーごと移動します（内部の`thumbnail.png`や`textures`も一緒）。
  パッケージの中へは、素材もフォルダーも入れられません。
- サムネイルは素材と一緒に移ります（`<名前>_thumbnail.png/.jpg`、`<名前>.preview.jpg`）。
  同名の別素材が同じサムネイルを使っている場合は、コピーして元を残します。
  Textureのサムネイルは`data/thumbnails`のキャッシュで、素材IDに紐づくため移動不要です。
  「Choose Thumbnail...」で指定した画像も、一緒に移動した場合は指定先を更新します。
- タグ・お気に入り・メモは素材IDに保存しているため、移動後も保持します。Rescanしても引き継ぎます。
- 移動先に同名のファイル・フォルダーがある場合は、上書きせずにエラーにして何も変更しません。
- 途中で失敗した場合は、移動済みのファイルを元に戻し、インデックスも変更しません。
- 移動のたびにインデックスを`data/backups/index`へバックアップします。
- USDを移動すると、Asset Catalog内の同じUSDのパスも、Catalogフォルダー内の全DBについて更新します（更新前のDBを`data/backups/catalog`へ保存）。
- フォルダーを選んだ時の一覧更新は、マウスを押した瞬間ではなく120ms後に行い、ドラッグ中は止めます（ドラッグ後に実行）。
  一覧の更新が遅いと、押した直後のドラッグが効かなくなるためです。一覧のアイコンは再利用します。
- スキャン中、またはサムネイル生成の待機中は移動できません。
- 素材一覧やフォルダーから左のツリーへドラッグして離した位置が、ツリーのフォルダー上だった場合は、
  Qtのドラッグイベントがツリーに届かなくても移動を実行します（Escで取り消した場合は何もしません）。

移動しても、HIPファイルやUSDレイヤーに書かれた既存のパスは書き換えません。
別の場所のUSDからこの素材を参照している場合は、参照先を更新してください。
USDパッケージが自分の外側（`../`）のファイルを相対パスで参照している場合は、階層が変わると壊れることがあります。

## PBRセットのスタック表示

Textureの一覧では、同じ素材の画像（albedo・roughness・normalなど）を1つのスタックにまとめて表示します。
フィルター行の「Stack PBR Sets」で切り替えられます。設定は保存され、次回も引き継ぎます。

- 同じフォルダーで、ファイル名からチャンネル名を除いた部分（解像度を含む）が同じ画像を、1つのセットとして扱います。
  例: `Brick_Wall_1K_albedo.tif`、`..._ao.tif`、`..._normal.tif`。
  チャンネル名は、ファイル名の最後に現れるものを使います（`Metal_Plate_normal`はnormalとして扱います）。
- 2枚以上あり、チャンネルが重複しないセットだけがスタックになります。1K / 2Kは別のスタックです。
  同じチャンネルの画像が2枚あるフォルダー（例: `.tif`と`.png`）は、曖昧なのでスタックにしません。
- スタックはカードを重ねたアイコンと枚数バッジで表示します。ベースカラー画像が代表サムネイルになります。
- スタックを選ぶと、含まれるすべての画像が選択された扱いになります。
  D&D、Import Selected、Copy Paths、サムネイル生成、フォルダーへの移動は、全画像が対象です。
  材質階層へ落とすと、PBRセット1つ分のMaterialXを作成します。
- タグとお気に入りはスタックの全画像へ同時に保存します（表示は全画像のタグの和集合です）。
- 検索やお気に入りで一部の画像だけが残った場合は、残った画像だけでスタックを作ります。

## GUIと複数選択

GUIは英語です。右側には大きな正方形プレビューと素材情報を常時表示します。
検索（`Library._where`）は空白区切りの各語をAND。語はlabel・relpath・tagsの部分一致（LIKE、大文字小文字無視）で、`-語`はそのNOT（除外）。`-`だけの語は普通の語として扱う。一覧・件数・一括生成のキュー（`iter_rows`）はすべて同じ条件を使う。
素材情報（`asset_info.py`、選択が落ち着いてから別プロセスで取得しキャッシュする。テクスチャとUSDはHoudini同梱のPythonで約0.2秒、Houdini独自形式の参照などで読めなければhythonで再取得。3DModelはhython）は、種類ごとに項目が異なる。
テクスチャはResolution・Channels・Pixel type。3DModel・USDはPolygons・Points・Meshes（Volumeがあれば数も）。
USDはさらにUSD prims・**Proxy（Yes/No、`purpose=proxy`の有無）**・Up axis（Y/Z）・Materials（`UsdShade.Material`の数、あれば）・Size（バウンディングボックス、幅x奥行x高さ、空なら省略）を表示する。
素材の右クリックメニューに「Import Selected」「Copy Paths」「Show in Explorer」、USD選択時のみ「Add Catalog」を表示します。
「Show in Explorer」は素材ファイル自体を選択した状態で開く（`reveal.py`: フォルダーごとに`SHOpenFolderAndSelectItems`で全ファイルを選択。失敗時は`explorer /select,"<file>"`で1件、それも失敗またはWindows以外ならフォルダーを開くだけ）。素材を選んでいない時（Libraries...メニュー）は従来どおり現在のフォルダーを開く。
ライブラリーの追加・再リンクは「Libraries...」、読み込み設定は「Options」から開きます。
上部は1行のツールバー（検索・種類・Stack PBR Sets・Favorites・Options・Rescan）。パネルにフォーカスがある時の`Ctrl+F`は検索欄へ移動します（QShortcutではなくkeyPressEventで処理し、Houdini全体のホットキーを奪わない）。
「Libraries...」メニューはLibrary / Generate / Cancel Background Jobs / Catalogのセクションに分け、Cancel項目は対象の待機・実行中件数を表示し、何もない時は無効にする。
サムネイル・Proxy・Element Switch（生成・削除）が残っている間は、ステータス欄の上に「Background: …」行（`jobs_bar`、400msごとに`update_jobs_bar`で件数を更新）と「Cancel All」（`cancel_all_jobs`）を表示する。
Ctrlで追加選択、Shiftで範囲選択、Ctrl+Aで読み込み済みの素材を全選択できます（一覧はページ分けせず、スクロールで続きを読み込む）。
素材一覧でCtrl+マウス中ボタンをドラッグすると、アイコンサイズを変更できます（右・上へ動かすと大きく、左・下へ動かすと小さくなります。64〜512px）。Ctrl+ホイールでも1ノッチ24pxずつ変更でき、保存（`iconSizeFinished`）はホイール操作が300ms止まってから1回だけ行います。
サイズは`settings.json`に保存し、次回も引き継ぎます。256pxを超える場合は、鮮明に表示するため一覧を作り直します。
D&D・Copy Paths・Generate Selected Thumbnailsは選択した全素材が対象です。
右側の編集項目はTagsとFavoriteのみで、active asset（最後に選んだ1件）が対象です。
TagsはEnterまたは入力欄から離れた時に保存し、Favoriteは切り替え時に保存します。
任意サムネイル指定・Publishは1件選択時の右クリックメニューに表示します。

画像は解像度・チャンネル数・画素型、モデルとUSDはポリゴン数・ポイント数・メッシュ数などを表示します。
形状の数値は最初のフレームで読み込まれたUSD Meshの面数（非三角化）です。ボリュームは個数を表示します。
情報取得は別プロセスのhythonで行い、作業HIPを変更しません。画像はヘッダーだけを読みます。
結果はメモリー内に保持し、Rescanで破棄します。読み込み不能や60秒を超える処理は情報欄にエラーを表示します。

## D&D

| 素材 | ドロップ先 | 動作 |
|---|---|---|
| 3DModel | stage / LOPネットワーク | SOP Createと形式別の読み込みSOP |
| 3DModel | obj / SOPネットワーク | Geometry内の読み込みSOP、または読み込みSOP |
| USD | stage / LOPネットワーク | Reference LOP |
| USD | obj / SOPネットワーク | USD Import SOP |
| 全種類 | テキスト入力欄（Network ViewのPパラメーターを含む） | ファイルパスを入力 |
| Texture | Material Library / MaterialX Builderなどの材質階層 | UVを明示接続したMaterialX ImageとStandard Surface |

複数素材のD&Dは1つのUndoで戻せます。LOP / SOPにはMergeを作り、読み込んだ全素材を表示します。
複数パスのテキストはスペース区切り（空白を含むパスは引用符付き）です。
1ファイルだけを受け付けるパラメーターには、1素材ずつドロップしてください。Houdiniのネイティブ入力欄ではその欄の標準D&D規則に従います。
Import Selectedの複数読み込みも同じ一括処理です。USDはReference、既存LOPへの自動接続とPrim割り当て設定は単体読み込み専用です。

`variant`タグを持つUSD（複数オブジェクト入りパックの切り替え機能を参照）をstage / LOPネットワークへD&D、またはImport Selectedで取り込むと、`houdini_ops._add_variant_switch`がReference/Sublayer LOPの直後に`setvariant`ノードを挿入する。挿入したノードを新しい先頭に（後続の材質割り当て等はこのノードから接続する）。`num_variants=1`、`enable1=True`、`primpattern1`は対象resulting stageを実際にTraverseして見つけた`element` variant setを持つプリムの**合成後のパス**（`/assets/<name>/...`。Reference remapで元ファイル内パスが保存されるかどうかはdefaultPrimと分岐点プリムの関係次第）、`variantset1='element'`、`variantnameuseindex1=True`、`variantnameindex1=0`を設定する。variant setを持たない資産では何もしない（`add_variant_switch`フラグ自体は常に渡されるが、判定はステージを開いてから行う）。
`dragdrop.py`のペイロード辞書（D&D・Import Selectedの両方）に`'tags': row['tags']`を含め、`import_payloads`が`'variant' in payload.get('tags','').split()`で`add_variant_switch`を決める。`import_into_context`/`import_asset`へその真偽値をそのまま通す。

Scene Viewなど、ドロップ先として想定していない場所へ落としても何も起きません（Houdiniがファイルを開こうとして保存確認が出ることはありません）。
パラメーターのネイティブ入力欄と文字列入力欄だけは、パスを受け取ります。
Textureを通常のstageやobjへ落としてもノードは作りません。
既存Builder内では新しいSurfaceを作成し、既存の出力接続は保持します。必要に応じて新しいSurfaceを出力へ接続してください。
生成したノードは、左から「UV → Image → 変換ノード（法線・AO乗算・Displacement）→ Standard Surface → 出力」の列に整理します。
Imageノードは全て同じx座標で等間隔に縦に並び、上から`base_color`・`ao`・`metalness`・`roughness`・`emission`・`opacity`・`normal`の順（Standard Surfaceの入力順）、一番下が`displacement`です。変換ノードは元のImageの横に置きます。
既存のBuilderへ追加する場合は、既存ノードを動かさず、その下に同じ形で配置します。
色画像はsRGB、HDR/EXRはlinear Rec.709、ノーマル・粗さなどのデータ画像はRawに設定します。
FBXはFBX Skin Import、ABCはAlembic、GLBはglTF、VDB等はFile SOPで読み込みます。

### PBR画像の自動接続

同じフォルダーの同じセット名を持つ画像をまとめて材質階層へD&Dすると、MaterialXを1セット生成します。
例: `stone_1K_albedo.tif`, `stone_1K_roughness.tif`, `stone_1K_normal.tif`。
画像すべてのtexcoord入力は、セット内の1つのMtlX Texcoordノードを共有します。

| 命名トークン例 | 接続先 |
|---|---|
| albedo / basecolor / diffuse | base_color |
| roughness / rough | specular_roughness |
| metallic / metalness | metalness |
| normal / normalgl / nor_gl | MtlX Normal Map → normal |
| height / displacement / disp | MtlX Displacement（初期scale 0.01） |
| ao / ambient_occlusion | ベースカラーに乗算 |
| opacity / alpha | opacity |
| emission / emissive | emission_color |

区切り文字は `_`・`-`・`.`・空白に対応し、大小文字は区別しません。1K / 2K等はセット名から除外します。
用途名を判定できない画像は、個別のベースカラー材質として扱います。
同一チャンネルの解像度違い等を同時選択した場合は、曖昧な接続を避けるためエラーにします。
NormalはOpenGL形式を前提とし、DirectXと判定した画像は変換を求めます。Packed ORMの分解・UDIMの集約は対象外です。
既存Builderの出力が接続済みなら保持するので、新しいSurfaceやDisplacementは必要に応じて接続してください。

## プレビューとサムネイル

TIFF・HDR・EXRはHoudini付属の画像変換ツールで縮小し、`data/thumbnails`へ保存します。
一覧と詳細プレビューは正方形です。画像の縦横比を保持し、非正方形の画像の余白は透明にします。
元画像に黒い帯を追加したり、引き延ばしたりしません。画像表示は近似色です。

| 種類 | 生成画像の保存先 |
|---|---|
| 3DModel | 元ファイルと同じフォルダーの`<モデル名>_thumbnail.png` |
| USDパッケージ | 入口USDと同じフォルダーの`thumbnail.png` |
| 単体USDZ | 元ファイルと同じフォルダーの`<ファイル名>_thumbnail.png`（複数ファイルの衝突防止） |
| Texture | `data/thumbnails` |

USDの配置は[Component Builder](https://www.sidefx.com/docs/houdini/solaris/component_builder.html)の出力と同じ規約です。
USD仕様全体で必須のファイル名という意味ではありません。既存の`thumbnail.jpg`も認識します。
素材の隣へ書き込める権限が必要です。生成失敗時は既存サムネイルを保持します。

USD・3DModelは右クリックの「Generate Selected Thumbnails」で作成できます。
「Libraries... → Generate Missing Thumbnails」は、現在の検索・フォルダー・種類の条件に一致する全素材の不足分を順番に処理します。
既存サムネイルがある素材は一括生成でスキップします。「Libraries... → Cancel Thumbnails」で待機分を解除できます。

形状のサムネイルは別プロセスのhythonとKarma CPUで512×512にレンダリングします。
屋外HDRI（`meadow_2_8k.exr`）のDome Light（Exposure -0.5、上方向軸まわりに-30度回転＝Houdiniの回転0, -30, 0）と、Exposure 1のDistant Lightを配置します。
HDRIは背景には映りません。
HDRIはライブラリーのフォルダーから独立させるため、本体の`python3.13libs/nanakusa_asset_library/resources/`にコピーして使います。
容量が大きい（約92MB）ためGitには含めません（`*.exr`は除外）。別のPCでも同じファイルをこのフォルダーへコピーしてください。
ファイルがない場合は、Houdini初期値の白いDome Light（Exposure 0）で生成します。
USDの上方向軸（Y-up / Z-up）は、構図とライティングに反映します。
既存のサムネイルには、右クリックのGenerate Selected Thumbnailsを実行して更新してください。
形状の境界から斜め前方のカメラと照明を自動設定するため、作業中のHIPにはノードを追加しません。
GPUを占有せず4 CPUスレッドを使います。Houdini / Karmaの利用可能なライセンスが必要です。
USDの材質と依存ファイルを参照し、最初のフレームを描画します。欠落した依存ファイルや読み込み不能な形状はエラーとして表示します。
ボリュームの見た目は元データの密度・材質に依存します。任意の画像を「Choose Thumbnail...」で割り当てることもできます。
元ファイル更新後は再スキャンして生成してください。USD内の依存画像だけを更新した場合は選択素材を再生成してください。

## Proxyの生成

`purpose=proxy`が無いUSDはScene Viewでもレンダー用の形状がそのまま表示される。`proxy_gen.py`が別プロセス（hython）で簡易形状を追加する。

- 対象はUSD種別の資産全て（`.usd`/`.usda`/`.usdc`/`.usdz`）。既に`purpose=proxy`を持つ資産、メッシュが無い資産は`{'skipped': 理由}`を返しスキップする。
  - 例外: 既存のproxyのうち`_needs_look`に当たるもの（下記）があれば、`NAL_proxy_look`の割り当てだけを書いて`{'restyled': [パス]}`を返す（`_restyle_proxies`。variantは`_sweep_variants`で全て見る）。ProxyJobの表示は"Already has a proxy; proxy material fixed (N mesh(es))"で、`proxy`タグも付く。
- 対象の入口ファイル自身（`Usd.Stage.Open`のルートレイヤー）だけを編集する。参照・ペイロード先の別ファイルは変更しない。
- デシメートはHoudiniの`polyreduce::2.0`（Output Polygon Count）を使う。事前に2つの前処理をしている：
  - `fuse`（Snap Distance、対角線の0.02%）でUV・材質境界の分離頂点を結合してから減らす。結合しないと、境界で分かれた各断片が個別に潰れて形が崩れる。
  - `divide`（Convex Polygons、最大3辺）で三角形化してから減らす。`polyreduce`の目標数はプリミティブ数であり、四角形・多角形主体のメッシュのまま渡すと、指定した三角形数のおよそ2倍が残ってしまう。
- 小さな部品のカード化（`_proxy_geometry`、目標を超えるメッシュのみ）: 同じ位置の点を結合してから連結成分に分け、外接箱の対角がメッシュの対角の3%（`SMALL_PIECE`）未満の部品を、0.5%（`CARD_GAP`）の格子で隣り合うものどうしグループ（穂1本）にまとめ、グループごとに1枚の四角形（主成分分析の面、長さは全範囲・幅は5〜95パーセンタイル、幅の下限は長さの15%）に置き換える。色はそのグループだけにテクスチャを焼き込んだ点の平均。残りの部品はこれまでどおりデシメートする。polyreduceは部品をまとめられないため、小花が約3900個あるHolcusLanatus_463gg_Big（var_01）のproxyは目標300に対して16,597三角形残っていた。カード化で1,046三角形（カード17枚）になった。穂は板として見え、以前のまばらな点より形が分かる。
- 各`UsdGeom.Mesh`を、三角形換算で`target_triangles`（ダイアログで指定、既定`TARGET_TRIANGLES`=300）を超える場合のみデシメートする。小さいメッシュはそのまま複製する。結果は元プリムの兄弟として`<name>_proxy`に追加し、`purpose=proxy`を設定する。元プリムには`purpose=render`を明示する（Scene Viewでproxyが優先されるため）。名前衝突時は`_`を付けて回避する。
  - 元プリムの束縛材質からbase colorテクスチャを検出できた場合（`UsdPreviewSurface`の`diffuseColor`/`baseColor`が`UsdUVTexture`に接続され、`file`が解決できる形）、そのUV primvar（`UsdPrimvarReader`の`varname`、既定`st`。`faceVarying`/`vertex`/`uniform`/`constant`のいずれの補間にも対応）を使い、Houdiniの`attribfrommap`相当でテクスチャ色を頂点（点）ごとにサンプルし、`primvars:displayColor`（vertex補間）としてproxyへ焼き込む。デシメート後も色は点属性としてそのまま補間で引き継がれる（`polyreduce`が境界で多少オーバーシュートすることがあるため0〜1にクランプする）。材質・テクスチャが見つからない場合は無地のまま。
  - proxyに書く属性は`points`・`faceVertexCounts`・`faceVertexIndices`・`purpose`と、色がある時の`primvars:displayColor`だけ（Houdiniでは`@P`と`@Cd`）。UV・法線・不透明度は書かない。
  - **材質**: proxyはレンダーメッシュの兄弟なので、上の階層（例: 資産の`geo` Scope）に束縛された材質を受け継ぐ。UVの無いproxyはその材質のテクスチャを(0,0)で読み、葉・草の不透明度マップは(0,0)が0のため、`opacityThreshold`（0.5）でproxy全体が切り抜かれてScene Viewから消えた（`AcerPseudoplatanus_abw4u_Leaves_OL.usd`で確認）。空の`material:binding`は継承を止めない（UsdShadeはさらに上を探す）。
  - そのため、直接の束縛もUVも無く、何かの材質を受け継ぐproxy（`_needs_look`）は、一番上のプリム直下の`NAL_proxy_look`（`PROXY_LOOK`。**Materialではない空のScope**）へ束縛する（`_bind_look`、Sdfで書く。variantへ複製する前に書くので複製にも付く）。束縛先がMaterialでないと、`ComputeBoundMaterial`はproxy自身の束縛で探索を止め、材質なしを返す（上の階層へは進まない。plain Pythonで確認）。ビューポートは材質の無いメッシュとしてproxy自身の`displayColor`で描く。
  - 0.22.2ではシェーダーの無い`UsdShade.Material`にしていたが、HoudiniのScene Viewはそれを灰色の材質として描き、`@Cd`を無視した（ユーザーのスクリーンショット、`AcerPseudoplatanus_853se_Big_OL.usd`）。`_proxy_look`はその形のMaterialをScopeへ変え、`_restyle_proxies`は旧形式に束縛されたproxy（`_on_old_look`）も対象にする。`UsdPreviewSurface`＋`UsdPrimvarReader_float3`（displayColor）の材質は、husk（Storm）では定数色でも黒くなったため採らなかった。材質を受け継がないproxyには何も書かない。
  - 2026-09-24に実ライブラリーの既存proxyへ同じ割り当てを追加した（Plantsの235ファイル・3835メッシュ。元ファイルは`data/backups/repairs/proxy_look_repair_20260924_002800/`、各ファイルの差分は割り当ての追加だけであることを確認）。同日、0.22.3で同じ235ファイルの`NAL_proxy_look`をScopeへ変えた（元ファイルは`data/backups/_delete_candidates/proxy_look_scope_20260924_010737/`（削除候補）、差分はその型だけ、全proxyが材質なしになることを確認）。asset_infoのMaterials数には`NAL_proxy_look`を数えない（旧形式の名残）。
- **variantのある資産**（例: `ThymusVulgaris_e95j6_*_OL.usd`。各`LOD_*` variantが`var_01`〜を定義し、`variant` setが選ばれなかったものを`active=false`にする）:
  - 対象メッシュは、そのままの状態に加え、LOD以外の各variant setの各variantを1つずつsession layerで選んで集める（`_collect_meshes`。ファイルの選択は変えない）。LOD系のset（`lod`で始まる名前）は切り替えない（形状が変わるだけで、proxyは1つで全段を代表する）。
  - proxyはメッシュが定義されている場所に作る。メッシュがvariantの中でだけ定義されている場合は、定義している全variantへ`Sdf.CopySpec`で同じproxyを置く（`_variant_definitions`）。
  - メッシュの`active`・`visibility`の意見（直接のもの、およびLOD以外の全variantの中のもの）をproxyにも同じ場所で書く（`_copy_switching`）。これで選ばれなかったversionのproxyも一緒に消える。LOD系のsetは写さない（lod_genが元メッシュを隠しても、proxyは代表として残す）。
  - `purpose=render`はSdfで直接書く（メッシュが今の選択では合成されていない場合があるため）。lod_genが先に作った`<名前>_LOD_k`の複製も`purpose=render`にする（`_mark_lod_copies_render`。そうしないとScene Viewにproxyと並んで出る）。
  - 修正前（0.21.0まで）は今の選択で見えるメッシュ（var_01）だけにproxyを作り、variantの外に置いていたため、var_02以降を選ぶとScene Viewに「var_01のproxy」と「選んだものの形状」が並んで出た。実ライブラリーの52件を、proxy生成前のバックアップ（旧proxyを取り除いた結果がバックアップと完全一致することを確認）から作り直した（壊れていたファイルは`data/backups/_delete_candidates/proxy_repair/`、削除候補）。
- デシメートへの受け渡しはメモリー上のジオメトリ（Stash SOP）、読み戻しは一括の属性読み取り（点番号を点属性にして角へpromoteし`vertexIntAttribValues`）。以前のOBJ書き出し＋頂点ごとのPythonループでは、100万三角形×6のSalixCaprea_1x94n_Big_OL（葉が離れた部品のためpolyreduceでも約25万三角形までしか減らない）が201秒かかりジョブの240秒制限を超えた。現在は69秒。
- 出力は常に新規ファイルへの書き出し（`Sdf.Layer.Export`）。元ファイルへの`Save()`は行わない。
- `.usdz`は`UsdUtils.ExtractUsdzPackage`で展開し、アーカイブの先頭エントリ（usdz仕様のルートレイヤー）だけを編集する。展開先で参照・テクスチャは相対パスのまま解決できる。編集後は`UsdUtils.CreateNewUsdzPackage`で参照ファイルごと再パッケージする。

`ui.py`の`ProxyJob`が`hython proxy_gen.py <元ファイル> <一時出力> <結果JSON> <target_triangles>`を実行し、成功時のみ`data/backups/proxy/`へ元ファイルをバックアップしてから、一時出力で元ファイルを置き換える（`os.replace`）。失敗時・スキップ時は元ファイルを一切変更しない。
**一時出力は元ファイルと同じフォルダーに書く**（`<stem>.nanakusa_generate_tmp<suffix>`）。システムのTEMPフォルダーに書いてから`os.replace`すると、TEMPと素材ライブラリーが別ドライブの場合にWindowsが受け付けず（`WinError 17`）、置き換えが毎回失敗する（原因調査済みの実バグ。失敗時もバックアップだけは先に作られてしまうため、直った後の再生成で不要なバックアップが残る）。

右クリックの「Generate Selected Proxies...」は選択したUSD資産に生成する。実行前にダイアログで値を確認し、Cancelなら何もしない。値は`settings.json`（`proxy_target_triangles`）に保存し、次回の初期値にする。
「Libraries... → Generate Missing Proxies...」は、現在の検索・フォルダー・種類の条件に一致するUSD資産を全てキューへ入れる（既に`purpose=proxy`があるかどうかはファイルを開かないと分からないため、UIスレッドでは判定せず、各ジョブが自分のファイルを見て判断する）。「Cancel Proxies」で待機分を解除できる。
フォルダー移動・素材移動は、待機中のproxy生成がある間はできない（該当のCancelで解除するか完了を待つ）。

## 複数オブジェクトパックの切り替え

Sketchfab等のパックは、同じ原点付近に複数の独立したトップレベルオブジェクト（例: きのこの品種違い数体）を1ファイルへまとめていることがある。そのまま配置すると全オブジェクトが重なって表示される。`element_gen.py`が別プロセスで`element`という名前のvariant setを1つ追加し、常にちょうど1つだけを表示できるようにする。

- 対象の検出（`_find_pack_root`）: ステージのpseudo-rootから幅優先探索し、**子を2つ以上持ち、かつそのうち2つ以上の子孫にメッシュを含む、最も浅いプリム**を「パックの分岐点」とする。見つからなければ`{'skipped': 'no multi-object pack found (need 2+ sibling objects with geometry)'}`。
  - `purpose=proxy`を持つメッシュ自身は候補から除外する（`_is_proxy_mesh`）。除外しないと、proxy生成済みの単一オブジェクト資産（レンダーメッシュとその`_proxy`兄弟）が「2要素のパック」に誤検出され、どちらか一方しか表示されなくなる回帰があった。
  - **この検出はヒューリスティックであり、「複数の独立した代替オブジェクトを束ねたパック」と「複数パーツが揃って初めて1つの物になるモジュール式キット」を区別できない**（両者ともファイルの形は同じ：トップレベルに複数のメッシュ持ちグループが並ぶ）。誤ってキットに使うと大半のパーツが非表示になる。そのためLibraries...の一括生成（Generate Missing...相当）は用意せず、ui.pyの「Generate Selected Element Switch...」による資産ごとの手動生成のみとする。
- 既に`element` variant setを一番上のプリムに持つ資産は`{'skipped': 'already has an element switch'}`。0.19.0より前の形式（分岐点プリムに付いている）なら、まず`_remove_element_switch`で生成前の状態へ戻してから下記の手順で作り直し、結果に`moved_from`を付ける（UIの表示は「…, moved to the top prim」）。
- variant setは**一番上のプリム**（`_anchor`: 分岐点がdefault primの下ならdefault prim、そうでなければ分岐点の最上位の祖先）に作る。Reference / Stage Managerはこのプリムを配置したプリムへ対応づけるため、配置したプリム自身がvariant setを持つ。Stage ManagerはInspectorの「Variant Sets」も、書き込むvariant選択（changeの`variantset#_#`）も配置したプリム自身の分だけを扱い、子孫プリム（`…/RootNode`）への指定は無視する（22.0.447、hythonで確認。Scene Graph Treeの右クリックのSet VariantもStage Managerの変更として書くため効かない）。
- `element` variant setを作り、検出した各子に`Element0`, `Element1`, ... という名前のバリアントを割り当てる。各バリアントの中で、選ばれた子には`visibility=inherited`、それ以外の子には`visibility=invisible`を明示的に設定する（ジオメトリ自体は変更しない。分岐点プリムに元々ローカルなvisibility値は無いため、LODのようなClear()は不要）。選択は`Element0`に設定して終える（何もしなければ全オブジェクトが重なって見える現状を、生成直後から解消するため）。
- proxy_gen.pyと同じファイル入出力（`generate_plain`/`generate_usdz`）を再利用する。`.usdz`は展開・編集・再パッケージ、プレーンUSDは`Export()`のみ。

`ui.py`の`ElementJob`（`ProxyJob`と`_MeshGenerateJob`を共通基底とする）が`hython element_gen.py <元ファイル> <一時出力> <結果JSON>`を実行し、成功時のみ`data/backups/element/`へバックアップしてから`os.replace`で置き換える。失敗・スキップ時は元ファイルを一切変更しない。
右クリックの「Generate Selected Element Switch...」は選択したUSD資産に生成する（ダイアログでの値入力は無い）。「Libraries... → Cancel Element Switches」で待機分を解除できる。フォルダー移動・素材移動は、待機中のelement switch生成がある間はできない。

削除（`_remove_element_switch`）は入口レイヤーに書かれた`element` variant setを位置に関係なく消し、variant setを置くためだけに作った空の`over`も消す。生成→削除でルートレイヤーの内容は生成前と一致する（実資産58件で確認: 旧形式を削除した結果と一致し、パッケージ内の他ファイルも不変）。
配置後にどのオブジェクトを表示するか切り替えるには、Stage ManagerのInspector（Variant Sets）か、LOPネットワークの**Set Variant**ノード（`setvariant`）でPrimitivesに配置したプリム、Variant Setに`element`、Variant Nameに`Element0`〜`ElementN-1`のいずれかを指定する。Scene Graph TreeのVariantsタブから直接切り替えることもできる。`variant`タグの付いた資産をD&D/Import Selectedで配置した場合、このノードは`houdini_ops._add_variant_switch`が既に自動で挿入している（[D&D](#dd)を参照）。配置は、1回の取り込みで作ったノードの連なり（`import_chain`: Reference → Set Variant / Assign Material）の先頭をドロップ位置・格子のセルに置き、続くノードを`CHAIN_STEP`（1.0）ずつ真下に並べる（`place_chain`）。複数ドロップの行間とMergeの位置は最長の連なりに合わせる。

### 削除・自動タグ・サムネイルバッジ

- `element_gen.py`の`_remove_element_switch`（`remove()`関数、`element_gen.py <元> <一時出力> <結果JSON> remove`で呼ばれる）は`_add_element_switch`を完全に取り消す。`UsdVariantSets`にはこのUSDバージョンで`RemoveVariantSet`が無いため、`Sdf.PrimSpec`を直接編集する: `spec.variantSets`から該当キーを`del`、`spec.variantSelections`から`del`、`spec.variantSetNameList.prependedItems`/`explicitItems`から該当名を`remove`。バリアント内に書いた可視性の上書きも、variant自体のspecごと消えるため個別に消す必要はない。見つからなければ`{'skipped': 'no element switch found in this file'}`。
- `ui.py`の`ElementDeleteJob`（`_MeshGenerateJob`を継承、`extra_args=('remove',)`でelement_gen.pyへ渡す）が右クリックの「Delete Element Switch」から呼ばれる。`data/backups/element/`へバックアップしてから置き換える点はElementJobと同じ。「Libraries... → Cancel Element Switch Removals」で待機分を解除できる。
- **`variant`タグの自動付与・削除**: `ElementJob`が成功しresultメッセージが`'Element switch added'`で始まる時、`LibraryWidget.set_variant_tag(aid, True)`が対象資産の`tags`（スペース区切り文字列）へ`variant`を追加し、`library.update`でDBへ保存する。`ElementDeleteJob`が成功し`'Element switch removed'`で始まる時は同じ仕組みで`variant`を取り除く（`set_variant_tag(aid, False)`）。スキップ（既にある/対象なし）の時は触らない。ジェネレーター側は一切タグを知らず、ui.py側の完了ハンドラーだけがタグを管理する。
- **サムネイルバッジ**: `load_icons()`が、PBRスタックでない各アイテムについて`entry['rep']['tags']`に`variant`が含まれるかを見て、含まれていれば`variant_badge()`（`stacked_icon`と同じ256論理座標のcompositing手法で、左上に「VARIANT」の角丸ラベルを重ねる）でアイコンを差し替える。ファイルを開いて`HasVariantSet`を確認する必要はなく、DBの`tags`列を見るだけなので、一覧に並ぶ全アイテムに対して安価に行える。
- タグの付け外しは`set_tag(aid, tag, present)`（`set_variant_tag`はその別名）。一覧に読み込まれていない資産（一括キュー）は`assets_by_ids`で索引から読んで更新する。バッジは`variant`→「VARIANT」（青）、`lod`→「LOD」（橙）を左上へ横に並べる。
- `set_variant_tag`はタグ変更後、`item_index`から対象アイテムのインデックスを引き、`icons_loaded`から外して`icon_todo`の先頭へ積み直す（`thumbnail_done`と同じ手法）ことで、次の`load_icons`スライスで確実にバッジ付きへ再描画させる。選択中の資産であれば`selection_changed()`も呼び、右側のTagsフィールドにも反映する。

## LOD

`lod_gen.py`（別プロセスのhython）がUSD資産に`LOD` variant setを追加する。Houdiniの標準ノードがそのまま使える形にする:
- Create LOD LOPの作る形（配置・計測されるプリム自身のvariant set、詳細な順に名前が並ぶvariant）に合わせ、variant setは資産の一番上のプリム（`element_gen.anchor`）に置き、名前は`LOD_1`〜`LOD_N`（N≦9。Auto Select LODはvariant名の順に閾値を当てるため、10以上だと並びが崩れる）。
- **Auto Select LOD LOP**（`autoselectlod`、HDA）: 内部のwrangleが、Primitivesの各プリムについてカメラ位置とプリムのワールド原点の距離を求め、variant名の順に`thresh_dist#`を超えた最後のものを選ぶ。Number of LODsは段数以上にする。Primitivesのプリム自身にvariant setが必要（22.0.447、hythonで確認: 2つの配置で距離0→LOD_1、5000→LOD_4）。
- **Stage Manager**: 配置したプリムのInspector「Variant Sets」に`LOD`が出て、選択はStage Managerの変更として書かれる（hythonでchangeの`variantset#_#`を指定して確認）。

生成（`_add_lods`）:
- 対象は`purpose=proxy`以外の全Mesh。まず全メッシュを縮小し、1つでも扱えなければ何も書かずにスキップする。
- `LOD_1`は何も変えない（元のまま）。`LOD_k`（k≧2）の中でだけ、各メッシュの縮小版を兄弟プリム`<名前>_LOD_k`として定義し、元のメッシュに`visibility=invisible`を書く。元データに触れないため、削除（`remove_variant_set`）で生成前と同じ内容に戻る（テストと実資産で、ルートレイヤーの文字列一致を確認）。
- 縮小: SOP verb（ノードを作らない）で、UV・法線などのfloatのprimvarを頂点（face corner）属性として運び、座標の一致する点をfuse（UVの継ぎ目で分かれた点を結合。しないとUVの島ごとに縮小されて形が崩れる）→三角形化→polyreduce（`percentage`、属性の継ぎ目は保持）。1段ごとの割合は元の三角形数に対して`keep^(k-1)`。
- 結果は、値の異なる角でだけ点を分けて頂点（vertex補間）データに戻す（faceVaryingのままだと容量が約3倍。60万三角形の資産で29.7→65.8MBが45.9MBになった）。法線は縮小後の面でNormal SOP（角度重み、60度で分割）を使って計算し直す（元の法線を補間すると大きな三角形に暗いムラが出た）。Houdiniは逆向きの巻きを表とみなすため、rightHandedのメッシュでは`reverse`で戻す。
- マテリアルの割り当て（`material:binding*`）、subdivisionScheme・orientation・doubleSided・purpose・xformOpを複製する。GeomSubsetは現在の素材に無いため未対応。
- スキップ: 既に`LOD`がある／メッシュが無い／メッシュが複数の最上位プリムに分かれている／メッシュに既に`visibility`がある（variantの意見が負けるため）／扱えないprimvar（float以外の非constant、elementSize>1など）。段数2〜9・割合1〜99%以外はエラー。
- Element Switchとの共存: 素材のElement Switchの候補は全てXform（実資産302件で確認）なので、縮小メッシュは候補の中にでき、Element Switchの非表示が及ぶ。LODとElement Switchはそれぞれ単独で削除できる。

UI:
- 右クリックの「Generate Selected LODs...」（段数・割合のダイアログ、`settings.json`の`lod_levels`/`lod_keep`に保存）、「Delete LODs」。「Libraries... → Generate Missing LODs...」は条件に合うUSDのうち`lod`タグが無いものをキューへ入れる（各ジョブが自分のファイルを見てスキップもする）。キャンセル・Backgroundバー・移動の禁止は他のジョブと同じ。バックアップは`data/backups/lod/`。
- 成功で`lod`タグとバッジ、削除で外す。素材情報にはUSDの一番上のプリムのvariant set（例: `LOD (4), element (5)`）を表示する。
- D&D / Import Selected: `lod`タグがあれば`houdini_ops._add_lod_select`がAuto Select LODを追加する（Primitives=配置プリム、Variant Set=`LOD`、段数、距離=対角線×4×2^(k-2)、カメラ=上流の最初のカメラ、無ければ`/cameras/camera1`）。

## 風のアニメーション（Generate Wind Animation）

`wind_gen.py`（別プロセスのhython。pxrとnumpyだけで動く）が、植物USDを風で揺らした**別の資産**`<名前>_Anim.usd`を作る。元ファイルは一切書き換えない。インスタンスでどの向きに置いても成り立つよう、特定の風向きを持たない（周囲から吹く）揺れにする。

出力の形:
- 入口`<名前>_Anim.usd`: 元ファイルをreferenceし（同じフォルダーなら`./`の相対パス、別ドライブなら絶対パス）、足すのはUsdSkelの情報だけ。一番上のプリム名は`<名前>_Anim`。
  - `SkelRoot`は全メッシュの共通の親のうち一番上のプリムより下（例: `geo`）。**一番上のプリムにしない**: 配置側（Reference・プロトタイプ）が同じプリムに型を書くと`SkelRoot`が消えてスキニングが止まる（試作で確認）。共通の親が一番上のプリムしかない場合だけ、そこを`SkelRoot`にする（resultの`note`に記録）。
  - 植物（`variant`の各選択）ごとに`<SkelRoot>/NAL_wind/<メッシュ名>`のSkeletonと`<メッシュ名>_anim`のSkelAnimation。Skeletonは`visibility=invisible`: HoudiniのScene ViewはSkeletonの骨を（purpose=guideでも）数百本の線として描くため。invisibleでもScene ViewとKarmaのスキニングは変わらない（22.0.447で確認、同フレームの差はノイズ程度）。
  - 各メッシュに`primvars:skel:jointIndices`/`jointWeights`（3影響: 部品に沿った2関節＋地面の関節）。点がLODで変わるメッシュは`LOD` variantの中に、変わらないもの（proxyなど）は外に書く。元の`LOD`/`variant`の選択は書かない（元の選択のまま）。
- クリップ`anim/<名前>_Anim_clip.usd`: 各SkelAnimationの`rotations`を0〜Lフレーム（Lフレーム目＝0フレーム目）で持つ。入口ではvalue clipとして`times`を`[(kL+o,0),((k+1)L+o,L)]`（k=-100〜999）で並べ、ほぼ全フレーム（Houdiniの1、ショットの1001など）でループさせる。
  - 入口側の`rotations`は**値なしで宣言だけ**する。同じレイヤースタックにdefault値があるとクリップより強くなり、アニメーションが消える（試作で確認）。
  - `wind_phase` variant set（`phase_0`〜`phase_3`、既定`phase_0`）: 同じ動きをL/4ずつずらしたクリップ設定。Point Instancerは1プロトタイプ1動作なので、同じ株を並べるときにphase違いのプロトタイプを作ると揃って揺れない。クリップ設定はvariantの中だけに書く（外に書くとvariantより強くなる）。D&Dの自動Set Variantは`wind_phase`より元の`variant`を選ぶ（`houdini_ops._add_variant_switch`）。
- `customLayerData.nanakusa_wind`に元ファイル名・強さ・ループ長を記録する。

リグ（植物ごと、最も詳細なLODから作る。作業空間はY上向き、Z-upは回転して扱う。`Rig`）:
1. メッシュを連結成分（部品）に分ける。根元近く（高さの5%以内）から1.5帯以上伸びる部品を「根付き」、残りで大きさが2帯を超える部品を「枝」（茎の途中から出る葉、茎に届いていないスキャンの葉）、それ以外を「ばら」（花穂の小花、種、ごみ）とする。帯＝高さ/14。地面に寝た部品（最高点が1帯未満）は動かない地面の関節（関節0）へ。
2. 根付き部品は根元（下端付近で株の中心軸に最も近い点）から、枝は根付き部品に最も近い点からの測地距離（辺をたどるダイクストラ）を帯に切り、帯の中でつながった塊を1関節にする。親は最短経路の元の塊（分岐する茎・葉も木構造になる）。枝の根の関節は、付け根の点を最も動かす関節の子。
3. 点のウェイトは自分の関節と距離方向の前後の関節の線形補間（子が複数なら近い子）。関節は回転だけなので長さが保たれる。高さの6%以下では地面の関節へなめらかに移し、根元・根は動かさない（地面から8mm以内の点の移動0）。
4. ばら部品は、互いに高さの0.6%以内のものをまとめてグループ（花穂）にし、グループ全体として一番近い（近さで重み付けした多数決）部品を選び、各部品はその部品の最も近い点へ剛体で付く。部品ごとに最寄りへ付けると、別の茎と接する花穂が2本の茎に引き裂かれた（実資産で最大4.6cmずれた。修正後は1cm超0件）。
5. 他のLOD・proxyは、最も詳細なLODの最寄り点のウェイトを写す（格子バケットの厳密な最近傍、`nearest`）。

動き（`animate`）:
- 風＝いろいろな方向から来る突風（植物を通り抜ける平面波6本、ゆっくりした強弱の包絡）＋植物の大きさ程度で位相が変わる乱流。平均の向きを持たない。
- 部品ごとに減衰振動子として応答: 主曲げ（固有振動数は長さ0.5mで1.1Hz、長さの平方根に反比例、小花などが付いて重いほど遅い、上限2.5Hz、減衰0.32）、外側の速い曲げ（主の2.8倍）、ねじれ。力もすべてLで周期的なので、FFTで定常応答を解けばループの継ぎ目が無い。減衰を0.18にした試作は、固有振動が目立ってメトロノームのように見えた。
- 角度はRMSで正規化する（主曲げの先端RMS 0.2rad×強さ、ソフト上限0.6rad）ので資産が変わっても揃う。さらに植物の高さで弱める（`size_response`: 0.5〜1mで1、小さい株は√(h/0.5)、大きい株は1/√h、下限0.3。7cmの株が草と同じ角度で振れて大げさだった）。数値は`wind_gen.py`冒頭の定数。
- 生成時に参照メッシュをnumpyでスキニングし、辺の長さの変化（裂け）・沈み込み・ループの差を`checks`に出す。書き出し後に入口を開き、合成エラー・クリップの解決・ループ・スキニング対象を確かめてから、クリップのパスを最終名へ書き換える。

UI・ジョブ:
- USDの右クリック「Generate Wind Animation...」（強さ0.1〜3、ループ4〜30秒のダイアログ。`settings.json`の`wind_strength`/`wind_loop_seconds`）。`WindJob`が`hython wind_gen.py <元> <出力> <結果JSON> <強さ> <秒>`を実行し、成功後にクリップ→入口の順で一時名から`os.replace`する。以前の出力は`data/backups/anim/`へ複製してから置き換える。Houdiniが開いているファイルはWindowsでは置き換えられずエラーになり、前の版が残る。
- 出力先（`wind_gen.output_for`）: 共有レイヤーのフォルダー（Big/Smallと`textures`）なら隣、パッケージの入口なら隣に新しいパッケージフォルダー`<名前>_Anim/`、単体`.usdz`なら隣に新しいパッケージフォルダー（ばらの`.usd`を置くと、そのフォルダーが共有レイヤー扱いになり中のフォルダーが一覧から消えるため）。パッケージ・usdzからの出力は`../`で元を参照するので、元だけを移動すると切れる（制限）。
- 全ジョブが終わると再スキャンし、新しい資産に元の資産のタグ（自動タグ以外）を写す（`adopt_wind_outputs`）。元のサムネイルPNGは`<名前>_Anim_thumbnail.png`として複製する。`anim`などの自動タグはスキャン後の`variant_scan`が付ける。
- 出力（`wind_gen.is_output`: 名前が`_Anim`で終わり`anim/<名前>_clip.usd`がある）には、proxy・LOD・Element Switchの生成を行わない（入口を編集するとスキニングと合わなくなる）。元の資産で生成し直す。すでにスキニング・アニメーションのあるメッシュは`wind_gen`自体がスキップする。
- 所要時間: HolcusLanatus Big（98MB、7株×3LOD＋proxy）で約20秒、入口15MB＋クリップ4.5MB（10秒ループ）。

確認済み（22.0.447）: Karma CPU/XPUとScene Viewでスキニングされる。Point Instancerのプロトタイプでも動く（Karma）。1001フレームやループ先でも同じ値。別の形の植物（シダ、キキョウ類、ゴボウの小株）でも生成・レンダーできた。未対応: 木（幹と枝の階層・葉の揺れを分けたモデルは持たない）、モーションブラー用の速度は書かない。

## 自動タグ（lod / variant / proxy / anim）

- `variant_scan.py`（Houdini同梱のPython、`VariantTagJob`）が、索引の`vstamp`（`"mtime:size"`、索引v6で追加）が現在のファイルと違うUSD資産だけを開き、一番上のプリム（default prim、無ければ最初のルートプリム）のvariant set名を読む。`Usd.Stage.OpenMasked`（そのプリムだけ、payloadなし）なので、実ライブラリー914件で約1.2秒、変更なしなら約0.1秒。
- `core.sync_auto_tags`: 名前が`lod`で始まる（大文字小文字無視、`core.is_lod_set`）セットがあれば`lod`、それ以外のセットがあれば`variant`を付け、無ければ外す。他の語と順序は保つ。`Library.save_variant_tags`は1トランザクション内で現在のtagsを読み直してから書く（並行してユーザーが編集したタグを失わない）。読めなかったファイルはタグに触れずstampだけ記録する。
- `proxy`: 一番上のプリムの下（今の選択で合成されるもの）に`purpose=proxy`のGprimがあれば付け、無ければ外す。ProxyJobの成功時（"Proxy added"または"already has a proxy"）にも`set_tag`で付ける。stampは`p2:mtime:size`（`Library.STAMP_VERSION`。読む内容を増やしたら上げ、全USDを1回確認し直させる。p2で`anim`を追加）。バッジはVARIANT（青）・LOD（橙）・PROXY（緑）・ANIM（紫）の順に左上へ並べ、アイコン幅（256）に入らない分は2段目へ折り返す（4つ並ぶと約330で、ANIMがはみ出していた）。
- `anim`: 一番上のプリムの下（今の選択で合成されるもの）の属性のどれかが`ValueMightBeTimeVarying()`（時間サンプルかvalue clipがある）なら付け、無ければ外す。サンプルの有無だけを見るので大きなメッシュも読まない。p2への更新で実ライブラリー1148件を確認し直して約7.5秒、変化は風のアニメーション1件だけだった（他の資産に誤検出なし）。生成ジョブではタグを付けず、出力のスキャン後にこの確認が付ける。
- 起動時（1.5秒後、`AUTO_TAGS`）とスキャン完了時に実行し、変化があれば一覧を再読み込みする（バッジはタグから描く）。
- Houdiniのセッションで最初に開いたパネルは、0.8秒後に自動でRescanする（`AUTO_SCAN`、モジュール変数`_session_scanned`で1回だけ。`settings.json`の`rescan_on_first_open`がfalseなら行わない）。その場合の起動時タグ確認は、スキャン完了時のものに任せる。生成ジョブのタグ操作（`set_tag`）とは独立で、ファイルが変わるため次回の確認で同じ結果になる。
- D&Dの自動ノード（`houdini_ops._find_variant_set`）は、Referenceで配置したプリム自身→その子孫→（Sublayerでは）ステージ全体の順に探す。Set Variantは`element`を優先し、無ければLOD以外の最初のセット。Auto Select LODは`LOD`、無ければ`is_lod_set`の最初のセット。

## USD / Asset Catalog

左下の「Libraries...」ボタンの下にある「Catalog」選択欄でUSDの登録先を選びます。素材ルートの`Catalog`内のDBを自動検出します。
選択欄を右クリックすると「Open Catalog」「Select Catalog...」「New Catalog...」を使用できます。
「Select Catalog...」では別の場所の既存DBも指定でき、「New Catalog...」は空のDBを作成します。既存ファイルは上書きしません。
選択はライブラリールートごとに保存されます。ルート内のDBは相対パスで記録します。
「Add Catalog」は選択中のDBへの登録です。「Open Catalog」はスクリプトからは開かず、代わりに手動での開き方（パネルの「+」→Python Panel→Asset Catalog→そのパネル自身のメニュー→Open Asset Database File...→対象のDBパス）を`hou.ui.displayMessage`で案内するだけです。
理由: HoudiniのAsset Gallery/Asset Catalogネイティブメニュー（`$HFS/houdini/AssetGallerySourceMenu.xml`）は`hou.ui.setSharedLayoutDataSource(hou.AssetGalleryDataSource(...))`を使っており、`asset_gallery`というPython Panelインターフェース（ラベル「Asset Catalog」、`layout/assetgallery.py`）も`hou.qt._createAssetGallery()`を引数無しで呼ぶことから、このAPIが正しい呼び先だと確認済み。似た名前の`setSharedAssetGalleryDataSource(source, gallery_name)`は別物で、組み込みの`'layout'`（Paint Instances LOP）/`'material'`（Material LOP）専用のギャラリーにしか使えず、`gallery_name`必須のため以前の実装ではTypeErrorになっていた。
**しかし実機検証の結果、`hou.ui.setSharedLayoutDataSource`自体を呼ぶだけで（ペインを一切作らなくても）、以後セッション内のHoudiniネイティブなメニュー・ポップアップ（自作のNanakusaAssetLibraryパネル自身のメニューは除く）が一切反応しなくなる（要Houdini再起動）ことを、使い捨てのhoudinifx.exeインスタンスで何度もクリーンな前後比較を行い確認した。** `createFloatingPaneTab`（フローティング）・`Pane.createTab`（ドッキング）のどちらでペインを作っても症状は同じ。Houdini 22.0.447自体のエンジンバグと判断し、この呼び出し自体を行わないことにした（2026-09-23調査）。
未作成の標準DBには「(new)」を表示し、最初のAdd Catalogで作成します。
旧`_catalog`内のDBは`Catalog`へ移してRescanするか、Select Catalogで指定してください。自動移動・削除は行いません。

USDを選択して右クリックの「Add Catalog」でHoudini Asset Catalogのデータベースへ登録できます。
複数のUSDをまとめて登録できます。同じパスの重複を防ぎ、ファイル所有権は移しません。Catalogは素材ルートの `Catalog` に保存します（素材分類としては表示しません）。
DB更新前のバックアップは`data/backups/index`へ、インストール時の設定の控えは`data/backups/install/<日時>/`へ保存します。

`data/backups`の構成（2026-09-24に整理。実データ側の`backups/README.md`にも記載）: 生成ジョブは`proxy/`・`element/`・`lod/`、索引は`index/`、Asset Catalog DBは`catalog/`、インストールは`install/`、一括修正の前の状態は`repairs/`、開発作業前の控えは`dev-snapshots/`。`proxy/`は整理時に資産ごとの一番古い世代（proxyを付ける前の元ファイル）だけを残し、それ以降の世代・置き換え済みの修正前状態は`_delete_candidates/`へ移した（削除はユーザーが判断する）。移動記録は`backups/reorg_log_20260924.tsv`。
右クリックの「Publish Static USD...」は素材単体の現在フレームを書き出します。画像を同梱する機能ではないため、元画像の参照先も共有する必要があります。
外部依存を持つUSDを他PCへ渡すときは、その依存ファイルと相対パスも保ってください。

## 検証

標準Python:

```text
python -m unittest discover -s tests -p test_core.py
python -m unittest discover -s tests -p test_storage.py
python -m unittest discover -s tests -p test_organize.py
python -m unittest discover -s tests -p test_pbr.py
python -m unittest discover -s tests -p test_embedded.py
```

Houdini 22のhython（作業シーンとは別プロセス）:

```text
hython -m unittest discover -s tests
```

0.10.0ではHoudini 22.0.447 / Windowsで96件、0.24.0では176件のテストが通過しました。`tests/test_wind.py`は、根元の根・茎の途中の葉・ばらの小花・LOD・proxyを持つ合成の株で、ループ・スキニング・根元の固定・小花の剛体追従・LODの切り替え・phaseのずれ・`anim`タグの検出を確認します。
約11万ファイルの合成ライブラリーでの測定値: パネルを開く0.14秒、フォルダー切替・全体表示0.03秒、スクロールで200件追加が約3ミリ秒（DB）、スキャン約7秒（別プロセス。その間、UIのイベントループは最大でも0.1秒未満）。
移動（`organize.py`）は標準Pythonでも検証できます。
Scene Viewへのドロップ抑止は、実機のマウス操作では未検証です（イベントフィルターの判定のみテスト済み）。
保存先・サムネイルの回帰に加え、複数MIME、Merge表示、失敗時の復旧、PBR共有UVと既存出力・配置の保持を検証しています。
画像・形状の情報取得、右クリック項目、タグ・お気に入り保存、バックアップ先も検証しています。
検証結果はこのバージョン時点の記録です。変更後は影響するテストと実際の利用経路を確認してください。

## 実装の構成

| ファイル | 担当 |
|---|---|
| `install.py` | package JSONと保存先の設定 |
| `core.py` | 固定分類の走査、SQLite、メタデータ |
| `organize.py` | 素材・フォルダーの移動（サムネイル同梱、失敗時の巻き戻し） |
| `storage.py` | dataの決定、サムネイルの保存先・検出（USDZ内プレビューのキャッシュ先を含む） |
| `scan_worker.py` | 別プロセス（Houdini同梱のPython）で1つのライブラリーをスキャン |
| `embedded.py` | `.usdz`内のプレビュー画像の検出と取り出し（標準はUsdMediaAssetPreviewsAPI、慣用名は`thumbnail`/`preview`/`Thumbnails`/`.thumbs`） |
| `ui.py` | パネル、正方形プレビュー、生成キュー |
| `dragdrop.py` | 複数D&D、Pパラメーター領域とグラフの判定、フォルダーツリー |
| `pbr.py` | ファイル名によるPBR用途・セットの判定、スタック表示用のグループ化 |
| `houdini_ops.py` | ノード生成、USD書き出し、Catalog |
| `reveal.py` | Show in Explorerで素材ファイルを選択状態にする（Windowsシェル、Houdini非依存） |
| `asset_info.py` | 別プロセスの画像・形状情報取得（USDはProxy有無・上方向軸・マテリアル数・サイズも） |
| `thumbnail_scene.py` | 別プロセスのサムネイル用シーン作成（`resources/`のHDRIを使用） |
| `proxy_gen.py` | 別プロセスでのproxy（`purpose=proxy`）生成。デシメート、色の焼き込み、USDZの展開・再パッケージ |
| `element_gen.py` | 別プロセスでの複数オブジェクトパックの切り替え（`element` variant set）。proxy_gen.pyのファイル入出力を再利用。`anchor`・`remove_variant_set`はlod_gen.pyと共用 |
| `variant_scan.py` | 別プロセスでUSDのvariant set・proxy・アニメーションを読み、`lod` / `variant` / `proxy` / `anim`タグを同期（新規・変更分のみ） |
| `lod_gen.py` | 別プロセスでのLOD生成・削除（`LOD` variant set、Auto Select LOD / Stage Manager対応） |
| `wind_gen.py` | 別プロセスでの風のアニメーション生成（UsdSkelのリグ・ループするvalue clip、`<名前>_Anim.usd`） |

モジュールは`python3.13libs/nanakusa_asset_library/`内にあります。
