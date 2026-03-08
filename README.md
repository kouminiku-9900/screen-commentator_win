# Screen Commentator Windows

Windows 向けに作り直した `screen-commentator` の Python 実装です。  
ローカル LLM 実行基盤は `llmster` のみを使い、モデルは `HauhauCS/Qwen3.5-4B-Uncensored-HauhauCS-Aggressive` に固定しています。

## Features

- Windows 専用の `PySide6` ランチャー
- `Install` / `Start` / `Stop` の最小 GUI
- `mss` による画面キャプチャ
- ローカル `llmster` + OpenAI 互換 API によるマルチモーダル推論
- 透過・最前面・クリック透過オーバーレイ
- app-local な `llmster` 導入

## Runtime Layout

アプリの実行時データは `%LOCALAPPDATA%\ScreenCommentatorWin\` に集約されます。

- `config.toml`
- `logs\`
- `state\`
- `llmster-home\`

`llmster` のインストール時は `HOME=%LOCALAPPDATA%\ScreenCommentatorWin\llmster-home` を強制し、通常の PATH は変更しません。

## Requirements

- Windows
- Python 3.12+
- `llmster` をダウンロードできるネットワーク
- モデルを動かせる RAM / VRAM

## Development Setup

```powershell
pwsh -File .\scripts\dev-setup.ps1
.\.venv\Scripts\Activate.ps1
python -m screen_commentator_win
```

## Build EXE

```powershell
pwsh -File .\scripts\build.ps1
```

正規の配布物は `release\ScreenCommentatorLauncher\` と `release\ScreenCommentatorLauncher-win64.zip` に生成されます。  
ビルド時には packaged exe に対して `--self-test smoke` と `--self-test demo-overlay` を実行し、`release\demo-overlay.png` まで検証します。

## Notes

- GUI から設定変更はしません。必要なら `%LOCALAPPDATA%\ScreenCommentatorWin\config.toml` を編集してください。
- `Install` は runtime とモデルの取得のみを行い、完了後は server / daemon を停止します。
- `Start` は server 起動、モデル load、コメント生成、オーバーレイ表示までを行います。
- `Stop` はコメント生成停止、overlay clear、model unload、server 停止、daemon 停止を行います。
- テスト時に `%LOCALAPPDATA%` を汚したくない場合は `SCW_APP_ROOT` で app root を差し替えられます。
