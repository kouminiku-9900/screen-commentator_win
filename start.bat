@echo off
cd /d "%~dp0"
set "SCW_APP_ROOT=%~dp0ScreenCommentatorWin"
uv run screen-commentator-win %*
