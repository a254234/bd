@echo off
chcp 65001 >nul
cd /d "%~dp0"
if "%~1"=="" (
    echo 请把「订单数据表」和「一张或多张图片 / 结果JSON / 文件夹」一起拖到本文件上。
    pause
    exit /b
)
python "%~dp0match_rename.py" %*
echo.
pause
