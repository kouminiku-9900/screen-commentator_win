# Third-Party Notices

This repository publishes source code for `screen-commentator_win`.

- The source code in this repository is licensed under MIT.
- Third-party software, downloaded models, and bundled runtime dependencies keep their own licenses and terms.
- This file is informational only and does not relicense any third-party component.

## Publication Scope

The intended public distribution for this project is the source repository itself.

- `llmster` / LM Studio is not included in git.
- Models are not included in git.

## Third-Party Components Used By The Project

### Runtime and model download path

- `llmster` / LM Studio
  - Terms: [LM Studio Terms](https://lmstudio.ai/terms)
  - Notes: downloaded at install time by the user; not relicensed by this repository

- Default model: `unsloth/Qwen3.5-4B-GGUF`
  - Model page: [Hugging Face](https://huggingface.co/unsloth/Qwen3.5-4B-GGUF)
  - 2026-03-08 Hugging Face license label: `apache-2.0`
  - Notes: users may change `runtime.model_repo_url`; any replacement model keeps its own license

### Direct Python dependencies

- `httpx`
  - Declared license: BSD-3-Clause

- `mss`
  - Declared license: MIT

- `Pillow`
  - Commonly distributed under HPND-style terms

- `PySide6`
  - Declared license expression in installed metadata: `LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only`
  - Notes: local binary builds may bundle Qt/PySide files; review upstream Qt/PySide licensing before redistributing binaries

- `tomli-w`
  - Declared license: MIT

### Development dependencies

- `pytest`
  - Included only for development and test

- `pytest-qt`
  - Included only for development and test

## Trademarks

本リポジトリ内で言及される製品名、サービス名、企業名等は、それぞれの所有者の商標または登録商標です。
本プロジェクトはこれらの商標権者との提携・推奨関係を示すものではありません。

## Disclaimer

- This project is intended for personal, hobby, and evaluation use.
- The author does not guarantee legality, safety, fitness for a particular purpose, or output quality.
- Compliance with third-party licenses, terms, and model restrictions remains the responsibility of the user.
