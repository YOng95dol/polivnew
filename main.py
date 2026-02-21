import os
import sys
import json
import time
import requests
import html
from bs4 import BeautifulSoup
import feedparser
import google.generativeai as genai

# --- [1. Fail-Fast: 환경 변수 무결성 검증] ---
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

# 셋 중 하나라도 비어있다면 즉시 스크립트를 빨간불(❌)로 종료시킵니다.
if not all([TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, GEMINI_API_KEY]):
    print("🚨 [치명적 오류] API 키 또는 Chat ID가 설정되지 않았습니다!")
    print("GitHub Repository Settings -> Secrets and variables -> Actions에 변수들이 정확히 등록되었는지 확인하세요.")
    sys.exit(1)

SENT_URLS_FILE = 'sent_urls.json'

RSS_FEEDS = {
    '한겨레 정치': 'https://www.hani.co.kr/rss/politics/',
    '경향신문 정치': 'https://www.khan.co.kr/rss/rssdata/politic.xml',
    'MBC 뉴스': 'https://imnews.imbc.com/rss/news.xml',
    '뉴스타파': 'https://newstapa.org/feed'
}

def load_sent_urls():
    if os.path.exists(SENT_URLS_FILE):
        with open(SENT_URLS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def save_sent_urls(urls):
    with open(SENT_URLS_FILE, 'w', encoding='utf-8') as f:
        json.dump(urls[-100:], f, ensure_ascii=False, indent=2)

# --- [2. 정보 수집] ---
def get_latest_news():
    all_entries = []
    for source, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries:
                entry['source_name'] = source
                all_entries.append(entry)
        except Exception as e:
            print(f"⚠️ Error parsing {source}: {e}")
            
    def get_published_time(entry):
        return entry.get('published_parsed', time.localtime(0))
        
    all_entries.sort(key=get_published_time, reverse=True)
    return all_entries

def scrape_article_text(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36'}
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.content, 'html.parser')
        paragraphs = soup.find_all('p')
        text = ' '.join([p.get_text().strip() for p in paragraphs if len(p.get_text().strip()) > 20])
        return text[:3000] 
    except Exception as e:
        print(f"⚠️ Scraping error: {e}")
        return ""

# --- [3. AI 요약] ---
def summarize_text(text):
    if not text or len(text) < 100:
        return "본문 추출에 실패하여 요약할 수 없습니다."
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-1.5-flash')
        prompt = f"다음은 한국 정치 뉴스 기사 본문입니다. 핵심 내용을 1~3줄의 불릿 포인트(-)로 요약하세요.\n본문: {text}"
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"⚠️ Gemini API error: {e}")
        return "AI 요약 중 오류가 발생했습니다."

# --- [4. 텔레그램 전송 (엄격한 에러 핸들링)] ---
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
        # [핵심] 실패 원인을 명확히 출력하고, 세팅 오류면 즉시 스크립트 박살내기
        error_msg = result.get('description', 'Unknown error')
        print(f"\n🚨 [텔레그램 서버 거절] 원인: {error_msg}")
        
        if result.get('error_code') in [400, 401, 404]:
            print("❌ 치명적인 오류: 봇 토큰(Token)이나 채팅방 ID(Chat ID)가 잘못되었습니다!")
            sys.exit(1) # 강제 종료하여 GitHub Actions에 빨간불을 켭니다.
            
        return False

# --- [5. 메인 로직 실행] ---
def main():
    print("🚀 봇 실행을 시작합니다...")
    sent_urls = load_sent_urls()
    print(f"📂 이전에 전송한 기사 수: {len(sent_urls)}개")
    
    news_entries = get_latest_news()
    print(f"📡 RSS에서 총 {len(news_entries)}개의 기사를 찾았습니다.")
    
    if not news_entries:
        print("🚨 기사를 하나도 가져오지 못했습니다. 스크립트를 종료합니다.")
        sys.exit(1)
    
    sent_count = 0
    new_sent_urls = list(sent_urls)

    for entry in news_entries:
        if sent_count >= 3:
            break
            
        link = entry.link
        if link in sent_urls:
            continue
            
        print(f"\n⏳ 처리 중: [{entry.source_name}] {entry.title}")
        
        article_text = scrape_article_text(link)
        summary = summarize_text(article_text)
        
        is_success = send_telegram_message(entry.source_name, entry.title, link, summary)
        
        if is_success:
            new_sent_urls.append(link)
            sent_count += 1
            print("✅ 텔레그램 전송 성공!")
        
        time.sleep(2)

    save_sent_urls(new_sent_urls)
    print(f"\n🎉 작업 완료! 총 {sent_count}개의 기사를 새로 전송했습니다.")

if __name__ == "__main__":
    main()
