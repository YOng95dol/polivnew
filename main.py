import os
import json
import time
import requests
from bs4 import BeautifulSoup
import feedparser
import google.generativeai as genai

# --- [1. 환경 변수 및 설정] ---
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

SENT_URLS_FILE = 'sent_urls.json'

# [아키텍처 변경] 구글 뉴스를 거치지 않는 언론사 공식 다이렉트 RSS
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

# --- [2. 정보 수집 (언론사 다이렉트 통합)] ---
def get_latest_news():
    all_entries = []
    
    # 4개 언론사의 RSS를 모두 가져와서 합칩니다.
    for source, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries:
                entry['source_name'] = source # 출처 태그 달기
                all_entries.append(entry)
        except Exception as e:
            print(f"Error parsing {source}: {e}")
            
    # 수집한 모든 기사를 발행 시간(최신순)으로 정렬합니다.
    def get_published_time(entry):
        return entry.get('published_parsed', time.localtime(0))
        
    all_entries.sort(key=get_published_time, reverse=True)
    return all_entries

# 기사 본문 스크래핑 (직접 접속)
def scrape_article_text(url):
    try:
        # 봇 접근을 허용하도록 일반 브라우저처럼 위장 (User-Agent 강화)
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # 기사 본문은 주로 <p> 태그에 있음. 모두 긁어모음
        paragraphs = soup.find_all('p')
        text = ' '.join([p.get_text().strip() for p in paragraphs if len(p.get_text().strip()) > 20])
        
        return text[:3000] 
    except Exception as e:
        print(f"Scraping error: {e}")
        return ""

# --- [3. AI 요약 (Google Gemini API)] ---
def summarize_text(text):
    if not text or len(text) < 100:
        return "본문 추출에 실패하여 요약할 수 없습니다."
    
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        prompt = f"""
        다음은 한국 정치 뉴스 기사 본문입니다.
        이 기사의 가장 핵심적인 내용을 파악하여, 읽기 쉽게 1~3줄의 불릿 포인트(-)로 요약해주세요.
        중립적인 어조를 유지해주세요.
        
        기사 본문:
        {text}
        """
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"Gemini API error: {e}")
        return "AI 요약 중 오류가 발생했습니다."

# --- [4. 텔레그램 전송 (UI 변경 적용)] ---
def send_telegram_message(source, title, link, summary):
    # 요청하신 대로 텍스트 문구를 빼고 📰 이모지로 심플하게 변경했습니다.
    # 출처(언론사명)를 제목 앞에 달아주어 어디 기사인지 알기 쉽게 했습니다.
    text = f"*{source} | {title}*\n\n📰\n{summary}\n\n🔗 [기사 원문 보기]({link})"
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': text,
        'parse_mode': 'Markdown',
        'disable_web_page_preview': False
    }
    requests.post(url, json=payload)

# --- [5. 메인 로직 실행] ---
def main():
    sent_urls = load_sent_urls()
    news_entries = get_latest_news()
    
    sent_count = 0
    new_sent_urls = list(sent_urls)

    for entry in news_entries:
        if sent_count >= 3: # 한 시간당 3개 제한
            break
            
        link = entry.link
        if link in sent_urls:
            continue
            
        print(f"Processing: [{entry.source_name}] {entry.title}")
        
        # 1. 원문 링크로 직접 스크래핑
        article_text = scrape_article_text(link)
        
        # 2. AI 요약
        summary = summarize_text(article_text)
        
        # 3. 텔레그램 전송
        send_telegram_message(entry.source_name, entry.title, link, summary)
        
        # 4. 상태 저장
        new_sent_urls.append(link)
        sent_count += 1
        
        # API 속도 제한(Rate Limit) 방지를 위해 기사 사이에 2초 대기
        time.sleep(2)

    save_sent_urls(new_sent_urls)
    print(f"Successfully sent {sent_count} news articles.")

if __name__ == "__main__":
    main()
