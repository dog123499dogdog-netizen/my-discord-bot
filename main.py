import http.server
import socketserver
import threading
import os

# Render 무료(Web Service) 인식을 위한 웹 서버 실행 (8080 포트)
def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    handler = http.server.SimpleHTTPRequestHandler
    with socketserver.TCPServer(("", port), handler) as httpd:
        httpd.serve_forever()

threading.Thread(target=run_web_server, daemon=True).start()

# ------ 여기서부터는 기존 코드 시작 ------
import discord
from discord.ext import commands
# ... (이하 기존 main.py 코드 그대로 유지)
import discord
from discord.ext import commands
import random
import asyncio
from datetime import date

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

user_points = {}
last_daily = {}

@bot.event
async def on_ready():
    print(f"로그인 성공: {bot.user}")
    print("아날로그 섯다 판이 열렸습니다! (! 느낌표 명령어 모드)")

# [기본 명령어] !잔액
@bot.command(name="잔액")
async def balance(ctx):
    user_id = ctx.author.id
    if user_id not in user_points: user_points[user_id] = 1000
    await ctx.send(f"💰 **{ctx.author.name}**님의 남은 쩐: **{user_points[user_id]:,}원**")

# [기본 명령어] !출석
@bot.command(name="출석")
async def daily(ctx):
    user_id = ctx.author.id
    today = date.today()
    if user_id not in user_points: user_points[user_id] = 1000

    if user_id in last_daily and last_daily[user_id] == today:
        await ctx.send("❌ 오늘은 이미 지원금을 받았습니다. 내일 다시 오시지요.")
        return

    last_daily[user_id] = today
    user_points[user_id] += 1000
    
    embed = discord.Embed(title="🎁 일일 지원금 도착", description="투전판에 오신 것을 환영합니다.", color=0x2ecc71)
    embed.add_field(name="지급액", value="+1,000원", inline=True)
    embed.add_field(name="현재 잔액", value=f"{user_points[user_id]:,}원", inline=True)
    await ctx.send(embed=embed)

# ---------------------------------------------------------
# [핵심] 고퀄리티 아날로그 섯다 시스템
# ---------------------------------------------------------

def get_deck():
    """1월~10월 화투패 생성 (1, 3, 8월은 광 포함)"""
    deck = []
    gwang_months = [1, 3, 8]
    for m in range(1, 11):
        if m in gwang_months:
            deck.append((m, True))  # (월, 광 여부)
            deck.append((m, False)) # 일반 패
        else:
            deck.append((m, False))
            deck.append((m, False))
    random.shuffle(deck)
    return deck

def evaluate_seotda(cards):
    """섯다 족보 계산기"""
    c1, c2 = cards[0], cards[1]
    
    if c1[0] > c2[0]:
        c1, c2 = c2, c1
        
    m1, is_gwang1 = c1
    m2, is_gwang2 = c2

    # 1. 광땡
    if is_gwang1 and is_gwang2:
        if m1 == 3 and m2 == 8: return 3800, "🌟 38광땡 🌟"
        if m1 == 1 and m2 == 8: return 1800, "✨ 18광땡 ✨"
        if m1 == 1 and m2 == 3: return 1300, "✨ 13광땡 ✨"

    # 2. 땡
    if m1 == m2:
        return 1000 + (m1 * 10), f"🔥 {m1}땡 🔥"

    # 3. 특수 족보
    if m1 == 1 and m2 == 2: return 900, "알리"
    if m1 == 1 and m2 == 4: return 800, "독사"
    if m1 == 4 and m2 == 9: return 700, "구사"
    if m1 == 4 and m2 == 10: return 600, "장사"
    if m1 == 1 and m2 == 10: return 500, "장삥"
    if m1 == 4 and m2 == 6: return 400, "세륙"

    # 4. 끗
    ggut = (m1 + m2) % 10
    if ggut == 9: return 90, "갑오 (9끗)"
    if ggut == 0: return 0, "망통 (0끗) 💦"
    return ggut, f"{ggut}끗"

def format_card(card):
    month, is_gwang = card
    if is_gwang:
        return f"**[{month}월 광(光)]** ☀️"
    else:
        return f"[{month}월]"

# [게임 명령어] !섯다 [금액]
@bot.command(name="섯다")
async def seotda(ctx, 배팅금액: int):
    user_id = ctx.author.id
    if user_id not in user_points: user_points[user_id] = 1000

    if 배팅금액 <= 0:
        await ctx.send("❌ 배팅 금액은 1원 이상이어야 합니다.")
        return
    if user_points[user_id] < 배팅금액:
        await ctx.send(f"❌ 쩐이 부족합니다. (현재: {user_points[user_id]:,}원)")
        return

    # 카드 분배
    deck = get_deck()
    player_cards = [deck.pop(), deck.pop()]
    dealer_cards = [deck.pop(), deck.pop()]

    # 1단계: 패를 섞고 돌리는 연출
    embed = discord.Embed(title="🎴 섯다 판이 열렸습니다", description="딜러가 화투패를 섞는 중입니다...", color=0x34495e)
    msg = await ctx.send(embed=embed)
    await asyncio.sleep(2)

    embed.description = "손목은 걸지 않겠습니다. 패를 돌립니다...\n\n**[ 당신의 패 ]**\n 🎴 [ ? ]   🎴 [ ? ]"
    await msg.edit(embed=embed)
    await asyncio.sleep(2)

    # 2단계: 첫 번째 패 공개
    embed.description = f"첫 번째 패를 슬쩍 확인합니다...\n\n**[ 당신의 패 ]**\n {format_card(player_cards[0])}   🎴 [ ? ]"
    embed.color = 0xe67e22
    await msg.edit(embed=embed)
    await asyncio.sleep(2.5)

    # 3단계: 결과 공개 및 승패 판정
    p_score, p_name = evaluate_seotda(player_cards)
    d_score, d_name = evaluate_seotda(dealer_cards)

    result_title = ""
    color = 0x000000

    if p_score > d_score:
        user_points[user_id] += 배팅금액
        result_title = f"🎉 승리! (+{배팅금액:,}원)"
        color = 0x2ecc71
    elif p_score < d_score:
        user_points[user_id] -= 배팅금액
        result_title = f"💀 패배... (-{배팅금액:,}원)"
        color = 0xe74c3c
    else:
        result_title = "🤝 무승부 (판돈 반환)"
        color = 0x95a5a6

    final_embed = discord.Embed(title=result_title, color=color)
    final_embed.add_field(
        name=f"👤 {ctx.author.name}의 패", 
        value=f"{format_card(player_cards[0])}  {format_card(player_cards[1])}\n**결과: {p_name}**", 
        inline=False
    )
    final_embed.add_field(
        name="🤖 딜러의 패", 
        value=f"{format_card(dealer_cards[0])}  {format_card(dealer_cards[1])}\n**결과: {d_name}**", 
        inline=False
    )
    final_embed.add_field(name="💰 남은 쩐", value=f"**{user_points[user_id]:,}원**", inline=False)
    final_embed.set_footer(text="다음 판을 준비하시겠습니까?")

    await msg.edit(embed=final_embed)

# 금액을 안 적고 !섯다 만 쳤을 때 발생하는 에러 처리
@seotda.error
async def seotda_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("❌ 배팅할 금액을 입력해 주세요! (예: `!섯다 500`)")

# token.txt 메모장 파일에서 토큰을 읽어와 실행합니다.
with open("token.txt", "r", encoding="utf-8") as f:
    TOKEN = f.read().strip()

bot.run(TOKEN)
