import os
import json
import random
import asyncio
import http.server
import socketserver
import threading
import traceback
from datetime import datetime, timedelta, timezone
import discord
from discord.ext import commands

# ==============================================================================
# 1. 24시간 가동 웹 서버 (Render Keep-Alive)
# ==============================================================================
def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", port), handler) as httpd:
        httpd.serve_forever()

threading.Thread(target=run_web_server, daemon=True).start()

# ==============================================================================
# 2. 봇 코어 및 데이터베이스 설정
# ==============================================================================
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

def get_user_data(data, user_id, user_name, is_bot=False):
    uid = str(user_id)
    if uid not in data:
        data[uid] = {
            "name": user_name,
            "points": 50 if not is_bot else 100000000,
            "max_points": 50 if not is_bot else 100000000,
            "wins": 0,
            "losses": 0,
            "ties": 0,
            "highest_win": 0,
            "bankruptcies": 0,
            "last_attendance": "",
            "streak": 0,
            "is_bot": is_bot,
            "inventory": []
        }
    else:
        # 데이터베이스 마이그레이션 (누락된 키 자동 보완)
        defaults = {
            "max_points": data[uid].get("points", 50),
            "wins": 0, "losses": 0, "ties": 0,
            "highest_win": 0, "bankruptcies": 0,
            "inventory": []
        }
        for key, value in defaults.items():
            if key not in data[uid]:
                data[uid][key] = value
        
        data[uid]["name"] = user_name
        data[uid]["is_bot"] = is_bot
    return data[uid]

def update_stats_post_game(user, result, win_amount=0):
    """게임 종료 후 전적 및 최고 기록을 업데이트하는 헬퍼 함수"""
    if result == "win":
        user["wins"] += 1
        if win_amount > user["highest_win"]:
            user["highest_win"] = win_amount
    elif result == "loss":
        user["losses"] += 1
    elif result == "tie":
        user["ties"] += 1
        
    if user["points"] > user["max_points"]:
        user["max_points"] = user["points"]
        
    if user["points"] <= 0:
        user["points"] = 0
        user["bankruptcies"] += 1

# ==============================================================================
# 3. 섯다 족보 판독 엔진
# ==============================================================================
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

# ==============================================================================
# 4. 아이템 상점 및 역할 관리
# ==============================================================================
SHOP_ITEMS = {
    "초보타짜": {"price": 1000, "role_name": "🔰 초보 타짜", "color": discord.Color.green(), "desc": "섯다 입문자를 위한 기본 역할"},
    "중급타짜": {"price": 5000, "role_name": "⚔️ 중급 타짜", "color": discord.Color.blue(), "desc": "판돈을 좀 만져본 유저 전용 역할"},
    "전설의타짜": {"price": 20000, "role_name": "👑 전설의 타짜", "color": discord.Color.gold(), "desc": "서버 최고의 승부사 전용 황금 빛 역할"},
    "신풍": {"price": 50000, "role_name": "🐉 신풍 (神風)", "color": discord.Color.purple(), "desc": "신에 경지에 도달한 타짜 전용 보라빛 역할"}
}

async def ensure_role_exists(guild, role_name, color):
    role = discord.utils.get(guild.roles, name=role_name)
    if not role:
        try:
            role = await guild.create_role(name=role_name, color=color, hoist=True)
        except discord.Forbidden:
            return None
    return role

# ==============================================================================
# 5. PvP 대결 인터랙션 뷰 (버그 완벽 방어)
# ==============================================================================
class SeotdaPvPView(discord.ui.View):
    def __init__(self, challenger: discord.Member, target: discord.Member, bet: int):
        super().__init__(timeout=60.0)
        self.challenger = challenger
        self.target = target
        self.bet = bet
        self.clicked = False
        self.message = None

    async def on_timeout(self):
        if not self.clicked and self.message:
            for child in self.children:
                child.disabled = True
            try:
                await self.message.edit(content=f"⏳ {self.target.mention}님이 응답하지 않아 승부가 자동 취소되었습니다.", view=self)
            except:
                pass

    @discord.ui.button(label="수락", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target.id:
            await interaction.response.send_message("❌ 지목된 당사자만 누를 수 있습니다.", ephemeral=True)
            return

        if self.clicked: return

        data = load_data()
        user_a = get_user_data(data, self.challenger.id, self.challenger.display_name, self.challenger.bot)
        user_b = get_user_data(data, self.target.id, self.target.display_name, self.target.bot)

        if user_b["points"] < self.bet:
            await interaction.response.send_message(f"❌ 포인트가 부족합니다! (현재 보유: {user_b['points']:,})", ephemeral=True)
            return
        
        if user_a["points"] < self.bet:
            self.clicked = True
            await interaction.response.edit_message(content=f"❌ {self.challenger.display_name}님의 포인트가 그새 부족해져 게임이 무효 처리되었습니다.", view=None)
            return

        self.clicked = True
        user_a["points"] -= self.bet
        user_b["points"] -= self.bet
        save_data(data)

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

        # 타격감 100% 슬로우 오픈 연출
        await asyncio.sleep(1.5)
        await msg.edit(content="🔀 딜러가 비장하게 패를 섞고 있습니다... (촤르륵)")
        await asyncio.sleep(2)
        await msg.edit(content="🎴 두 사람 앞에 패를 엎어두었습니다... 숨 막히는 순간!")
        await asyncio.sleep(2)
        await msg.edit(content=f"👤 **{self.challenger.display_name}**님의 패를 조심스럽게 까봅니다...\n\n...")
        await asyncio.sleep(2)
        await msg.edit(content=f"👤 **{self.challenger.display_name}**님의 패!\n카드: [{format_card(a_cards[0])}], [{format_card(a_cards[1])}]\n족보: **{a_hand}**\n\n상대방의 패를 오픈합니다...")
        await asyncio.sleep(2.5)
        
        # 정산 로직
        data = load_data()
        user_a = get_user_data(data, self.challenger.id, self.challenger.display_name, self.challenger.bot)
        user_b = get_user_data(data, self.target.id, self.target.display_name, self.target.bot)

        if a_score > b_score:
            win_prize = self.bet * 2
            winner_msg = f"🎉 **{self.challenger.display_name} 승리!** 판돈 **{win_prize:,} 포인트** 싹쓸이!"
            user_a["points"] += win_prize
            update_stats_post_game(user_a, "win", win_prize - self.bet)
            update_stats_post_game(user_b, "loss")
        elif b_score > a_score:
            win_prize = self.bet * 2
            winner_msg = f"🎉 **{self.target.display_name} 승리!** 판돈 **{win_prize:,} 포인트** 싹쓸이!"
            user_b["points"] += win_prize
            update_stats_post_game(user_b, "win", win_prize - self.bet)
            update_stats_post_game(user_a, "loss")
        else:
            winner_msg = "🤝 **무승부!** 치열한 접전 끝에 각자 베팅금이 환불됩니다."
            user_a["points"] += self.bet
            user_b["points"] += self.bet
            update_stats_post_game(user_a, "tie")
            update_stats_post_game(user_b, "tie")

        save_data(data)

        final_content = (
            f"🔥 **아섯다 영혼의 승부 최종 결과!** 🔥\n\n"
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
        await interaction.response.edit_message(content=f"💨 **{self.target.display_name}**님이 겁을 먹고 승부를 거절했습니다.", view=None)

# ==============================================================================
# 6. 시스템 전역 이벤트 및 에러 핸들러
# ==============================================================================
@bot.event
async def on_ready():
    print(f"✅ {bot.user} 아섯다 봇 가동 준비 완료!")
    await bot.change_presence(activity=discord.Game(name="!명령어 | !내정보 | 아섯다 플레이 중"))

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        # 무시 가능한 에러
        return
    elif isinstance(error, commands.MissingRequiredArgument) or isinstance(error, commands.BadArgument):
        await ctx.send(f"⚠️ 올바른 형식이 아닙니다. 명령어 사용법을 확인해주세요.\n(도움말: `!명령어`)")
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ 이 명령어를 실행할 관리자 권한이 부족합니다.")
    else:
        # 예상치 못한 시스템 충돌 및 버그 완벽 방어망
        traceback.print_exc()
        try:
            await ctx.send("🚨 `[시스템 : 일시적인 오류가 발생했습니다. 잠시 후나 다시 실행해주세요.]`")
        except:
            pass

# ==============================================================================
# 7. 마이페이지 (!내정보) 및 기타 명령어
# ==============================================================================
@bot.command(name="내정보")
async def my_profile(ctx):
    data = load_data()
    user = get_user_data(data, ctx.author.id, ctx.author.name, ctx.author.bot)
    
    total_games = user["wins"] + user["losses"] + user["ties"]
    win_rate = (user["wins"] / total_games * 100) if total_games > 0 else 0.0
    
    # 뱃지 시스템 로직 구성
    badges = " ".join([f"`[{SHOP_ITEMS[item]['role_name']}]`" for item in user.get("inventory", [])])
    if not badges:
        badges = "`[없음]`"
        
    bankrupt_badge = "💸 **파산의 아이콘** (파산 기록 다수)" if user["bankruptcies"] >= 3 else ""

    embed = discord.Embed(title=f"📊 {ctx.author.display_name}님의 타짜 프로필", color=0x2ecc71)
    
    embed.add_field(name="💰 경제 정보", value=f"**현재 잔액:** {user['points']:,} P\n**최고 기록:** {user['max_points']:,} P", inline=False)
    embed.add_field(name="🎴 전적 및 통계", value=f"**승률:** {win_rate:.1f}%\n**전적:** {total_games}전 {user['wins']}승 {user['ties']}무 {user['losses']}패\n**역대 최고 당첨금:** {user['highest_win']:,} P", inline=False)
    
    status_text = f"**연속 출석:** {user['streak']}일\n**파산 횟수:** {user['bankruptcies']}회\n**보유 칭호:** {badges}\n{bankrupt_badge}"
    embed.add_field(name="🏆 활동 및 뱃지", value=status_text, inline=False)
    
    embed.set_thumbnail(url=ctx.author.display_avatar.url if ctx.author.display_avatar else None)
    embed.set_footer(text="시스템 갱신 시간 기준")
    
    await ctx.send(embed=embed)

@bot.command(name="명령어")
async def show_help(ctx):
    embed = discord.Embed(title="📜 아섯다 봇 시스템 매뉴얼", description="디스코드 최고의 화투 봇 명령어를 확인하세요!", color=0x3498db)
    embed.add_field(name="📈 개인 통계", value="`!내정보` - 나의 전적, 승률, 파산 뱃지, 자산 확인", inline=False)
    embed.add_field(name="💰 경제 & 활동", value="`!출석` - 매일 출석체크\n`!포인트` - 현재 잔액 확인\n`!랭킹` - 서버 포인트 Top 10 확인", inline=False)
    embed.add_field(name="🎴 섯다 게임", value="`!섯다 [베팅금]` - 딜러와 1:1 대결\n`!섯다뜨자 [@유저] [베팅금]` - 유저/봇과 판돈 내기\n`!섯다설명` - 섯다 족보 및 규칙 설명", inline=False)
    embed.add_field(name="🛒 상점 시스템", value="`!상점` - 구매 가능한 칭호/역할 확인\n`!구매 [아이템이름]` - 칭호 구매", inline=False)
    await ctx.send(embed=embed)

@bot.command(name="섯다설명")
async def seotda_guide(ctx):
    embed = discord.Embed(title="🎴 섯다 족보 및 배당률 가이드", color=0xe74c3c)
    embed.add_field(name="🥇 특수 족보", value="**38광땡** (배율 5배): 3광 + 8광\n**18 / 13광땡** (배율 3배): 1광 + 8/3광\n**땡** (배율 3배): 같은 월 2장", inline=False)
    embed.add_field(name="🥈 일반 족보", value="알리, 독사, 구빙, 장빙, 장사, 세륙", inline=False)
    embed.add_field(name="🥉 끗", value="두 월의 합 끝자리 (예: 7+8=15 ➡️ 5끗)", inline=False)
    await ctx.send(embed=embed)

@bot.command(name="출석")
async def check_in(ctx):
    data = load_data()
    user = get_user_data(data, ctx.author.id, ctx.author.name, ctx.author.bot)
    
    if ctx.author.bot:
        user["last_attendance"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        user["streak"] = 1
        save_data(data)
        await ctx.send(f"🤖 **{ctx.author.display_name}**(봇)은 출석체크 보상이 누적되지 않습니다.")
        return

    kst_now = datetime.now(timezone.utc) + timedelta(hours=9)
    today_str = kst_now.strftime("%Y-%m-%d")
    yesterday_str = (kst_now - timedelta(days=1)).strftime("%Y-%m-%d")

    if user["last_attendance"] == today_str:
        await ctx.send(f"❌ 이미 오늘 출석을 완료했습니다.")
        return

    if user["last_attendance"] == yesterday_str:
        user["streak"] += 1
        reward = min(10000, user["streak"] * 100)
        msg = f"🎉 **연속 {user['streak']}일차 출석!**\n보상: **+{reward:,} P**"
    else:
        if user["last_attendance"] != "" and user["streak"] > 0:
            reward = max(100, (user["streak"] * 100) - 5000)
            msg = f"⚠️ 연속 출석을 놓쳤습니다! (페널티 적용)\n보상: **+{reward:,} P** (연속 출석 1일차 리셋)"
        else:
            reward = 100
            msg = f"🎉 첫 출석을 환영합니다!\n보상: **+{reward:,} P**"
        user["streak"] = 1

    user["points"] += reward
    if user["points"] > user["max_points"]:
        user["max_points"] = user["points"]
        
    user["last_attendance"] = today_str
    save_data(data)

    embed = discord.Embed(title="📅 출석체크", description=msg, color=0x2ecc71)
    await ctx.send(embed=embed)

@bot.command(name="포인트")
async def show_points(ctx):
    data = load_data()
    user = get_user_data(data, ctx.author.id, ctx.author.name, ctx.author.bot)
    await ctx.send(f"💰 **{ctx.author.display_name}**님의 잔액: **{user['points']:,} P**")

@bot.command(name="랭킹")
async def show_ranking(ctx):
    data = load_data()
    user_list = [u for u in data.values() if not u.get("is_bot", False)]
    sorted_users = sorted(user_list, key=lambda x: x.get("points", 0), reverse=True)

    embed = discord.Embed(title="🏆 서버 최고의 타짜 Top 10", color=0xf1c40f)
    for idx, u in enumerate(sorted_users[:10], start=1):
        medal = "🥇" if idx == 1 else "🥈" if idx == 2 else "🥉" if idx == 3 else f"`{idx}위`"
        embed.add_field(name=f"{medal} {u.get('name', '알 수 없음')}", value=f"**{u.get('points', 0):,} P**", inline=False)
    await ctx.send(embed=embed)

@bot.command(name="상점")
async def show_shop(ctx):
    embed = discord.Embed(title="🛒 아섯다 비밀 상점", description="`!구매 [아이템이름]`으로 칭호(역할)를 구매하세요!", color=0x9b59b6)
    for item_key, info in SHOP_ITEMS.items():
        embed.add_field(name=f"🏷️ {item_key}", value=f"💰 가격: {info['price']:,} P\n📝 {info['desc']}", inline=False)
    await ctx.send(embed=embed)

@bot.command(name="구매")
async def buy_item(ctx, item_name: str):
    if item_name not in SHOP_ITEMS:
        await ctx.send("❌ 존재하지 않는 아이템입니다.")
        return

    item = SHOP_ITEMS[item_name]
    data = load_data()
    user = get_user_data(data, ctx.author.id, ctx.author.name, ctx.author.bot)

    if user["points"] < item["price"]:
        await ctx.send(f"❌ 잔액이 부족합니다! (필요: **{item['price']:,} P**)")
        return

    role = await ensure_role_exists(ctx.guild, item["role_name"], item["color"])
    if not role:
        await ctx.send("🚨 `[시스템 : 봇 권한 부족. 관리자에게 봇의 '역할 관리' 권한을 요청해주세요.]`")
        return

    if role in ctx.author.roles:
        await ctx.send("❌ 이미 해당 칭호를 보유하고 계십니다!")
        return

    user["points"] -= item["price"]
    if item_name not in user["inventory"]:
        user["inventory"].append(item_name)
    save_data(data)

    await ctx.author.add_roles(role)
    await ctx.send(f"🎉 성공적으로 **[{item['role_name']}]** 칭호를 장착했습니다!")

@bot.command(name="섯다")
async def play_seotda_pve(ctx, bet: int):
    data = load_data()
    user = get_user_data(data, ctx.author.id, ctx.author.name, ctx.author.bot)

    if bet <= 0:
        await ctx.send("❌ 베팅 금액은 1 P 이상이어야 합니다.")
        return
    if user["points"] < bet:
        await ctx.send(f"❌ 잔액이 부족합니다! (현재: **{user['points']:,} P**)")
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

    embed = discord.Embed(title="🎴 딜러와의 승부 결과", color=0x3498db)
    embed.add_field(name=f"👤 {ctx.author.display_name}", value=f"[{format_card(player_cards[0])}], [{format_card(player_cards[1])}]\n➡️ **{p_hand}**", inline=False)
    embed.add_field(name="🤖 딜러", value=f"[{format_card(dealer_cards[0])}], [{format_card(dealer_cards[1])}]\n➡️ **{d_hand}**", inline=False)

    if p_score > d_score:
        multiplier = 2
        if p_score == 100: multiplier = 5
        elif p_score >= 80: multiplier = 3

        win_amount = bet * multiplier
        net_profit = win_amount - bet
        user["points"] += net_profit
        update_stats_post_game(user, "win", net_profit)
        embed.color = 0x2ecc71
        embed.description = f"🎉 **승리!** (배율: {multiplier}배)\n**+{net_profit:,} P**"
    elif p_score < d_score:
        user["points"] -= bet
        update_stats_post_game(user, "loss")
        embed.color = 0xe74c3c
        embed.description = f"💀 **패배...**\n**-{bet:,} P**"
    else:
        update_stats_post_game(user, "tie")
        embed.color = 0x95a5a6
        embed.description = "🤝 **무승부!** 베팅금이 환불됩니다."

    save_data(data)
    await ctx.send(embed=embed)

@bot.command(name="섯다뜨자")
async def play_seotda_pvp(ctx, target: discord.Member, bet: int):
    if target == ctx.author:
        await ctx.send("❌ 자신과의 대결은 불가능합니다.")
        return
    if bet <= 0:
        await ctx.send("❌ 베팅 금액은 1 P 이상이어야 합니다.")
        return

    data = load_data()
    user_a = get_user_data(data, ctx.author.id, ctx.author.display_name, ctx.author.bot)

    if user_a["points"] < bet:
        await ctx.send(f"❌ 판돈이 부족합니다! (현재: **{user_a['points']:,} P**)")
        return

    if target.bot:
        user_b = get_user_data(data, target.id, target.display_name, target.bot)
        await ctx.send(f"🤖 **{target.display_name}**(봇)이 도전을 즉시 수락했습니다!")
        
        user_a["points"] -= bet
        user_b["points"] -= bet
        
        deck = []
        for month in range(1, 11):
            deck.append((month, True if month in [1, 3, 8] else False))
            deck.append((month, False))
        random.shuffle(deck)

        a_cards = [deck.pop(), deck.pop()]
        b_cards = [deck.pop(), deck.pop()]
        a_score, a_hand = evaluate_seotda_hand(*a_cards)
        b_score, b_hand = evaluate_seotda_hand(*b_cards)

        await asyncio.sleep(1.5)
        
        if a_score > b_score:
            winner_msg = f"🎉 **{ctx.author.display_name} 승리!** 판돈 **{bet * 2:,} P** 획득!"
            user_a["points"] += (bet * 2)
            update_stats_post_game(user_a, "win", bet)
            update_stats_post_game(user_b, "loss")
        elif b_score > a_score:
            winner_msg = f"🤖 **{target.display_name} 승리!** 포인트를 잃었습니다."
            user_b["points"] += (bet * 2)
            update_stats_post_game(user_b, "win", bet)
            update_stats_post_game(user_a, "loss")
        else:
            winner_msg = "🤝 **무승부!** 판돈이 환불됩니다."
            user_a["points"] += bet
            user_b["points"] += bet
            update_stats_post_game(user_a, "tie")
            update_stats_post_game(user_b, "tie")

        save_data(data)
        
        final_content = (
            f"🔥 **VS {target.display_name} 섯다 결과!** 🔥\n\n"
            f"👤 **{ctx.author.display_name}**: [{format_card(a_cards[0])}], [{format_card(a_cards[1])}] ➡️ **{a_hand}**\n"
            f"🤖 **{target.display_name}**: [{format_card(b_cards[0])}], [{format_card(b_cards[1])}] ➡️ **{b_hand}**\n\n"
            f"{winner_msg}"
        )
        await ctx.send(final_content)
        return

    view = SeotdaPvPView(ctx.author, target, bet)
    msg = await ctx.send(
        f"🔥 {target.mention}님! **{ctx.author.display_name}**님이 **{bet:,} P**를 걸고 섯다 승부를 신청했습니다!\n"
        f"👉 아래 버튼을 눌러 응답하세요. (60초 제한)",
        view=view
    )
    view.message = msg

@bot.command(name="포인트지급")
@commands.has_permissions(administrator=True)
async def admin_give_points(ctx, target: discord.Member, amount: int):
    data = load_data()
    user = get_user_data(data, target.id, target.display_name, target.bot)
    
    user["points"] += amount
    if user["points"] > user["max_points"]:
        user["max_points"] = user["points"]
        
    save_data(data)
    await ctx.send(f"🛠️ `[시스템 : 관리자 권한]`\n**{target.display_name}**님에게 **{amount:,} P**를 지급했습니다. (잔액: **{user['points']:,} P**)")

# ==============================================================================
# 8. 토큰 실행부
# ==============================================================================
TOKEN = os.getenv("TOKEN")

if not TOKEN:
    try:
        with open("token.txt", "r", encoding="utf-8") as f:
            TOKEN = f.read().strip()
    except FileNotFoundError:
        print("❌ 토큰 오류: 환경변수나 token.txt를 확인하세요.")

if TOKEN:
    bot.run(TOKEN)
