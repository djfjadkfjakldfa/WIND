import sqlite3
import os
import threading
import urllib.parse
import time
from datetime import datetime
from flask import Flask, request, render_template_string
import discord
from discord import app_commands
from discord.ext import commands
import requests
from dotenv import load_dotenv
import random
import string

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI", "http://localhost:5000/callback")
WEB_PORT = int(os.getenv("WEB_PORT", 5000))
OWNER_ID = int(os.getenv("OWNER_ID", 0))

# DB 초기화 및 컬럼 마이그레이션 처리
def init_db():
    conn = sqlite3.connect('bot.db', check_same_thread=False)
    conn.text_factory = str
    cursor = conn.cursor()
    cursor.execute("PRAGMA encoding = 'UTF-8';")
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS config (
                        guild_id INTEGER PRIMARY KEY, 
                        auth_channel INTEGER, 
                        auth_role INTEGER, 
                        auth_log_channel INTEGER,
                        recovery_channel INTEGER)''')
    
    try:
        cursor.execute("ALTER TABLE config ADD COLUMN auth_log_channel INTEGER;")
    except sqlite3.OperationalError:
        pass

    cursor.execute('''CREATE TABLE IF NOT EXISTS users (
                        discord_id TEXT PRIMARY KEY, 
                        access_token TEXT, 
                        refresh_token TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS recovery_keys (
                        key_str TEXT PRIMARY KEY, 
                        max_uses INTEGER, 
                        used_count INTEGER)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS recovery_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, 
                        key_str TEXT, 
                        total INT, 
                        success INT, 
                        failed INT, 
                        duplicate INT, 
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    return conn, cursor

conn, cursor = init_db()

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

app = Flask(__name__)

SUCCESS_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>인증 성공</title>
    <style>
        body { font-family: Arial, sans-serif; background-color: #313338; color: #dbdee1; text-align: center; padding-top: 100px; }
        .box { background: #2b2d31; padding: 40px; border-radius: 10px; display: inline-block; box-shadow: 0 4px 10px rgba(0,0,0,0.3); }
        h1 { color: #5865F2; }
    </style>
</head>
<body>
    <div class="box">
        <h1>인증 성공! ✅</h1>
        <p>디스코드 계정이 성공적으로 인증되었습니다.</p>
        <p>이 창을 닫고 디스코드로 돌아가셔도 됩니다.</p>
    </div>
</body>
</html>
"""

@app.route("/callback")
def oauth_callback():
    code = request.args.get("code")
    if not code:
        return "인증 코드가 누락되었습니다.", 400

    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    res = requests.post("https://discord.com/api/oauth2/token", data=data, headers=headers)
    if res.status_code != 200:
        return "토큰을 가져오지 못했습니다.", 400
    
    token_json = res.json()
    access_token = token_json.get("access_token")
    refresh_token = token_json.get("refresh_token")

    user_res = requests.get("https://discord.com/api/users/@me", headers={"Authorization": f"Bearer {access_token}"})
    if user_res.status_code != 200:
        return "유저 정보를 가져오지 못했습니다.", 400
    
    user_data = user_res.json()
    user_id = user_data.get("id")

    cursor.execute("REPLACE INTO users (discord_id, access_token, refresh_token) VALUES (?, ?, ?)", (user_id, access_token, refresh_token))
    conn.commit()

    try:
        cursor.execute("SELECT guild_id, auth_role, auth_log_channel FROM config WHERE auth_role IS NOT NULL LIMIT 1")
        cfg_row = cursor.fetchone()
        if cfg_row:
            guild_id, auth_role_id, auth_log_channel_id = cfg_row
            
            headers_bot = {"Authorization": f"Bot {DISCORD_TOKEN}", "Content-Type": "application/json"}
            
            add_url = f"https://discord.com/api/guilds/{guild_id}/members/{user_id}"
            requests.put(add_url, json={"access_token": access_token}, headers=headers_bot)
            
            time.sleep(0.5)
            
            role_url = f"https://discord.com/api/guilds/{guild_id}/members/{user_id}/roles/{auth_role_id}"
            requests.put(role_url, headers=headers_bot)

            if auth_log_channel_id:
                log_channel = bot.get_channel(auth_log_channel_id)
                if log_channel:
                    created_at_timestamp = ((int(user_id) >> 22) + 1420070400000) / 1000
                    created_at = datetime.fromtimestamp(created_at_timestamp).strftime('%Y-%m-%d %H:%M:%S')
                    verified_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

                    embed = discord.Embed(title="📝 새로운 인증 로그", color=0x5865F2, timestamp=discord.utils.utcnow())
                    embed.add_field(name="유저 ID", value=f"`{user_id}` (<@{user_id}>)", inline=False)
                    embed.add_field(name="계정 생성일", value=created_at, inline=True)
                    embed.add_field(name="인증 시간", value=verified_at, inline=True)
                    
                    bot.loop.create_task(log_channel.send(embed=embed))
    except Exception as e:
        print(f"콜백 처리 오류: {e}")

    return render_template_string(SUCCESS_PAGE)

def run_flask():
    app.run(host="0.0.0.0", port=WEB_PORT, debug=False, use_reloader=False)

# 복구키 입력 모달 (누구나 버튼을 눌러 사용할 수 있도록 제한 해제)
class RecoveryModal(discord.ui.Modal, title="복구키 사용"):
    key_input = discord.ui.TextInput(label="복구키", placeholder="복구키를 입력하세요 (예: REC-XXXX)", required=True)
    server_invite = discord.ui.TextInput(label="서버 초대 링크 또는 코드", placeholder="예: https://discord.gg/코드", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        key_str = self.key_input.value.strip()
        invite_input = self.server_invite.value.strip()
        invite_code = invite_input.split("/")[-1]

        cursor.execute("SELECT max_uses, used_count FROM recovery_keys WHERE key_str = ?", (key_str,))
        row = cursor.fetchone()
        if not row:
            await interaction.followup.send("❌ 존재하지 않는 유효하지 않은 복구키입니다.", ephemeral=True)
            return
        
        max_uses, used_count = row
        remaining = max_uses - used_count
        if remaining <= 0:
            await interaction.followup.send("❌ 이미 모두 소진된 복구키입니다.", ephemeral=True)
            return

        cursor.execute("SELECT discord_id, access_token FROM users LIMIT ?", (remaining,))
        users_to_restore = cursor.fetchall()

        if not users_to_restore:
            await interaction.followup.send("❌ 데이터베이스에 복구할 수 있는 유저가 없습니다.", ephemeral=True)
            return

        try:
            invite = await bot.fetch_invite(invite_code)
            target_guild = invite.guild
        except Exception as e:
            await interaction.followup.send(f"❌ 유효하지 않은 서버 초대 링크입니다: {e}", ephemeral=True)
            return

        success_count = 0
        failed_count = 0
        duplicate_count = 0

        headers_bot = {"Authorization": f"Bot {DISCORD_TOKEN}", "Content-Type": "application/json"}

        for discord_id, access_token in users_to_restore:
            try:
                member_res = requests.get(f"https://discord.com/api/guilds/{target_guild.id}/members/{discord_id}", headers=headers_bot)
                if member_res.status_code == 200:
                    duplicate_count += 1
                    continue
            except:
                pass

            url = f"https://discord.com/api/guilds/{target_guild.id}/members/{discord_id}"
            payload = {"access_token": access_token}
            add_res = requests.put(url, json=payload, headers=headers_bot)

            if add_res.status_code in [201, 204]:
                success_count += 1
            else:
                failed_count += 1

        total_attempted = len(users_to_restore)

        cursor.execute("UPDATE recovery_keys SET used_count = used_count + ? WHERE key_str = ?", (success_count, key_str))
        cursor.execute("INSERT INTO recovery_logs (key_str, total, success, failed, duplicate) VALUES (?, ?, ?, ?, ?)",
                       (key_str, total_attempted, success_count, failed_count, duplicate_count))
        conn.commit()

        cursor.execute("SELECT recovery_channel FROM config WHERE guild_id = ?", (interaction.guild.id,))
        cfg = cursor.fetchone()
        if cfg and cfg[0]:
            log_channel = bot.get_channel(cfg[0])
            if log_channel:
                embed = discord.Embed(title="📊 서버 복구 로그", color=0x00FF00, timestamp=discord.utils.utcnow())
                embed.add_field(name="🔑 사용된 복구키", value=f"`{key_str}`", inline=False)
                embed.add_field(name="🎯 대상 서버", value=f"{target_guild.name} (`{target_guild.id}`)", inline=False)
                embed.add_field(name="📥 시도 인원", value=f"{total_attempted}명", inline=True)
                embed.add_field(name="✅ 성공", value=f"{success_count}명", inline=True)
                embed.add_field(name="❌ 실패", value=f"{failed_count}명", inline=True)
                embed.add_field(name="🔁 중복", value=f"{duplicate_count}명", inline=True)
                await log_channel.send(embed=embed)

        await interaction.followup.send(f"✅ 복구 작업이 완료되었습니다! 성공: {success_count}명, 실패: {failed_count}명, 중복: {duplicate_count}명", ephemeral=True)

class RecoveryPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    # 버튼은 누구나 누를 수 있도록 권한 제한 해제
    @discord.ui.button(label="복구키 사용하기", style=discord.ButtonStyle.secondary, custom_id="use_recovery_key_btn", emoji="🔑")
    async def use_key(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(RecoveryModal())

@bot.event
async def on_ready():
    print(f"로그인 완료: {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"총 {len(synced)}개의 슬래시 명령어를 동기화했습니다.")
    except Exception as e:
        print(e)

@bot.tree.command(name="인증설정", description="인증 패널 채널, 역할, 인증로그 채널을 설정합니다.")
@app_commands.describe(채널="인증 패널을 생성할 채널", 역할="인증 시 부여할 역할", 인증로그채널="인증 로그를 전송할 채널")
async def setup_auth(interaction: discord.Interaction, 채널: discord.TextChannel, 역할: discord.Role, 인증로그채널: discord.TextChannel):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 이 명령어는 봇 관리자만 사용할 수 있습니다.", ephemeral=True)
        return

    cursor.execute("""
        REPLACE INTO config (guild_id, auth_channel, auth_role, auth_log_channel, recovery_channel) 
        VALUES (?, ?, ?, ?, COALESCE((SELECT recovery_channel FROM config WHERE guild_id=?), ?))
    """, (interaction.guild.id, 채널.id, 역할.id, 인증로그채널.id, interaction.guild.id, None))
    conn.commit()
    await interaction.response.send_message(f"✅ 인증 설정이 저장되었습니다!\n- 패널 채널: {채널.mention}\n- 부여 역할: {역할.mention}\n- 로그 채널: {인증로그채널.mention}", ephemeral=True)

@bot.tree.command(name="인증패널", description="인증 패널을 생성합니다.")
async def auth_panel(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 이 명령어는 봇 관리자만 사용할 수 있습니다.", ephemeral=True)
        return

    cursor.execute("SELECT auth_channel FROM config WHERE guild_id = ?", (interaction.guild.id,))
    row = cursor.fetchone()
    if not row or not row[0]:
        await interaction.response.send_message("❌ 먼저 `/인증설정` 명령어를 실행해주세요.", ephemeral=True)
        return

    embed = discord.Embed(
        title="✨ 서버 인증 센터",
        description="아래 버튼을 눌러 인증을 진행해주세요.\n\n🔄 **상태:** 인증 중입니다... (인증 완료 시 역할이 부여됩니다)",
        color=0x2b2d31
    )
    
    encoded_redirect = urllib.parse.quote(REDIRECT_URI, safe='')
    oauth_url = f"https://discord.com/api/oauth2/authorize?client_id={CLIENT_ID}&redirect_uri={encoded_redirect}&response_type=code&scope=identify%20guilds.join"

    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="지금 인증하기", style=discord.ButtonStyle.link, url=oauth_url, emoji="🔄"))

    channel = interaction.guild.get_channel(row[0])
    if channel:
        await channel.send(embed=embed, view=view)
        await interaction.response.send_message(f"✅ {channel.mention} 채널에 인증 패널을 생성했습니다.", ephemeral=True)
    else:
        await interaction.response.send_message("❌ 채널을 찾을 수 없습니다.", ephemeral=True)

@bot.tree.command(name="인원확인", description="데이터베이스에 인증된 총 유저 수를 확인합니다.")
async def check_users(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 이 명령어는 봇 관리자만 사용할 수 있습니다.", ephemeral=True)
        return

    cursor.execute("SELECT COUNT(*) FROM users")
    count = cursor.fetchone()[0]
    await interaction.response.send_message(f"👥 데이터베이스에 인증된 총 유저 수: **{count}명**", ephemeral=True)

@bot.tree.command(name="복구키", description="특정 인원수만큼 복구할 수 있는 복구키를 생성합니다.")
@app_commands.describe(인원="이 복구키로 복구할 최대 인원수")
async def create_recovery_key(interaction: discord.Interaction, 인원: int):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 이 명령어는 봇 관리자만 사용할 수 있습니다.", ephemeral=True)
        return

    if 인원 <= 0:
        await interaction.response.send_message("❌ 복구 인원은 1명 이상이어야 합니다.", ephemeral=True)
        return
    
    key_str = "REC-" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    cursor.execute("INSERT INTO recovery_keys (key_str, max_uses, used_count) VALUES (?, ?, 0)", (key_str, 인원))
    conn.commit()

    embed = discord.Embed(title="🔑 복구키가 생성되었습니다", color=0xFEE75C)
    embed.add_field(name="복구키", value=f"`{key_str}`", inline=False)
    embed.add_field(name="최대 사용 가능 인원", value=f"{인원}명", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="복구키패널", description="복구키 사용 패널을 생성합니다.")
async def recovery_panel(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 이 명령어는 봇 관리자만 사용할 수 있습니다.", ephemeral=True)
        return

    cursor.execute("SELECT recovery_channel FROM config WHERE guild_id = ?", (interaction.guild.id,))
    row = cursor.fetchone()
    if not row or not row[0]:
        await interaction.response.send_message("❌ 복구로그 채널이 설정되어 있지 않습니다. 먼저 `/복구로그` 명령어로 채널을 설정해주세요.", ephemeral=True)
        return

    embed = discord.Embed(
        title="🔑 서버 복구 패널",
        description="아래 버튼을 눌러 복구키를 입력하고 서버 인원을 복구하세요.",
        color=0x57F287
    )
    view = RecoveryPanelView()
    await interaction.channel.send(embed=embed, view=view)
    await interaction.response.send_message("✅ 복구키 패널이 생성되었습니다.", ephemeral=True)

@bot.tree.command(name="복구로그", description="복구 로그를 전송할 채널을 설정합니다.")
@app_commands.describe(채널id="복구 로그를 받을 텍스트 채널")
async def set_recovery_log(interaction: discord.Interaction, 채널id: discord.TextChannel):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 이 명령어는 봇 관리자만 사용할 수 있습니다.", ephemeral=True)
        return

    cursor.execute("UPDATE config SET recovery_channel = ? WHERE guild_id = ?", (채널id.id, interaction.guild.id))
    if cursor.rowcount == 0:
        cursor.execute("INSERT INTO config (guild_id, recovery_channel) VALUES (?, ?)", (interaction.guild.id, 채널id.id))
    conn.commit()
    await interaction.response.send_message(f"✅ 복구로그 채널이 {채널id.mention} (으)로 설정되었습니다.", ephemeral=True)

if __name__ == "__main__":
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print(f"웹 서버(OAuth2 콜백)가 포트 {WEB_PORT}에서 실행 중입니다.")
    bot.run(DISCORD_TOKEN)