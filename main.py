import os
import json
import urllib.parse
import requests
from bs4 import BeautifulSoup
import feedparser
import google.generativeai as genai

# --- [1. 환경 변수 및 설정] ---
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

# 상태 관리 파일 (중복 전송 방지용)
SENT_URLS_FILE = 'sent_urls.json'

def load_sent_urls():
    if os.path.exists(SENT_URLS_FILE):
        with open(SENT_URLS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def save_sent_urls(urls):
    # 최근 100개만 유지하여 파일이 너무 커지는 것을 방지
    with open(SENT_URLS_FILE, 'w', encoding='utf-8') as f:
        json.dump(urls[-100:], f, ensure_ascii=False, indent=2)

# --- [2. 정보 수집 (Google News RSS)] ---
def get_latest_news():
    # 타겟 언론사: 한겨레, 경향, MBC, 뉴스타파
    query = '정치 (site:hani.co.kr OR site:khan.co.kr OR site:imnews.imbc.com OR site:newstapa.org) when:1d'
    encoded_query = urllib.parse.quote(query)
    rss_url = f'https://news.google.com/rss/search?q={encoded_query}&hl=ko&gl=KR&ceid=KR:ko'
    
    feed = feedparser.parse(rss_url)
    return feed.entries

# 기사 본문 스크래핑 (범용)
def scrape_article_text(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        response = requests.get(url, headers=headers, timeout=10)
        # 구글 뉴스 리다이렉트를 따라간 최종 URL의 HTML 파싱
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # 기사 본문은 주로 <p> 태그에 있음. 모두 긁어모음
        paragraphs = soup.find_all('p')
        text = ' '.join([p.get_text().strip() for p in paragraphs if len(p.get_text().strip()) > 20])
        
        # 텍스트가 너무 길면 API 비용이 발생하므로 앞의 3000자만 자름
        return text[:3000] 
    except Exception as e:
        print(f"Scraping error: {e}")
        return ""

# --- [3. AI 요약 (Google Gemini API)] ---
def summarize_text(text):
    if not text:
        return "본문 추출에 실패하여 요약할 수 없습니다."
    
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        # 모델 설정 (gemini-1.5-flash가 빠르고 저렴하며 요약에 적합)
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

# --- [4. 텔레그램 전송] ---
def send_telegram_message(title, link, summary):
    text = f"📰 *{title}*\n\n💡 *[AI 핵심 요약]*\n{summary}\n\n🔗 [기사 원문 보기]({link})"
    
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
            continue # 이미 보낸 기사는 패스
            
        print(f"Processing: {entry.title}")
        
        # 1. 본문 스크래핑
        article_text = scrape_article_text(link)
        
        # 2. AI 요약
        summary = summarize_text(article_text)
        
        # 3. 텔레그램 전송
        send_telegram_message(entry.title, link, summary)
        
        # 4. 상태 저장
        new_sent_urls.append(link)
        sent_count += 1

    save_sent_urls(new_sent_urls)
    print(f"Successfully sent {sent_count} news articles.")

if __name__ == "__main__":
    main()
