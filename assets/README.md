# 素材庫規範（Asset Library Spec）

所有素材集中於 `assets/`，合成管線只透過 `manifest.json` 索引，不掃描目錄。新增素材後必須登記到 manifest。

## 命名規範（Naming Convention）

| 類型 | 目錄 | 格式 | 範例 |
| --- | --- | --- | --- |
| 背景素材（Background） | `backgrounds/` | `bg_<遊戲>_<類型>_<序號>_<寬x高>_<秒數>s.mp4` | `bg_minecraft_parkour_001_720x1280_45s.mp4` |
| 圖解卡（Diagram Card） | `diagram_cards/` | `card_<主題>_<序號>.png`（透明底 PNG） | `card_data_units_001.png` |
| 角色立繪（Avatar） | `avatars/` | `avatar_<角色>_<情緒>.png` | `avatar_questioner_shocked.png` |
| 逐句語音（TTS Line） | `audio/lines/` | `tts_<影片ID>_line<四位序號>_<角色>.mp3` | `tts_revive_line0001_A.mp3` |
| 背景音樂（BGM） | `bgm/` | `bgm_<風格>_<序號>.mp3` | `bgm_lofi_001.mp3` |
| 輸出（Output） | `output/` | `out_<影片ID>_v<二位版號>.mp4` | `out_revive_v01.mp4` |

## 素材要求（Requirements）

- **背景素材**：豎屏 720×1280（不足者合成時自動縮放裁切），單段建議 20~60 秒，只收 CC0/自有版權（Pexels、Pixabay、Mixkit 或自行錄製）。
- **畫幅適配（Fit Mode）**：素材可設 `"fit"` 欄位——`"crop"`（預設，放大後中央裁切滿版；第一人稱跑酷建議用此）或 `"blur"`（模糊填充，原片縮至 720 寬置中、背景鋪放大模糊版，保留完整橫向畫面）。
- **查重規避（Dedup）**：管線每次合成自動對背景做隨機起點、變速（0.92x~1.08x）、隨機鏡像，並以 `--seed` 控制可重現性；同一段素材連續兩支影片不得使用相同起點。
- **圖解卡**：透明底 PNG，寬度建議 600~660px，只在關鍵知識點（約 10% 時長）疊加。
- **授權登記**：manifest 中每筆素材必填 `source` 與 `license`（`cc0` / `owned` / `licensed`）。

## manifest.json 格式

```json
{
  "backgrounds": [
    {"file": "backgrounds/bg_minecraft_parkour_001_720x1280_45s.mp4", "duration": 45.0, "source": "self-recorded", "license": "owned", "fit": "crop", "tags": ["minecraft", "parkour"]}
  ],
  "diagram_cards": [
    {"file": "diagram_cards/card_data_units_001.png", "topic": "data-units", "license": "owned"}
  ],
  "bgm": [
    {"file": "bgm/bgm_lofi_001.mp3", "license": "cc0"}
  ]
}
```

`file` 路徑相對於 `assets/` 目錄。`duration` 單位為秒。