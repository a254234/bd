@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在启动网站：订单图片识别与重命名（局域网内其他设备也可访问）...
echo 本机浏览器会自动打开 http://127.0.0.1:5000/
echo 启动后窗口会显示局域网地址，例如 http://192.168.1.10:5000/
echo 处理完成后关闭本窗口即可停止网站。
echo.
python "%~dp0webapp.py"
echo.
echo 网站已停止。
pause
