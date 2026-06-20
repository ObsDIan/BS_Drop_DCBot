# bot.py

import os
import csv
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import discord
from discord import app_commands
from dotenv import load_dotenv
from rapidfuzz import process, fuzz
from opencc import OpenCC
import asyncio
from aiohttp import web


async def health_check(request):
    return web.Response(text="Discord bot is running.")


async def start_web_server():
    port = int(os.environ.get("PORT", 10000))

    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(
        runner,
        host="0.0.0.0",
        port=port
    )

    await site.start()

    print(f"Health check server started on port {port}")
# =========================
# 基本設定
# =========================

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
CSV_FILE = "BotData.csv"

MIN_MATCH_SCORE = 50
AUTOCOMPLETE_LIMIT = 25
EMBED_FIELD_LIMIT = 1024

cc = OpenCC("s2twp")


REQUIRED_HEADERS = [
    "副本名稱",
    "來源",
    "掉落分類",
    "掉落物名稱",
]


ALIASES = {
    # 掉落物簡稱
    "洪結": "洪門結晶體",
    "洪門結晶": "洪門結晶體",
    "洪门结晶": "洪門結晶體",
    "靈精": "靈石精髓",
    "灵精": "靈石精髓",
    "月精": "月石精髓",
    "靈丹精": "靈丹精髓",
    "灵丹精": "靈丹精髓",
    "仙丹精": "仙丹精髓",

    # 副本簡稱可以之後補
    # "沙灣": "血浪沙灣",
}


# =========================
# 工具函式
# =========================

def to_traditional(value) -> str:
    if value is None:
        return ""

    text = str(value).strip()

    if not text:
        return ""

    return cc.convert(text)


def apply_alias(keyword: str) -> str:
    keyword = to_traditional(keyword)
    return ALIASES.get(keyword, keyword)


def chunk_lines(lines: List[str], limit: int = EMBED_FIELD_LIMIT) -> List[str]:
    chunks = []
    current = ""

    for line in lines:
        next_text = f"{current}\n{line}" if current else line

        if len(next_text) > limit:
            if current:
                chunks.append(current)
            current = line
        else:
            current = next_text

    if current:
        chunks.append(current)

    return chunks


def best_match(keyword: str, choices: List[str]) -> Optional[str]:
    keyword = apply_alias(keyword)

    if not keyword:
        return None

    if keyword in choices:
        return keyword

    result = process.extractOne(
        keyword,
        choices,
        scorer=fuzz.WRatio
    )

    if not result:
        return None

    name, score, _ = result

    if score < MIN_MATCH_SCORE:
        return None

    return name


def suggest(keyword: str, choices: List[str]) -> List[str]:
    keyword = apply_alias(keyword)

    if not keyword:
        return choices[:AUTOCOMPLETE_LIMIT]

    results = process.extract(
        keyword,
        choices,
        scorer=fuzz.WRatio,
        limit=AUTOCOMPLETE_LIMIT
    )

    return [
        name
        for name, score, _ in results
        if score >= 40
    ]


# =========================
# 資料模型
# =========================

@dataclass
class DropRecord:
    dungeon: str
    source_type: str
    category: str
    item: str
    quantity: str = ""
    note: str = ""

    def item_display(self) -> str:
        text = self.item

        if self.quantity:
            text += f" x{self.quantity}"

        if self.note:
            text += f"（{self.note}）"

        return text

    def source_display(self) -> str:
        text = f"{self.dungeon}（{self.source_type}）"

        if self.quantity:
            text += f" x{self.quantity}"

        if self.note:
            text += f"｜{self.note}"

        return text


@dataclass
class DropDatabase:
    csv_file: str

    records: List[DropRecord] = field(default_factory=list)
    by_item: Dict[str, List[DropRecord]] = field(default_factory=dict)
    by_dungeon: Dict[str, List[DropRecord]] = field(default_factory=dict)

    item_names: List[str] = field(default_factory=list)
    dungeon_names: List[str] = field(default_factory=list)

    def reload(self):
        records = []

        with open(self.csv_file, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)

            if not reader.fieldnames:
                raise RuntimeError("CSV 沒有標題列。")

            header_map = {
                original: to_traditional(original)
                for original in reader.fieldnames
            }

            headers = list(header_map.values())

            missing_headers = [
                header
                for header in REQUIRED_HEADERS
                if header not in headers
            ]

            if missing_headers:
                raise RuntimeError(f"CSV 缺少必要欄位：{', '.join(missing_headers)}")

            for row in reader:
                normalized = {
                    header_map[key]: to_traditional(value)
                    for key, value in row.items()
                }

                dungeon = normalized.get("副本名稱", "")
                source_type = normalized.get("來源", "")
                category = normalized.get("掉落分類", "")
                item = normalized.get("掉落物名稱", "")

                if not dungeon or not source_type or not category or not item:
                    continue

                records.append(
                    DropRecord(
                        dungeon=dungeon,
                        source_type=source_type,
                        category=category,
                        item=item,
                    )
                )

        self.records = records
        self._build_indexes()

    def _build_indexes(self):
        by_item = {}
        by_dungeon = {}
        seen = set()

        for record in self.records:
            key = (
                record.dungeon,
                record.source_type,
                record.category,
                record.item,
                record.quantity,
                record.note,
            )

            if key in seen:
                continue

            seen.add(key)

            by_item.setdefault(record.item, []).append(record)
            by_dungeon.setdefault(record.dungeon, []).append(record)

        self.by_item = by_item
        self.by_dungeon = by_dungeon
        self.item_names = sorted(by_item.keys())
        self.dungeon_names = sorted(by_dungeon.keys())

    def find_item(self, keyword: str) -> Optional[str]:
        return best_match(keyword, self.item_names)

    def find_dungeon(self, keyword: str) -> Optional[str]:
        return best_match(keyword, self.dungeon_names)

    def suggest_items(self, keyword: str) -> List[str]:
        return suggest(keyword, self.item_names)

    def suggest_dungeons(self, keyword: str) -> List[str]:
        return suggest(keyword, self.dungeon_names)


# =========================
# Discord 回覆格式
# =========================

def group_by_source(records: List[DropRecord]) -> Dict[str, List[DropRecord]]:
    grouped = {}

    for record in records:
        grouped.setdefault(record.source_type, []).append(record)

    return grouped


def group_by_source_and_category(records: List[DropRecord]) -> Dict[str, List[DropRecord]]:
    grouped = {}

    for record in records:
        key = f"{record.source_type} / {record.category}"
        grouped.setdefault(key, []).append(record)

    return grouped


def make_item_embed(
    item: str,
    keyword: str,
    records: List[DropRecord]
) -> discord.Embed:
    embed = discord.Embed(
        title=f"掉落物查詢：{item}",
        description=f"查詢關鍵字：{to_traditional(keyword)}",
        color=0x2ecc71,
    )

    grouped = group_by_source(records)

    for source_type, group in grouped.items():
        lines = [
            f"・{record.source_display()}"
            for record in group
        ]

        chunks = chunk_lines(lines)

        for index, chunk in enumerate(chunks, start=1):
            field_name = source_type

            if len(chunks) > 1:
                field_name = f"{source_type} {index}/{len(chunks)}"

            embed.add_field(
                name=field_name,
                value=chunk,
                inline=False,
            )

    return embed


def make_dungeon_embed(
    dungeon: str,
    keyword: str,
    records: List[DropRecord]
) -> discord.Embed:
    embed = discord.Embed(
        title=f"副本查詢：{dungeon}",
        description=f"查詢關鍵字：{to_traditional(keyword)}",
        color=0x3498db,
    )

    grouped = group_by_source_and_category(records)

    for group_name, group in grouped.items():
        lines = [
            f"・{record.item_display()}"
            for record in group
        ]

        chunks = chunk_lines(lines)

        for index, chunk in enumerate(chunks, start=1):
            field_name = group_name

            if len(chunks) > 1:
                field_name = f"{group_name} {index}/{len(chunks)}"

            embed.add_field(
                name=field_name,
                value=chunk,
                inline=False,
            )

    return embed


# =========================
# Discord Bot
# =========================

db = DropDatabase(CSV_FILE)
db.reload()

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


async def item_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> List[app_commands.Choice[str]]:
    return [
        app_commands.Choice(name=name, value=name)
        for name in db.suggest_items(current)
    ]


async def dungeon_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> List[app_commands.Choice[str]]:
    return [
        app_commands.Choice(name=name, value=name)
        for name in db.suggest_dungeons(current)
    ]


@client.event
async def on_ready():
    await tree.sync()

    print(f"Bot 已登入：{client.user}")
    print(f"資料筆數：{len(db.records)}")
    print(f"副本數量：{len(db.dungeon_names)}")
    print(f"掉落物種類：{len(db.item_names)}")


@tree.command(name="查掉落物", description="透過掉落物查詢來源副本")
@app_commands.describe(掉落物="輸入掉落物名稱，例如：靈石精髓")
@app_commands.autocomplete(掉落物=item_autocomplete)
async def search_item(interaction: discord.Interaction, 掉落物: str):
    item = db.find_item(掉落物)

    if not item:
        await interaction.response.send_message(
            f"找不到與「{to_traditional(掉落物)}」相近的掉落物。",
            ephemeral=True,
        )
        return

    embed = make_item_embed(
        item=item,
        keyword=掉落物,
        records=db.by_item[item],
    )

    await interaction.response.send_message(embed=embed)


@tree.command(name="查副本", description="透過副本名稱查詢掉落物")
@app_commands.describe(副本="輸入副本名稱，例如：血浪沙灣")
@app_commands.autocomplete(副本=dungeon_autocomplete)
async def search_dungeon(interaction: discord.Interaction, 副本: str):
    dungeon = db.find_dungeon(副本)

    if not dungeon:
        await interaction.response.send_message(
            f"找不到與「{to_traditional(副本)}」相近的副本。",
            ephemeral=True,
        )
        return

    embed = make_dungeon_embed(
        dungeon=dungeon,
        keyword=副本,
        records=db.by_dungeon[dungeon],
    )

    await interaction.response.send_message(embed=embed)


@tree.command(name="重載資料", description="重新讀取 BotData.csv")
@app_commands.default_permissions(administrator=True)
async def reload_data(interaction: discord.Interaction):
    try:
        db.reload()

        await interaction.response.send_message(
            (
                "資料已重新載入。\n"
                f"資料筆數：{len(db.records)}\n"
                f"副本數量：{len(db.dungeon_names)}\n"
                f"掉落物種類：{len(db.item_names)}"
            ),
            ephemeral=True,
        )

    except Exception as error:
        await interaction.response.send_message(
            f"重載失敗：{error}",
            ephemeral=True,
        )


async def main():
    if not TOKEN:
        raise RuntimeError("找不到 DISCORD_TOKEN，請確認環境變數是否設定正確。")

    async with client:
        await start_web_server()
        await client.start(TOKEN)

asyncio.run(main())

client.run(TOKEN)