import asyncio
from datetime import datetime, timedelta
import os
import sqlite3
import threading
import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
import uvicorn
import requests

# .env 파일 로드
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
MASTER_KEY = os.getenv("MASTER_KEY", "KLKLKLKL-00000-11193828-KKKKK-JHDNXBBVIDLSJ")
PORT = int(os.getenv("PORT", "8000"))

# 데이터베이스 초기화 (DB 분리)
def init_db():
    conn_server = sqlite3.connect("server_backup.db")
    cursor_server = conn_server.cursor()
    cursor_server.execute(
        """
        CREATE TABLE IF NOT EXISTS servers (
            guild_id TEXT PRIMARY KEY,
            owner_id TEXT,
            channels TEXT,
            roles TEXT,
            created_at TEXT
        )
    """
    )
    conn_server.commit()
    conn_server.close()

    conn_bot = sqlite3.connect("bot_system.db")
    cursor_bot = conn_bot.cursor()
    cursor_bot.execute(
        """
        CREATE TABLE IF NOT EXISTS guild_settings (
            guild_id TEXT PRIMARY KEY,
            registered_user_id TEXT,
            license_expire TEXT,
            auth_log_channel_id TEXT,
            recovery_log_channel_id TEXT,
            auth_target_role_id TEXT,
            auth_desc TEXT,
            auth_btn_name TEXT,
            recovery_desc TEXT,
            attempt_count INTEGER DEFAULT 0,
            success_count INTEGER DEFAULT 0,
            dup_count INTEGER DEFAULT 0,
            fail_count INTEGER DEFAULT 0
        )
    """
    )
    cursor_bot.execute(
        """
        CREATE TABLE IF NOT EXISTS license_keys (
            key_string TEXT PRIMARY KEY,
            days INTEGER,
            is_used INTEGER DEFAULT 0
        )
    """
    )
    cursor_bot.execute(
        """
        CREATE TABLE IF NOT EXISTS recovery_keys (
            key_string TEXT PRIMARY KEY,
            guild_id TEXT,
            max_uses INTEGER,
            used_count INTEGER DEFAULT 0
        )
    """
    )
    cursor_bot.execute(
        """
        CREATE TABLE IF NOT EXISTS verified_users (
            guild_id TEXT,
            user_id TEXT,
            PRIMARY KEY (guild_id, user_id)
        )
    """
    )
    cursor_bot.execute(
        """
        CREATE TABLE IF NOT EXISTS extension_keys (
            key_string TEXT PRIMARY KEY,
            days INTEGER
        )
    """
    )
    conn_bot.commit()
    conn_bot.close()

init_db()

# ----------------- FastAPI 웹서버 (OAuth2 Redirect URL 처리) -----------------
app = FastAPI()

@app.get("/")
async def root():
    return {"status": "Discord Recovery Bot API is running smoothly."}

@app.get("/callback")
async def oauth_callback(code: str, state: str):
    guild_id = state  # state = 서버 고유 ID

    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    
    resp = requests.post("https://discord.com/api/oauth2/token", data=data, headers=headers)
    if resp.status_code != 200:
        return HTMLResponse("""
        <html>
        <head><meta charset="utf-8"><title>인증 실패</title></head>
        <body style="background-color: #121212; color: #ffffff; font-family: sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0;">
            <div style="background: #1e1e1e; padding: 40px; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.5); text-align: center; max-width: 400px;">
                <h2 style="color: #ff5252;">❌ 인증 실패</h2>
                <p style="color: #b0b0b0;">토큰을 발급받지 못했습니다. 다시 시도해주세요.</p>
            </div>
        </body>
        </html>
        """, status_code=400)
    
    token_json = resp.json()
    access_token = token_json.get("access_token")

    user_resp = requests.get(
        "https://discord.com/api/users/@me",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    if user_resp.status_code != 200:
        return HTMLResponse("""
        <html>
        <head><meta charset="utf-8"><title>인증 실패</title></head>
        <body style="background-color: #121212; color: #ffffff; font-family: sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0;">
            <div style="background: #1e1e1e; padding: 40px; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.5); text-align: center; max-width: 400px;">
                <h2 style="color: #ff5252;">❌ 인증 실패</h2>
                <p style="color: #b0b0b0;">유저 정보를 불러오지 못했습니다.</p>
            </div>
        </body>
        </html>
        """, status_code=400)
    
    user_data = user_resp.json()
    user_id = user_data.get("id")
    username = user_data.get("username")
    discriminator = user_data.get("discriminator", "0")
    full_username = f"{username}#{discriminator}" if discriminator != "0" else username
    is_bot_flag = user_data.get("bot", False)
    
    snowflake = int(user_id)
    created_at_timestamp = ((snowflake >> 22) + 1420070400000) / 1000
    created_at_dt = datetime.fromtimestamp(created_at_timestamp)

    conn = sqlite3.connect("bot_system.db")
    cursor = conn.cursor()

    cursor.execute("UPDATE guild_settings SET attempt_count = attempt_count + 1 WHERE guild_id = ?", (guild_id,))

    cursor.execute("SELECT 1 FROM verified_users WHERE guild_id = ? AND user_id = ?", (guild_id, str(user_id)))
    if cursor.fetchone():
        cursor.execute("UPDATE guild_settings SET dup_count = dup_count + 1 WHERE guild_id = ?", (guild_id,))
        conn.commit()
        conn.close()
        return HTMLResponse("""
        <html>
        <head><meta charset="utf-8"><title>인증 중복</title></head>
        <body style="background-color: #121212; color: #ffffff; font-family: sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0;">
            <div style="background: #1e1e1e; padding: 40px; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.5); text-align: center; max-width: 400px;">
                <h2 style="color: #ffa726;">⚠️ 이미 인증된 계정</h2>
                <p style="color: #b0b0b0;">이미 해당 서버에서 인증을 완료한 계정입니다.</p>
            </div>
        </body>
        </html>
        """)

    cursor.execute("INSERT INTO verified_users (guild_id, user_id) VALUES (?, ?)", (guild_id, str(user_id)))
    cursor.execute("UPDATE guild_settings SET success_count = success_count + 1 WHERE guild_id = ?", (guild_id,))

    cursor.execute("SELECT auth_log_channel_id, auth_target_role_id FROM guild_settings WHERE guild_id = ?", (guild_id,))
    settings = cursor.fetchone()
    conn.commit()
    conn.close()

    guild = bot.get_guild(int(guild_id))
    if guild:
        member = guild.get_member(int(user_id))
        if not member:
            try:
                member = await guild.fetch_member(int(user_id))
            except Exception:
                member = None

        if settings and settings[1] and member:
            role = guild.get_role(int(settings[1]))
            if role:
                try:
                    await member.add_roles(role)
                except Exception:
                    pass

        is_suspicious = ":white_check_mark:" if (datetime.now() - created_at_dt).days < 7 else ":x:"
        joined_at_str = member.joined_at.strftime("%Y년 %m월 %d일") if member and member.joined_at else "알 수 없음"
        bot_status_str = "봇" if is_bot_flag else "유저"
        invite_code_str = "(웹 인증 완료)"

        log_text = (
            f"@{full_username} 가 인증했습니다\n"
            f"- **USER ID:** `{user_id}`\n"
            f"- **계정 생성일:** {created_at_dt.strftime('%Y년 %m월 %d일')}\n"
            f"- **부계정 의심:** {is_suspicious}\n"
            f"- **서버 접속일:** {joined_at_str}\n"
            f"- **봇 여부:** {bot_status_str}\n"
            f"- **초대링크:** `{invite_code_str}`"
        )

        if settings and settings[0]:
            log_chan = guild.get_channel(int(settings[0]))
            if log_chan:
                await log_chan.send(log_text)

    return HTMLResponse("""
    <html>
    <head><meta charset="utf-8"><title>인증 완료</title></head>
    <body style="background-color: #121212; color: #ffffff; font-family: sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0;">
        <div style="background: #1e1e1e; padding: 40px; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.5); text-align: center; max-width: 400px;">
            <h2 style="color: #66bb6a;">✅ 인증 완료!</h2>
            <p style="color: #b0b0b0;">인증이 성공적으로 완료되었습니다. 이 창을 닫으셔도 됩니다.</p>
        </div>
    </body>
    </html>
    """)


# ----------------- 디스코드 봇 설정 (discord.py) -----------------
intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.messages = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

def is_registered_or_owner():
    async def predicate(interaction: discord.Interaction):
        if interaction.user.id == OWNER_ID:
            return True
        conn = sqlite3.connect("bot_system.db")
        cursor = conn.cursor()
        cursor.execute(
            "SELECT registered_user_id FROM guild_settings WHERE guild_id = ?",
            (str(interaction.guild_id),),
        )
        res = cursor.fetchone()
        conn.close()
        if res and str(res[0]) == str(interaction.user.id):
            return True
        await interaction.response.send_message(
            "❌ 이 서버에서 `/복구봇등록`을 진행한 사용자만 사용할 수 있는 명령어입니다.",
            ephemeral=True,
        )
        return False
    return app_commands.check(predicate)

@bot.event
async def on_ready():
    print(f"디스코드 봇 로그인 완료: {bot.user.name} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"슬래시 명령어 {len(synced)}개 동기화 완료.")
    except Exception as e:
        print(e)
    check_licenses.start()

@tasks.loop(hours=24)
async def check_licenses():
    conn = sqlite3.connect("bot_system.db")
    cursor = conn.cursor()
    cursor.execute("SELECT guild_id, registered_user_id, license_expire FROM guild_settings")
    rows = cursor.fetchall()
    conn.close()

    now = datetime.now()
    for guild_id, reg_user_id, expire_str in rows:
        if not expire_str or expire_str == "무제한":
            continue
        try:
            expire_date = datetime.strptime(expire_str, "%Y-%m-%d")
        except ValueError:
            continue
        diff = (expire_date - now).days
        if diff == 3:  
            try:
                user = await bot.fetch_user(int(reg_user_id))
                guild = bot.get_guild(int(guild_id))
                guild_name = guild.name if guild else "알 수 없는 서버"
                await user.send(f"⚠️ **[라이센스 경고]** 관리 중이신 **{guild_name}** 서버의 복구봇 라이센스가 **3일 뒤({expire_str}) 만료**됩니다. 연장키를 통해 연장해주세요!")
            except Exception:
                pass


# ----------------- 슬래시 명령어 -----------------

@bot.tree.command(name="복구봇등록", description="license_key를 입력하여 서버에 복구봇을 등록합니다.")
@app_commands.describe(license_key="발급받은 라이센스 키 또는 마스터키 입력")
async def register_bot(interaction: discord.Interaction, license_key: str):
    guild_id = str(interaction.guild_id)
    user_id = str(interaction.user.id)

    expire_date = ""
    display_period = ""

    if license_key == MASTER_KEY:
        expire_date = "무제한"
        display_period = "무제한 (마스터키)"
    else:
        conn = sqlite3.connect("bot_system.db")
        cursor = conn.cursor()
        cursor.execute("SELECT days, is_used FROM license_keys WHERE key_string = ?", (license_key,))
        row = cursor.fetchone()

        if not row:
            conn.close()
            await interaction.response.send_message("❌ 유효하지 않은 라이센스 키입니다.", ephemeral=True)
            return
        
        if row[1] == 1:
            conn.close()
            await interaction.response.send_message("❌ 이미 사용된(소모된) 라이센스 키입니다.", ephemeral=True)
            return

        days = row[0]
        expire_date = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
        display_period = f"{days}일"

        cursor.execute("UPDATE license_keys SET is_used = 1 WHERE key_string = ?", (license_key,))
        conn.commit()
        conn.close()

    conn = sqlite3.connect("bot_system.db")
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO guild_settings (guild_id, registered_user_id, license_expire)
        VALUES (?, ?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET registered_user_id = ?, license_expire = ?
    """,
        (guild_id, user_id, expire_date, user_id, expire_date),
    )
    conn.commit()
    conn.close()

    await interaction.response.send_message(
        f"✅ 복구봇이 성공적으로 등록되었습니다!\n- **등록자:** {interaction.user.mention}\n- **부여된 기간:** {display_period}\n- **라이센스 만료일:** {expire_date}",
        ephemeral=True,
    )


@bot.tree.command(name="라이센스발급", description="[Owner 전용] 지정한 일수만큼 사용할 수 있는 라이센스 키를 발급합니다.")
@app_commands.describe(일수="해당 키로 등록 시 부여할 일수")
async def issue_license(interaction: discord.Interaction, 일수: int):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 봇 소유자(Owner)만 사용할 수 있는 명령어입니다.", ephemeral=True)
        return

    import random, string
    lic_key = ''.join(random.choices(string.ascii_uppercase + string.digits, k=16))

    conn = sqlite3.connect("bot_system.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO license_keys (key_string, days, is_used) VALUES (?, ?, 0)", (lic_key, 일수))
    conn.commit()
    conn.close()

    await interaction.response.send_message(
        f"✅ 라이센스 키가 발급되었습니다!\n- **키 (`license_key`):** `{lic_key}`\n- **부여 일수:** {일수}일",
        ephemeral=True,
    )


@bot.tree.command(name="연장키", description="[Owner 전용] 라이센스 연장키를 발급합니다.")
@app_commands.describe(일수="연장할 일수")
async def extend_key_admin(interaction: discord.Interaction, 일수: int):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 봇 소유자(Owner)만 사용할 수 있습니다.", ephemeral=True)
        return
    
    import random, string
    ext_key = ''.join(random.choices(string.ascii_uppercase + string.digits, k=16))
    
    conn = sqlite3.connect("bot_system.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO extension_keys (key_string, days) VALUES (?, ?)", (ext_key, 일수))
    conn.commit()
    conn.close()

    await interaction.response.send_message(f"✅ 연장키가 생성되었습니다!\n- **키:** `{ext_key}`\n- **연장 일수:** {일수}일", ephemeral=True)


@bot.tree.command(name="서버저장", description="현재 서버의 구조를 백업 저장합니다.")
@app_commands.describe(id="백업을 구분할 서버 고유 ID")
@is_registered_or_owner()
async def save_server(interaction: discord.Interaction, id: str):
    guild = interaction.guild
    conn = sqlite3.connect("bot_system.db")
    cursor = conn.cursor()
    cursor.execute("SELECT license_expire FROM guild_settings WHERE guild_id = ?", (str(guild.id),))
    res = cursor.fetchone()
    conn.close()

    if not res or not res[0]:
        await interaction.response.send_message("❌ 라이센스가 등록되지 않았습니다.", ephemeral=True)
        return
    
    if res[0] != "무제한" and datetime.strptime(res[0], "%Y-%m-%d") < datetime.now():
        await interaction.response.send_message("❌ 라이센스가 만료되었습니다.", ephemeral=True)
        return

    import json
    channels_data = [{"name": c.name, "type": str(c.type), "category": c.category.name if c.category else None} for c in guild.channels]
    roles_data = [{"name": r.name, "color": r.color.value, "permissions": r.permissions.value} for r in guild.roles]

    conn_s = sqlite3.connect("server_backup.db")
    cursor_s = conn_s.cursor()
    cursor_s.execute("""
        INSERT INTO servers (guild_id, owner_id, channels, roles, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET channels = ?, roles = ?, created_at = ?
    """, (id, str(interaction.user.id), json.dumps(channels_data), json.dumps(roles_data), str(datetime.now()), json.dumps(channels_data), json.dumps(roles_data), str(datetime.now())))
    conn_s.commit()
    conn_s.close()

    await interaction.response.send_message(f"✅ 서버 구조가 성공적으로 저장되었습니다! (ID: `{id}`)", ephemeral=True)


@bot.tree.command(name="섭붙여넣기", description="저장된 서버 구조를 현재 서버에 불러옵니다.")
@app_commands.describe(id="붙여넣을 서버 고유 ID")
@is_registered_or_owner()
async def paste_server(interaction: discord.Interaction, id: str):
    conn_s = sqlite3.connect("server_backup.db")
    cursor_s = conn_s.cursor()
    cursor_s.execute("SELECT owner_id, channels, roles FROM servers WHERE guild_id = ?", (id,))
    row = cursor_s.fetchone()
    conn_s.close()

    if not row:
        await interaction.response.send_message("❌ 일치하는 저장된 서버 ID가 없습니다.", ephemeral=True)
        return

    saved_owner_id, channels_json, roles_json = row
    if str(interaction.user.id) != str(saved_owner_id) and interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 자신이 저장한 서버 데이터가 아니므로 거부되었습니다.", ephemeral=True)
        return

    await interaction.response.send_message(f"🔄 서버 ID `{id}`의 구조를 불러오는 중입니다...", ephemeral=True)


@bot.tree.command(name="복구키", description="지정한 인원수만큼 복구키를 생성합니다.")
@app_commands.describe(인원="생성할 복구키의 인원수")
@is_registered_or_owner()
async def create_recovery_key(interaction: discord.Interaction, 인원: int):
    import random, string
    key_str = ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))
    guild_id = str(interaction.guild_id)

    conn = sqlite3.connect("bot_system.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO recovery_keys (key_string, guild_id, max_uses) VALUES (?, ?, ?)", (key_str, guild_id, 인원))
    conn.commit()
    conn.close()

    await interaction.response.send_message(f"✅ 복구키가 생성되었습니다.\n- **키:** `{key_str}`\n- **가능 인원:** {인원}명", ephemeral=True)


@bot.tree.command(name="인원확인", description="현재 DB에 저장된 복구 가능한 잔여 인원 및 통계를 확인합니다.")
@is_registered_or_owner()
async def check_users(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    conn = sqlite3.connect("bot_system.db")
    cursor = conn.cursor()
    cursor.execute("SELECT attempt_count, success_count, dup_count, fail_count FROM guild_settings WHERE guild_id = ?", (guild_id,))
    row = cursor.fetchone()
    
    cursor.execute("SELECT SUM(max_uses - used_count) FROM recovery_keys WHERE guild_id = ?", (guild_id,))
    key_res = cursor.fetchone()
    conn.close()

    att = row[0] if row else 0
    suc = row[1] if row else 0
    dup = row[2] if row else 0
    fail = row[3] if row else 0
    rem_keys = key_res[0] if key_res and key_res[0] else 0

    embed = discord.Embed(title="📊 복구 시스템 통계 및 인원 현황", color=discord.Color.blue())
    embed.add_field(name="남은 복구키 허용 인원", value=f"{rem_keys}명", inline=False)
    embed.add_field(name="시도 인원", value=f"{att}명", inline=True)
    embed.add_field(name="성공 인원", value=f"{suc}명", inline=True)
    embed.add_field(name="중복 인원", value=f"{dup}명", inline=True)
    embed.add_field(name="실패 인원", value=f"{fail}명", inline=True)

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="설정", description="인증 및 복구 관련 채널, 설명, 역할, 버튼 이름을 설정합니다.")
@app_commands.describe(
    인증로그채널="인증 로그가 전송될 채널",
    복구로그채널="복구 로그가 전송될 채널",
    인증설명="인증 패널 설명",
    복구설명="복구 패널 설명",
    인증부여역할="인증 성공 시 지급할 역할",
    인증버튼이름="인증 패널 버튼 이름"
)
@is_registered_or_owner()
async def set_config(
    interaction: discord.Interaction, 
    인증로그채널: discord.TextChannel, 
    복구로그채널: discord.TextChannel, 
    인증설명: str, 
    복구설명: str, 
    인증부여역할: discord.Role, 
    인증버튼이름: str
):
    guild_id = str(interaction.guild_id)
    conn = sqlite3.connect("bot_system.db")
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE guild_settings 
        SET auth_log_channel_id = ?, recovery_log_channel_id = ?, auth_desc = ?, recovery_desc = ?, auth_target_role_id = ?, auth_btn_name = ?
        WHERE guild_id = ?
    """, (str(인증로그채널.id), str(복구로그채널.id), 인증설명, 복구설명, str(인증부여역할.id), 인증버튼이름, guild_id))
    conn.commit()
    conn.close()

    await interaction.response.send_message("✅ 설정이 성공적으로 저장되었습니다.", ephemeral=True)


# 1. /인증패널: 웹사이트 인증 링크로 이동하는 패널 (회색 버튼)
@bot.tree.command(name="인증패널", description="웹사이트 인증을 진행할 수 있는 패널을 생성합니다.")
@is_registered_or_owner()
async def auth_panel(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    conn = sqlite3.connect("bot_system.db")
    cursor = conn.cursor()
    cursor.execute("SELECT auth_desc, auth_btn_name FROM guild_settings WHERE guild_id = ?", (guild_id,))
    row = cursor.fetchone()
    conn.close()

    desc = row[0] if row and row[0] else "아래 버튼을 눌러 인증을 진행하세요."
    btn_text = row[1] if row and row[1] else "인증하기"

    embed = discord.Embed(
        title="🛡️ 서버 인증하기",
        description=desc,
        color=discord.Color.blurple()
    )

    class AuthPanelView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)

        @discord.ui.button(label=btn_text, style=discord.ButtonStyle.secondary, custom_id="web_auth_button")
        async def web_auth(self, interaction: discord.Interaction, button: discord.ui.Button):
            oauth_url = f"https://discord.com/api/oauth2/authorize?client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}&response_type=code&scope=identify guilds.join&state={guild_id}"
            await interaction.response.send_message(f"🔗 아래 링크를 눌러 로그인을 진행해주세요:\n{oauth_url}", ephemeral=True)

    await interaction.channel.send(embed=embed, view=AuthPanelView())
    await interaction.response.send_message("✅ 인증 패널이 생성되었습니다.", ephemeral=True)


# 2. /복구패널: 복구키 이용하기 및 라이센스 연장 패널 (회색 버튼, 조건부 표시)
@bot.tree.command(name="복구패널", description="복구키를 사용하여 인원을 복구하는 패널을 생성합니다.")
@is_registered_or_owner()
async def recovery_panel(interaction: discord.Interaction):
    guild_id = str(interaction.guild_id)
    conn = sqlite3.connect("bot_system.db")
    cursor = conn.cursor()
    cursor.execute("SELECT recovery_desc, license_expire FROM guild_settings WHERE guild_id = ?", (guild_id,))
    row = cursor.fetchone()
    conn.close()

    desc = row[0] if row and row[0] else "아래 버튼을 눌러 복구키를 입력하고 서버 인원을 복구하세요."
    license_expire = row[1] if row else ""

    embed = discord.Embed(
        title="🔄 복구키 사용하기",
        description=desc,
        color=discord.Color.dark_grey()
    )

    class RecoveryPanelView(discord.ui.View):
        def __init__(self, show_extend: bool):
            super().__init__(timeout=None)

            # 복구키 사용하기 버튼 (회색)
            self.add_item(RecoveryButton())

            # 마스터키 또는 조건 충족 시 라이센스 연장 버튼 추가 (회색)
            if show_extend:
                self.add_item(ExtendButton())

    class RecoveryButton(discord.ui.Button):
        def __init__(self):
            super().__init__(label="복구키 사용하기", style=discord.ButtonStyle.secondary, custom_id="open_recovery_modal")

        async def callback(self, interaction: discord.Interaction):
            await interaction.response.send_modal(RecoveryModal())

    class ExtendButton(discord.ui.Button):
        def __init__(self):
            super().__init__(label="라이센스 연장하기", style=discord.ButtonStyle.secondary, custom_id="license_extend_button")

        async def callback(self, interaction: discord.Interaction):
            await interaction.response.send_modal(LicenseExtendModal())

    # 마스터키(무제한)이거나 Owner인 경우에만 연장 버튼 표시
    is_master = (license_expire == "무제한") or (interaction.user.id == OWNER_ID)

    await interaction.channel.send(embed=embed, view=RecoveryPanelView(show_extend=is_master))
    await interaction.response.send_message("✅ 복구 패널이 생성되었습니다.", ephemeral=True)


# 복구키 입력 모달 (팝업창)
class RecoveryModal(discord.ui.Modal, title="서버 복구 인증"):
    key_input = discord.ui.TextInput(label="복구키", placeholder="발급받은 복구키를 입력하세요", required=True)
    link_input = discord.ui.TextInput(label="서버 초대링크", placeholder="접속할 서버 링크 혹은 뒷자리", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild_id)
        user = interaction.user
        key_val = self.key_input.value.strip()

        conn = sqlite3.connect("bot_system.db")
        cursor = conn.cursor()

        cursor.execute("UPDATE guild_settings SET attempt_count = attempt_count + 1 WHERE guild_id = ?", (guild_id,))

        cursor.execute("SELECT 1 FROM verified_users WHERE guild_id = ? AND user_id = ?", (guild_id, str(user.id)))
        if cursor.fetchone():
            cursor.execute("UPDATE guild_settings SET dup_count = dup_count + 1 WHERE guild_id = ?", (guild_id,))
            conn.commit()
            conn.close()
            await interaction.response.send_message("❌ 이미 해당 서버에서 복구/인증을 완료한 계정입니다. (중복)", ephemeral=True)
            return

        cursor.execute("SELECT max_uses, used_count FROM recovery_keys WHERE key_string = ? AND guild_id = ?", (key_val, guild_id))
        key_row = cursor.fetchone()

        if not key_row or key_row[1] >= key_row[0]:
            cursor.execute("UPDATE guild_settings SET fail_count = fail_count + 1 WHERE guild_id = ?", (guild_id,))
            conn.commit()
            conn.close()
            await interaction.response.send_message("❌ 유효하지 않거나 사용 횟수가 초과된 복구키입니다.", ephemeral=True)
            return

        cursor.execute("UPDATE recovery_keys SET used_count = used_count + 1 WHERE key_string = ?", (key_val,))
        cursor.execute("INSERT INTO verified_users (guild_id, user_id) VALUES (?, ?)", (guild_id, str(user.id)))
        cursor.execute("UPDATE guild_settings SET success_count = success_count + 1 WHERE guild_id = ?", (guild_id,))
        
        cursor.execute("SELECT recovery_log_channel_id, auth_target_role_id FROM guild_settings WHERE guild_id = ?", (guild_id,))
        settings = cursor.fetchone()
        conn.commit()
        conn.close()

        if settings and settings[1]:
            role = interaction.guild.get_role(int(settings[1]))
            if role:
                try:
                    await user.add_roles(role)
                except Exception:
                    pass

        created_at = user.created_at.strftime("%Y년 %m월 %d일")
        joined_at = user.joined_at.strftime("%Y년 %m월 %d일") if user.joined_at else "알 수 없음"
        is_bot = "봇" if user.bot else "유저"
        is_suspicious = ":white_check_mark:" if (datetime.now(user.created_at.tzinfo) - user.created_at).days < 7 else ":x:"
        invite_code = self.link_input.value.strip().split("/")[-1]

        log_text = (
            f"{user.mention} 가 인증했습니다\n"
            f"- **USER ID:** `{user.id}`\n"
            f"- **계정 생성일:** {created_at}\n"
            f"- **부계정 의심:** {is_suspicious}\n"
            f"- **서버 접속일:** {joined_at}\n"
            f"- **봇 여부:** {is_bot}\n"
            f"- **초대링크:** `{invite_code}`"
        )

        if settings and settings[0]:
            log_chan = interaction.guild.get_channel(int(settings[0]))
            if log_chan:
                await log_chan.send(log_text)

        await interaction.response.send_message("✅ 복구 인증이 성공적으로 완료되었습니다!", ephemeral=True)


class LicenseExtendModal(discord.ui.Modal, title="라이센스 연장"):
    ext_key_input = discord.ui.TextInput(label="연장키 입력", placeholder="소유자에게 받은 연장키를 입력하세요", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        key_val = self.ext_key_input.value.strip()
        guild_id = str(interaction.guild_id)

        conn = sqlite3.connect("bot_system.db")
        cursor = conn.cursor()
        
        cursor.execute("SELECT days FROM extension_keys WHERE key_string = ?", (key_val,))
        res = cursor.fetchone()
        if not res:
            conn.close()
            await interaction.response.send_message("❌ 유효하지 않은 연장키입니다.", ephemeral=True)
            return

        add_days = res[0]
        cursor.execute("DELETE FROM extension_keys WHERE key_string = ?", (key_val,))

        cursor.execute("SELECT license_expire FROM guild_settings WHERE guild_id = ?", (guild_id,))
        g_res = cursor.fetchone()
        
        now = datetime.now()
        if g_res and g_res[0]:
            if g_res[0] == "무제한":
                new_expire = "무제한"
            else:
                current_expire = datetime.strptime(g_res[0], "%Y-%m-%d")
                base_date = current_expire if current_expire > now else now
                new_expire = (base_date + timedelta(days=add_days)).strftime("%Y-%m-%d")
        else:
            new_expire = (now + timedelta(days=add_days)).strftime("%Y-%m-%d")

        cursor.execute(
            """
            INSERT INTO guild_settings (guild_id, license_expire)
            VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET license_expire = ?
        """,
            (guild_id, new_expire, new_expire),
        )
        conn.commit()
        conn.close()

        await interaction.response.send_message(f"✅ 라이센스가 성공적으로 연장되었습니다!\n- **추가된 일수:** {add_days}일\n- **새로운 만료일:** {new_expire}", ephemeral=True)


def run_fastapi():
    uvicorn.run(app, host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    fastapi_thread = threading.Thread(target=run_fastapi, daemon=True)
    fastapi_thread.start()
    bot.run(TOKEN)
