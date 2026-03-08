$ErrorActionPreference = "Stop"

$python = "py -3.12"
Invoke-Expression "$python -m venv .venv"
Invoke-Expression ".\.venv\Scripts\python.exe -m pip install --upgrade pip"
Invoke-Expression ".\.venv\Scripts\python.exe -m pip install -e .[dev]"

