# NanakusaAssetLibrary

Houdini 22のSolaris / Karma XPU向け、プロジェクト共通のアセットブラウザーです。
Python Panel、フォルダー表示、検索、タグ、お気に入り、D&D、Asset Catalog登録に対応します。

現在のバージョンは **0.6.3** です。開発・修正を行うエージェントは [AGENTS.md](AGENTS.md) を参照してください。

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

素材を追加したら「Rescan」を押してください。元ファイルの移動・改名・削除はしません。
USDのパッケージ内は「Libraries... → New Folder...」の対象外です。UDIM・シーケンスの自動集約はありません。
ルート移動後は「Libraries... → Relink Library...」で再リンクできます。HIP内の既存パスは書き換えません。

## GUIと複数選択

GUIは英語です。右側には大きな正方形プレビューと素材情報を常時表示します。
素材の右クリックメニューに「Import Selected」「Copy Paths」「Show in Explorer」、USD選択時のみ「Add Catalog」を表示します。
ライブラリーの追加・再リンクは「Libraries...」、読み込み設定は「Options」から開きます。
Ctrlで追加選択、Shiftで範囲選択、Ctrl+Aで表示ページ内を全選択できます。
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

Textureを通常のstageやobjへ落としてもノードは作りません。
既存Builder内では新しいSurfaceを作成し、既存の出力接続は保持します。必要に応じて新しいSurfaceを出力へ接続してください。
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
「Libraries... → Generate Missing Thumbnails」は、現在の検索・フォルダー・種類の条件に一致する全ページの不足分を順番に処理します。
既存サムネイルがある素材は一括生成でスキップします。「Libraries... → Cancel Thumbnails」で待機分を解除できます。

形状のサムネイルは別プロセスのhythonとKarma CPUで512×512にレンダリングします。
Houdini初期値のDome Light（Intensity 1 / Exposure 0 / 白色・画像なし）と補助のDistant Lightを配置します。
既存の暗いサムネイルには、右クリックのGenerate Selected Thumbnailsを実行してください。
形状の境界から斜め前方のカメラと照明を自動設定するため、作業中のHIPにはノードを追加しません。
GPUを占有せず4 CPUスレッドを使います。Houdini / Karmaの利用可能なライセンスが必要です。
USDの材質と依存ファイルを参照し、最初のフレームを描画します。欠落した依存ファイルや読み込み不能な形状はエラーとして表示します。
ボリュームの見た目は元データの密度・材質に依存します。任意の画像を「Choose Thumbnail...」で割り当てることもできます。
元ファイル更新後は再スキャンして生成してください。USD内の依存画像だけを更新した場合は選択素材を再生成してください。

## USD / Asset Catalog

左下の「Libraries...」ボタンの下にある「Catalog」選択欄でUSDの登録先を選びます。素材ルートの`Catalog`内のDBを自動検出します。
選択欄を右クリックすると「Open Catalog」「Select Catalog...」「New Catalog...」を使用できます。
「Select Catalog...」では別の場所の既存DBも指定でき、「New Catalog...」は空のDBを作成します。既存ファイルは上書きしません。
選択はライブラリールートごとに保存されます。ルート内のDBは相対パスで記録します。
「Add Catalog」は選択中のDBへの登録で、「Open Catalog」でそのDBをHoudiniのAsset Catalogとして開きます。
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
```

Houdini 22のhython（作業シーンとは別プロセス）:

```text
hython -m unittest discover -s tests
```

0.6.3ではHoudini 22.0.447 / Windowsで39件のテストが通過しました。
保存先・サムネイルの回帰に加え、複数MIME、Merge表示、失敗時の復旧、PBR共有UVと既存出力・配置の保持を検証しています。
画像・形状の情報取得、右クリック項目、タグ・お気に入り保存、バックアップ先も検証しています。
検証結果はこのバージョン時点の記録です。変更後は影響するテストと実際の利用経路を確認してください。

## 実装の構成

| ファイル | 担当 |
|---|---|
| `install.py` | package JSONと保存先の設定 |
| `core.py` | 固定分類の走査、SQLite、メタデータ |
| `storage.py` | dataの決定、サムネイルの保存先・検出 |
| `ui.py` | パネル、正方形プレビュー、生成キュー |
| `dragdrop.py` | 複数D&D、Pパラメーター領域とグラフの判定 |
| `pbr.py` | ファイル名によるPBR用途・セットの判定 |
| `houdini_ops.py` | ノード生成、USD書き出し、Catalog |
| `asset_info.py` | 別プロセスの画像・形状情報取得 |
| `thumbnail_scene.py` | 別プロセスのサムネイル用シーン作成 |

モジュールは`python3.13libs/nanakusa_asset_library/`内にあります。
