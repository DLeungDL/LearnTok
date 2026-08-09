# 角色語音設定（Character Voice Settings）

> 本檔案記錄每個角色的 TTS（文字轉語音）與 RVC（語音轉換）完整參數設定。
> 機器可讀版本見 `assets/characters.json`，兩者必須同步。
> 修改 `pipeline/tools/tts_edge.py` 和 `pipeline/tools/rvc_convert.py` 後請同步更新此檔案與 `characters.json`。

---

## 角色配對原則（Pairing Principle）

每支影片 = **1 個 Questioner + 1 個 Explainer**，從角色庫中配對：
- 腳本 JSON 中 `A` 永遠是 Questioner，`B` 永遠是 Explainer
- `characters` 欄位指定具體角色名稱，工具依名稱查詢 `characters.json` 取得聲線 / RVC 設定
- 未來新增角色時，在 `characters.json` 和本檔案新增條目即可

---

## 說話者 A — 企鵝燈（Tomori）

| 類別 | 參數 | 值 | 說明 |
|------|------|------|------|
| **角色資訊** | 角色名 | 企鵝燈（Tomori） | MyGO!!!!! |
| | 身份 | Questioner（提問者） | |
| | 字幕顏色 | `#FFD54F` | 黃色 |
| | 頭像（Avatar） | `avatars/avatar_deng_default.png` | 疊加於左上角（top-left corner） |
| **TTS** | 語音引擎 | Edge-TTS | |
| | 語音 | `zh-CN-XiaoyiNeural` | 微軟曉伊（女聲） |
| | 語速 | `None`（使用預設 `+12%`） | |
| **RVC** | 模型 | `rvc_deng_v1.pth` | |
| | 索引 | `rvc_deng_v1.index` | |
| | pitch（音高） | `+6` | 半音位移 |
| | f0method | `rmvpe` | 基頻偵測方法 |
| | index_rate | `0.5` | 0~1，越高越多原始音色 |
| **響度平衡** | voice_gain_db | `-4.1` dB | 與熊大響度對齊（2026-08-03 實測校準） |
| | protect | 預設 `0.33` | 未設定，使用 RVC 預設值 |
| | filter_radius | 預設 `3` | 未設定，使用 RVC 預設值 |
| | rms_mix_rate | 預設 `1` | 未設定，使用 RVC 預設值 |

### 性格特質（Personality）
- 外表呆呆、反應慢半拍，行動搖搖晃晃像企鵝走路
- 天然呆，時常脫線，偶爾冒出充滿詩意的奇怪想法
- 腦袋想法跳躍

### 說話習慣（Speech Habits）
- 句子簡短，不太會長篇大論；緊張時斷斷續續
- 標誌擬聲：**咕咕嘎嘎**（gugu gaga）— 只在情緒高漲時出現（開心、慌張、委屈），不是常態說話

### 口頭禪 / Meme
- 咕咕嘎嘎 / 咕——（萬用擬聲，情緒高漲時觸發）

---

## 說話者 B — 派大星（Patrick Star）

| 類別 | 參數 | 值 | 說明 |
|------|------|------|------|
| **角色資訊** | 角色名 | 派大星（Patrick Star） | 海綿寶寶 |
| | 身份 | Questioner（提問者） | |
| | 字幕顏色 | `#4FC3F7` | 藍色 |
| | 頭像（Avatar） | `avatars/avatar_paidaxing_default.png` | 疊加於右上角（top-right corner） |
| **TTS** | 語音引擎 | Edge-TTS | |
| | 語音 | `zh-CN-YunjianNeural` | 微軟雲健（男聲） |
| | 語速 | `-12%` | 較慢，符合角色特性 |
| **RVC** | 模型 | `rvc_paidaxing_v2.pth` | |
| | 索引 | `rvc_paidaxing_v2.index` | |
| | pitch（音高） | `+4` | 半音位移，提亮音色 |
| | f0method | `rmvpe` | 基頻偵測方法 |
| | index_rate | `0.9` | 0~1，越高越多原始音色 |
| | protect | `0` | 0~0.5，不保護氣音 |
| | filter_radius | `5` | 0~7，基頻中位數濾波 |
| | rms_mix_rate | `0` | 0~1，0=使用模型音量包絡 |

### 性格特質（Personality）
- 憨厚呆滯、極度遲鈍，理解能力和反應速度比常人慢好幾拍
- 對危險、諷刺或尷尬完全無感知
- 偶爾迸發「大智若愚」的哲理金句（派大星哲學）

### 說話習慣（Speech Habits）
- 重鼻音、低沉壓喉，像塞著鼻子在講話
- 慢吞吞拖腔、拉長尾音（如「好——啊——」）
- 反應遲鈍的單音節回應

### 口頭禪 / Meme
- 「呃……」「啊？」「嗯……」— 被問到不懂、思考時觸發
- 「我不知道」— 完全摸不著頭腦時觸發
- 大智若愚金句 — 偶爾一本正經脫口而出

---

## 說話者 C — 熊大（Xiong Da）

| 類別 | 參數 | 值 | 說明 |
|------|------|------|------|
| **角色資訊** | 角色名 | 熊大（Xiong Da） | 《熊出沒》主角 |
| | 身份 | Explainer（解說者） | |
| | 字幕顏色 | `#81C784` | 綠色 |
| | 頭像（Avatar） | `avatars/avatar_xiongda_default.png` | 疊加於右上角（top-right corner） |
| **TTS** | 語音引擎 | Edge-TTS | |
| | 語音 | `zh-CN-YunxiNeural` | 微軟雲希（男聲，溫暖清晰） |
| | 語速 | `None`（使用預設 `+12%`） | 語速中等，日後微調 |
| **RVC** | 模型 | `null`（暫無） | 未來可加入 RVC 模型 |
| | 索引 | `null` | |
| | pitch | `0` | 無 RVC 時不適用 |
| **響度平衡** | voice_gain_db | `+3.9` dB | 與企鵝燈響度對齊（2026-08-03 實測校準） |

### 性格特質（Personality）
- 成熟穩重、有責任感
- 頭腦聰明，策劃擔當
- 是非分明，有耐心

### 說話習慣（Speech Habits）
- 語氣沉穩，聲音厚實，語速中等
- 邏輯清晰，習慣先講道理，喜歡點出他人的問題
- 口語接地氣，文辭簡單直白，保留「俺」的用法
- 吐槽十分直白，但不會惡毒

### 口頭禪 / Meme
- 「真是拿你沒辦法。」— 對 questioner 的呆萌感到無奈時觸發
- 「凡事動腦子」— 勸人思考時觸發
- 「俺想到辦法了！」— 解決問題時觸發

---

## 響度平衡（Voice Gain Balance）

- `compose.py` 混音時依角色 `voice_gain_db`（dB）套用增益：A/B 兩角色響度對齊後，聽感才不會一邊大聲一邊小聲。
- 數值由實際產出音檔量測（EBU R128 integrated loudness）校準；角色聲線或 RVC 設定變動後需重新量測更新。
- 未設定的角色預設 `0.0`（不調整）。`pipeline/tools/calibrate_audio.py` 可一鍵量測並寫回校準值。

---

## 頂角頭像疊加（Top-Corner Avatar Overlay）

正式影片（final video）頂部左右兩角會疊加角色頭像：說話者 A（Questioner）在左上角，說話者 B（Explainer）在右上角。

| 項目 | 值 | 說明 |
|------|------|------|
| 設定來源 | `assets/manifest.json` 的 `avatars` 陣列 | 每個角色一筆 |
| 疊加實作 | `pipeline/compose.py` | 在 ASS 字幕燒錄之後依序疊加 |
| 預設寬度 | `width_ratio = 0.27` | 頭像寬度 = 影片寬度 x 0.27（無條件捨去至偶數） |
| 預設邊距 | `margin_ratio = 0.035` | 距左右邊緣 = 影片寬度 x 0.035 |
| 預設垂直位置 | `y_ratio = 0.085` | 距頂部 = 影片高度 x 0.085 |
| 水平翻轉 | `flip`（選填） | 設為 `true` 時水平翻轉頭像（hflip） |
| 缺少檔案時 | 印出 warning 並略過（skip） | 不會中斷渲染 |

### 頭像命名規範（Avatar Naming Convention）

`avatar_<角色拼音>_<表情狀態>.png`

| 角色 | 預設 | 其他表情（範例） |
|------|------|-----------------|
| 企鵝燈 | `avatar_deng_default.png` | `avatar_deng_leftfacing.png` |
| 派大星 | `avatar_paidaxing_default.png` | `avatar_paidaxing_rightfacing.png` |
| 熊大 | `avatar_xiongda_default.png` | `avatar_xiongda_thinking.png`（未來擴充） |

頭像放入 `assets/avatars/`，路徑登記到 `assets/manifest.json` 的 `avatars` 陣列。

> 注意：頭像是在渲染時燒入影片的。若更換頭像圖檔或調整參數，必須重新渲染影片才會生效；舊有輸出檔不會自動更新。

---

## 新增角色指南

新增角色時需更新以下檔案：

1. **`assets/characters.json`** — 新增角色條目（TTS、RVC、顏色、頭像）

2. **`pipeline/tools/tts_edge.py`** — 無需修改（自動從 characters.json 讀取）

3. **`pipeline/tools/rvc_convert.py`** — 無需修改（自動從 characters.json 讀取）

4. **`pipeline/compose.py`**
   - `DEFAULT_CHARACTERS` 字典：新增角色名、身份（questioner / explainer）、字幕顏色

5. **`assets/manifest.json`**
   - `avatars` 陣列：新增頭像條目（`file`、`speaker`、`side`）
   - 將頭像圖檔（建議 PNG、透明背景 transparent background）放入 `assets/avatars/`

6. **更新本檔案**：記錄新角色的完整設定（含性格特質、說話習慣、口頭禪 / meme）

7. **更新 `assets/characters.json`**：與本檔案保持同步

### RVC 參數速查

| 參數 | 範圍 | 預設 | 說明 |
|------|------|------|------|
| `pitch` | -24~+24 | 0 | 半音位移，正=升高，負=降低 |
| `f0method` | rmvpe/crepe/pm/harvest | harvest | 基頻偵測方法（rmvpe 品質佳） |
| `index_rate` | 0~1 | 0.5 | 索引匹配率，越高越多原始音色 |
| `filter_radius` | 0~7 | 3 | 基頻中位數濾波器半徑 |
| `rms_mix_rate` | 0~1 | 1 | 0=模型包絡，1=輸入包絡 |
| `protect` | 0~0.5 | 0.33 | 保護氣音/清音，越低轉換越徹底 |
| `resample_sr` | 0/16000~48000 | 0 | 重取樣率，0=不重取樣 |