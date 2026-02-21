import os
import sys
import json
import time
import requests
import html
import re
from bs4 import BeautifulSoup
import feedparser

# 최신 구글 AI 라이브러리와 설정(types) 모듈 불러오기
from google import genai
from google.genai import types

# --- [1. Fail-Fast: 환경 변수 검증] ---
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

if not all([TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, GEMINI_API_KEY]):
    print("🚨 [치명적 오류] API 키 또는 Chat ID가 설정되지 않았습니다!")
    sys.exit(1)

SENT_URLS_FILE = 'sent_urls.json'

# [핵심 변경 1] 사용자가 요청한 총 6개의 고품질 정치 전문 RSS 라인업
RSS_FEEDS = {
    '경향신문': 'https://www.khan.co.kr/rss/rssdata/politic_news.xml',
    'JTBC': 'https://news-ex.jtbc.co.kr/v1/get/rss/section/politics',
    '오마이뉴스': 'https://rss.ohmynews.com/rss/politics.xml',
    '노컷뉴스': 'https://rss.nocutnews.co.kr/category/politics.xml',
    '뉴시스': 'https://www.newsis.com/RSS/politics.xml',
    '연합뉴스TV': 'https://www.yonhapnewstv.co.kr/category/news/politics/feed/'
}

BROWSER_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36'
}

def load_sent_urls():
    if os.path.exists(SENT_URLS_FILE):
        with open(SENT_URLS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def save_sent_urls(urls):
    with open(SENT_URLS_FILE, 'w', encoding='utf-8') as f:
        json.dump(urls[-150:], f, ensure_ascii=False, indent=2) # 6개 언론사로 늘었으므로 기억 용량 증가

def get_one_news_per_source(sent_urls):
    selected_entries = []
    
    for source, url in RSS_FEEDS.items():
        try:
            response = requests.get(url, headers=BROWSER_HEADERS, timeout=10)
            feed = feedparser.parse(response.content)
            
            def get_published_time(entry):
                return entry.get('published_parsed', time.localtime(0))
            sorted_entries = sorted(feed.entries, key=get_published_time, reverse=True)
            
            for entry in sorted_entries:
                if entry.link not in sent_urls:
                    entry['source_name'] = source
                    selected_entries.append(entry)
                    break 
                    
        except Exception as e:
            print(f"⚠️ {source} 파싱 에러: {e}")
            
    return selected_entries

# --- [2. 정보 수집 (뉴시스, 연합뉴스TV 등 모든 구조에 대응하는 슈퍼 스크래퍼)] ---
def scrape_article_text(url):
    try:
        response = requests.get(url, headers=BROWSER_HEADERS, timeout=10)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        text = ""
        
        # 언론사별 본문이 담긴 커다란 상자(div, article)를 먼저 찾는 로직 (뉴시스 textBody 등 추가)
        article_body = soup.find(id=['articlebody', 'article_body', 'dic_area', 'newsEndContents', 'news_body_id', 'content_zone', 'textBody'])
        if not article_body:
            article_body = soup.find(class_=['article_content', 'article_body', 'news_body', 'content_area', 'at_contents', 'viewer', 'article-con'])
            
        if article_body:
            text = article_body.get_text(separator=' ', strip=True)
            
        # 위 방법으로 못 찾았거나 텍스트가 너무 짧으면 범용 <p> 태그 탐색
        if len(text) < 100:
            paragraphs = soup.find_all('p')
            text = ' '.join([p.get_text(separator=' ', strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 20])
            
        text = re.sub(r'\s+', ' ', text)
        return text[:3000] 
        
    except Exception as e:
        print(f"⚠️ 본문 추출 에러: {e}")
        return ""

# --- [3. AI 요약 (국내 정치 판별 AI 데스크 아키텍처)] ---
def summarize_text(text):
    if not text or len(text) < 100:
        return "ERROR_NO_TEXT"
        
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=text, 
            config=types.GenerateContentConfig(
                # [핵심 변경 2] AI에게 '한국 국내 정치'가 아니면 "SKIP"을 외치라고 명확히 지시
                system_instruction="당신은 한국 국내 정치 뉴스를 선별하고 요약하는 전문 편집장(AI)입니다. 주어진 기사 본문을 읽고, 만약 이 기사가 '한국 국내 정치'와 관련이 없다면(예: 해외 정치, 단순 경제, 연예, 스포츠, 날씨 등) 오직 'SKIP'이라고만 출력하세요. 한국 국내 정치 기사가 맞다면, 핵심 내용만 1~3줄의 불릿 포인트(-) 형식으로 요약하세요. 인사말, 안내문구는 절대 금지합니다.",
                
                safety_settings=[
                    types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=types.HarmBlockThreshold.BLOCK_NONE),
                    types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
                    types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
                    types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=types.HarmBlockThreshold.BLOCK_NONE)
                ]
            )
        )
        return response.text.strip()
    except Exception as e:
        print(f"⚠️ Gemini API error 상세: {e}")
        return "ERROR_AI_FILTER"

# --- [4. 텔레그램 전송 (UI 유지)] ---
def send_telegram_message(title, link, summary):
    safe_title = html.escape(title)
    safe_summary = html.escape(summary)
    
    text = f"<b>{safe_title}</b>\n\n📰\n{safe_summary}\n\n🔗 <a href='{link}'>기사 원문 보기</a>"
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': text,
        'parse_mode': 'HTML',
        'disable_web_page_preview': False
    }
    
    response = requests.post(url, json=payload)
    result = response.json()
    
    if result.get('ok'):
        return True
    else:
        print(f"\n🚨 [텔레그램 서버 거절] 원인: {result.get('description')}")
        return False

# --- [5. 메인 로직 실행] ---
def main():
    print("🚀 봇 실행을 시작합니다... (6개 언론사 & AI 국내정치 필터링 모드)")
    sent_urls = load_sent_urls()
    
    news_entries = get_one_news_per_source(sent_urls)
    print(f"📡 각 언론사별 최신 기사 총 {len(news_entries)}개를 선정했습니다.")
    
    if not news_entries:
        print("ℹ️ 새로 업데이트된 기사가 없습니다. 스크립트를 종료합니다.")
        sys.exit(0)
    
    sent_count = 0
    new_sent_urls = list(sent_urls)

    for entry in news_entries:
        print(f"\n⏳ 처리 중: [{entry.source_name}] {entry.title}")
        
        article_text = scrape_article_text(entry.link)
        summary = summarize_text(article_text)
        
        # 본문 스크래핑 실패 시
        if summary == "ERROR_NO_TEXT":
            print("⚠️ 본문 추출 실패. 전송을 건너뜁니다.")
            new_sent_urls.append(entry.link) # 에러난 기사는 재시도하지 않도록 저장
            continue
            
        # [핵심 변경 3] AI가 한국 국내 정치가 아니라고 판단하여 SKIP을 외친 경우
        if summary == "SKIP":
            print(f"⏩ [AI 필터링 됨] 비정치/해외 기사로 분류되어 텔레그램 전송을 차단합니다: {entry.title}")
            new_sent_urls.append(entry.link) # 이미 검토했으므로 저장하여 다음 시간에 또 읽지 않게 함
            continue
            
        if summary == "ERROR_AI_FILTER":
            summary = "🤖 AI 안전 필터가 작동하여 요약이 차단된 기사입니다.\n아래 기사 원문을 직접 확인해주세요."
        
        # AI 검증을 통과한 양질의 국내 정치 뉴스만 텔레그램 전송!
        is_success = send_telegram_message(entry.title, entry.link, summary)
        
        if is_success:
            new_sent_urls.append(entry.link)
            sent_count += 1
            print("✅ 텔레그램 전송 성공!")
        
        time.sleep(3)

    save_sent_urls(new_sent_urls)
    print(f"\n🎉 작업 완료! 엄선된 총 {sent_count}개의 기사를 텔레그램으로 전송했습니다.")

if __name__ == "__main__":
    main()
