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
- 既定モデルは `unsloth/Qwen3.5-4B-GGUF`
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

`Install` では `llmster` と設定中の Hugging Face モデルを取得し、通常の PATH は変更しません。

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

## Publication Policy

この repository で公開対象として想定しているのは source code です。  
`release/` の exe や zip はローカル build 成果物であり、この repository の公式バイナリ配布物という位置づけにはしません。

- git には `llmster` 本体を含めません
- git にはモデル weights / GGUF / `mmproj` を含めません
- 公開時は source repo のみを基本とし、ローカルで作ったバイナリを第三者へ再配布する場合は利用者自身が第三者ライセンス条件を確認してください

## Install And First Run

GitHub から取得して使う前提なら、最短手順はこれです。

```powershell
pwsh -File .\scripts\build.ps1
.\release\ScreenCommentatorLauncher\ScreenCommentatorLauncher.exe
```

初回はランチャーで `Install` を押してください。  
これで app-local の `llmster` と、`config.toml` で指定されたモデルを `%LOCALAPPDATA%\ScreenCommentatorWin\` 配下へ取得します。

## Changing The Model

モデル repo は `%LOCALAPPDATA%\ScreenCommentatorWin\config.toml` の `runtime.model_repo_url` で切り替えられます。  
既定値は `unsloth/Qwen3.5-4B-GGUF` です。

```toml
[runtime]
model_repo_url = "https://huggingface.co/unsloth/Qwen3.5-4B-GGUF"
quantization = "Q4_K_M"
```

別モデルへ変える場合は、この URL を Hugging Face の repo URL に差し替えてから再度 `Install` を押してください。  
この fork は multimodal 用なので、GGUF 本体だけでなく `mmproj` も含まれる repo を使ってください。

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

## Third-Party Software, Model Licenses, And Disclaimer

- この repository のコード自体は [MIT](./LICENSE) です。
- この repository は、第三者ソフトウェアや第三者配布モデルのライセンスまで再許諾するものではありません。
- `llmster` / LM Studio は別ソフトウェアです。利用時はそれぞれの利用条件に従ってください。
  - LM Studio Terms: [https://lmstudio.ai/terms](https://lmstudio.ai/terms)
- 既定モデルは Hugging Face の [unsloth/Qwen3.5-4B-GGUF](https://huggingface.co/unsloth/Qwen3.5-4B-GGUF) を参照します。
  - 2026-03-08 時点の Hugging Face 表記では `apache-2.0` です。
- モデルを差し替える場合、その weights / GGUF / mmproj のライセンス確認は利用者側で行ってください。
- Python 依存ライブラリもそれぞれ独自のライセンスで提供されています。
  - 例: `httpx`, `mss`, `Pillow`, `PySide6`, `tomli-w`, 開発時の `PyInstaller`, `pytest`, `pytest-qt`
- release 物やローカル `.venv` に含まれる依存ライブラリについても、それぞれの upstream license が優先されます。
- 必要なら `pyproject.toml` を見て依存一覧を確認し、各 upstream project の license を追ってください。
- 依存関係の整理と source-only 公開方針については [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md) も参照してください。
- この fork は個人の趣味・検証用途を想定しています。
- 生成結果の内容、正確性、安全性、適法性について作者は保証しません。利用と生成物の扱いは各利用者の責任で行ってください。
- 第三者ソフトウェア、依存ライブラリ、モデルの利用条件に反する使い方について、作者は責任を負いません。

## License

MIT.  
See [LICENSE](LICENSE).
