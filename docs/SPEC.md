# NanakusaAssetLibrary 仕様・実装メモ

開発者・エージェント向けの現行仕様です。利用者向けの説明は [README.md](../README.md)、作業ルールは [AGENTS.md](../AGENTS.md) を参照してください。
仕様を変更したら、このファイルと README.md（利用者向けの表現）の両方を実装と揃えます。

現在のバージョンは **0.10.0** です。

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
- 移動のたびにインデックスを`data/backups`へバックアップします。
- USDを移動すると、Asset Catalog内の同じUSDのパスも、Catalogフォルダー内の全DBについて更新します（更新前のDBを`data/backups`へ保存）。
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
素材情報（`asset_info.py`、選択が落ち着いてから別プロセスのhythonで取得しキャッシュする）は、種類ごとに項目が異なる。
テクスチャはResolution・Channels・Pixel type。3DModel・USDはPolygons・Points・Meshes（Volumeがあれば数も）。
USDはさらにUSD prims・**Proxy（Yes/No、`purpose=proxy`の有無）**・Up axis（Y/Z）・Materials（`UsdShade.Material`の数、あれば）・Size（バウンディングボックス、幅x奥行x高さ、空なら省略）を表示する。
素材の右クリックメニューに「Import Selected」「Copy Paths」「Show in Explorer」、USD選択時のみ「Add Catalog」を表示します。
ライブラリーの追加・再リンクは「Libraries...」、読み込み設定は「Options」から開きます。
Ctrlで追加選択、Shiftで範囲選択、Ctrl+Aで読み込み済みの素材を全選択できます（一覧はページ分けせず、スクロールで続きを読み込む）。
素材一覧でCtrl+マウス中ボタンをドラッグすると、アイコンサイズを変更できます（右・上へ動かすと大きく、左・下へ動かすと小さくなります。64〜512px）。
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
- 対象の入口ファイル自身（`Usd.Stage.Open`のルートレイヤー）だけを編集する。参照・ペイロード先の別ファイルは変更しない。
- デシメートはHoudiniの`polyreduce::2.0`（Output Polygon Count）を使う。事前に2つの前処理をしている：
  - `fuse`（Snap Distance、対角線の0.02%）でUV・材質境界の分離頂点を結合してから減らす。結合しないと、境界で分かれた各断片が個別に潰れて形が崩れる。
  - `divide`（Convex Polygons、最大3辺）で三角形化してから減らす。`polyreduce`の目標数はプリミティブ数であり、四角形・多角形主体のメッシュのまま渡すと、指定した三角形数のおよそ2倍が残ってしまう。
- 各`UsdGeom.Mesh`を、三角形換算で`target_triangles`（ダイアログで指定、既定`TARGET_TRIANGLES`=300）を超える場合のみデシメートする。小さいメッシュはそのまま複製する。結果は元プリムの兄弟として`<name>_proxy`に追加し、`purpose=proxy`を設定する。元プリムには`purpose=render`を明示する（Scene Viewでproxyが優先されるため）。名前衝突時は`_`を付けて回避する。
  - 元プリムの束縛材質からbase colorテクスチャを検出できた場合（`UsdPreviewSurface`の`diffuseColor`/`baseColor`が`UsdUVTexture`に接続され、`file`が解決できる形）、そのUV primvar（`UsdPrimvarReader`の`varname`、既定`st`。`faceVarying`/`vertex`/`uniform`/`constant`のいずれの補間にも対応）を使い、Houdiniの`attribfrommap`相当でテクスチャ色を頂点（点）ごとにサンプルし、`primvars:displayColor`（vertex補間）としてproxyへ焼き込む。デシメート後も色は点属性としてそのまま補間で引き継がれる（`polyreduce`が境界で多少オーバーシュートすることがあるため0〜1にクランプする）。材質・テクスチャが見つからない場合は無地のまま。
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
- 既に`element` variant setを持つ資産は`{'skipped': 'already has an element switch'}`。
- 分岐点プリムに`element` variant setを作り、検出した各子に`Element0`, `Element1`, ... という名前のバリアントを割り当てる。各バリアントの中で、選ばれた子には`visibility=inherited`、それ以外の子には`visibility=invisible`を明示的に設定する（ジオメトリ自体は変更しない。分岐点プリムに元々ローカルなvisibility値は無いため、LODのようなClear()は不要）。選択は`Element0`に設定して終える（何もしなければ全オブジェクトが重なって見える現状を、生成直後から解消するため）。
- proxy_gen.pyと同じファイル入出力（`generate_plain`/`generate_usdz`）を再利用する。`.usdz`は展開・編集・再パッケージ、プレーンUSDは`Export()`のみ。

`ui.py`の`ElementJob`（`ProxyJob`と`_MeshGenerateJob`を共通基底とする）が`hython element_gen.py <元ファイル> <一時出力> <結果JSON>`を実行し、成功時のみ`data/backups/element/`へバックアップしてから`os.replace`で置き換える。失敗・スキップ時は元ファイルを一切変更しない。
右クリックの「Generate Selected Element Switch...」は選択したUSD資産に生成する（ダイアログでの値入力は無い）。「Libraries... → Cancel Element Switches」で待機分を解除できる。フォルダー移動・素材移動は、待機中のelement switch生成がある間はできない。

配置後にどのオブジェクトを表示するか切り替えるには、LOPネットワークの**Set Variant**ノード（`setvariant`）でPrimitivesに分岐点プリム、Variant Setに`element`、Variant Nameに`Element0`〜`ElementN-1`のいずれかを指定する。Scene Graph TreeのVariantsタブから直接切り替えることもできる。`variant`タグの付いた資産をD&D/Import Selectedで配置した場合、このノードは`houdini_ops._add_variant_switch`が既に自動で挿入している（[D&D](#dd)を参照）。

### 削除・自動タグ・サムネイルバッジ

- `element_gen.py`の`_remove_element_switch`（`remove()`関数、`element_gen.py <元> <一時出力> <結果JSON> remove`で呼ばれる）は`_add_element_switch`を完全に取り消す。`UsdVariantSets`にはこのUSDバージョンで`RemoveVariantSet`が無いため、`Sdf.PrimSpec`を直接編集する: `spec.variantSets`から該当キーを`del`、`spec.variantSelections`から`del`、`spec.variantSetNameList.prependedItems`/`explicitItems`から該当名を`remove`。バリアント内に書いた可視性の上書きも、variant自体のspecごと消えるため個別に消す必要はない。見つからなければ`{'skipped': 'no element switch found in this file'}`。
- `ui.py`の`ElementDeleteJob`（`_MeshGenerateJob`を継承、`extra_args=('remove',)`でelement_gen.pyへ渡す）が右クリックの「Delete Element Switch」から呼ばれる。`data/backups/element/`へバックアップしてから置き換える点はElementJobと同じ。「Libraries... → Cancel Element Switch Removals」で待機分を解除できる。
- **`variant`タグの自動付与・削除**: `ElementJob`が成功しresultメッセージが`'Element switch added'`で始まる時、`LibraryWidget.set_variant_tag(aid, True)`が対象資産の`tags`（スペース区切り文字列）へ`variant`を追加し、`library.update`でDBへ保存する。`ElementDeleteJob`が成功し`'Element switch removed'`で始まる時は同じ仕組みで`variant`を取り除く（`set_variant_tag(aid, False)`）。スキップ（既にある/対象なし）の時は触らない。ジェネレーター側は一切タグを知らず、ui.py側の完了ハンドラーだけがタグを管理する。
- **サムネイルバッジ**: `load_icons()`が、PBRスタックでない各アイテムについて`entry['rep']['tags']`に`variant`が含まれるかを見て、含まれていれば`variant_badge()`（`stacked_icon`と同じ256論理座標のcompositing手法で、左上に「VARIANT」の角丸ラベルを重ねる）でアイコンを差し替える。ファイルを開いて`HasVariantSet`を確認する必要はなく、DBの`tags`列を見るだけなので、一覧に並ぶ全アイテムに対して安価に行える。
- `set_variant_tag`はタグ変更後、`item_index`から対象アイテムのインデックスを引き、`icons_loaded`から外して`icon_todo`の先頭へ積み直す（`thumbnail_done`と同じ手法）ことで、次の`load_icons`スライスで確実にバッジ付きへ再描画させる。選択中の資産であれば`selection_changed()`も呼び、右側のTagsフィールドにも反映する。

## USD / Asset Catalog

左下の「Libraries...」ボタンの下にある「Catalog」選択欄でUSDの登録先を選びます。素材ルートの`Catalog`内のDBを自動検出します。
選択欄を右クリックすると「Open Catalog」「Select Catalog...」「New Catalog...」を使用できます。
「Select Catalog...」では別の場所の既存DBも指定でき、「New Catalog...」は空のDBを作成します。既存ファイルは上書きしません。
選択はライブラリールートごとに保存されます。ルート内のDBは相対パスで記録します。
「Add Catalog」は選択中のDBへの登録で、「Open Catalog」でそのDBをHoudiniのAsset Catalogとして開きます。
`open_catalog`は`hou.AssetGalleryDataSource`を`hou.ui.setSharedAssetGalleryDataSource`で渡してから、Python Panelインターフェース`asset_gallery`をフローティングペインで開く。**`setSharedLayoutDataSource`（似た名前の別関数、Labs Layoutツール用）を誤って呼ぶと**、`asset_gallery`インターフェースがデータソースを持てず空の「Quick Start」プレースホルダーにフォールバックし、以後セッション内のHoudiniネイティブなメニュー・ポップアップ（自作のNanakusaAssetLibraryパネル自身のメニューは除く）が一切反応しなくなる（要Houdini再起動）。使い捨てのhoudinifx.exeインスタンスで実際に再現・修正確認済み（2026-09-23）。
未作成の標準DBには「(new)」を表示し、最初のAdd Catalogで作成します。
旧`_catalog`内のDBは`Catalog`へ移してRescanするか、Select Catalogで指定してください。自動移動・削除は行いません。

USDを選択して右クリックの「Add Catalog」でHoudini Asset Catalogのデータベースへ登録できます。
複数のUSDをまとめて登録できます。同じパスの重複を防ぎ、ファイル所有権は移しません。Catalogは素材ルートの `Catalog` に保存します（素材分類としては表示しません）。
DB更新前のバックアップは`data/backups`へ保存します。
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

0.10.0ではHoudini 22.0.447 / Windowsで96件のテストが通過しました。
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
| `asset_info.py` | 別プロセスの画像・形状情報取得（USDはProxy有無・上方向軸・マテリアル数・サイズも） |
| `thumbnail_scene.py` | 別プロセスのサムネイル用シーン作成（`resources/`のHDRIを使用） |
| `proxy_gen.py` | 別プロセスでのproxy（`purpose=proxy`）生成。デシメート、色の焼き込み、USDZの展開・再パッケージ |
| `element_gen.py` | 別プロセスでの複数オブジェクトパックの切り替え（`element` variant set）。proxy_gen.pyのファイル入出力を再利用 |

モジュールは`python3.13libs/nanakusa_asset_library/`内にあります。
