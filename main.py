import os
import json
import random
import asyncio
import http.server
import socketserver
import threading
from datetime import datetime, timedelta, timezone
import discord
from discord.ext import commands

# -------------------------------------------------------------
# 1. Render 24시간 자동 가동용 미니 웹 서버
# -------------------------------------------------------------
def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", port), handler) as httpd:
        httpd.serve_forever()

threading.Thread(target=run_web_server, daemon=True).start()

# -------------------------------------------------------------
# 2. 봇 기본 설정 및 데이터 저장 관리 (users.json)
# -------------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

DATA_FILE = "users.json"

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def get_user_data(data, user_id, user_name):
    uid = str(user_id)
    if uid not in data:
        # 신규 유저 초기 포인트: 50
        data[uid] = {
            "name": user_name,
            "points": 50,
            "last_attendance": "",
            "streak": 0
        }
    else:
        data[uid]["name"] = user_name
    return data[uid]

# -------------------------------------------------------------
# 3. 섯다 게임 족보 판정 로직
# -------------------------------------------------------------
def evaluate_seotda_hand(c1, c2):
    m1, g1 = c1
    m2, g2 = c2
    months = sorted([m1, m2])
    
    if g1 and g2:
        if set([m1, m2]) == {3, 8}: return (100, "38광땡 (최고 족보)")
        if set([m1, m2]) == {1, 8}: return (99, "18광땡")
        if set([m1, m2]) == {1, 3}: return (98, "13광땡")

    if m1 == m2: return (80 + m1, f"{m1}땡")

    pair = tuple(months)
    if pair == (1, 2): return (70, "알리 (1, 2)")
    if pair == (1, 4): return (69, "독사 (1, 4)")
    if pair == (1, 9): return (68, "구빙 (1, 9)")
    if pair == (1, 10): return (67, "장빙 (1, 10)")
    if pair == (4, 10): return (66, "장사 (4, 10)")
    if pair == (4, 6): return (65, "세륙 (4, 6)")

    kkat = (m1 + m2) % 10
    if kkat == 0: return (0, "망통 (0끗)")
    return (kkat, f"{kkat}끗")

def format_card(card):
    month, is_gwang = card
    return f"{month}월{'[광]' if is_gwang else ''}"

# -------------------------------------------------------------
# 4. 유저간 섯다 배틀 (PvP) UI 클래스
# -------------------------------------------------------------
class SeotdaPvPView(discord.ui.View):
    def __init__(self, challenger: discord.Member, target: discord.Member, bet: int):
        super().__init__(timeout=60.0) # 60초간 응답 없으면 취소
        self.challenger = challenger
        self.target = target
        self.bet = bet
        self.clicked = False
        self.message = None

    async def on_timeout(self):
        if not self.clicked and self.message:
            for child in self.children:
                child.disabled = True
            await self.message.edit(content=f"⏳ {self.target.mention}님이 응답하지 않아 섯다 신청이 취소되었습니다.", view=self)

    @discord.ui.button(label="수락", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 1. 본인(B유저)만 누를 수 있게 방어
        if interaction.user.id != self.target.id:
            await interaction.response.send_message("❌ 지목된 당사자만 누를 수 있습니다.", ephemeral=True)
            return

        if self.clicked:
            return

        data = load_data()
        user_a = get_user_data(data, self.challenger.id, self.challenger.display_name)
        user_b = get_user_data(data, self.target.id, self.target.display_name)

        # 2. B유저 잔액 확인 (부족할 경우 B에게만 몰래 메시지)
        if user_b["points"] < self.bet:
            await interaction.response.send_message(f"❌ 포인트가 부족하여 수락할 수 없습니다! (현재 보유: {user_b['points']:,})", ephemeral=True)
            return
        
        # 3. A유저 잔액 확인 (그새 다른 곳에 돈을 썼을 수도 있으므로 방어)
        if user_a["points"] < self.bet:
            self.clicked = True
            await interaction.response.edit_message(content=f"❌ {self.challenger.display_name}님의 포인트가 그새 부족해져 게임이 취소되었습니다.", view=None)
            return

        # 4. 게임 수락 처리 및 포인트 임시 차감(버그 방지)
        self.clicked = True
        user_a["points"] -= self.bet
        user_b["points"] -= self.bet
        save_data(data)

        # 5. 긴장감 넘치는 연출 시작
        await interaction.response.edit_message(content=f"⚔️ **{self.target.display_name}**님이 도전을 수락했습니다! 긴장감 넘치는 승부가 시작됩니다...", view=None)
        msg = interaction.message

        deck = []
        for month in range(1, 11):
            deck.append((month, True if month in [1, 3, 8] else False))
            deck.append((month, False))
        random.shuffle(deck)

        a_cards = [deck.pop(), deck.pop()]
        b_cards = [deck.pop(), deck.pop()]
        
        a_score, a_hand = evaluate_seotda_hand(*a_cards)
        b_score, b_hand = evaluate_seotda_hand(*b_cards)

        await asyncio.sleep(2)
        await msg.edit(content="🔀 딜러가 패를 섞고 있습니다... (촤르륵)")
        
        await asyncio.sleep(2)
        await msg.edit(content="🎴 두 사람 앞에 패를 한 장씩 엎어두었습니다... 숨 막히는 순간!")
        
        await asyncio.sleep(2.5)
        await msg.edit(content=f"👤 먼저 **{self.challenger.display_name}**님의 패를 조심스럽게 까봅니다...\n\n...")
        
        await asyncio.sleep(2.5)
        await msg.edit(content=f"👤 **{self.challenger.display_name}**님의 패!\n카드: [{format_card(a_cards[0])}], [{format_card(a_cards[1])}]\n족보: **{a_hand}**\n\n이제 상대방의 패를 엽니다...")
        
        await asyncio.sleep(3)
        await msg.edit(content=f"👤 **{self.challenger.display_name}**님의 패: **{a_hand}**\n\n👤 **{self.target.display_name}**님의 패를 깝니다...\n\n...")
        
        await asyncio.sleep(2.5)
        
        # 6. 승패 정산
        data = load_data() # 최신 데이터 로드
        user_a = get_user_data(data, self.challenger.id, self.challenger.display_name)
        user_b = get_user_data(data, self.target.id, self.target.display_name)

        if a_score > b_score:
            winner_msg = f"🎉 **{self.challenger.display_name} 승리!** 판돈 **{self.bet * 2:,} 포인트**를 싹쓸이합니다!"
            user_a["points"] += (self.bet * 2)
        elif b_score > a_score:
            winner_msg = f"🎉 **{self.target.display_name} 승리!** 판돈 **{self.bet * 2:,} 포인트**를 싹쓸이합니다!"
            user_b["points"] += (self.bet * 2)
        else:
            winner_msg = "🤝 **무승부!** 너무 치열한 승부였네요. 베팅금이 각자에게 환불됩니다."
            user_a["points"] += self.bet
            user_b["points"] += self.bet

        save_data(data)

        final_content = (
            f"🔥 **유저간 섯다 승부 최종 결과!** 🔥\n\n"
            f"👤 **{self.challenger.display_name}**: [{format_card(a_cards[0])}], [{format_card(a_cards[1])}] ➡️ **{a_hand}**\n"
            f"👤 **{self.target.display_name}**: [{format_card(b_cards[0])}], [{format_card(b_cards[1])}] ➡️ **{b_hand}**\n\n"
            f"{winner_msg}"
        )
        await msg.edit(content=final_content)

    @discord.ui.button(label="거절", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target.id:
            await interaction.response.send_message("❌ 지목된 당사자만 누를 수 있습니다.", ephemeral=True)
            return
        
        self.clicked = True
        await interaction.response.edit_message(content=f"💨 **{self.target.display_name}**님이 섯다 승부를 거절하며 도망갔습니다!", view=None)

# -------------------------------------------------------------
# 5. 명령어 기능 구현
# -------------------------------------------------------------
@bot.event
async def on_ready():
    print(f"✅ {bot.user} 로그인 성공!")

@bot.command(name="출석")
async def check_in(ctx):
    data = load_data()
    user = get_user_data(data, ctx.author.id, ctx.author.name)
    
    kst_now = datetime.now(timezone.utc) + timedelta(hours=9)
    today_str = kst_now.strftime("%Y-%m-%d")
    yesterday_str = (kst_now - timedelta(days=1)).strftime("%Y-%m-%d")

    if user["last_attendance"] == today_str:
        await ctx.send(f"❌ **{ctx.author.display_name}**님은 오늘 이미 출석하셨습니다!")
        return

    if user["last_attendance"] == yesterday_str:
        user["streak"] += 1
        reward = min(10000, user["streak"] * 100)
        msg = f"🎉 **연속 {user['streak']}일차 출석 성공!**\n보상: **+{reward:,} 포인트**"
    else:
        old_streak = user["streak"]
        if user["last_attendance"] != "" and old_streak > 0:
            previous_reward = old_streak * 100
            calculated_reward = previous_reward - 5000
            reward = max(100, calculated_reward)
            msg = f"⚠️ 연속 출석을 놓쳤습니다! (페널티 적용)\n보상: **+{reward:,} 포인트** (연속 출석이 1일차로 리셋됩니다)"
        else:
            reward = 100
            msg = f"🎉 첫 출석을 환영합니다!\n보상: **+{reward:,} 포인트**"
        
        user["streak"] = 1

    user["points"] += reward
    user["last_attendance"] = today_str
    save_data(data)

    embed = discord.Embed(title="📅 출석체크 완료", description=msg, color=0x2ecc71)
    embed.add_field(name="현재 보유 포인트", value=f"**{user['points']:,} 포인트**", inline=False)
    await ctx.send(embed=embed)

@bot.command(name="포인트")
async def show_points(ctx):
    data = load_data()
    user = get_user_data(data, ctx.author.id, ctx.author.name)
    save_data(data)
    await ctx.send(f"💰 **{ctx.author.display_name}**님의 포인트: **{user['points']:,} 포인트** (연속 출석: {user['streak']}일)")

@bot.command(name="랭킹")
async def show_ranking(ctx):
    data = load_data()
    if not data:
        await ctx.send("등록된 유저 데이터가 없습니다.")
        return

    sorted_users = sorted(data.values(), key=lambda x: x.get("points", 0), reverse=True)

    embed = discord.Embed(title="🏆 포인트 랭킹 Top 10", color=0xf1c40f)
    for idx, u in enumerate(sorted_users[:10], start=1):
        medal = "🥇" if idx == 1 else "🥈" if idx == 2 else "🥉" if idx == 3 else f"`{idx}위`"
        name = u.get("name", "알 수 없음")
        pts = u.get("points", 0)
        embed.add_field(name=f"{medal} {name}", value=f"**{pts:,} 포인트**", inline=False)

    await ctx.send(embed=embed)

# 딜러(봇)와의 섯다 (PvE)
@bot.command(name="섯다")
async def play_seotda_pve(ctx, bet: int):
    data = load_data()
    user = get_user_data(data, ctx.author.id, ctx.author.name)

    if bet <= 0:
        await ctx.send("❌ 베팅 금액은 1 포인트 이상이어야 합니다.")
        return

    if user["points"] < bet:
        await ctx.send(f"❌ 포인트가 부족합니다! (현재 포인트: **{user['points']:,}**)")
        return

    deck = []
    for month in range(1, 11):
        deck.append((month, True if month in [1, 3, 8] else False))
        deck.append((month, False))

    random.shuffle(deck)
    player_cards = [deck.pop(), deck.pop()]
    dealer_cards = [deck.pop(), deck.pop()]

    p_score, p_hand = evaluate_seotda_hand(*player_cards)
    d_score, d_hand = evaluate_seotda_hand(*dealer_cards)

    embed = discord.Embed(title="🎴 딜러와의 섯다 승부!", color=0x3498db)
    embed.add_field(
        name=f"👤 {ctx.author.display_name}의 카드",
        value=f"[{format_card(player_cards[0])}], [{format_card(player_cards[1])}]\n족보: **{p_hand}**",
        inline=False
    )
    embed.add_field(
        name="🤖 딜러의 카드",
        value=f"[{format_card(dealer_cards[0])}], [{format_card(dealer_cards[1])}]\n족보: **{d_hand}**",
        inline=False
    )

    if p_score > d_score:
        multiplier = 2
        if p_score == 100: multiplier = 5
        elif p_score >= 80: multiplier = 3

        win_amount = bet * multiplier
        user["points"] += (win_amount - bet)
        embed.color = 0x2ecc71
        embed.description = f"🎉 **승리했습니다!** (배율: {multiplier}배)\n**+{win_amount:,} 포인트**를 획득했습니다."
    elif p_score < d_score:
        user["points"] -= bet
        embed.color = 0xe74c3c
        embed.description = f"💀 **패배했습니다...**\n**-{bet:,} 포인트**를 잃었습니다."
    else:
        embed.color = 0x95a5a6
        embed.description = "🤝 **무승부입니다!** 베팅금이 환불됩니다."

    save_data(data)
    embed.add_field(name="현재 보유 포인트", value=f"**{user['points']:,} 포인트**", inline=False)
    await ctx.send(embed=embed)

@play_seotda_pve.error
async def play_seotda_pve_error(ctx, error):
    if isinstance(error, commands.BadArgument) or isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ 사용법: `!섯다 [베팅포인트]` (예: `!섯다 50`)")

# 유저와의 섯다 (PvP)
@bot.command(name="섯다뜨자")
async def play_seotda_pvp(ctx, target: discord.Member, bet: int):
    # 어뷰징 및 오류 방어
    if target.bot:
        await ctx.send("❌ 봇과는 대결할 수 없습니다! (딜러와 하려면 `!섯다` 명령어를 이용하세요)")
        return
    if target == ctx.author:
        await ctx.send("❌ 자기 자신과는 대결할 수 없습니다! 거울이랑 가위바위보 하는 건가요?")
        return
    if bet <= 0:
        await ctx.send("❌ 베팅 금액은 1 포인트 이상이어야 합니다.")
        return

    data = load_data()
    user_a = get_user_data(data, ctx.author.id, ctx.author.display_name)
    
    if user_a["points"] < bet:
        await ctx.send(f"❌ 도전을 신청하기엔 포인트가 부족합니다! (현재: **{user_a['points']:,}**)")
        return

    # View(버튼 UI) 생성
    view = SeotdaPvPView(ctx.author, target, bet)
    
    msg = await ctx.send(
        f"🔥 {target.mention}님! **{ctx.author.display_name}**님이 **{bet:,} 포인트**를 걸고 영혼의 섯다 승부를 신청했습니다!\n\n"
        f"👉 아래 버튼을 눌러 수락하거나 거절하세요. (60초 내 미응답 시 자동 취소)",
        view=view
    )
    view.message = msg # 시간 초과 처리를 위해 메시지 객체 저장

@play_seotda_pvp.error
async def play_seotda_pvp_error(ctx, error):
    if isinstance(error, commands.BadArgument) or isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ 사용법: `!섯다뜨자 @유저멘션 [베팅포인트]` (예: `!섯다뜨자 @홍길동 1000`)")

# -------------------------------------------------------------
# 6. 토큰 불러오기 및 봇 실행
# -------------------------------------------------------------
TOKEN = os.getenv("TOKEN")

if not TOKEN:
    try:
        with open("token.txt", "r", encoding="utf-8") as f:
            TOKEN = f.read().strip()
    except FileNotFoundError:
        print("❌ 토큰을 찾을 수 없습니다.")

if TOKEN:
    bot.run(TOKEN)
