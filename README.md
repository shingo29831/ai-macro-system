# AI Macro System

OSレベルのマウス・キーボード操作監視、ローカルAI推論による画面認識、および自律的ワークフロー実行・自己修復を提供するマクロシステムです。

---

## 前提環境

- **OS**: Windows 10 / 11 (64-bit)
- **Python**: 3.10 以上推奨

---

## セットアップ手順 (venv)

プロジェクトルートで以下のコマンドを実行し、Python仮想環境の作成と依存ライブラリのインストールを行います。

### 1. 仮想環境の作成
```powershell
python -m venv venv
```

### 2. 仮想環境の有効化
- **PowerShell の場合**:
  ```powershell
  .\venv\Scripts\Activate.ps1
  ```
  ※ スクリプト実行権限エラーが出る場合は、PowerShellを管理者として開き `Set-ExecutionPolicy RemoteSigned -Scope CurrentUser` を実行してください。

- **コマンドプロンプト (cmd.exe) の場合**:
  ```cmd
  .\venv\Scripts\activate.bat
  ```

### 3. 依存パッケージのインストール
```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

---

## アプリケーション起動

仮想環境が有効化された状態で、以下のコマンドを実行してメイン画面を起動します。

```powershell
python src/main.py
```

---

## 主な機能と構成

- **記録モード**: 画面上部の最小UIからユーザー操作（クリック、タイピング、スクロール、ウィンドウ切り替え等）を常時キャプチャ。
- **ワークフロー生成**: 記録データから不要な動作を最適化し、実行可能なマクロシーケンスを生成。
- **エディタ**: アクションブロックの視覚的確認、ループ・Excel連携、ドラッグ＆ドロップ並び替え。
- **実行・自己修復**: 画面マッチング（SSIM/ORB）およびUI要素の動的再探索による自動修復実行。
- **設定**: `config.json` または設定画面よりローカルAIサーバーのホスト・ポート設定が可能。
