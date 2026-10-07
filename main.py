import discord
from discord.ext import commands, tasks
import gspread
import os
import re
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from aiohttp import web

# 環境変数の読み込み
load_dotenv()
TOKEN = os.getenv('DISCORD_BOT_TOKEN')

# Googleスプレッドシートの準備
gc = gspread.service_account(filename='credentials.json')
SHEET_KEY = '1fOlux4ls7yFasqOr_qcrFkExMUvD1vqdIDYAN7481xc'

# 日本時間（JST）の設定
JST = timezone(timedelta(hours=9), 'JST')

# ボットの設定
intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.members = True
bot = commands.Bot(command_prefix='!', intents=intents)

# ==========================================
# 秘密の入力フォーム（Modal）の設定
# ==========================================
class EventModal(discord.ui.Modal, title='🍻 打ち上げイベント作成'):
    greeting = discord.ui.TextInput(
        label='挨拶',
        style=discord.TextStyle.paragraph,
        placeholder='お疲れ様です！秋の打ち上げやります！\nみんな参加してね〜！',
        required=True
    )
    
    venue = discord.ui.TextInput(
        label='会場',
        style=discord.TextStyle.short,
        placeholder='新宿の〇〇居酒屋（詳細は後日！）',
        required=True
    )

    event_date = discord.ui.TextInput(
        label='開催日時（※〇月〇日を入れてね）',
        style=discord.TextStyle.short,
        placeholder='10月25日 19:00〜',
        required=True
    )
    
    deadline = discord.ui.TextInput(
        label='締切日時（例: 2026/10/20 23:59）',
        style=discord.TextStyle.short,
        placeholder='2026/10/20 23:59',
        required=True
    )
    
    fees = discord.ui.TextInput(
        label='参加費（数字だけ書き換えてね）',
        style=discord.TextStyle.paragraph,
        default='1年: 1500\n2年: 3000\n3年: 3000\n4年: 3000',
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        date_match = re.search(r'(\d{1,2})月(\d{1,2})日', self.event_date.value)
        if date_match:
            sheet_name = f"{date_match.group(1)}月{date_match.group(2)}日_打ち上げ"
        else:
            sheet_name = f"{self.event_date.value[:5]}_打ち上げ".replace("/", "月")
        
        fees_dict = {}
        for g in ["1", "2", "3", "4"]:
            fee_match = re.search(rf'{g}年[:：]\s*([0-9０-９,，]+)', self.fees.value)
            if fee_match:
                fees_dict[f"{g}年"] = f"{fee_match.group(1)}円"
            else:
                fees_dict[f"{g}年"] = "要確認"
        
        try:
            sh = gc.open_by_key(SHEET_KEY)
            existing_sheets = [ws.title for ws in sh.worksheets()]
            if sheet_name not in existing_sheets:
                template_ws = sh.worksheet("template")
                sh.duplicate_sheet(template_ws.id, new_sheet_name=sheet_name, insert_sheet_index=len(sh.worksheets()))
        except Exception as e:
            await interaction.followup.send(f"スプレッドシートの作成に失敗したよ: {e}", ephemeral=True)
            return

        fee_lines = []
        for i, (k, v) in enumerate(fees_dict.items()):
            if i == 0:
                fee_lines.append(f"{k}: {v}")
            else:
                fee_lines.append(f"          {k}: {v}")
        fee_text = "\n".join(fee_lines)
        
        message_content = (
            f"@everyone\n"
            f"【🍺{sheet_name} のお知らせ🍺】\n\n"
            f"{self.greeting.value}\n\n"
            f"【会場】 {self.venue.value}\n"
            f"【開催日時】 {self.event_date.value}\n"
            f"【締切】 {self.deadline.value}\n"
            f"【参加費】 {fee_text}\n\n"
            "※締切後のキャンセルは必ず担当者へ直接ご連絡ください。\n"
            "（締切後にリアクションを外しても自動的に不参加にはなりません。無断での操作は把握できる仕様となっております）\n\n"
            "参加する方はこの投稿に「👍」のリアクションをお願いします！"
        )
        
        bot_msg = await interaction.channel.send(content=message_content)
        await bot_msg.add_reaction("👍")
        
        try:
            ws = sh.worksheet(sheet_name)
            meta_data = [[str(interaction.channel.id), str(bot_msg.id), self.deadline.value, "0", "0", "0"]]
            ws.update(range_name='Z1:AE1', values=meta_data)
        except Exception as e:
            print(f"リマインダー用データの保存エラー: {e}")
            
        await interaction.followup.send(f"✅ {sheet_name} の受付メッセージを作成しました！", ephemeral=True)

# ==========================================
# スラッシュコマンドの設定
# ==========================================
@bot.tree.command(name="event", description="新しいイベントの受付を作成します")
async def create_event(interaction: discord.Interaction
