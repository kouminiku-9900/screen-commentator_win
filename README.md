# screen-commentator_win

[r1cA18/screen-commentator](https://github.com/r1cA18/screen-commentator) を Windows 向けに改変したフォークです。
本プロジェクトの着想と設計は r1cA18 氏による macOS 版の実装に基づいており、その公開に深く感謝いたします。

## 概要

画面をキャプチャし、ローカルで動作するマルチモーダル LLM にコメントを生成させ、スクロールコメントとして画面上にオーバーレイ表示するアプリケーションです。
推論はすべてローカルで完結します。外部サーバーへのデータ送信は行いません。

## 原作との主な相違

| 項目 | 原作 (macOS) | 本フォーク (Windows) |
|------|-------------|---------------------|
| 言語 | Swift | Python |
| GUI | SwiftUI | PySide6 |
| 推論 | Gemini API / Ollama | llmster（ローカルのみ） |
| 既定モデル | — | unsloth/Qwen3.5-4B-GGUF |

- 外部 API モードは廃止し、ローカル推論に限定しています。
- ランタイムは `%LOCALAPPDATA%\ScreenCommentatorWin\` 配下に隔離されます。

## 機能

- PySide6 による最小構成の GUI ランチャー（Install / Start / Stop）
- `mss` による画面キャプチャ
- ローカル llmster + OpenAI 互換 API によるマルチモーダル推論
- 透過・最前面・クリック透過オーバーレイ
- 画面変化量に基づく検出
- 直近コメントとの重複抑制
- mood / excitement の反映
- スクロールおよび固定コメント表示
- レーン分散

## 動作要件

- Windows 10 / 11 (x64)
- Python 3.12 以上
- llmster およびモデルをダウンロード可能なネットワーク環境
- ローカル VLM を実行可能な RAM / VRAM

### 動作確認環境

- AMD Ryzen 7 5700X / 32 GB RAM / NVIDIA GeForce RTX 3060 12 GB

## セットアップ

### 開発環境

```powershell
pwsh -File .\scripts\dev-setup.ps1
.\.venv\Scripts\Activate.ps1
python -m screen_commentator_win
```

### ビルド

```powershell
pwsh -File .\scripts\build.ps1
```

成果物は `release\ScreenCommentatorLauncher\` および `release\ScreenCommentatorLauncher-win64.zip` に出力されます。
ビルド時にはパッケージ済み実行ファイルに対する self-test を実施し、overlay の描画まで検証します。

## インストールと初回実行

ソースコードから実行する場合は、以下のような手順になります。

```powershell
pwsh -File .\scripts\build.ps1
.\release\ScreenCommentatorLauncher\ScreenCommentatorLauncher.exe
```

初回はランチャー上で `Install` を押下してください。
`llmster` および `config.toml` で指定されたモデルが `%LOCALAPPDATA%\ScreenCommentatorWin\` 配下に取得されます。

## モデルの変更

`%LOCALAPPDATA%\ScreenCommentatorWin\config.toml` の `runtime.model_repo_url` を変更してください。
既定値は `unsloth/Qwen3.5-4B-GGUF` です。

```toml
[runtime]
model_repo_url = "https://huggingface.co/unsloth/Qwen3.5-4B-GGUF"
quantization = "Q4_K_M"
```

別モデルに変更する場合は URL を書き換えたうえで再度 `Install` を実行してください。
マルチモーダル推論を前提としているため、GGUF 本体に加え `mmproj` を含むリポジトリを指定する必要があります。

## ランタイムレイアウト

実行時データは `%LOCALAPPDATA%\ScreenCommentatorWin\` に集約されます。

```
%LOCALAPPDATA%\ScreenCommentatorWin\
├── config.toml
├── logs\
├── state\
└── llmster-home\
```

`Install` 操作はシステムの PATH を変更しません。

## 操作上の注意

- GUI からの設定変更機能は提供していません。設定を変更する場合は `config.toml` を直接編集してください。
- `Install`: ランタイムおよびモデルを取得します。完了後、サーバーとデーモンは自動停止します。
- `Start`: サーバー起動、モデルロード、コメント生成、オーバーレイ表示を一括で行います。
- `Stop`: 生成停止、オーバーレイ消去、モデルアンロード、サーバー停止、デーモン停止を行います。
- `%LOCALAPPDATA%` 以外の場所でテストする場合は環境変数 `SCW_APP_ROOT` を設定してください。
- 本フォークは `%LOCALAPPDATA%\ScreenCommentatorWin\llmster-home\` のアプリケーションローカルランタイムのみを使用します。
- LM Studio デスクトップアプリケーションが起動中の場合、llmster を起動できません。本ランチャーの使用前に LM Studio を終了してください。

## プライバシー

- 本アプリケーションはプライマリディスプレイのスクリーンショットを取得し、ローカルの LLM に送信してコメントを生成します。
- スクリーンショットおよび推論結果はすべてローカル（`127.0.0.1`）で処理されます。外部サーバーへの送信は行いません。
- スクリーンショットはメモリ上でのみ保持され、ディスクには保存されません。
- 画面上に表示されている機密情報（パスワード、個人情報、金融情報等）もキャプチャ対象に含まれます。実行中は画面表示内容にご注意ください。
- `%LOCALAPPDATA%\ScreenCommentatorWin\logs\` 配下のログファイルに、生成されたコメントテキストが記録される場合があります。
- テレメトリやアナリティクスの送信機能は含まれていません。
- 外部ネットワークへの通信は `Install` 操作時の llmster およびモデルのダウンロードに限られます。

## アンインストール

アンインストールする場合は、ランチャーを終了したうえで以下を削除してください。

- `%LOCALAPPDATA%\ScreenCommentatorWin\`
- ソースコードまたはビルド成果物を展開したディレクトリ

本フォークは `%USERPROFILE%\.lmstudio\` を使用しません。
削除対象は `%LOCALAPPDATA%\ScreenCommentatorWin\` のみです。

## 公開方針

本リポジトリの公開対象はソースコードです。

- `release/` の実行ファイルおよびアーカイブはローカルビルド成果物であり、公式のバイナリ配布物ではありません。
- `llmster` 本体およびモデルファイル（weights / GGUF / mmproj）は Git に含まれません。
- ローカルビルドしたバイナリを第三者に再配布する場合は、同梱される依存ライブラリのライセンス条件を利用者自身でご確認ください。

## 第三者ソフトウェア・モデルのライセンスおよび免責事項

- 本リポジトリのソースコードは [MIT License](./LICENSE) の下で提供されます。
- 本リポジトリは第三者ソフトウェアや第三者配布モデルのライセンスを再許諾するものではありません。
- `llmster` / LM Studio は本プロジェクトとは別のソフトウェアです。利用にあたってはそれぞれの利用条件に従ってください。
  - [LM Studio Terms of Use](https://lmstudio.ai/terms)
- 既定モデルは Hugging Face の [unsloth/Qwen3.5-4B-GGUF](https://huggingface.co/unsloth/Qwen3.5-4B-GGUF) を参照します（2026-03-08 時点で `apache-2.0`）。
- モデルを差し替える場合、当該モデルのライセンス確認は利用者の責任において行ってください。
- Python 依存ライブラリはそれぞれ独自のライセンスで提供されています。詳細は [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md) を参照してください。
- 本プロジェクトは個人の趣味・検証用途を想定しています。
- 生成結果の内容、正確性、安全性、適法性について保証はありません。利用および生成物の取り扱いは利用者の責任において行ってください。
- 第三者ソフトウェア、依存ライブラリ、モデルの利用条件に反する使用について、作者は一切の責任を負いかねます。

## ライセンス

MIT — [LICENSE](./LICENSE) を参照してください。
