# NanakusaAssetLibrary

Houdini 22のSolaris / Karma XPU向け、プロジェクト共通のアセットブラウザーです。
Python Panel、フォルダー表示、検索、タグ、お気に入り、D&D、Asset Catalog登録に対応します。

現在のバージョンは **0.3.0** です。開発・修正を行うエージェントは [AGENTS.md](AGENTS.md) を参照してください。

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
  backups/                            # 移行・変更前の保全
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
5. Houdiniを起動し、素材ルートを移した場合は既存の登録を「設定 → ルートの場所を変更」で再リンクします。
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
- **3DModel**: `.obj`, `.fbx`, `.vdb`, `.bgeo`, `.bgeo.sc`, `.geo`, `.geo.sc`, `.abc`, `.glb`, `.stl`, `.ply`。
  形状を単一ファイルから読める形式が対象です。FBXの外部画像や元の材質は再構築しません。
- **Texture**: PNG、JPEG、TIFF、HDR、EXRなどの画像。HDRやデカールもこの分類に置きます。

素材を追加したら「再スキャン」を押してください。元ファイルの移動・改名・削除はしません。
USDのパッケージ内は「新規フォルダー」の対象外です。UDIM・シーケンスの自動集約はありません。
ルート移動後は「設定 → ルートの場所を変更」で再リンクできます。HIP内の既存パスは書き換えません。

## D&D

| 素材 | ドロップ先 | 動作 |
|---|---|---|
| 3DModel | stage / LOPネットワーク | SOP Createと形式別の読み込みSOP |
| 3DModel | obj / SOPネットワーク | Geometry内の読み込みSOP、または読み込みSOP |
| USD | stage / LOPネットワーク | Reference LOP |
| USD | obj / SOPネットワーク | USD Import SOP |
| Texture | テキスト入力欄 | ファイルパスを入力 |
| Texture | Material Library / MaterialX Builderなどの材質階層 | UVを明示接続したMaterialX ImageとStandard Surface |

Textureを通常のstageやobjへ落としてもノードは作りません。
既存Builder内では新しいSurfaceを作成し、既存の出力接続は保持します。必要に応じて新しいSurfaceを出力へ接続してください。
普通の画像はsRGB、HDR/EXRはlinear Rec.709として設定します。ノーマル等のデータ画像は用途に合わせてRawと接続先を調整してください。
FBXはFBX Skin Import、ABCはAlembic、GLBはglTF、VDB等はFile SOPで読み込みます。

## プレビューとサムネイル

TIFF・HDR・EXRはHoudini付属の画像変換ツールで縮小し、`data/thumbnails`へ保存します。
一覧と詳細プレビューは正方形です。画像の縦横比を保持し、非正方形の画像の余白は透明にします。
元画像に黒い帯を追加したり、引き延ばしたりしません。画像表示は近似色です。

| 種類 | 生成画像の保存先 |
|---|---|
| 3DModel | 元ファイルと同じフォルダーの`<モデル名>_thumbnail.png` |
| USD | 入口USDと同じフォルダーの`thumbnail.png` |
| Texture | `data/thumbnails` |

USDの配置は[Component Builder](https://www.sidefx.com/docs/houdini/solaris/component_builder.html)の出力と同じ規約です。
USD仕様全体で必須のファイル名という意味ではありません。既存の`thumbnail.jpg`も認識します。
素材の隣へ書き込める権限が必要です。生成失敗時は既存サムネイルを保持します。

USD・3DModelは「選択素材のサムネイルを生成」で作成できます。
「表示対象の不足サムネイルを生成」は、現在の検索・フォルダー・種類の条件に一致する全ページの不足分を順番に処理します。
既存サムネイルがある素材は一括生成でスキップします。「サムネイル生成を中止」で待機分を解除できます。

形状のサムネイルは別プロセスのhythonとKarma CPUで512×512にレンダリングします。
形状の境界から斜め前方のカメラと照明を自動設定するため、作業中のHIPにはノードを追加しません。
GPUを占有せず4 CPUスレッドを使います。Houdini / Karmaの利用可能なライセンスが必要です。
USDの材質と依存ファイルを参照し、最初のフレームを描画します。欠落した依存ファイルや読み込み不能な形状はエラーとして表示します。
ボリュームの見た目は元データの密度・材質に依存します。任意の画像を「サムネイル指定」で割り当てることもできます。
元ファイル更新後は再スキャンして生成してください。USD内の依存画像だけを更新した場合は選択素材を再生成してください。

## USD / Asset Catalog

USDを選択して「USDをCatalogへ登録」でHoudini Asset Catalogのデータベースへ登録できます。
同じパスの重複を防ぎ、ファイル所有権は移しません。Catalogは素材ルートの `_catalog` に保存します（素材分類としては表示しません）。
「素材を静的USD化」は素材単体の現在フレームを書き出します。画像を同梱する機能ではないため、元画像の参照先も共有する必要があります。
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

0.3.0ではHoudini 22.0.447 / Windowsで28件のテストが通過しました。
保存先の分離、モデル・USDの隣への出力、縦横比の維持、画像変換時の黒帯防止を検証しています。
検証結果はこのバージョン時点の記録です。変更後は影響するテストと実際の利用経路を確認してください。

## 実装の構成

| ファイル | 担当 |
|---|---|
| `install.py` | package JSONと保存先の設定 |
| `core.py` | 固定分類の走査、SQLite、メタデータ |
| `storage.py` | dataの決定、サムネイルの保存先・検出 |
| `ui.py` | パネル、正方形プレビュー、生成キュー |
| `dragdrop.py` | D&Dの形式と読み込み先判定 |
| `houdini_ops.py` | ノード生成、USD書き出し、Catalog |
| `thumbnail_scene.py` | 別プロセスのサムネイル用シーン作成 |

モジュールは`python3.13libs/nanakusa_asset_library/`内にあります。
