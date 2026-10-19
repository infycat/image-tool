# Batch BG Remover｜批量去背景工具

![Platform](https://img.shields.io/badge/platform-Windows-0078D4?logo=windows)
![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![Release](https://img.shields.io/github/v/release/infycat/image-tool?display_name=tag)
![License](https://img.shields.io/badge/license-not%20specified-lightgrey)

一款重視隱私的 Windows 批量圖片去背景工具。所有圖片均在本機處理，不需網路，也不會上傳到任何伺服器。除了透明背景輸出，也支援批量旋轉、翻轉、裁切、即時預覽與命令列操作。

![Batch BG Remover icon](https://raw.githubusercontent.com/infycat/image-tool/main/icon.png)

## 下載 Windows 版

前往 **[Releases 下載最新版](https://github.com/infycat/image-tool/releases/latest)**。

- 推薦：下載 Release 內的 `Batch-BG-Remover-v1.0.0-Windows-x64.zip`，解壓後雙擊 `batch_bg_remover.exe`。
- 單獨的 `batch_bg_remover.exe` 也保留在 Release 資產中；若該檔在你的環境無法啟動，請改用完整 ZIP 版。
- Windows SmartScreen 可能會提示「Windows 已保護您的電腦」，這是未購買程式碼簽章憑證的常見提示。請先核對 Release 內的 SHA-256，再自行決定是否執行。
- 後續版本會繼續發布到 [Releases](https://github.com/infycat/image-tool/releases)，可在 GitHub 點選 **Watch → Custom → Releases** 接收更新通知。

> Windows 版不需要另外安裝 Python。原始碼使用者請參考下方「從原始碼執行」。

## 功能特色

### 去背景核心

1. **色度鍵（Chroma Key）**：自動取樣圖片四角的平均色作為背景參考，以雙閾值與 smoothstep 計算透明度，減少鋸齒。
2. **綠色溢色抑制（Spill Suppression）**：降低前景邊緣混入的背景綠色，減少綠邊。
3. **連通域清理（Connected-Component Cleanup）**：保留主要主體並清除水印、雜散像素與噪點。

### 批量處理

| 功能 | 說明 |
|---|---|
| 旋轉 | 逆時針 90°、順時針 90°、180° 或自訂任意角度 |
| 翻轉 | 水平或垂直翻轉 |
| 裁切 | 在預覽畫面拖曳框選，依比例批量套用到所有圖片 |
| 即時預覽 | 對照原圖與變換後效果，去背結果以棋盤格背景顯示 |
| 拖放匯入 | 支援多張圖片、含空格路徑與 `file://` URI |
| 安全輸出 | 同名檔案自動加上 `_1`、`_2`，不覆蓋既有輸出 |

支援 PNG、JPG、JPEG、BMP、WEBP、GIF、TIF 與 TIFF。

## 使用方法

1. 拖入圖片，或選擇「新增圖片／新增資料夾」。
2. 選擇輸出資料夾；預設為桌面的「去背輸出」。
3. 視需要設定旋轉、翻轉與裁切，並勾選「去除背景」。
4. 按下「開始批量處理」。

![步驟 1：加入圖片並即時預覽](https://raw.githubusercontent.com/infycat/image-tool/main/step1.png)

![步驟 2：設定去背並開始處理](https://raw.githubusercontent.com/infycat/image-tool/main/step2.png)

![步驟 3：查看輸出結果](https://raw.githubusercontent.com/infycat/image-tool/main/step3.png)

### 去背參數

| 參數 | 預設值 | 作用 |
|---|---:|---|
| LO（背景閾值下限） | 55 | 越高會移除越多背景 |
| HI（前景閾值上限） | 115 | 越高越保守，能保留更多前景 |
| 雜塊面積下限 | 3000 | 小於此面積的孤立連通域會被清除 |

## 從原始碼執行

需求：Windows、Python 3.10 或以上。

```powershell
git clone https://github.com/infycat/image-tool.git
cd image-tool
python -m pip install -r requirements.txt
python batch_bg_remover.py
```

Windows 使用者亦可雙擊 `開啓批量去背景工具.bat`。

### 命令列模式

```powershell
python batch_bg_remover.py --cli <輸入資料夾> <輸出資料夾> [選項]
```

| 選項 | 說明 |
|---|---|
| `--rotate <角度>` | 旋轉角度，例如 `90`、`-90` 或 `180` |
| `--flip h\|v` | 水平翻轉（`h`）或垂直翻轉（`v`） |
| `--crop x0,y0,x1,y1` | 以 0～1 歸一化比例裁切 |
| `--no-bg` | 不去背景，只套用變換與裁切 |

範例：

```powershell
python batch_bg_remover.py --cli frames output --rotate 90 --crop 0,0,0.5,1.0 --no-bg
```

## 建置 Windows 版本

完整的 PyInstaller 設定、圖示與建置腳本都已收錄在倉庫中：

```powershell
python -m pip install -r requirements-build.txt
.\build.ps1
```

建置結果位於 `dist\batch_bg_remover\`。發布時應將整個資料夾壓縮成 ZIP，避免遺漏 `_internal` 依賴。

## 測試

```powershell
# 演算法與 CLI 自測
python batch_bg_remover.py --selftest

# GUI 冒煙測試（約 1.2 秒後自動關閉）
python batch_bg_remover.py --smoke
```

## 專案結構

```text
image-tool/
├── batch_bg_remover.py       # GUI、CLI 與核心演算法
├── batch_bg_remover.spec     # PyInstaller 建置設定
├── build.ps1                 # Windows 建置腳本
├── build_assets/icon.ico     # EXE 圖示
├── requirements.txt          # 執行依賴
├── requirements-build.txt    # 建置依賴
├── icon.png                  # 應用程式圖示
├── step1.png                 # 操作截圖
├── step2.png
├── step3.png
├── 開啓批量去背景工具.bat    # Windows 原始碼快捷啟動
└── README.md
```

## 隱私與安全

- 所有圖片皆在本機處理。
- 程式不會自動上傳圖片或分析結果。
- 建議只從本倉庫的 [Releases](https://github.com/infycat/image-tool/releases) 下載，並核對 SHA-256。

## 版本紀錄

### v1.0.0 — 2026-10-20

- 首次提供正式 GitHub Release。
- 提供 Windows x64 可執行檔與完整可攜式 ZIP。
- 補齊 PyInstaller 設定、建置腳本、依賴清單與原始碼。
- 改善下載、更新通知、建置與驗證說明。
