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
- ランタイムは `start.bat` から `uv run` で起動します。フォルダを丸ごと削除すればアンインストール完了です。

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
- Start 時にモデル未取得の場合は自動ダウンロード
- モデルダウンロードの REST / CLI 自動フォールバック
- デーモン起動の競合自動リカバリ（旧プロセス検出・リトライ）

## 動作要件

- Windows 10 / 11 (x64)
- Python 3.12 以上
- [uv](https://docs.astral.sh/uv/) がインストール済みであること
- llmster およびモデルをダウンロード可能なネットワーク環境
- ローカル VLM を実行可能な RAM / VRAM

### 動作確認環境

- AMD Ryzen 7 5700X / 32 GB RAM / NVIDIA GeForce RTX 3060 12 GB

## クイックスタート

```powershell
# リポジトリをクローン
git clone https://github.com/kouminiku-9900/screen-commentator_win.git
cd screen-commentator_win

# start.bat をダブルクリック、または PowerShell から実行
.\start.bat
```

`start.bat` は以下を行います。

1. 環境変数 `SCW_APP_ROOT` を `start.bat` と同じフォルダ内の `ScreenCommentatorWin\` に設定
2. `uv run screen-commentator-win` でアプリを起動（依存パッケージの同期も自動）

初回は GUI ランチャー上で **Install** を押して llmster とモデルを取得してください。

## セットアップ（開発向け）

```powershell
# uv で依存を同期
uv sync --extra dev

# 直接実行
uv run screen-commentator-win

# テスト
uv run pytest
```

## インストールと初回実行

1. `start.bat` を実行して GUI ランチャーを起動します。
2. **Install** を押して llmster ランタイムとモデルをダウンロードします。
3. **Start** でサーバー起動 → モデルロード → コメント生成 → オーバーレイ表示が一括で行われます。

モデルが未取得の状態で **Start** を押した場合は、自動的にダウンロードが行われます。
`config.toml` でモデルを変更した場合も、次回の **Start** 時に新しいモデルが自動取得されます。

## モデルの変更

`ScreenCommentatorWin\config.toml`（`SCW_APP_ROOT` 配下）を編集してください。
既定値は `unsloth/Qwen3.5-4B-GGUF` です。

```toml
[runtime]
model_repo_url = "https://huggingface.co/unsloth/Qwen3.5-4B-GGUF"
quantization = "Q4_K_M"
```

別モデルに変更する場合は URL と quantization を書き換えて、次回 **Start** すれば自動でダウンロードされます。
マルチモーダル推論を前提としているため、GGUF 本体に加え `mmproj` を含むリポジトリを指定する必要があります。

## ランタイムレイアウト

`start.bat` から起動した場合、実行時データは `start.bat` と同じフォルダ配下の `ScreenCommentatorWin\` に集約されます。

```
screen-commentator_win\          ← リポジトリ / 展開先
├── start.bat
├── pyproject.toml
├── src\
└── ScreenCommentatorWin\        ← SCW_APP_ROOT（自動作成）
    ├── config.toml
    ├── logs\
    ├── state\
    └── llmster-home\
```

環境変数 `SCW_APP_ROOT` を別のパスに設定すれば保存先を変更できます。
未設定の場合は `%LOCALAPPDATA%\ScreenCommentatorWin\` が使われます。

`Install` 操作はシステムの PATH を変更しません。

## 操作上の注意

- GUI からの設定変更機能は提供していません。設定を変更する場合は `config.toml` を直接編集してください。
- **Install**: ランタイムおよびモデルを取得します。完了後、サーバーとデーモンは自動停止します。
- **Start**: サーバー起動、モデルロード、コメント生成、オーバーレイ表示を一括で行います。モデル未取得なら自動ダウンロードします。
- **Stop**: 生成停止、オーバーレイ消去、モデルアンロード、サーバー停止、デーモン停止を行います。
- `%LOCALAPPDATA%` 以外の場所でテストする場合は環境変数 `SCW_APP_ROOT` を設定してください。
- 本フォークはアプリローカルの `llmster-home\` のみを使用します。
- LM Studio デスクトップアプリケーションが起動中の場合、llmster を起動できません。本ランチャーの使用前に LM Studio を終了してください。

## プライバシー

- 本アプリケーションはプライマリディスプレイのスクリーンショットを取得し、ローカルの LLM に送信してコメントを生成します。
- スクリーンショットおよび推論結果はすべてローカル（`127.0.0.1`）で処理されます。外部サーバーへの送信は行いません。
- スクリーンショットはメモリ上でのみ保持され、ディスクには保存されません。
- 画面上に表示されている機密情報（パスワード、個人情報、金融情報等）もキャプチャ対象に含まれます。実行中は画面表示内容にご注意ください。
- `ScreenCommentatorWin\logs\` 配下のログファイルに、生成されたコメントテキストが記録される場合があります。
- テレメトリやアナリティクスの送信機能は含まれていません。
- 外部ネットワークへの通信は `Install` 操作時（および Start 時の自動ダウンロード）の llmster・モデル取得に限られます。

## アンインストール

リポジトリ（または展開先フォルダ）を丸ごと削除すれば完了です。
`ScreenCommentatorWin\` にすべてのランタイムデータが収まっているため、別途クリーンアップは不要です。

環境変数 `SCW_APP_ROOT` を変更していた場合は、そのパスも削除してください。

本フォークは `%USERPROFILE%\.lmstudio\` を使用しません。

## 公開方針

本リポジトリの公開対象はソースコードです。

- `llmster` 本体およびモデルファイル（weights / GGUF / mmproj）は Git に含まれません。

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
