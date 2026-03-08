# screen-commentator_win

`screen-commentator` を Windows 向けに大きく作り直した fork です。  
大元の着想と macOS 実装は [r1cA18/screen-commentator](https://github.com/r1cA18/screen-commentator) によるものです。Windows 向けへ展開するうえで、その設計と公開に敬意を表します。

## Original Project

- Original repository: [r1cA18/screen-commentator](https://github.com/r1cA18/screen-commentator)
- This repository: Windows-focused Python rewrite for local multimodal commentary overlay

## What Changed For Windows

- Windows 専用実装に変更
- Swift / macOS 実装ではなく Python メインへ再構成
- GUI は `Install` / `Start` / `Stop` の最小ランチャーに整理
- LLM 実行環境は `llmster` ベースに一本化
- モデルは `HauhauCS/Qwen3.5-4B-Uncensored-HauhauCS-Aggressive` 固定
- パイプラインは multimodal `smart` 相当のみを維持
- 外部 API モードは廃止
- `%LOCALAPPDATA%\ScreenCommentatorWin\` 配下へ runtime を隔離

## Features

- Windows 向け `PySide6` ランチャー
- `mss` による画面キャプチャ
- ローカル `llmster` + OpenAI 互換 API によるマルチモーダル推論
- 透過・最前面・クリック透過オーバーレイ
- 画面変化量検出
- recent comments による重複抑制
- mood / excitement 反映
- スクロール / 固定コメント表示
- レーン分散

## Runtime Layout

実行時データは `%LOCALAPPDATA%\ScreenCommentatorWin\` に集約されます。

- `config.toml`
- `logs\`
- `state\`
- `llmster-home\`

`Install` では `llmster` と固定モデルを取得し、通常の PATH は変更しません。

## Requirements

- Windows 10/11 x64
- Python 3.12+
- `llmster` とモデルをダウンロードできるネットワーク
- ローカル VLM を動かせる RAM / VRAM

## Development Setup

```powershell
pwsh -File .\scripts\dev-setup.ps1
.\.venv\Scripts\Activate.ps1
python -m screen_commentator_win
```

## Build

```powershell
pwsh -File .\scripts\build.ps1
```

生成物:

- `release\ScreenCommentatorLauncher\`
- `release\ScreenCommentatorLauncher-win64.zip`

ビルド時には packaged exe に対して self-test を実行し、overlay PNG まで確認します。

## Notes

- GUI から設定変更はしません。必要なら `%LOCALAPPDATA%\ScreenCommentatorWin\config.toml` を編集してください。
- `Install` は runtime とモデル取得を行い、完了後は server / daemon を停止します。
- `Start` は server 起動、model load、コメント生成、overlay 表示まで行います。
- `Stop` は生成停止、overlay clear、model unload、server 停止、daemon 停止を行います。
- `%LOCALAPPDATA%` を汚したくないテストでは `SCW_APP_ROOT` を使えます。
- この fork は毎回 `%LOCALAPPDATA%\ScreenCommentatorWin\llmster-home\` の app-local runtime だけを使います。
- LM Studio デスクトップアプリが起動中だと isolated `llmster` を起動できないので、このランチャーを使う前に LM Studio は閉じてください。

## Uninstall

### App-local files only

この fork を消すだけなら、まずランチャーを閉じてから以下を削除してください。

- `%LOCALAPPDATA%\ScreenCommentatorWin\`
- 配布版を展開したフォルダ
- 必要なら `release\ScreenCommentatorLauncher\` と `release\ScreenCommentatorLauncher-win64.zip`

この fork は user-profile 側の `%USERPROFILE%\.lmstudio\` を使いません。  
アンインストール対象は app-local の `%LOCALAPPDATA%\ScreenCommentatorWin\` だけです。

## License

MIT.  
See [LICENSE](LICENSE).
