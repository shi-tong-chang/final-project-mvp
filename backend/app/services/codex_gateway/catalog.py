"""Gateway 展示櫥窗的 typed mock provider。"""

from __future__ import annotations

from typing import Protocol

from app.schemas.api.codex_gateway import (
    CatalogItem,
    CharacterStyleItem,
    GatewayCatalog,
)


class GatewayCatalogProvider(Protocol):
    """後續正式資料 provider 必須維持的最小介面。"""

    def get_catalog(self) -> GatewayCatalog:
        """回傳已驗證且不含任意本機檔案路徑的 catalog。"""


class MockGatewayCatalogProvider:
    """提供可被正式圖片資料直接替換的展示卡。"""

    def get_catalog(self) -> GatewayCatalog:
        """建立無外部圖片依賴的三工作區 placeholder catalog。"""

        return GatewayCatalog(
            schema_version="storyboard-studio.catalog.v2",
            character_styles=(
                CharacterStyleItem(
                    item_id="cel-anime",
                    title="賽璐璐動畫",
                    description="乾淨輪廓、平塗色塊與俐落的兩階段陰影。",
                    preview_kind="cel",
                    tags=("動畫", "平塗", "清晰輪廓"),
                    prompt_fragment=(
                        "以日系賽璐璐動畫風格呈現，使用乾淨外輪廓、分區明確的"
                        "平塗色塊、兩階段陰影與精準高光。"
                    ),
                ),
                CharacterStyleItem(
                    item_id="manga-ink",
                    title="黑白漫畫",
                    description="強弱墨線、網點與高反差黑白構成。",
                    preview_kind="manga",
                    tags=("漫畫", "墨線", "網點"),
                    prompt_fragment=(
                        "以黑白漫畫風格呈現，使用有粗細變化的墨線、大片黑色陰影、"
                        "細緻網點與高反差留白。"
                    ),
                ),
                CharacterStyleItem(
                    item_id="clean-line-art",
                    title="純線稿",
                    description="專注角色結構與服裝細節的乾淨線條。",
                    preview_kind="line-art",
                    tags=("線條", "結構", "無上色"),
                    prompt_fragment=(
                        "以精緻純線稿呈現，使用流暢且有筆壓變化的深色線條，保留"
                        "乾淨白底，不加入大面積上色。"
                    ),
                ),
                CharacterStyleItem(
                    item_id="dark-fairytale",
                    title="黑暗童話",
                    description="哥德輪廓、幽暗色調與帶危險感的童話氣息。",
                    preview_kind="dark-fairytale",
                    tags=("哥德", "幽暗", "童話"),
                    prompt_fragment=(
                        "以黑暗童話插畫風格呈現，採用哥德式輪廓、低彩度深色調、"
                        "幽微逆光、古老紙張質感與精緻而不安的裝飾細節。"
                    ),
                ),
                CharacterStyleItem(
                    item_id="classical-oil",
                    title="古典油畫",
                    description="厚實筆觸、畫布肌理與古典明暗塑形。",
                    preview_kind="oil",
                    tags=("油畫", "筆觸", "古典光"),
                    prompt_fragment=(
                        "以古典油畫風格呈現，保留可見筆觸與畫布肌理，使用深沉"
                        "背景、溫暖膚色與古典明暗法塑造立體感。"
                    ),
                ),
                CharacterStyleItem(
                    item_id="cinematic-film",
                    title="電影劇照",
                    description="敘事光線、電影鏡頭與克制的膠片色彩。",
                    preview_kind="cinematic",
                    tags=("電影", "敘事光", "膠片"),
                    prompt_fragment=(
                        "以電影劇照風格呈現，使用具敘事性的主光與輪廓光、35mm "
                        "鏡頭語言、淺景深及克制的膠片色彩分級。"
                    ),
                ),
                CharacterStyleItem(
                    item_id="photoreal",
                    title="寫實攝影",
                    description="自然皮膚、真實材質與可信的鏡頭細節。",
                    preview_kind="realistic",
                    tags=("寫實", "攝影", "自然材質"),
                    prompt_fragment=(
                        "以高品質寫實人物攝影呈現，保留自然皮膚紋理、真實布料與"
                        "金屬材質、可信光學景深與柔和自然光，避免塑膠感。"
                    ),
                ),
                CharacterStyleItem(
                    item_id="watercolor-wash",
                    title="透明水彩",
                    description="通透暈染、紙張留白與柔和邊緣。",
                    preview_kind="watercolor",
                    tags=("水彩", "暈染", "留白"),
                    prompt_fragment=(
                        "以透明水彩插畫呈現，使用通透疊色、自然水痕、柔和暈染邊緣"
                        "與可見紙張纖維，保留呼吸感留白。"
                    ),
                ),
                CharacterStyleItem(
                    item_id="gouache-poster",
                    title="厚塗水粉",
                    description="不透明色塊、手繪筆觸與海報式構成。",
                    preview_kind="gouache",
                    tags=("水粉", "厚塗", "色塊"),
                    prompt_fragment=(
                        "以厚塗水粉畫風格呈現，採用不透明霧面色塊、可見手繪筆觸、"
                        "簡化明暗與具有設計感的海報式構圖。"
                    ),
                ),
                CharacterStyleItem(
                    item_id="storybook-soft",
                    title="柔霧繪本",
                    description="溫柔色塊、紙張質感與親近的敘事氣氛。",
                    preview_kind="storybook",
                    tags=("繪本", "柔霧", "溫暖"),
                    prompt_fragment=(
                        "以柔霧繪本插畫呈現，使用溫暖低對比色塊、細緻紙張顆粒、"
                        "圓潤邊緣與安靜親近的敘事氣氛。"
                    ),
                ),
                CharacterStyleItem(
                    item_id="noir-film",
                    title="黑色電影",
                    description="硬質側光、百葉陰影與冷峻黑白灰階。",
                    preview_kind="noir",
                    tags=("黑色電影", "硬光", "黑白"),
                    prompt_fragment=(
                        "以黑色電影風格呈現，採用高反差黑白灰階、硬質側光、百葉窗"
                        "切割陰影、薄霧與冷峻懸疑氣氛。"
                    ),
                ),
                CharacterStyleItem(
                    item_id="cyberpunk-neon",
                    title="賽博龐克",
                    description="霓虹反光、科技材質與夜雨都市色彩。",
                    preview_kind="cyberpunk",
                    tags=("霓虹", "科技", "夜雨"),
                    prompt_fragment=(
                        "以賽博龐克插畫風格呈現，使用青藍與洋紅霓虹反光、夜雨"
                        "空氣、細緻科技材質與高密度都市光源。"
                    ),
                ),
                CharacterStyleItem(
                    item_id="art-nouveau",
                    title="新藝術海報",
                    description="植物曲線、裝飾邊框與復古平面配色。",
                    preview_kind="art-nouveau",
                    tags=("新藝術", "植物", "裝飾"),
                    prompt_fragment=(
                        "以新藝術海報風格呈現，使用流動植物曲線、精緻裝飾邊框、"
                        "扁平復古配色與優雅對稱構圖。"
                    ),
                ),
                CharacterStyleItem(
                    item_id="fantasy-concept",
                    title="奇幻概念藝術",
                    description="史詩光線、魔法粒子與豐富材質層次。",
                    preview_kind="fantasy",
                    tags=("奇幻", "史詩光", "魔法"),
                    prompt_fragment=(
                        "以奇幻概念藝術呈現，使用史詩尺度的戲劇光線、細緻服裝與"
                        "道具材質、微量魔法粒子及深遠氣氛層次。"
                    ),
                ),
                CharacterStyleItem(
                    item_id="charcoal-sketch",
                    title="炭筆素描",
                    description="粗細炭痕、擦拭灰階與手工紙肌理。",
                    preview_kind="charcoal",
                    tags=("炭筆", "素描", "灰階"),
                    prompt_fragment=(
                        "以炭筆素描呈現，保留粗細不一的炭痕、擦拭形成的灰階、"
                        "手工紙肌理與強調結構的明暗塑形。"
                    ),
                ),
                CharacterStyleItem(
                    item_id="ukiyo-e",
                    title="浮世繪",
                    description="木刻線條、平面色版與日式傳統構圖。",
                    preview_kind="ukiyo-e",
                    tags=("浮世繪", "木刻", "色版"),
                    prompt_fragment=(
                        "以浮世繪木版畫風格呈現，使用流暢木刻輪廓、平面套色色版、"
                        "和紙顆粒與富有節奏的日式傳統構圖。"
                    ),
                ),
                CharacterStyleItem(
                    item_id="paper-cut",
                    title="剪紙拼貼",
                    description="分層紙片、俐落剪影與手作陰影。",
                    preview_kind="paper-cut",
                    tags=("剪紙", "拼貼", "層次"),
                    prompt_fragment=(
                        "以分層剪紙拼貼風格呈現，使用俐落紙片剪影、有限色盤、"
                        "手工纖維邊緣與柔和的實體疊層陰影。"
                    ),
                ),
                CharacterStyleItem(
                    item_id="pixel-art",
                    title="像素藝術",
                    description="受控像素塊、有限色盤與清晰剪影。",
                    preview_kind="pixel",
                    tags=("像素", "有限色盤", "遊戲"),
                    prompt_fragment=(
                        "以精緻像素藝術呈現，使用受控像素塊、有限色盤、清晰角色"
                        "剪影與復古遊戲肖像的明暗分區。"
                    ),
                ),
                CharacterStyleItem(
                    item_id="retro-comic",
                    title="復古美漫",
                    description="粗黑輪廓、印刷網點與鮮明原色。",
                    preview_kind="retro-comic",
                    tags=("美漫", "復古印刷", "網點"),
                    prompt_fragment=(
                        "以復古美式漫畫風格呈現，使用粗黑輪廓、鮮明原色色塊、"
                        "老式印刷網點、微小套印偏移與動態構圖。"
                    ),
                ),
                CharacterStyleItem(
                    item_id="stylized-3d",
                    title="風格化 3D",
                    description="雕塑般造型、柔和材質與動畫電影燈光。",
                    preview_kind="stylized-3d",
                    tags=("3D", "動畫電影", "雕塑感"),
                    prompt_fragment=(
                        "以高品質風格化 3D 動畫角色呈現，使用雕塑般清楚造型、"
                        "柔和次表面材質、細緻布料與動畫電影式棚拍燈光。"
                    ),
                ),
            ),
            scene_showcase=(
                CatalogItem(
                    item_id="architectural",
                    title="空間與建築",
                    description="先建立主要結構、入口、視線與空間關係。",
                    preview_kind="architecture",
                    tags=("結構", "機位"),
                ),
                CatalogItem(
                    item_id="atmospheric",
                    title="光線與氣氛",
                    description="以時段、天候與主光方向定義場景情緒。",
                    preview_kind="atmosphere",
                    tags=("光源", "天候"),
                ),
                CatalogItem(
                    item_id="object-led",
                    title="物件與線索",
                    description="由故事關鍵物件帶出空間焦點與視線動線。",
                    preview_kind="object",
                    tags=("物件", "視線"),
                ),
            ),
            storyboard_showcase=(
                CatalogItem(
                    item_id="establish-react-detail",
                    title="建立・反應・細節",
                    description="以全景交代、角色反應與關鍵細節建立節奏。",
                    preview_kind="establish",
                    tags=("建立", "反應", "細節"),
                ),
                CatalogItem(
                    item_id="detail-reveal",
                    title="細節・揭露",
                    description="從局部線索逐步擴大資訊，形成揭露感。",
                    preview_kind="reveal",
                    tags=("細節", "揭露"),
                ),
                CatalogItem(
                    item_id="storyboard-sequence",
                    title="鏡頭序列",
                    description="把節拍拆成可逐格討論的分鏡段落。",
                    preview_kind="sequence",
                    tags=("節拍", "連續性"),
                ),
            ),
        )
