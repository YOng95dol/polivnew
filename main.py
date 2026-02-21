import os
import sys
import json
import time
import requests
import html
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

RSS_FEEDS = {
    '한겨레 정치': 'https://www.hani.co.kr/rss/politics/',
    '경향신문 정치': 'https://www.khan.co.kr/rss/rssdata/politic.xml',
    'MBC 뉴스': 'https://imnews.imbc.com/rss/news.xml',
    '뉴스타파': 'https://newstapa.org/feed'
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
        json.dump(urls[-100:], f, ensure_ascii=False, indent=2)

# --- [2. 정보 수집 (각 언론사별 1개씩 공평 분배 아키텍처)] ---
def get_one_news_per_source(sent_urls):
    selected_entries = []
    
    # 4개 언론사를 하나씩 순회합니다.
    for source, url in RSS_FEEDS.items():
        try:
            response = requests.get(url, headers=BROWSER_HEADERS, timeout=10)
            feed = feedparser.parse(response.content)
            
            # 해당 언론사의 기사들을 최신순으로 정렬
            def get_published_time(entry):
                return entry.get('published_parsed', time.localtime(0))
            sorted_entries = sorted(feed.entries, key=get_published_time, reverse=True)
            
            # 정렬된 기사 중 "아직 안 보낸" 가장 최신 기사 딱 '1개'만 찾고 빠져나옵니다.
            for entry in sorted_entries:
                if entry.link not in sent_urls:
                    entry['source_name'] = source
                    selected_entries.append(entry)
                    break # 1개를 찾았으므로 이 언론사 검색은 종료하고 다음 언론사로 넘어감
                    
        except Exception as e:
            print(f"⚠️ {source} 파싱 에러: {e}")
            
    return selected_entries

def scrape_article_text(url):
    try:
        response = requests.get(url, headers=BROWSER_HEADERS, timeout=10)
        soup = BeautifulSoup(response.content, 'html.parser')
        paragraphs = soup.find_all('p')
        text = ' '.join([p.get_text().strip() for p in paragraphs if len(p.get_text().strip()) > 20])
        return text[:3000] 
    except Exception as e:
        print(f"⚠️ 본문 추출 에러: {e}")
        return ""

# --- [3. AI 요약 (안전 필터 해제 아키텍처 적용)] ---
def summarize_text(text):
    if not text or len(text) < 100:
        return "본문 추출에 실패하여 요약할 수 없습니다."
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        prompt = f"다음은 한국 정치 뉴스 기사 본문입니다. 핵심 내용을 1~3줄의 불릿 포인트(-)로 요약하세요.\n본문: {text}"
        
        # [핵심] 정치 뉴스의 격한 단어 때문에 AI가 거부하지 않도록 안전 필터링을 완전히 끕니다.
        response = client.models.generate_content(
            model='gemini-2.5-flash', # 최신 빠르고 안정적인 모델
            contents=prompt,
            config=types.GenerateContentConfig(
                safety_settings=[
                    types.SafetySetting(
                        category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                        threshold=types.HarmBlockThreshold.BLOCK_NONE,
                    ),
                    types.SafetySetting(
                        category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                        threshold=types.HarmBlockThreshold.BLOCK_NONE,
                    ),
                    types.SafetySetting(
                        category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                        threshold=types.HarmBlockThreshold.BLOCK_NONE,
                    ),
                    types.SafetySetting(
                        category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                        threshold=types.HarmBlockThreshold.BLOCK_NONE,
                    )
                ]
            )
        )
        return response.text.strip()
    except Exception as e:
        # 안전 필터마저 뚫지 못한 극단적 예외 상황 처리 (텔레그램 전송 자체는 성공하게 만듦)
        print(f"⚠️ Gemini API error 상세: {e}")
        return "🤖 AI 안전 필터가 작동하여 요약이 차단된 기사입니다. (정치적 민감 단어 과다 포함)\n아래 기사 원문을 직접 확인해주세요."

# --- [4. 텔레그램 전송] ---
def send_telegram_message(source, title, link, summary):
    safe_title = html.escape(title)
    safe_summary = html.escape(summary)
    
    text = f"<b>{source} | {safe_title}</b>\n\n📰\n{safe_summary}\n\n🔗 <a href='{link}'>기사 원문 보기</a>"
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
    print("🚀 봇 실행을 시작합니다... (1언론사 1기사 할당 & AI 필터 해제 모드)")
    sent_urls = load_sent_urls()
    
    # [핵심] 4개 언론사에서 각각 1개씩, 최대 4개의 기사만 정확히 뽑아옵니다.
    news_entries = get_one_news_per_source(sent_urls)
    print(f"📡 각 언론사별 최신 기사 총 {len(news_entries)}개를 선정했습니다.")
    
    if not news_entries:
        print("ℹ️ 새로 업데이트된 기사가 없습니다. 스크립트를 종료합니다.")
        sys.exit(0)
    
    sent_count = 0
    new_sent_urls = list(sent_urls)

    for entry in news_entries:
        print(f"\n⏳ 처리 중: [{entry.source_name}] {entry.title}")
        
        article_text = scrape_article_text(link=entry.link)
        summary = summarize_text(article_text)
        
        is_success = send_telegram_message(entry.source_name, entry.title, entry.link, summary)
        
        if is_success:
            new_sent_urls.append(entry.link)
            sent_count += 1
            print("✅ 텔레그램 전송 성공!")
        
        time.sleep(3)

    save_sent_urls(new_sent_urls)
    print(f"\n🎉 작업 완료! 각 언론사별 총 {sent_count}개의 기사를 새로 전송했습니다.")

if __name__ == "__main__":
    main()
