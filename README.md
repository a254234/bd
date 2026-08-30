# 图片信息 OCR 识别脚本

识别图片中的文字，并提取手机号（支持 `138****5678` 掩码格式）、订单号、下单时间、收货人、订单金额等字段；图片中不存在的字段输出为空。

## 0. 快速开始

### 方式一：本地网站（推荐）

1. 双击 `启动网站.bat`，本机浏览器会自动打开 `http://127.0.0.1:5000/`；窗口里同时会打印局域网地址（如 `http://192.168.1.10:5000/`），同一局域网内的手机/电脑用该地址即可访问；
2. 网页上选择「订单数据表（.xls/.xlsx）」；如需缩小匹配范围，可先选「匹配平台」：美团外卖、淘宝闪购、其他（可填写平台名称）；
3. 选择「一张或多张订单图片」（图片也可直接拖入网页），点「开始处理」，自动完成 OCR 识别、字段提取、订单匹配；
4. 页面表格显示每张图的识别字段与匹配结果（绿色=匹配成功并已改名，橙色=未匹配保持原名）；完全相同的图片或同一订单的多张图片会自动去重，重复的显示为灰色并跳过，不进入下载包；
5. 点「下载结果 ZIP」：图片按下单日期分文件夹存放（如 `2026-08-10/8.10_美团_16.jpg`），未匹配的图片放在 `未匹配` 文件夹，另含 `识别结果.csv`、`重命名记录.csv`；
6. 处理完关闭黑色命令窗口即停止网站。上传文件只保存在本机 `tmp_uploads` 文件夹，24 小时后自动清理，不会修改你原始图片。

其他设备打不开时，请在 Windows 防火墙中允许 Python（首次监听时通常会自动弹出提示，勾选允许即可），或以管理员身份放行端口：

```powershell
netsh advfirewall firewall add rule name="订单识别网站" dir=in action=allow protocol=TCP localport=5000
```

端口 5000 被占用时可用：`python webapp.py --port 8000`，然后访问 `http://127.0.0.1:8000/`。

### 方式三：部署到 Debian/Linux

项目压缩包中已包含 Debian 安装脚本。上传并解压后执行：

```bash
cd ocr_tool
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
bash install_debian.sh
bash start_debian.sh
```

安装脚本会使用清华 PyPI 镜像创建 `.venv` 并安装 Linux 依赖。启动后终端会显示局域网访问地址，其他设备访问：

```text
http://Debian服务器局域网IP:5000/
```

如需换端口：

```bash
bash start_debian.sh --port 8000
```

如需后台开机启动，可将项目放到 `/opt/ocr_tool` 后执行：

```bash
sudo cp ocr-tool.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ocr-tool
sudo systemctl status ocr-tool
```

如果开启了 UFW，还需要放行端口：

```bash
sudo ufw allow 5000/tcp
```

### 方式二：拖拽脚本（无需网站）

### 第一步：识别图片

1. 选中一张或多张图片（也可以整个文件夹）；
2. 拖到 `拖拽识别.bat` 上松开；
3. 窗口打印识别到的字段，结果自动保存到 `results\识别结果_时间戳.csv/.json`。

### 第二步：匹配订单表并重命名

1. 同时选中「订单数据表（.xls/.xlsx）」和「图片」（或第一步生成的识别结果 JSON）；
2. 一起拖到 `拖拽匹配重命名.bat` 上松开（表格和图片的先后顺序无所谓）；
3. 窗口显示每张图的匹配情况：
   - 匹配成功 → 图片就地改名为 `8.16_美团_14.jpg`（月日_平台前两字_原流水号）；
   - 匹配失败 → 保持原名并打印原因（如手机号命中多行、图片里没有下单时间）；
4. 每次运行生成 `results\重命名记录_时间戳.csv` 备查。

> 首次使用请先完成第 1 节的依赖安装；拿不准时可用命令行加 `--dry-run` 先预览，不真正改名。

## 1. 安装依赖（清华镜像）

```powershell
cd D:\bd\ocr_tool
python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
```

若镜像源不可用，可换成阿里云镜像：

```powershell
python -m pip install -i https://mirrors.aliyun.com/pypi/simple -r requirements.txt
```

若系统 Python 目录不可写，可安装到项目内 vendor 目录（脚本会自动加载）：

```powershell
python -m pip install --target D:\bd\ocr_tool\vendor -i https://pypi.tuna.tsinghua.edu.cn/simple rapidocr onnxruntime
```

## 2. 使用

```powershell
# 单张图片
python ocr_extract.py D:\图片\order.png

# 整个文件夹（含子文件夹）
python ocr_extract.py D:\图片 -o result.csv --json result.json

# 一次处理多张图片
python ocr_extract.py 1.jpg 2.jpg 3.jpg

# 打印 OCR 原文，便于核对识别结果
python ocr_extract.py D:\图片\order.png -v
```

也可以直接把图片（可多选）拖到 `拖拽识别.bat` 上运行，结果自动保存到 `results` 文件夹（不指定 `-o`/`--json` 时也会自动保存，文件名带时间戳，多次运行不会互相覆盖）。

控制台输出示例：

```
------------------------------------------------------------
图片: D:\bd\ocr_tool\sample_order.png
  手机号  : 181****9836
  订单号  : 3202250290703347042
  下单时间: 2026-08-10 16:58:10
  收货人  : 伟**
  订单金额: ¥62.5
  配送地址: 湖南惠同新材料股份有限公司-南门
  商品件数: 1件
  支付方式: 在线支付
  预计送达: 17:52-18:07
```

未识别到的字段显示 `(空)`，CSV/JSON 中对应单元格为空字符串。

字段清单：手机号、订单号（自动去掉中间空格）、下单时间、收货人、订单金额、配送地址、商品件数、支付方式、预计送达。

没有字段标签的图片也会兜底识别：

- 手机号：按「1 开头 + 掩码」识别，兼容 `138****5678`、`137****262` 等不同掩码长度；
- 订单金额：按 `¥`/`￥` 符号识别（如 `¥88`）；
- 配送地址：按「路/街/号/楼/区/公司」等地址特征词猜测，OCR 把地址拆成多行时自动合并，但可能猜错，建议人工核对；
- 收货人：支持 `符**(先生)` 这类「掩码姓名+称呼」的写法。

## 3. 自定义字段

编辑 `ocr_extract.py` 顶部的 `FIELDS` 配置即可增删字段：

```python
FIELDS = [
    (
        "字段名",                          # 输出字段名
        ["关键词1", "关键词2"],             # 图片中该字段的标签文字
        [r"取值正则1", r"取值正则2"],        # 值的格式（依次尝试）
        True,                              # 找不到关键词时是否全文兜底查找
    ),
]
```

## 4. 常见问题

- 识别不到 / 识别错：优先使用清晰、高分辨率、正向的截图；OCR 对倾斜和模糊图片效果较差。
- 默认引擎为 RapidOCR（模型内置在包内）；若异常，可改装 PaddleOCR：
  `python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple paddlepaddle paddleocr`
- 手机号掩码形式不同（如 `138xxxx5678`、`138＊＊＊＊5678`）时，修改 `FIELDS` 中手机号的正则即可。

## 5. 生成测试图片

```powershell
python make_sample.py   # 生成 sample_order.png
python ocr_extract.py sample_order.png
```

## 6. 匹配订单表并重命名图片

把图片识别结果与订单数据表（`.xls`/`.xlsx`）匹配，成功后把图片重命名为「来源平台+原流水号」：

```powershell
# 先预览匹配计划（不真正改名）
python match_rename.py 订单数据.xls D:\图片 --dry-run

# 正式执行（图片会被改名）
python match_rename.py 订单数据.xls D:\图片

# 也可以复用已有的识别结果，避免重复 OCR
python match_rename.py 订单数据.xls results\识别结果_xxx.json
```

匹配规则：

1. 先用订单号码（自动去空格）精确匹配表中的「订单编号」；
2. 匹配不到时改用手机号匹配（查看表的「收货人电话」和「备注」，备注里通常含 `138****5678` 掩码手机号）；
3. 手机号命中多行时，用图片中的下单时间与表的「下单日期」比对排除；
4. 匹配成功后重命名为如 `8.16_美团_16.jpg`（下单日期月日 + 平台前两字 + 原流水号，美团外卖取「美团」）；同名文件自动追加 `_2`、`_3`，不会覆盖。

也可以把「订单数据表 + 图片」一起拖到 `拖拽匹配重命名.bat` 上执行，每次处理都会在 `results` 文件夹生成 `重命名记录_时间戳.csv` 备查。
