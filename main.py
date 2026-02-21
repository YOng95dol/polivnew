import os
import json
import time
import requests
import html # HTML 이스케이프를 위한 내장 모듈 추가
from bs4 import BeautifulSoup
import feedparser
import google.generativeai as genai

# --- [1. 환경 변수 및 설정] ---
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

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
            print(f"Error parsing {source}: {e}")
            
    def get_published_time(entry):
        return entry.get('published_parsed', time.localtime(0))
        
    all_entries.sort(key=get_published_time, reverse=True)
    return all_entries

def scrape_article_text(url):
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        paragraphs = soup.find_all('p')
        text = ' '.join([p.get_text().strip() for p in paragraphs if len(p.get_text().strip()) > 20])
        return text[:3000] 
    except Exception as e:
        print(f"Scraping error: {e}")
        return ""

# --- [3. AI 요약] ---
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

# --- [4. 텔레그램 전송 (근본적 문제 해결)] ---
def send_telegram_message(source, title, link, summary):
    # [핵심] HTML 문법 충돌을 막기 위해 원문의 <, >, & 등을 안전한 문자로 변환
    safe_title = html.escape(title)
    safe_summary = html.escape(summary)
    
    # [핵심] Markdown 대신 HTML 태그(<b>, <a>)를 사용하여 구조적 안정성 확보
    text = f"<b>{source} | {safe_title}</b>\n\n📰\n{safe_summary}\n\n🔗 <a href='{link}'>기사 원문 보기</a>"
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': text,
        'parse_mode': 'HTML', # HTML 모드로 변경
        'disable_web_page_preview': False
    }
    
    # [핵심] 텔레그램의 응답을 확인하여 진짜 성공했을 때만 True 반환
    try:
        response = requests.post(url, json=payload)
        result = response.json()
        if result.get('ok'):
            return True
        else:
            print(f"Telegram Delivery Failed: {result}") # GitHub 로그에 실패 원인 출력
            return False
    except Exception as e:
        print(f"Telegram Request Error: {e}")
        return False

# --- [5. 메인 로직 실행] ---
def main():
    sent_urls = load_sent_urls()
    news_entries = get_latest_news()
    
    sent_count = 0
    new_sent_urls = list(sent_urls)

    for entry in news_entries:
        if sent_count >= 3:
            break
            
        link = entry.link
        if link in sent_urls:
            continue
            
        print(f"Processing: [{entry.source_name}] {entry.title}")
        
        article_text = scrape_article_text(link)
        summary = summarize_text(article_text)
        
        # 텔레그램 전송 시도
        is_success = send_telegram_message(entry.source_name, entry.title, link, summary)
        
        # [핵심] 텔레그램 전송에 "성공"했을 때만 상태를 저장함 (False Positive 방지)
        if is_success:
            new_sent_urls.append(link)
            sent_count += 1
            print(f"Successfully sent to Telegram: {entry.title}")
        else:
            print(f"Skipping URL save due to Telegram error: {entry.title}")
            
        time.sleep(2)

    save_sent_urls(new_sent_urls)
    print(f"Total {sent_count} news articles delivered.")

if __name__ == "__main__":
    main()
