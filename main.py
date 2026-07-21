import discord
from discord.ext import commands
from discord.ui import Button, View
import os
import random
import math
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from keep_alive import keep_alive

# ==========================================
# 1. 봇 기본 설정 및 버그 방어용 변수
# ==========================================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# 어뷰징 방지: 현재 게임 중인 유저 목록 (동시 실행 방지)
active_games = set()

# MongoDB 연결 설정 (안전한 에러 처리 포함)
MONGO_URL = os.environ.get("MONGODB_URL")
if not MONGO_URL:
    print("🚨 [치명적 오류] Render 환경 변수에 MONGODB_URL이 없습니다!")
db_client = AsyncIOMotorClient(MONGO_URL)
db = db_client["aseotda_db"]
users_collection = db["users"]

# ==========================================
# 2. 섯다 핵심 엔진 (족보 & 확률)
# ==========================================
def calculate_score(card1, card2):
    """
    고퀄리티 섯다 족보 엔진:
    점수가 높을수록 강력한 패입니다.
    """
    cards = sorted([card1, card2])
    c1, c2 = cards[0], cards[1]
    
    # 1. 광땡 (최강의 패)
    if cards == [3, 8]: return 10000, "👑 38광땡", "모든 패를 이기는 최강의 패!"
    if cards == [1, 8]: return 9000, "🌟 18광땡", "38광땡 다음으로 강력한 패!"
    if cards == [1, 3]: return 9000, "🌟 13광땡", "38광땡 다음으로 강력한 패!"
    
    # 2. 땡 (같은 숫자 2개)
    if c1 == c2:
        if c1 == 10: return 8000, "🔥 장땡 (10땡)", "땡 중에서 가장 높은 장땡!"
        return 7000 + (c1 * 100), f"💥 {c1}땡", "강력한 땡입니다!"
        
    # 3. 특수 족보 (알리, 독사, 구삥, 장삥, 세륙)
    if cards == [1, 2]: return 6000, "✨ 알리", "특수 족보 중 최고 (1, 2)"
    if cards == [1, 4]: return 5900, "🐍 독사", "강력한 특수 족보 (1, 4)"
    if cards == [1, 9]: return 5800, "🎯 구삥", "준수한 특수 족보 (1, 9)"
    if cards == [1, 10]: return 5700, "🎯 장삥", "준수한 특수 족보 (1, 10)"
    if cards == [4, 6]: return 5600, "🎲 세륙", "준수한 특수 족보 (4, 6)"
    
    # 4. 끗 (합의 일의 자리)
    kkeut = (c1 + c2) % 10
    if kkeut == 9: return 900, "오 갑오 (9끗)", "끗 중 최고 점수!"
    if kkeut == 0: return 0, "💀 망통 (0끗)", "가장 낮은 패입니다..."
    return kkeut * 100, f"숫자 {kkeut}끗", f"무난한 {kkeut}끗입니다."

def draw_cards():
    # 1~10까지 2장씩 총 20장의 화투패 구현
    deck = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10] * 2
    return random.sample(deck, 2)

# ==========================================
# 3. DB 안전 관리자 (유저 데이터 제어)
# ==========================================
async def get_user_data(user_id):
    user = await users_collection.find_one({"_id": user_id})
    if not user:
        user = {"_id": user_id, "points": 10000, "wins": 0, "losses": 0}
        await users_collection.insert_one(user)
    return user

async def update_user_data(user_id, points_change, win_change=0, loss_change=0):
    await users_collection.update_one(
        {"_id": user_id},
        {"$inc": {"points": points_change, "wins": win_change, "losses": loss_change}},
        upsert=True
    )

# ==========================================
# 4. 하이엔드 UI: 섯다 게임 뷰 (버튼)
# ==========================================
class SeotdaGameView(View):
    def __init__(self, ctx, bet_amount, is_practice=False):
        super().__init__(timeout=60.0) # 60초 타임아웃
        self.ctx = ctx
        self.user = ctx.author
        self.bet_amount = bet_amount
        self.is_practice = is_practice
        
        # 봇과 유저의 패 생성
        self.user_cards = draw_cards()
        self.bot_cards = draw_cards()
        self.user_score, self.user_name, self.u_desc = calculate_score(self.user_cards[0], self.user_cards[1])
        self.bot_score, self.bot_name, self.b_desc = calculate_score(self.bot_cards[0], self.bot_cards[1])

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # 다른 사람이 버튼을 누르는 것 방지
        if interaction.user != self.user:
            await interaction.response.send_message("❌ 남의 게임 버튼은 누를 수 없습니다!", ephemeral=True)
            return False
        return True

    async def on_timeout(self) -> None:
        # 시간 초과 시: 버튼을 비활성화하고 게임 강제 종료 (어뷰징 방지)
        if self.user.id in active_games:
            active_games.remove(self.user.id)
            
        for child in self.children:
            child.disabled = True
            
        embed = discord.Embed(title="⏳ 시간 초과", description="60초 동안 선택하지 않아 게임이 취소되었습니다.\n배팅금은 그대로 돌려받습니다.", color=discord.Color.dark_grey())
        try:
            await self.message.edit(embed=embed, view=self)
        except:
            pass

    @discord.ui.button(label="콜 (진행)", style=discord.ButtonStyle.success, emoji="⚔️", custom_id="btn_call")
    async def call_button(self, interaction: discord.Interaction, button: Button):
        # 1. 게임 종료 처리 및 잠금 해제
        if self.user.id in active_games:
            active_games.remove(self.user.id)
            
        for child in self.children:
            child.disabled = True

        # 2. 승패 판정 로직
        if self.user_score > self.bot_score:
            color = discord.Color.green()
            result_title = "🎉 승리하셨습니다!"
            win_points = self.bet_amount
            win_rate_update = (1, 0)
        elif self.user_score < self.bot_score:
            color = discord.Color.red()
            result_title = "💀 패배하셨습니다..."
            win_points = -self.bet_amount
            win_rate_update = (0, 1)
        else:
            color = discord.Color.gold()
            result_title = "🤝 무승부! (배팅금 반환)"
            win_points = 0
            win_rate_update = (0, 0)

        # 3. 실전 모드일 경우 DB 업데이트
        if not self.is_practice:
            await update_user_data(self.user.id, win_points, win_change=win_rate_update[0], loss_change=win_rate_update[1])

        # 4. 압도적인 퀄리티의 결과 임베드
        embed = discord.Embed(title=result_title, color=color)
        embed.add_field(name=f"👤 {self.user.display_name}의 패", value=f"**[{self.user_cards[0]}, {self.user_cards[1]}]** ➔ **{self.user_name}**\n*{self.u_desc}*", inline=False)
        embed.add_field(name="🤖 아섯다 봇의 패", value=f"**[{self.bot_cards[0]}, {self.bot_cards[1]}]** ➔ **{self.bot_name}**\n*{self.b_desc}*", inline=False)
        
        if self.is_practice:
            embed.set_footer(text="※ 연습 모드이므로 포인트가 변동되지 않습니다.")
        else:
            embed.add_field(name="💰 포인트 정산", value=f"**{win_points:+,} P**", inline=False)

        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="다이 (포기)", style=discord.ButtonStyle.danger, emoji="🏳️", custom_id="btn_die")
    async def die_button(self, interaction: discord.Interaction, button: Button):
        # 1. 잠금 해제
        if self.user.id in active_games:
            active_games.remove(self.user.id)
            
        for child in self.children:
            child.disabled = True

        # 2. 다이 패널티 처리 (배팅금의 50% 반올림)
        penalty = round(self.bet_amount * 0.5)
        
        if not self.is_practice:
            await update_user_data(self.user.id, -penalty, loss_change=1)

        # 3. 결과 임베드
        embed = discord.Embed(title="🏳️ 게임 포기 (다이)", description="패가 좋지 않아 승부를 포기했습니다.", color=discord.Color.red())
        embed.add_field(name="💸 잃은 포인트", value=f"배팅금의 50%인 **-{penalty:,} P** 차감", inline=False)
        embed.add_field(name="👀 참고용: 봇의 패", value=f"**[{self.bot_cards[0]}, {self.bot_cards[1]}]** ➔ **{self.bot_name}**", inline=False)

        await interaction.response.edit_message(embed=embed, view=self)


# ==========================================
# 5. 명령어 모음 (Commands)
# ==========================================
@bot.command(name="명령어")
async def help_cmd(ctx):
    embed = discord.Embed(title="🃏 아섯다 봇 명령어 가이드", color=discord.Color.blurple())
    embed.add_field(name="`!출석`", value="매일 1회 무료 포인트를 받습니다.", inline=False)
    embed.add_field(name="`!내정보`", value="내 포인트와 승률을 확인합니다.", inline=False)
    embed.add_field(name="`!랭킹`", value="서버 내 최고 부자 TOP 5를 보여줍니다.", inline=False)
    embed.add_field(name="`!연습모드`", value="포인트 소모 없이 봇과 가볍게 섯다를 연습합니다.", inline=False)
    embed.add_field(name="`!섯다 [금액]`", value="포인트를 걸고 실전 섯다 승부를 펼칩니다.\n예) `!섯다 1000`", inline=False)
    await ctx.send(embed=embed)

@bot.command(name="출석")
@commands.cooldown(1, 86400, commands.BucketType.user) # 24시간(86400초)에 1번 제한
async def daily(ctx):
    user_data = await get_user_data(ctx.author.id)
    reward = 5000
    await update_user_data(ctx.author.id, reward)
    
    embed = discord.Embed(title="🎁 출석 보상 지급!", description=f"매일 지급되는 지원금 **{reward:,} P**를 받으셨습니다!", color=discord.Color.green())
    embed.add_field(name="현재 잔액", value=f"**{user_data['points'] + reward:,} P**")
    await ctx.send(embed=embed)

@bot.command(name="내정보")
async def profile(ctx):
    user_data = await get_user_data(ctx.author.id)
    wins, losses = user_data["wins"], user_data["losses"]
    total_games = wins + losses
    win_rate = round((wins / total_games * 100), 1) if total_games > 0 else 0.0

    embed = discord.Embed(title=f"📊 {ctx.author.display_name}님의 도박장 기록", color=discord.Color.blue())
    embed.set_thumbnail(url=ctx.author.display_avatar.url)
    embed.add_field(name="💰 보유 자산", value=f"**{user_data['points']:,} P**", inline=False)
    embed.add_field(name="⚔️ 전적", value=f"**{wins}**승 **{losses}**패 (승률 **{win_rate}%**)", inline=False)
    await ctx.send(embed=embed)

@bot.command(name="랭킹")
async def ranking(ctx):
    top_users = await users_collection.find().sort("points", -1).limit(5).to_list(length=5)
    
    embed = discord.Embed(title="🏆 아섯다 서버 랭킹 TOP 5", description="누가 가장 많은 자산을 보유하고 있을까요?", color=discord.Color.gold())
    
    for i, user in enumerate(top_users):
        user_obj = bot.get_user(user["_id"])
        name = user_obj.display_name if user_obj else f"알수없는유저({user['_id']})"
        medal = ["🥇", "🥈", "🥉", "🏅", "🏅"][i]
        embed.add_field(name=f"{medal} {i+1}위: {name}", value=f"**{user['points']:,} P** ({user['wins']}승 {user['losses']}패)", inline=False)
        
    await ctx.send(embed=embed)

@bot.command(name="섯다")
async def play_seotda(ctx, amount: int = None):
    # 1. 입력값 검증 (버그 완벽 차단)
    if amount is None or amount <= 0:
        return await ctx.send("❌ **올바른 배팅 금액을 입력해 주세요.** (예: `!섯다 1000`)")
    
    if ctx.author.id in active_games:
        return await ctx.send("⚠️ **이미 진행 중인 게임이 있습니다!** 기존 게임에서 콜이나 다이를 선택해 주세요.")

    user_data = await get_user_data(ctx.author.id)
    if user_data["points"] < amount:
        return await ctx.send(f"❌ **잔액이 부족합니다!** (현재 자산: **{user_data['points']:,} P**)")

    # 2. 게임 시작 (동시 실행 방지 락 걸기)
    active_games.add(ctx.author.id)
    view = SeotdaGameView(ctx, amount, is_practice=False)
    
    embed = discord.Embed(title="🎴 섯다 한 판 승부!", description="당신의 패를 확인하고 승부를 결정하세요.", color=discord.Color.dark_theme())
    embed.add_field(name="💸 배팅 금액", value=f"**{amount:,} P**", inline=False)
    embed.add_field(name="👤 당신의 패", value=f"**[{view.user_cards[0]}, {view.user_cards[1]}]** ➔ **{view.user_name}**", inline=False)
    embed.set_footer(text="콜: 상대 패와 대결 / 다이: 포기하고 배팅금의 50%를 잃음")
    
    view.message = await ctx.send(embed=embed, view=view)

@bot.command(name="연습모드")
async def practice_seotda(ctx):
    if ctx.author.id in active_games:
        return await ctx.send("⚠️ **이미 진행 중인 게임이 있습니다!**")

    active_games.add(ctx.author.id)
    view = SeotdaGameView(ctx, bet_amount=0, is_practice=True)
    
    embed = discord.Embed(title="🎮 섯다 연습모드", description="포인트가 소모되지 않는 안전한 대결입니다.", color=discord.Color.light_grey())
    embed.add_field(name="👤 당신의 패", value=f"**[{view.user_cards[0]}, {view.user_cards[1]}]** ➔ **{view.user_name}**", inline=False)
    
    view.message = await ctx.send(embed=embed, view=view)

# ==========================================
# 6. 전역 에러 핸들러 (봇이 꺼지는 것 방지)
# ==========================================
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"⏳ **아직 출석할 수 없습니다!** {math.ceil(error.retry_after / 3600)}시간 후에 다시 시도해 주세요.")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("❌ **숫자만 입력해 주세요!** (예: `!섯다 1000`)")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ **명령어 형식이 틀렸습니다.** `!명령어`를 쳐서 확인해 보세요.")

# ==========================================
# 7. 봇 실행
# ==========================================
@bot.event
async def on_ready():
    print(f"✅ 로그인 성공: {bot.user.name} 봇이 최고급 퀄리티로 가동을 시작합니다!")
    await bot.change_presence(activity=discord.Game(name="!명령어 | 섯다의 신"))

keep_alive()
bot.run(os.environ.get("DISCORD_TOKEN"))
