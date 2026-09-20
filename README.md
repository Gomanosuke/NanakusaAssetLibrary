# Solaris Asset Library

Houdini 22 / Karma XPU向けの、プロジェクト共通アセットブラウザーです。
Python Panelとしてドッキングでき、複数ライブラリーのフォルダー階層、検索、種類、タグ、お気に入りから素材を探せます。
OD Toolsの公開機能説明を参考にした独立実装です。OD Toolsのコード・素材は含みません。

## コードとアセットを分離

このリポジトリーに保存するのはコード・テスト・ドキュメントだけです。

| 保存場所 | 内容 | Git管理 |
|---|---|---|
| このリポジトリー | Python、Python Panel、Shelf、説明書 | 対象 |
| ユーザーが指定するライブラリー | モデル、画像、USD、PBRセット定義など | 対象外 |
| `$HOUDINI_USER_PREF_DIR/solaris_asset_library/` | 検索インデックス、タグ、お気に入り、設定、サムネイルキャッシュ | 対象外 |
| `$HOUDINI_USER_PREF_DIR/packages/solaris_asset_library.json` | コードの場所を指すローカル設定 | 対象外 |

ライブラリーの実パスはユーザー設定に保存します。コードに個人の保存先は埋め込みません。
リポジトリーを任意の場所へcloneし、インストーラーにコードと独立した素材フォルダーを渡してください。
Gitに含めない拡張子を`.gitignore`でも定義していますが、push前に`git diff --cached`を確認してください。

## インストール

Houdiniの作業を保存してから、Houdini 22のhython、またはPython 3.10以上で実行します。

```text
python install.py --prefs "<Houdini 22のユーザー設定フォルダー>" --library "<共通アセットフォルダー>"
```

`--library`は省略可能です。その場合は画面の「ライブラリー追加」から指定します。
既存のパッケージ設定・ユーザー設定がある場合、インストーラーは日時付きでバックアップします。
アセットをコードフォルダーにコピーする処理はありません。

Houdiniを再起動し、Python Panelのメニューから **Solaris Asset Library** を開きます。
またはAsset Libraryシェルフを表示し、ボタンからフローティングウィンドウを開けます。

```python
import solaris_asset_library
solaris_asset_library.show()
```

アップデートはコードを更新してパネルを再起動（モジュール変更時はHoudiniを再起動）してください。
コードフォルダーを移動した場合はインストーラーを再実行します。
アンインストールは上記のpackage JSONを削除してHoudiniを再起動します。素材や検索データは保持されます。

## フォルダー整理

分類は固定ではありません。使い慣れた階層をそのまま登録できます。
新規ライブラリーの例:

```text
<library-root>/
  Models/
    Nature/Rocks/
    Architecture/
    Props/
  Materials/
    Wood/
    Metal/
  Textures/
  HDRI/
    Outdoor/
    Studio/
  Decals/
    Signs/
    Dirt/
  USD/
  _catalog/
```

最初にルートを登録して「再スキャン」を押します。スキャンはバックグラウンドで実行し、元ファイルを変更しません。
左側でフォルダーを選択すると、そのフォルダー以下だけを表示します。「下位フォルダーを含む」をOFFにすると直下だけを表示します。
検索は選択フォルダー・種類・お気に入りの条件と組み合わせられます。
画像1枚ずつとセット定義の両方を表示するので、PBRセットだけを探す場合は種類を絞ってください。
表示は200件ずつのページ方式です。シーケンス・UDIMタイルの自動グループ化は行いません。

「新規フォルダー」で下位階層を作れます。既存素材の自動移動・自動改名・削除は行いません。
ライブラリー登録解除はインデックスの登録だけを削除します。元ファイルは残ります。
移動したライブラリーは「設定 → ルートの場所を変更」で再リンクすると、タグやお気に入りを維持できます。
再リンクはブラウザーの参照先を変更します。既存HIPや公開済みUSD内のパスを書き換えるものではありません。

## 読み込み

読み込み先は`/stage`などのLOPネットワークを指定します。
「選択LOPの後に接続」がONなら、同じネットワーク内で選択した1つのLOPに接続します。
既存の下流接続は差し替えません。新しいノードを選択・表示し、Undoグループにまとめます。

| 素材 | 動作 |
|---|---|
| USD / USDA / USDC / USDZ | Reference。シーン全体はSublayerモードも選択可 |
| OBJ / BGEO / GEO / STL / PLY | SOP Create内にFile SOPを作成 |
| ABC | SOP Create内にAlembic SOPを作成 |
| FBX | FBX Skin Importで静的な形状を読み込み |
| glTF / GLB | glTF SOPから形状を読み込み |
| HDRI | Dome Lightを作成し画像を設定 |
| 単体テクスチャ | Base Colorに画像を接続したMaterialXを作成 |
| `.pbr.json` | 指定した画像群からMaterialXを作成 |
| `.mtlx` | HoudiniのUSD/MaterialXプラグイン経由でReference / Sublayer |
| デカール画像 / `.decal.json` | UV付きカードとMaterialXを作成。表面への自動投影ではない |

FBX/glTFの元シェーダー、スケルトンやアニメーションを完全に再現するインポーターではありません。
必要に応じてPBRセットを作成し、材質を明示的に割り当ててください。
`.mtlx`はファイル構造・Houdiniの対応範囲に依存します。複数ルートでdefaultPrimがない場合はSublayerを使用します。
単体のRoughnessなどを「テクスチャ」として読み込むとBase Colorになります。用途別マップはPBRセットで指定してください。
マテリアルの「割当先Prim」には対象Primパターンを指定できます。空欄なら材質作成のみです。

EXRはデータマップにも使用するため、拡張子だけでHDRIとは判定しません。
HDRI/HDRIsフォルダーに置くか、詳細の「種類」をHDRIに変更してください。
同様にデカール画像はDecal/Decalsフォルダー、または種類の手動指定を使用できます。

## PBR / デカールセット

対象フォルダーまたは素材を選択し「PBR / Decalセット作成」を押します。
ファイル名からマップ候補を提示しますが、**保存前に各マップを確認してください**。
同じフォルダーに複数素材が混在する場合、候補が意図したセットとは限りません。
セット定義は画像と同じフォルダーへ新規JSONとして保存し、画像は複製・変更しません。

対応マップ: Base Color、Roughness、Metalness、Normal、Opacity、Displacement、Emission。
カラー画像の初期値は`sRGB texture`、データマップはRawです。カラーEXRなどは色空間を明示してください。
NormalはOpenGL形式を想定。DirectXのY反転、ORMのチャンネル分離、Glossiness反転は自動処理しません。
UDIMを使用する場合は画像パスのタイル番号を`<UDIM>`へ置き換えて指定できます。
デカールにOpacity画像がない場合、Base Colorのアルファを使用します。

マニフェスト例（実ファイルはライブラリー側に保存）:

```json
{
  "schema": 1,
  "maps": {
    "base_color": "wood_basecolor.png",
    "roughness": "wood_roughness.png",
    "normal": "wood_normal.png"
  },
  "color_space": "srgb_texture",
  "displacement_scale": 0.01
}
```

## サムネイル

画像素材は直接表示します。素材の隣に`thumbnail.jpg`、`thumbnail.png`、`preview.jpg`、または`<stem>.preview.jpg`があれば優先します。
モデルなどは既存のプレビューを「サムネイル指定」で割り当てられます。
HDR/EXR/RATなどは「画像からサムネイル生成」を使います。Houdiniのiconvertを別プロセスで実行します。
生成画像はユーザー設定側のキャッシュに保存します。表示色は近似で、Karmaの厳密なルック検証画像ではありません。
モデルを自動レンダリングしてサムネイルにする機能は、この版にはありません。

## USD出力と標準Asset Catalog

「素材を静的USD化」は、その素材だけを一時的なLOPネットワークで構築し、合成済みUSDを書き出します。
現在のショット全体を出力する処理ではありません。複数ルートは参照可能な一つのルートにまとめます。
既存ファイルは上書きしません。変更時は新しいバージョン名で保存してください。

**このUSD化は現在フレームの静的スナップショットです。** アニメーションのフレーム範囲書き出しは行いません。
USDをFlattenするため、レイヤー分割・Payload構造・未選択Variantを保持する出版処理ではありません。
既に適切に構成されたUSDは、USD化せずそのまま登録してください。
外部画像は共有ライブラリー内の元パスを参照し、コピーしません。別PCでは同じ参照先が必要です。

「USDをCatalogへ登録」はHoudiniの公開APIを使い、同じUSDの重複登録を防ぎます。
登録先は選択した公開用ライブラリーの`_catalog/solaris_assets.db`です。既存DBは変更前にバックアップします。
「Catalogを開く」で標準Asset Catalogを表示できます。Referenceで読み込めるdefaultPrimまたは単一ルートが必要です。
非USD素材は、先にUSD化してから登録します。
公開先ライブラリーは「設定 → このライブラリーをUSD / Catalogの保存先にする」で変更できます。

## 設定と共有

ライブラリーは複数登録可能です。重複したルート・親子関係のルートは登録しません。
設定の`publish_root`がUSD/Catalogの既定保存先です。
`SAL_ASSET_ROOT`環境変数で既定ライブラリー、`SAL_DATA_DIR`でユーザー設定/インデックスの場所を上書きできます。
インデックスのSQLiteはローカルディスクに配置してください。素材は共有ストレージにも置けます。
複数人が同一Catalogへ同時書き込みする運用や、ストレージ切断中の書き込みは未検証です。

## 開発・テスト

標準Pythonだけで実行できるインデックスのテスト:

```text
python -m unittest discover -s tests -p test_core.py -v
```

Houdini 22の新規hythonプロセスで実行する統合テスト:

```text
hython -m unittest discover -s tests -v
```

テスト用モデルや画像は実行時に一時ディレクトリーへ生成します。実ユーザーの素材・HIPは使いません。
検証対象はHoudini 22.0.447 / Python 3.13 / PySide6です。
MaterialX Builderの生成には同梱`voptoolutils`のヘルパーを使用しているため、将来のHoudini更新時は統合テストを実行してください。

## 参考資料

- [OD Tools Asset Library](https://odtools.notion.site/Asset-Library-11df5330bf9345ee9de0da6a24a5f428)
- [Houdini Asset Catalog](https://www.sidefx.com/docs/houdini/ref/panes/assetgallery.html)
- [AssetGalleryDataSource API](https://www.sidefx.com/docs/houdini/hom/hou/AssetGalleryDataSource.html)
- [Python Panel](https://www.sidefx.com/docs/houdini/ref/panes/pythonpanel)
