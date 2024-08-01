from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import google.generativeai as genai
import yt_dlp
import os
import re
from gtts import gTTS
import tempfile
import uvicorn
import logging
import base64


# Loglama konfigürasyonu
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# FastAPI uygulamasını oluştur
app = FastAPI(
    title="YouTube Video Summarizer",
    description="Bu API, YouTube videolarını özetler ve Türkçe'ye çevirir.",
    version="1.0.0",
)

# CORS ayarları
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Sabit değer
GEMINI_API_KEY = "AIzaSyDcwWntCfE6kxleEuxJqBHVvJ0-WErzamE"
genai.configure(api_key=GEMINI_API_KEY)

class VideoRequest(BaseModel):
    video_url: str
    target_language: str
    voice_gender: str
    
def download_transcript(url):
    ydl_opts = {
        'writesubtitles': True,
        'writeautomaticsub': True,
        'subtitleslangs': ['en'],
        'skip_download': True,
        'outtmpl': 'subtitle',
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if 'subtitles' in info and 'en' in info['subtitles']:
                subtitle_url = info['subtitles']['en'][0]['url']
                ydl.download([subtitle_url])
                with open('subtitle.en.vtt', 'r', encoding='utf-8') as f:
                    content = f.read()
                os.remove('subtitle.en.vtt')
                return clean_transcript(content)
            elif 'automatic_captions' in info and 'en' in info['automatic_captions']:
                subtitle_url = info['automatic_captions']['en'][0]['url']
                ydl.download([subtitle_url])
                with open('subtitle.en.vtt', 'r', encoding='utf-8') as f:
                    content = f.read()
                os.remove('subtitle.en.vtt')
                return clean_transcript(content)
            else:
                logger.warning("İngilizce altyazı bulunamadı.")
                return None
    except Exception as e:
        logger.error(f"Transkript indirme hatası: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Transkript indirme hatası: {str(e)}")

import re
import html

def clean_transcript(content):
    # HTML etiketlerini kaldır
    content = re.sub(r'<[^>]+>', '', content)
    
    # HTML karakter kodlarını çöz
    content = html.unescape(content)
    
    # Zaman damgalarını kaldır
    content = re.sub(r'\d{2}:\d{2}:\d{2}\.\d{3} --> \d{2}:\d{2}:\d{2}\.\d{3}', '', content)
    
    # Gereksiz boşlukları ve yeni satırları temizle
    content = re.sub(r'\s+', ' ', content)
    
    # Köşeli parantez içindeki metinleri kaldır (genellikle [Müzik] gibi açıklamalar)
    content = re.sub(r'\[.*?\]', '', content)
    
    # Sayıları kaldır (genellikle altyazı numaraları)
    content = re.sub(r'^\d+$', '', content, flags=re.MULTILINE)
    
    # WEBVTT gibi altyazı formatı etiketlerini kaldır
    content = re.sub(r'^WEBVTT$', '', content, flags=re.MULTILINE)
    
    # Başındaki ve sonundaki boşlukları temizle
    content = content.strip()
    
    return content

def summarize_with_gemini(text, target_language):
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        prompt = f"""
        Translate the following English text to {target_language}, focusing only on the spoken content. 
        Provide a meaningful and coherent translation without using special characters or HTML-like tags:

        {text[:50000]}

        Instructions:
        1. Translate the content accurately.
        2. Maintain the original tone and style of the speech.
        3. If the content is inappropriate or offensive, provide a neutral summary instead.
        4. Do not include any HTML tags, special characters, or formatting in your translation.
        5. If you can't translate or summarize for any reason, return a message saying so.
        """

        response = model.generate_content(prompt)
        
        if response.text:
            # Ek temizlik işlemi
            cleaned_response = re.sub(r'[<>]', '', response.text.strip())
            return cleaned_response
        else:
            logger.warning("Gemini API boş yanıt döndürdü.")
            return "Özet oluşturulamadı. Lütfen tekrar deneyin."

    except Exception as e:
        logger.error(f"Gemini işleme hatası: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Gemini işleme hatası: {str(e)}")

import pyttsx3
from gtts import gTTS
from pydub import AudioSegment
import os
import logging

logger = logging.getLogger(__name__)

def text_to_speech(text, output_file, gender='female', language='tr', video_duration=None):
    try:
        use_pyttsx3 = language == 'en' or gender == 'male'
        
        if use_pyttsx3:
            engine = pyttsx3.init()
            voices = engine.getProperty('voices')
            
            # Dil ve cinsiyet için uygun sesi seç
            target_voice = next((voice for voice in voices if 
                                (language == 'en' and 'en' in voice.languages and 
                                ((gender == 'male' and 'male' in voice.name.lower()) or 
                                (gender == 'female' and 'female' in voice.name.lower()))) or
                                (language == 'tr' and 'tr' in voice.languages and gender == 'male')), 
                                None)
            
            if target_voice:
                engine.setProperty('voice', target_voice.id)
                logger.info(f"Seçilen ses: {target_voice.name}")
            else:
                logger.warning(f"Uygun ses bulunamadı. Varsayılan ses kullanılıyor. Dil: {language}, Cinsiyet: {gender}")
            
            # Hızı ve tonu ayarla
            engine.setProperty('rate', 150)  # Konuşma hızı (varsayılan 200)
            engine.setProperty('pitch', 100)  # Ton (varsayılan 100)
            
            # Geçici bir dosyaya kaydet
            temp_file = f"{output_file}.temp.wav"
            engine.save_to_file(text, temp_file)
            engine.runAndWait()
            
            # WAV dosyasını MP3'e dönüştür
            audio = AudioSegment.from_wav(temp_file)
            audio.export(output_file, format="mp3")
            os.remove(temp_file)
        else:
            tts = gTTS(text=text, lang=language)
            tts.save(output_file)
        
        logger.info(f"Ses dosyası başarıyla oluşturuldu: {output_file}")
        
        # Ses dosyasının süresini video süresiyle eşleştir
        if video_duration:
            audio = AudioSegment.from_mp3(output_file)
            current_duration = len(audio) / 1000  # saniye cinsinden

            if current_duration < video_duration:
                # Ses dosyasını uzat
                silence = AudioSegment.silent(duration=int((video_duration - current_duration) * 1000))
                extended_audio = audio + silence
                extended_audio.export(output_file, format="mp3")
                logger.info(f"Ses dosyası {video_duration} saniyeye uzatıldı.")
            elif current_duration > video_duration:
                # Ses dosyasını kısalt
                shortened_audio = audio[:int(video_duration * 1000)]
                shortened_audio.export(output_file, format="mp3")
                logger.info(f"Ses dosyası {video_duration} saniyeye kısaltıldı.")
        
        file_size = os.path.getsize(output_file)
        logger.info(f"Son ses dosyasının boyutu: {file_size} byte")
        
        if file_size == 0:
            raise ValueError("Oluşturulan ses dosyası boş")
        
        return output_file
    except Exception as e:
        logger.error(f"Ses dosyası oluşturma hatası: {str(e)}")
        raise
        

@app.post("/api/process")
def process_video(video_request: VideoRequest):
    logger.info(f"Video işleme başladı: {video_request.video_url}")
    video_url = video_request.video_url
    target_language = video_request.target_language
    voice_gender = video_request.voice_gender
    output_language = 'en' if target_language.lower() == 'ingilizce' else 'tr'
    
    logger.info(f"Seçilen dil: {target_language}, Ses cinsiyeti: {voice_gender}")
    
    try:
        # Video süresini al
        with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
            info = ydl.extract_info(video_url, download=False)
            video_duration = info.get('duration')
            if not video_duration:
                raise ValueError("Video süresi alınamadı.")
        
        transcript = download_transcript(video_url)
        if not transcript:
            raise HTTPException(status_code=400, detail="Transkript indirilemedi.")

        summary = summarize_with_gemini(transcript, target_language)
        if not summary:
            raise HTTPException(status_code=500, detail="Özet oluşturulamadı.")

        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as temp_audio:
            audio_file = text_to_speech(summary, temp_audio.name, gender=voice_gender, language=output_language, video_duration=video_duration)

        if not os.path.exists(audio_file) or os.path.getsize(audio_file) == 0:
            raise FileNotFoundError("Ses dosyası oluşturulamadı veya boş.")

        with open(audio_file, "rb") as audio:
            audio_data = audio.read()
        
        try:
            os.unlink(audio_file)  # Geçici dosyayı sil
        except OSError as e:
            logger.warning(f"Geçici dosya silinirken hata oluştu: {e}")
        
        logger.info("Video işleme tamamlandı.")
        
        response_data = {
            "summary": summary,
            "audio": base64.b64encode(audio_data).decode('utf-8'),
            "video_url": video_url,
            "video_id": extract_video_id(video_url),
            "video_duration": video_duration
        }
        return JSONResponse(content=response_data)
    
    except HTTPException as he:
        # HTTP hataları olduğu gibi yeniden fırlat
        raise he
    except Exception as e:
        logger.error(f"Video işleme sırasında hata oluştu: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Video işleme hatası: {str(e)}")
 
@app.get("/", response_class=HTMLResponse)
async def read_root():
    return custom_html


  
def extract_video_id(url):
    youtube_regex = r'(https?://)?(www\.)?(youtube|youtu|youtube-nocookie)\.(com|be)/(watch\?v=|embed/|v/|.+\?v=)?(?P<id>[A-Za-z0-9\-=_]{11})'
    match = re.match(youtube_regex, url)
    if match:
        return match.group('id')
    return None

custom_html = """
<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI-Powered YouTube Çeviri ve Seslendirme</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0-beta3/css/all.min.css">
    <style>
        :root {
            --primary-color: #ffd700;
            --secondary-color: #ffeb3b;
            --text-color: #f0f0f0;
            --background-color: #1a1a1a;
            --section-background: rgba(42, 42, 42, 0.7);
            --ai-color: #4CAF50;
        }
        
        .icon-button {
            font-size: 24px;
            color: var(--primary-color);
            margin: 0 10px;
            cursor: pointer;
            transition: color 0.3s ease;
        }
        .icon-button:hover {
            color: var(--secondary-color);
        }
        
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            line-height: 1.6;
            margin: 0;
            padding: 20px;
            background: linear-gradient(135deg, #1a1a1a, #2c2c2c, #1a1a1a);
            background-size: 400% 400%;
            animation: gradientAnimation 15s ease infinite;
            color: var(--text-color);
            min-height: 100vh;
        }
        
        .container {
            max-width: 800px;
            width: 100%;
            margin: 0 auto;
            background-color: var(--section-background);
            padding: 30px;
            border-radius: 15px;
            box-shadow: 0 0 20px rgba(255,215,0,0.1);
            backdrop-filter: blur(10px);
        }
        
        h1, h2 {
            color: var(--primary-color);
            text-shadow: 2px 2px 4px rgba(0,0,0,0.5);
            font-size: 26px;
        }
        
        p {
            color: var(--text-color);
        }
        
        .section {
            margin-bottom: 25px;
            padding: 20px;
            background-color: rgba(255,255,255,0.05);
            border-radius: 10px;
            transition: all 0.3s ease;
        }
        
        .section:hover {
            background-color: rgba(255,255,255,0.1);
            transform: translateY(-5px);
        }
        
        .icon {
            margin-right: 10px;
            color: var(--primary-color);
        }
        
        .ai-highlight {
            color: var(--ai-color);
            font-weight: bold;
        }
        
        .translation-section {
            text-align: center;
            padding: 40px 0;
            background: linear-gradient(45deg, rgba(255,215,0,0.1), rgba(255,235,59,0.1));
            border-radius: 15px;
            margin: 40px 0;
            box-shadow: 0 10px 30px rgba(0,0,0,0.1);
        }
        
        #translationForm {
            display: flex;
            flex-direction: column;
            align-items: center;
            max-width: 600px;
            margin: 0 auto;
        }
        
        #translationForm input[type="text"] {
            width: 100%;
            padding: 15px;
            margin: 10px 0;
            border: 2px solid var(--primary-color);
            border-radius: 8px;
            background-color: rgba(26, 26, 26, 0.7);
            color: var(--text-color);
            font-size: 16px;
            transition: all 0.3s ease;
        }
        
        #translationForm input[type="text"]:focus {
            outline: none;
            box-shadow: 0 0 15px var(--primary-color);
        }
        
        #translationForm input[type="submit"] {
            background: linear-gradient(45deg, var(--primary-color), var(--secondary-color));
            color: var(--background-color);
            padding: 15px 30px;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-size: 18px;
            font-weight: bold;
            transition: all 0.3s ease;
            margin-top: 20px;
        }
        
        #translationForm input[type="submit"]:hover {
            background: linear-gradient(45deg, var(--secondary-color), var(--primary-color));
            transform: translateY(-2px);
            box-shadow: 0 5px 15px rgba(255,215,0,0.3);
        }
        
        #loading {
            margin-top: 20px;
            display: none;
        }
        
        .loader {
            border: 5px solid #f3f3f3;
            border-top: 5px solid var(--primary-color);
            border-radius: 50%;
            width: 50px;
            height: 50px;
            animation: spin 1s linear infinite;
            margin: 20px auto;
        }
        
        .feature-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-top: 20px;
        }
        
        .feature-item {
            background-color: rgba(255,255,255,0.05);
            padding: 15px;
            border-radius: 8px;
            text-align: center;
            transition: all 0.3s ease;
        }
        
        .feature-item:hover {
            background-color: rgba(255,255,255,0.1);
            transform: translateY(-5px);
        }
        
        .feature-icon {
            font-size: 2em;
            margin-bottom: 10px;
            color: var(--primary-color);
        }
        
        .ai-animation {
            width: 200px;
            height: 200px;
            margin: 20px auto;
            position: relative;
            display: none;
        }
        
        .ai-circle {
            position: absolute;
            width: 100%;
            height: 100%;
            border: 4px solid var(--ai-color);
            border-radius: 50%;
            animation: pulse 2s infinite;
        }
        
        transform: translate(-50%, -50%);
            font-size: 3em;
            color: var(--primary-color);
        }
        
        #player {
            margin-top: 20px;
            display: none;
            width: 100%;
            max-width: 100%;
        }
        
        #player iframe {
            width: 100%;
            height: 56.25vw;
            max-height: 360px;
        }
        
        .control-buttons {
            margin-top: 10px;
        }
        
        .control-button {
            background: linear-gradient(45deg, var(--primary-color), var(--secondary-color));
            color: var(--background-color);
            padding: 10px 20px;
            border: none;
            border-radius: 5px;
            cursor: pointer;
            font-size: 16px;
            font-weight: bold;
            transition: all 0.3s ease;
            margin: 0 5px;
            display: none;
        }
        
        .control-button:hover {
            background: linear-gradient(45deg, var(--secondary-color), var(--primary-color));
            transform: translateY(-2px);
            box-shadow: 0 5px 15px rgba(255,215,0,0.3);
        }
        
        @keyframes gradientAnimation {
            0% {background-position: 0% 50%;}
            50% {background-position: 100% 50%;}
            100% {background-position: 0% 50%;}
        }
        
        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
        
        @keyframes pulse {
            0% {
                transform: scale(0.95);
                opacity: 0.7;
            }
            50% {
                transform: scale(1.05);
                opacity: 1;
            }
            100% {
                transform: scale(0.95);
                opacity: 0.7;
            }
        }
        
        @media (max-width: 640px) {
            #player iframe {
                height: 56.25vw;
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 100" style="width: 150px; height: 50px; vertical-align: middle; margin-right: 10px;">
                <style>
                    @keyframes wave {
                        0% { transform: translateY(0); }
                        50% { transform: translateY(-5px); }
                        100% { transform: translateY(0); }
                    }
                    @keyframes flow {
                        0% { stroke-dashoffset: 1000; }
                        100% { stroke-dashoffset: 0; }
                    }
                    .wave { animation: wave 1.5s ease-in-out infinite; }
                    .flow { 
                        fill: none;
                        stroke: #FFD700;
                        stroke-width: 2;
                        stroke-dasharray: 1000;
                        stroke-dashoffset: 1000;
                        animation: flow 5s linear infinite;
                    }
                </style>
                <defs>
                    <linearGradient id="grad" x1="0%" y1="0%" x2="100%" y2="0%">
                        <stop offset="0%" style="stop-color:#FFD700;stop-opacity:1" />
                        <stop offset="100%" style="stop-color:#FFA500;stop-opacity:1" />
                    </linearGradient>
                </defs>
                <rect width="300" height="100" fill="#282828"/>
                <text x="10" y="70" font-family="Arial, sans-serif" font-size="50" font-weight="bold" fill="url(#grad)">Voice</text>
                <text x="160" y="70" font-family="Arial, sans-serif" font-size="50" font-weight="bold" fill="#FFD700">Flux</text>
                <g class="wave">
                    <path d="M10 80 Q 30 70, 50 80 T 90 80" fill="none" stroke="#FFD700" stroke-width="3"/>
                    <path d="M100 80 Q 120 90, 140 80 T 180 80" fill="none" stroke="#FFD700" stroke-width="3"/>
                    <path d="M190 80 Q 210 70, 230 80 T 270 80" fill="none" stroke="#FFD700" stroke-width="3"/>
                </g>
                <path class="flow" d="M5 90 C 100 40, 200 140, 295 90" />
            </svg>
            AI-Powered YouTube Çeviri ve Seslendirme
        </h1>
    
       <div class="section">
            <h2><i class="fas fa-info-circle icon"></i>Bu Uygulama Nedir?</h2>
            <p>Bu uygulama, <span class="ai-highlight">yapay zeka teknolojisi</span> kullanarak YouTube videolarının içeriğini Türkçe'ye çevirir ve seslendirir. Gelişmiş <span class="ai-highlight">doğal dil işleme</span> sayesinde, videolar için transkript oluşturur, bu transkripti Türkçe'ye çevirir ve son olarak çeviriyi sesli olarak okur.</p>
        </div>

        <div class="section">
            <h2><i class="fas fa-language icon"></i>Çoklu Dil Desteği</h2>
            <p>Uygulamamız artık <span class="ai-highlight">herhangi bir dildeki</span> YouTube videolarını Türkçe'ye çevirebilir. Yapay zeka modelimiz, videonun orijinal dilini otomatik olarak algılar ve Türkçe'ye çevirir. Bu özellik sayesinde, dünya çapındaki içeriklere Türkçe olarak erişebilirsiniz.</p>
        </div>

        <div class="translation-section">
            <h2><i class="fas fa-language icon"></i>Video Çevirisi Başlat</h2>
            <form id="translationForm">
                <input type="text" id="videoUrl" name="video_url" placeholder="YouTube Video URL'sini girin" required>
                
                <p>Videonun Çevrilmesini istediğiniz dili seçiniz.</p>
                <select id="targetLanguage" name="target_language" style="width: 25%; padding: 5px; margin: 10px 0; border: 2px solid var(--primary-color); border-radius: 8px; font-size: 16px;">
                    <option value="Türkçe">Türkçe</option>
                    <option value="İngilizce">İngilizce</option>
                    <option value="Fransızca">Fransızca</option>
                    <option value="Almanca">Almanca</option>
                </select>
                
                <p>Ses cinsiyetini seçiniz:</p>
                <select id="voiceGender" name="voice_gender" style="width: 25%; padding: 5px; margin: 10px 0; border: 2px solid var(--primary-color); border-radius: 8px; font-size: 16px;">
                    <option value="female">Kadın</option>
                    <option value="male">Erkek</option>
                </select>
                
                <input type="submit" value="Çevir ve Oynat">
            </form>
            <div id="loading">
                <p>Yapay zeka çeviri ve seslendirme işlemi devam ediyor...</p>
                <div class="loader"></div>
                <div class="ai-animation">
                    <div class="ai-circle"></div>
                    <i class="fas fa-robot ai-icon"></i>
                </div>
            </div>
            <div id="player"></div>
            <audio id="audioPlayer" hidden>
                <source src="" type="audio/mpeg">
            </audio>
            <div class="control-buttons">
                <button id="playButton" class="control-button">Oynat</button>
                <button id="pauseButton" class="control-button">Duraklat</button>
            </div>
            <div id="iconButtons" style="display: none; justify-content: center; margin-top: 10px;">
                <a id="shareLink" href="" target="_blank">
                    <i class="fas fa-share-alt icon-button" id="shareButton" title="Paylaş"></i>
                </a>
            </div>
            <input type="range" id="volumeControl" min="0" max="1" step="0.1" value="1" style="width: 200px; margin-top: 10px;">
        </div>

        <!-- Öne Çıkan Özellikler ve Footer bölümleri aynı kalacak -->

    </div>

    <script src="https://www.youtube.com/iframe_api"></script>
    <script src="https://ajax.googleapis.com/ajax/libs/jquery/3.5.1/jquery.min.js"></script>
    <script>
    let player;
    let audioPlayer;
    let isTranslationStarted = false;
    let isPlaying = false;

    document.getElementById('translationForm').addEventListener('submit', function(e) {
        e.preventDefault();
        const videoUrl = document.querySelector('input[name="video_url"]').value;
        const targetLanguage = document.getElementById('targetLanguage').value;
        const voiceGender = document.getElementById('voiceGender').value;
        document.getElementById('loading').style.display = 'block';
        document.querySelector('.ai-animation').style.display = 'block';
        
      fetch('/api/process', {
    method: 'POST',
    headers: {
        'Content-Type': 'application/json',
    },
    body: JSON.stringify({
        video_url: videoUrl,
        target_language: targetLanguage,
        voice_gender: voiceGender
    })
})

        .then(response => response.json())
        .then(data => {
            console.log("Received data:", data);  // Log the entire received data
            if (data.error) {
                throw new Error(data.error);
            }
            if (!data.audio || !data.video_id) {
                throw new Error("Ses verisi veya video ID eksik");
            }
            initializePlayer(data.video_id, data.audio);
            document.getElementById('shareLink').href = `https://www.youtube.com/watch?v=${data.video_id}`;
            document.getElementById('iconButtons').style.display = 'flex';
        })
        .catch(error => {
            console.error('Error:', error);
            alert('Çeviri işlemi sırasında bir hata oluştu: ' + error.message);
        })
        .finally(() => {
            document.getElementById('loading').style.display = 'none';
        });
    });

    function initializePlayer(videoId, audioData) {
        console.log("Initializing player with video ID:", videoId);
        console.log("Audio data length:", audioData.length);
        
        player = new YT.Player('player', {
            height: '360',
            width: '640',
            videoId: videoId,
            events: {
                'onReady': function() { onPlayerReady(audioData); },
                'onStateChange': onPlayerStateChange
            }
        });
        document.getElementById('player').style.display = 'block';
    }

    function onPlayerReady(audioData) {
        console.log("Player is ready. Processing audio data...");
        if (!audioData || audioData.length === 0) {
            console.error("Ses verisi boş veya geçersiz");
            alert("Ses dosyası oluşturulamadı. Lütfen tekrar deneyin.");
            return;
        }

        try {
            const audioBlob = new Blob([Uint8Array.from(atob(audioData), c => c.charCodeAt(0))], {type: 'audio/mp3'});
            console.log("Audio blob created, size:", audioBlob.size);
            
            audioPlayer = new Audio(URL.createObjectURL(audioBlob));
            audioPlayer.oncanplaythrough = function() {
                isTranslationStarted = true;
                showControls();
                console.log("Audio is ready to play");
            };
            audioPlayer.onerror = function(e) {
                console.error("Ses dosyası yüklenirken hata oluştu:", e);
                alert("Ses dosyası yüklenirken bir hata oluştu. Lütfen tekrar deneyin.");
            };
            audioPlayer.load();
        } catch (error) {
            console.error("Ses verisi işlenirken hata oluştu:", error);
            alert("Ses verisi işlenirken bir hata oluştu. Lütfen tekrar deneyin.");
        }
    }

    function onPlayerStateChange(event) {
        console.log("Player state changed:", event.data);
        if (event.data == YT.PlayerState.PLAYING) {
            if (!isPlaying) {
                console.log("Starting audio playback");
                audioPlayer.currentTime = player.getCurrentTime();
                var playPromise = audioPlayer.play();
                if (playPromise !== undefined) {
                    playPromise.then(_ => {
                        isPlaying = true;
                        console.log("Audio playback started successfully");
                    })
                    .catch(error => {
                        console.error("Audio playback failed:", error);
                    });
                }
            }
        } else if (event.data == YT.PlayerState.PAUSED) {
            console.log("Pausing audio");
            audioPlayer.pause();
            isPlaying = false;
        }
    }

    function showControls() {
        document.getElementById('playButton').style.display = 'inline-block';
        document.getElementById('pauseButton').style.display = 'inline-block';
    }

    document.getElementById('playButton').addEventListener('click', function() {
        if (isTranslationStarted) {
            player.playVideo();
            audioPlayer.currentTime = player.getCurrentTime();
            audioPlayer.play();
            isPlaying = true;
        } else {
            alert("Çeviri henüz hazır değil. Lütfen bekleyin.");
        }
    });

    document.getElementById('pauseButton').addEventListener('click', function() {
        player.pauseVideo();
        audioPlayer.pause();
        isPlaying = false;
    });

    setInterval(function() {
        if (isPlaying) {
            const videoDuration = player.getDuration();
            const videoCurrentTime = player.getCurrentTime();
            const audioDuration = audioPlayer.duration;
            
            if (videoCurrentTime >= videoDuration || audioPlayer.currentTime >= audioDuration) {
                player.pauseVideo();
                audioPlayer.pause();
                isPlaying = false;
            } else {
                const timeDiff = Math.abs(videoCurrentTime - audioPlayer.currentTime);
                if (timeDiff > 0.5) {
                    audioPlayer.currentTime = videoCurrentTime;
                }
            }
        }
    }, 100);

    document.getElementById('volumeControl').addEventListener('input', function(e) {
        audioPlayer.volume = e.target.value;
    });
    </script>
</body>
</html>
"""

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7860)

    
