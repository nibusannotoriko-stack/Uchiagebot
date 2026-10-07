import discord
from discord.ext import commands, tasks
import gspread
import os
import re
from datetime import datetime, timedelta
from dotenv import load_dotenv

# 環境変数の読み込み
load_dotenv()
TOKEN = os.getenv('DISCORD_BOT_TOKEN')

# Googleスプレッドシートの準備
gc = gspread.service_account(filename='credentials.json')
SHEET_KEY = '1fOlux4ls7yFasqOr_qcrFkExMUvD1vqdIDYAN7481xc'

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
        
        # 開催日時から「〇月〇日」を抽出してスプシ名にする
        date_match = re.search(r'(\d{1,2})月(\d{1,2})日', self.event_date.value)
        if date_match:
            sheet_name = f"{date_match.group(1)}月{date_match.group(2)}日_打ち上げ"
        else:
            sheet_name = f"{self.event_date.value[:5]}_打ち上げ".replace("/", "月")
        
        # 参加費の読み取り（Discordに表示するため「円」をつける）
        fees_dict = {}
        for g in ["1", "2", "3", "4"]:
            fee_match = re.search(rf'{g}年[:：]\s*([0-9０-９,，]+)', self.fees.value)
            if fee_match:
                fees_dict[f"{g}年"] = f"{fee_match.group(1)}円"
            else:
                fees_dict[f"{g}年"] = "要確認"
        
        # スプレッドシートの作成
        try:
            sh = gc.open_by_key(SHEET_KEY)
            existing_sheets = [ws.title for ws in sh.worksheets()]
            if sheet_name not in existing_sheets:
                template_ws = sh.worksheet("template")
                sh.duplicate_sheet(template_ws.id, new_sheet_name=sheet_name, insert_sheet_index=len(sh.worksheets()))
        except Exception as e:
            await interaction.followup.send(f"スプレッドシートの作成に失敗したよ: {e}", ephemeral=True)
            return

        # 参加費のフォーマット（2年目以降はスペースを入れて字下げする）
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
        
        await interaction.followup.send(f"✅ {sheet_name} の受付メッセージを作成しました！", ephemeral=True)

# ==========================================
# スラッシュコマンドの設定
# ==========================================
@bot.tree.command(name="event", description="新しいイベントの受付を作成します")
async def create_event(interaction: discord.Interaction):
    role_names = [r.name for r in interaction.user.roles]
    if "幹部" not in role_names:
        await interaction.response.send_message("このコマンドは幹部専用だよ！", ephemeral=True)
        return
    await interaction.response.send_modal(EventModal())

# ==========================================
# メンバーが「👍」を押して参加する処理
# ==========================================
@bot.event
async def on_raw_reaction_add(payload):
    if payload.user_id == bot.user.id:
        return
    if str(payload.emoji) != "👍":
        return

    guild = bot.get_guild(payload.guild_id)
    member = guild.get_member(payload.user_id)
    channel = bot.get_channel(payload.channel_id)
    message = await channel.fetch_message(payload.message_id)

    if message.author == bot.user and "のお知らせ" in message.content:
        match_title = re.search(r'【🍺(.+?)\s*のお知らせ🍺】', message.content)
        if not match_title:
            return

        sheet_name = match_title.group(1)
        
        # 【修正！】スプシの計算バグを防ぐため、カンマや「円」を取り除いて純粋な数字(int)にする
        fees = {}
        for g in ["1", "2", "3", "4"]:
            fee_match = re.search(rf'{g}年[:：]\s*([0-9０-９,，]+)円?', message.content)
            if fee_match:
                num_str = fee_match.group(1).replace(",", "").replace("，", "")
                try:
                    fees[f"{g}年"] = int(num_str)
                except ValueError:
                    fees[f"{g}年"] = "要確認"

        roles = [role.name for role in member.roles]
        univ = "不明"
        for u in ["白百合", "本女", "慶應", "早稲田"]:
            if u in roles:
                univ = u
                break
                
        grade = "不明"
        for role_name in roles:
            if "1年" in role_name:
                grade = "1年"
                break
            elif "2年" in role_name:
                grade = "2年"
                break
            elif "3年" in role_name:
                grade = "3年"
                break
            elif "4年" in role_name:
                grade = "4年"
                break

        fee = fees.get(grade, "要確認")
        user_name = member.display_name

        try:
            sh = gc.open_by_key(SHEET_KEY)
            ws = sh.worksheet(sheet_name)
            names = ws.col_values(1)
            
            if user_name in names:
                row_index = names.index(user_name) + 1
            else:
                row_index = len(names) + 1
                if row_index < 2: row_index = 2
                
            ws.update(f'A{row_index}:D{row_index}', [[user_name, univ, grade, fee]])
            ws.format(f'A{row_index}:D{row_index}', {"textFormat": {"strikethrough": False}})
        except Exception as e:
            print(f"参加エラー: {e}")

# ==========================================
# メンバーが「👍」を外してキャンセルする処理
# ==========================================
@bot.event
async def on_raw_reaction_remove(payload):
    if payload.user_id == bot.user.id:
        return
    if str(payload.emoji) != "👍":
        return

    guild = bot.get_guild(payload.guild_id)
    member = guild.get_member(payload.user_id)
    channel = bot.get_channel(payload.channel_id)
    message = await channel.fetch_message(payload.message_id)

    if message.author == bot.user and "のお知らせ" in message.content:
        match_title = re.search(r'【🍺(.+?)\s*のお知らせ🍺】', message.content)
        if not match_title:
            return

        sheet_name = match_title.group(1)
        
        deadline_match = re.search(r'【締切】\s*(\d{4}/\d{1,2}/\d{1,2})[\s/]+(\d{1,2}:\d{1,2})', message.content)
        if deadline_match:
            try:
                deadline_str = f"{deadline_match.group(1)} {deadline_match.group(2)}"
                deadline = datetime.strptime(deadline_str, "%Y/%m/%d %H:%M")
                if datetime.now() > deadline:
                    try:
                        await member.send(f"⚠️ {sheet_name} は締切を過ぎているため、キャンセルできません！担当者に直接連絡してね。")
                    except:
                        pass
                    return 
            except ValueError:
                pass

        user_name = member.display_name

        try:
            sh = gc.open_by_key(SHEET_KEY)
            ws = sh.worksheet(sheet_name)
            names = ws.col_values(1)
            
            if user_name in names:
                row_index = names.index(user_name) + 1
                ws.format(f'A{row_index}:D{row_index}', {"textFormat": {"strikethrough": True}})
        except Exception as e:
            print(f"キャンセルエラー: {e}")

@tasks.loop(hours=24)
async def reminder_task():
    pass

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f'ログイン完了: {bot.user}')
    reminder_task.start() 

# ==========================================
# RenderのWeb Service用：簡易HTTPサーバー
# ==========================================
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")

def run_server():
    # Renderが割り当てるポート番号（環境変数PORT）を取得。なければ8080を使う
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), SimpleHandler)
    server.serve_forever()

# ボットを起動する前に、裏でウェブサーバーを動かすスレッドをスタート
server_thread = threading.Thread(target=run_server, daemon=True)
server_thread.start()

bot.run(TOKEN)
