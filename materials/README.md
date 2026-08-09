# materials/ — RAG 知識庫素材（Knowledge Source Materials）

這個資料夾放「要餵進 RAG 知識庫」的**原始知識檔案**；向量索引本身在
`assets/rag/chroma`（已 gitignore、可隨時重建），這裡的原始知識要進 git 保存。

> 本公開 repo 為程式骨架，**不附教材**：請自行放入可自由散佈的素材（如 MIT／CC 授權內容）；
> 私人教材（如公司財務講義）請留在 private repo，不要放進公開版。

## 支援格式（learntok rag-build）

| 格式 | 說明 |
| --- | --- |
| `.md` / `.markdown` | 首選：結構清楚，可用標題分層 |
| `.txt` | 純文字 |
| `.json` | LearnTok 腳本（自動抽 title + 台詞） |
| `.srt` | 字幕（自動去掉時間軸） |
| PDF | 目前不支援，請先轉成 `.md` / `.txt` |

## 目錄建議（一主題 = 一個資料夾或一組檔案）

```
materials/
├── README.md
└── <系列>/
    └── <主題>/
        └── README.md   # 一個主題一個檔案（或一組檔案）
```

## 建庫流程

```powershell
# 教育系列：--series 標記系列、--topic 標記子課程（同一系列多個主題共用一個 series）
.venv\Scripts\learntok.exe rag-build --source materials/<系列>/<主題> --topic <主題-id> --series <系列-id>

# 查看知識庫現況（每個 series/topic 各幾個 chunk）
.venv\Scripts\learntok.exe rag-build --list-topics

# 抽測檢索
.venv\Scripts\learntok.exe rag-retrieve --query "<問題>" --topic <主題-id>
```

## 寫腳本時引用

- `terms[].source` 填 `materials/<相對路徑>.md:<chunk編號>`，例如 `materials/my-series/my-topic/README.md:3`
- 驗證出處：`learntok validate --script <腳本.json> --rag-sources`

## 命名與內容建議

- 路徑決定後不要亂改檔名/搬移（`source` 記錄的是相對路徑）
- 一個檔案一個主題；開頭先寫定義與關鍵數據，再用小節分層，檢索命中率最高
- 高風險數據（數字、年份、法規）寫清楚來源與脈絡
