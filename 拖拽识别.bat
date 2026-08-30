@echo off
chcp 65001 >nul
cd /d "%~dp0"
if "%~1"=="" (
    echo 请把一张或多张图片（或整个文件夹）拖到本文件上运行。
    pause
    exit /b
)
python "%~dp0ocr_extract.py" %*
echo.
echo 识别结果已保存到 %~dp0results 文件夹。
pause
