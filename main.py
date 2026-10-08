import discord
from discord.ext import commands, tasks
import gspread
import os
import re
import asyncio
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
# 順番待ち（キュー）システムの設定
# ==========================================
action_queue = None

async def process_queue():
    await bot.wait_until_ready()
    while not bot.is_closed():
        if action_queue is None:
            await asyncio.sleep(1)
            continue
            
        action = await action_queue.get()
        try:
            await process_sheet_update(action)
        except Exception as e:
            print(f"キュー処理エラー: {e}")
        finally:
            action_queue.task_done()
            # 連続アクセスを防ぐために6秒待つ（Google APIの制限対策）
            await asyncio.sleep(6)

async def process_sheet_update(action):
    action_type = action['type']
    sheet_name = action['sheet_name']
    user_name = action['user_name']
    
    try:
        sh = gc.open_by_key(SHEET_KEY)
        ws = sh.worksheet(sheet_name)
        names = ws.col_values(1)
        
        if user_name in names:
            row_index = names.index(user_name) + 1
        else:
            row_index = len(names) + 1
            if row_index < 2: row_index = 2

        if action_type == 'add':
            univ = action['univ']
            grade = action['grade']
            fee = action['fee']
            
            ws.update(range_name=f'A{row_index}:E{row_index}', values=[[user_name, univ, grade, fee, "未回収"]])
            ws.format(f'A{row_index}:E{row_index}', {
                "textFormat": {
                    "strikethrough": False,
                    "foregroundColor": {"red": 0.0, "green": 0.0, "blue": 0.0}
                }
            })
            
        elif action_type == 'remove':
            # スプシにまだ名前がないのにキャンセルされた場合はスキップ
            if user_name not in names:
                return
                
            is_late_cancel = action['is_late_cancel']
            
            if is_late_cancel:
                ws.format(f'A{row_index}:E{row_index}', {
                    "textFormat": {
                        "strikethrough": True,
                        "foregroundColor": {"red": 1.0, "green": 0.0, "blue": 0.0}
                    }
                })
                ws.update_acell(f'E{row_index}', "無断キャンセル")
            else:
                ws.format(f'A{row_index}:E{row_index}', {
                    "textFormat": {
                        "strikethrough": True,
                        "foregroundColor": {"red": 0.0, "green": 0.0, "blue": 0.0}
                    }
                })
                ws.update_acell(f'E{row_index}', "キャンセル")
                
    except Exception as e:
        print(f"スプシ更新エラー: {e}")


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
    
    # ★修正：強制的にユーザー情報を取得して絶対に無視させない！
    member = payload.member
    if not member:
        member = guild.get_member(payload.user_id)
    if not member:
        try:
            member = await guild.fetch_member(payload.user_id)
        except Exception:
            return
            
    channel = bot.get_channel(payload.channel_id)
    message = await channel.fetch_message(payload.message_id)

    if message.author == bot.user and "のお知らせ" in message.content:
        match_title = re.search(r'【🍺(.+?)\s*のお知らせ🍺】', message.content)
        if not match_title:
            return

        sheet_name = match_title.group(1)
        
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

        action = {
            'type': 'add',
            'sheet_name': sheet_name,
            'user_name': user_name,
            'univ': univ,
            'grade': grade,
            'fee': fee
        }
        if action_queue is not None:
            await action_queue.put(action)

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
    
    # ★修正：強制的にユーザー情報を取得して絶対に無視させない！
    member = guild.get_member(payload.user_id)
    if not member:
        try:
            member = await guild.fetch_member(payload.user_id)
        except Exception:
            return
            
    channel = bot.get_channel(payload.channel_id)
    message = await channel.fetch_message(payload.message_id)

    if message.author == bot.user and "のお知らせ" in message.content:
        match_title = re.search(r'【🍺(.+?)\s*のお知らせ🍺】', message.content)
        if not match_title:
            return

        sheet_name = match_title.group(1)
        
        is_late_cancel = False
        deadline_match = re.search(r'【締切】\s*(\d{4}/\d{1,2}/\d{1,2})[\s/]+(\d{1,2}:\d{1,2})', message.content)
        if deadline_match:
            try:
                deadline_str = f"{deadline_match.group(1)} {deadline_match.group(2)}"
                deadline = datetime.strptime(deadline_str, "%Y/%m/%d %H:%M").replace(tzinfo=JST)
                if datetime.now(JST) > deadline:
                    is_late_cancel = True
                    try:
                        await member.send(f"⚠️ {sheet_name} は締切を過ぎているため、無断キャンセルとして記録されました！至急、担当者に直接連絡してね。")
                    except:
                        pass
            except ValueError:
                pass

        user_name = member.display_name

        action = {
            'type': 'remove',
            'sheet_name': sheet_name,
            'user_name': user_name,
            'is_late_cancel': is_late_cancel
        }
        if action_queue is not None:
            await action_queue.put(action)

# ==========================================
# リマインダーをチェックするループ処理
# ==========================================
@tasks.loop(minutes=30)
async def reminder_task():
    try:
        sh = gc.open_by_key(SHEET_KEY)
        now = datetime.now(JST)

        for ws in sh.worksheets():
            if ws.title == "template":
                continue

            meta = ws.get('Z1:AE1')
            if not meta or len(meta[0]) < 6:
                continue

            data = meta[0]
            channel_id = int(data[0])
            msg_id = int(data[1])
            deadline_str = data[2]
            flag_7d = data[3]
            flag_3d = data[4]
            flag_12h = data[5]

            try:
                deadline = datetime.strptime(deadline_str, "%Y/%m/%d %H:%M").replace(tzinfo=JST)
            except ValueError:
                continue

            time_left = deadline - now
            
            if time_left.total_seconds() < 0:
                continue

            remind_msg = None
            update_cell = None

            if time_left <= timedelta(hours=12) and flag_12h == "0":
                remind_msg = "打ち上げ締切まであと12時間です！"
                update_cell = 'AE1'
            elif time_left <= timedelta(days=3) and flag_3d == "0":
                remind_msg = "打ち上げ締切まであと3日です！"
                update_cell = 'AD1'
            elif time_left <= timedelta(days=7) and flag_7d == "0":
                remind_msg = "打ち上げ締切まであと7日です！"
                update_cell = 'AC1'

            if remind_msg and update_cell:
                channel = bot.get_channel(channel_id)
                if channel:
                    try:
                        await channel.send(content=remind_msg)
                        ws.update_acell(update_cell, "1")
                    except Exception as e:
                        print(f"通知の送信に失敗: {e}")

    except Exception as e:
        print(f"リマインダータスクのエラー: {e}")

# ==========================================
# RenderのWeb Service用：HTTPサーバー
# ==========================================
async def handle_ping(request):
    return web.Response(text="Bot is running!")

async def web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"Web server started on port {port}")

@bot.event
async def setup_hook():
    global action_queue
    action_queue = asyncio.Queue()
    
    bot.loop.create_task(web_server())
    bot.loop.create_task(process_queue())

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f'ログイン完了: {bot.user}')
    if not reminder_task.is_running():
        reminder_task.start() 

bot.run(TOKEN)
