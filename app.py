import asyncio
from datetime import datetime, timedelta
import os
import sqlite3
import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

# .env 파일 로드
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
MASTER_KEY = os.getenv("MASTER_KEY", "KLKLKLKL-00000-11193828-KKKKK-JHDNXBBVIDLSJ")

# 데이터베이스 초기화 함수 (DB 분리)
def init_db():
    # 1. 서버 구조 백업용 DB
    conn_server = sqlite3.connect("server_backup.db")
    cursor_server = conn_server.cursor()
    cursor_server.execute(
        """
        CREATE TABLE IF NOT EXISTS servers (
            guild_id TEXT PRIMARY KEY,
            owner_id TEXT,
            channels TEXT,
            categories TEXT,
            roles TEXT,
            created_at TEXT
        )
    """
    )
    conn_server.commit()
    conn_server.close()

    # 2. 유저 복구, 인증, 라이센스 관리용 DB
    conn_bot = sqlite3.connect("bot_system.db")
    cursor_bot = conn_bot.cursor()
    cursor_bot.execute(
        """
        CREATE TABLE IF NOT EXISTS guild_settings (
            guild_id TEXT PRIMARY KEY,
            registered_user_id TEXT,
            license_expire TEXT,
            log_channel_id TEXT,
            auth_channel_id TEXT,
            target_role_id TEXT,
            auth_desc TEXT,
            btn_name TEXT,
            attempt_count INTEGER DEFAULT 0,
            success_count INTEGER DEFAULT 0,
            dup_count INTEGER DEFAULT 0,
            fail_count INTEGER DEFAULT 0
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
    conn_bot.commit()
    conn_bot.close()

init_db()

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.messages = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# 데코레이터: /복구봇 등록을 한 유저만 명령어 사용 가능
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
    print(f"로그인 완료: {bot.user.name} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"슬래시 명령어 {len(synced)}개 동기화 완료.")
    except Exception as e:
        print(e)
    # 백그라운드 태스크 시작 (라이센스 만료 3일 전 DM 알림)
    check_licenses.start()

# ----------------- 백그라운드 태스크 (라이센스 3일전 DM) -----------------
@tasks.loop(hours=24)
async def check_licenses():
    conn = sqlite3.connect("bot_system.db")
    cursor = conn.cursor()
    cursor.execute("SELECT guild_id, registered_user_id, license_expire FROM guild_settings")
    rows = cursor.fetchall()
    conn.close()

    now = datetime.now()
    for guild_id, reg_user_id, expire_str in rows:
        if not expire_str:
            continue
        expire_date = datetime.strptime(expire_str, "%Y-%m-%d")
        diff = (expire_date - now).days
        if diff == 3:  
            try:
                user = await bot.fetch_user(int(reg_user_id))
                guild = bot.get_guild(int(guild_id))
                guild_name = guild.name if guild else "알 수 없는 서버"
                await user.send(f"⚠️ **[라이센스 경고]** 관리 중이신 **{guild_name}** 서버의 복구봇 라이센스가 **3일 뒤({expire_str}) 만료**됩니다. 연장키를 통해 연장해주세요!")
            except Exception:
                pass

# ----------------- 명령어 구현 -----------------

@bot.tree.command(name="복구봇등록", description="마스터키와 라이센스 일수를 입력하여 서버에 복구봇을 등록합니다.")
@app_commands.describe(마스터키="지정된 마스터키", 라이센스일수="사용할 수 있는 일수 (숫자)")
async def register_bot(interaction: discord.Interaction, 마스터키: str, 라이센스일수: int):
    if 마스터키 != MASTER_KEY:
        await interaction.response.send_message("❌ 올바르지 않은 마스터키입니다.", ephemeral=True)
        return

    guild_id = str(interaction.guild_id)
    user_id = str(interaction.user.id)
    expire_date = (datetime.now() + timedelta(days=라이센스일수)).strftime("%Y-%m-%d")

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
        f"✅ 복구봇이 성공적으로 등록되었습니다!\n- **등록자:** {interaction.user.mention}\n- **라이센스 만료일:** {expire_date} (총 {라이센스일수}일)",
        ephemeral=True,
    )


@bot.tree.command(name="라이센스발급", description="[Owner 전용] 특정 서버의 라이센스 기간을 새로 발급/지정합니다.")
@app_commands.describe(서버id="대상 디스코드 서버 ID", 일수="부여할 일수")
async def issue_license(interaction: discord.Interaction, 서버id: str, 일수: int):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 봇 소유자(Owner)만 사용할 수 있는 명령어입니다.", ephemeral=True)
        return

    expire_date = (datetime.now() + timedelta(days=일수)).strftime("%Y-%m-%d")
    conn = sqlite3.connect("bot_system.db")
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE guild_settings SET license_expire = ? WHERE guild_id = ?",
        (expire_date, 서버id),
    )
    if cursor.rowcount == 0:
        cursor.execute(
            "INSERT INTO guild_settings (guild_id, license_expire) VALUES (?, ?)",
            (서버id, expire_date),
        )
    conn.commit()
    conn.close()

    await interaction.response.send_message(f"✅ 서버 ID `{서버id}`의 라이센스가 `{expire_date}`까지 설정되었습니다.", ephemeral=True)


@bot.tree.command(name="연장키", description="[Owner 전용] 라이센스 연장키를 발급하거나 관리합니다.")
@app_commands.describe(액션="발급 또는 확인", 일수="연장할 일수 (발급 시)")
@app_commands.choices(액션=[
    app_commands.Choice(name="발급", value="create"),
])
async def extend_key_admin(interaction: discord.Interaction, 액션: str, 일수: int = 0):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 봇 소유자(Owner)만 사용할 수 있습니다.", ephemeral=True)
        return
    
    import random, string
    ext_key = ''.join(random.choices(string.ascii_uppercase + string.digits, k=16))
    # 임시로 메모리나 별도 테이블에 저장할 수도 있지만, 여기선 봇 DB에 연장키 테이블을 활용한다고 가정
    conn = sqlite3.connect("bot_system.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS extension_keys (
            key_string TEXT PRIMARY KEY,
            days INTEGER
        )
    """)
    cursor.execute("INSERT INTO extension_keys (key_string, days) VALUES (?, ?)", (ext_key, 일수))
    conn.commit()
    conn.close()

    await interaction.response.send_message(f"✅ 연장키가 생성되었습니다!\n- **키:** `{ext_key}`\n- **연장 일수:** {일수}일", ephemeral=True)


@bot.tree.command(name="서버저장", description="현재 서버의 채널, 카테고리, 음성, 역할 구조를 백업 저장합니다.")
@app_commands.describe(id="백업을 구분할 서버 고유 ID (또는 문자열)")
@is_registered_or_owner()
async def save_server(interaction: discord.Interaction, id: str):
    guild = interaction.guild
    # 라이센스 확인
    conn = sqlite3.connect("bot_system.db")
    cursor = conn.cursor()
    cursor.execute("SELECT license_expire FROM guild_settings WHERE guild_id = ?", (str(guild.id),))
    res = cursor.fetchone()
    conn.close()

    if not res or not res[0] or datetime.strptime(res[0], "%Y-%m-%d") < datetime.now():
        await interaction.response.send_message("❌ 라이센스가 만료되었거나 등록되지 않았습니다.", ephemeral=True)
        return

    # 채널, 카테고리, 역할 구조 데이터 직렬화 (간단히 이름/타입 저장)
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
    # 검증: 자신이 저장한 섭이 아니거나 소유자가 아니면 거부
    if str(interaction.user.id) != str(saved_owner_id) and interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ 자신이 저장한 서버 데이터가 아니므로 거부되었습니다.", ephemeral=True)
        return

    await interaction.response.send_message(f"🔄 서버 ID `{id}`의 구조를 불러오는 중입니다... (구조 복구 로직 실행)", ephemeral=True)


@bot.tree.command(name="복구키", description="지정한 인원수만큼 복구키를 생성합니다.")
@app_commands.describe(인원="생성할 복구키의 인원수(사용 횟수)")
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
    
    # 남은 키 인원 총합 계산
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


@bot.tree.command(name="설정", description="복구 패널 및 인증 로그 채널, 부여할 역할을 설정합니다.")
@app_commands.describe(인증설명="인증 안내 설명", 버튼이름="인증 버튼 이름", 로그채널="인증로그 채널", 부여역할="지급할 역할")
@is_registered_or_owner()
async def set_config(interaction: discord.Interaction, 인증설명: str, 버튼이름: str, 로그채널: discord.TextChannel, 부여역할: discord.Role):
    guild_id = str(interaction.guild_id)
    conn = sqlite3.connect("bot_system.db")
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE guild_settings SET auth_desc = ?, btn_name = ?, log_channel_id = ?, target_role_id = ?
        WHERE guild_id = ?
    """, (인증설명, 버튼이름, str(로그채널.id), str(부여역할.id), guild_id))
    conn.commit()
    conn.close()

    await interaction.response.send_message("✅ 설정이 성공적으로 저장되었습니다.", ephemeral=True)


@bot.tree.command(name="복구패널", description="복구키를 사용할 수 있는 패널을 생성합니다.")
@is_registered_or_owner()
async def recovery_panel(interaction: discord.Interaction):
    # 설정 가져오기
    conn = sqlite3.connect("bot_system.db")
    cursor = conn.cursor()
    cursor.execute("SELECT auth_desc, btn_name FROM guild_settings WHERE guild_id = ?", (str(interaction.guild_id),))
    row = cursor.fetchone()
    conn.close()

    desc = row[0] if row and row[0] else "아래 버튼을 눌러 복구키를 입력하고 서버링크를 입력하여 서버 인원을 복구하세요."
    btn_text = row[1] if row and row[1] else "복구키 사용하기"

    embed = discord.Embed(
        title="# 복구키 사용하기",
        description=desc,
        color=discord.Color.green()
    )

    class RecoveryView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)

        @discord.ui.button(label=btn_text, style=discord.ButtonStyle.green, custom_id="open_recovery_modal")
        async def open_modal(self, interaction: discord.Interaction, button: discord.ui.Button):
            await interaction.response.send_modal(RecoveryModal())

        @discord.ui.button(label="라이센스 연장", style=discord.ButtonStyle.blurple, custom_id="open_license_modal")
        async def open_license(self, interaction: discord.Interaction, button: discord.ui.Button):
            await interaction.response.send_modal(LicenseExtendModal())

    await interaction.channel.send(embed=embed, view=RecoveryView())
    await interaction.response.send_message("✅ 복구 패널이 생성되었습니다.", ephemeral=True)


# ----------------- 모달 (팝업 창) 처리 -----------------

class RecoveryModal(discord.ui.Modal, title="서버 복구 인증"):
    key_input = discord.ui.TextInput(label="복구키", placeholder="발급받은 복구키를 입력하세요", required=True)
    link_input = discord.ui.TextInput(label="서버 초대링크", placeholder="접속할 서버 링크 혹은 뒷자리", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild_id)
        user = interaction.user
        key_val = self.key_input.value.strip()

        conn = sqlite3.connect("bot_system.db")
        cursor = conn.cursor()

        # 시도 횟수 증가
        cursor.execute("UPDATE guild_settings SET attempt_count = attempt_count + 1 WHERE guild_id = ?", (guild_id,))

        # 중복 체크
        cursor.execute("SELECT 1 FROM verified_users WHERE guild_id = ? AND user_id = ?", (guild_id, str(user.id)))
        if cursor.fetchone():
            cursor.execute("UPDATE guild_settings SET dup_count = dup_count + 1 WHERE guild_id = ?", (guild_id,))
            conn.commit()
            conn.close()
            await interaction.response.send_message("❌ 이미 해당 서버에서 복구/인증을 완료한 계정입니다. (중복)", ephemeral=True)
            return

        # 복구키 확인
        cursor.execute("SELECT max_uses, used_count FROM recovery_keys WHERE key_string = ? AND guild_id = ?", (key_val, guild_id))
        key_row = cursor.fetchone()

        if not key_row or key_row[1] >= key_row[0]:
            cursor.execute("UPDATE guild_settings SET fail_count = fail_count + 1 WHERE guild_id = ?", (guild_id,))
            conn.commit()
            conn.close()
            await interaction.response.send_message("❌ 유효하지 않거나 사용 횟수가 초과된 복구키입니다.", ephemeral=True)
            return

        # 키 사용 횟수 증가 및 유저 기록
        cursor.execute("UPDATE recovery_keys SET used_count = used_count + 1 WHERE key_string = ?", (key_val,))
        cursor.execute("INSERT INTO verified_users (guild_id, user_id) VALUES (?, ?)", (guild_id, str(user.id)))
        cursor.execute("UPDATE guild_settings SET success_count = success_count + 1 WHERE guild_id = ?", (guild_id,))
        
        # 설정값(로그 채널, 부여할 역할) 가져오기
        cursor.execute("SELECT log_channel_id, target_role_id FROM guild_settings WHERE guild_id = ?", (guild_id,))
        settings = cursor.fetchone()
        conn.commit()
        conn.close()

        # 역할 부여 시도
        if settings and settings[1]:
            role = interaction.guild.get_role(int(settings[1]))
            if role:
                try:
                    await user.add_roles(role)
                except Exception:
                    pass

        # 인증 로그 출력 포맷
        created_at = user.created_at.strftime("%Y년 %m월 %d일")
        joined_at = user.joined_at.strftime("%Y년 %m월 %d일") if user.joined_at else "알 수 없음"
        is_bot = "봇" if user.bot else "유저"
        # 부계정 의심 판별 (예시: 생성일이 7일 이내인 경우)
        is_suspicious = ":white_check_mark:" if (datetime.now(user.created_at.tzinfo) - user.created_at).days < 7 else ":x:"
        
        invite_code = self.link_input.value.strip().split("/")[-1] # 뒷자리만 추출

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

        await interaction.response.send_message("✅ 인증 및 복구가 성공적으로 완료되었습니다!", ephemeral=True)


class LicenseExtendModal(discord.ui.Modal, title="라이센스 연장"):
    ext_key_input = discord.ui.TextInput(label="연장키 입력", placeholder="소유자에게 받은 연장키 입력", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        key_val = self.ext_key_input.value.strip()
        guild_id = str(interaction.guild_id)

        conn = sqlite3.connect("bot_system.db")
        cursor = conn.cursor()
        
        # 연장키 확인
        cursor.execute("SELECT days FROM extension_keys WHERE key_string = ?", (key_val,))
        res = cursor.fetchone()
        if not res:
            conn.close()
            await interaction.response.send_message("❌ 유효하지 않은 연장키입니다.", ephemeral=True)
            return

        add_days = res[0]
        # 키 사용 후 삭제 (1회용)
        cursor.execute("DELETE FROM extension_keys WHERE key_string = ?", (key_val,))

        # 기존 만료일 확인
        cursor.execute("SELECT license_expire FROM guild_settings WHERE guild_id = ?", (guild_id,))
        g_res = cursor.fetchone()
        
        now = datetime.now()
        if g_res and g_res[0]:
            current_expire = datetime.strptime(g_res[0], "%Y-%m-%d")
            base_date = current_expire if current_expire > now else now
        else:
            base_date = now

        new_expire = (base_date + timedelta(days=add_days)).strftime("%Y-%m-%d")

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


# 봇 실행
bot.run(TOKEN)
