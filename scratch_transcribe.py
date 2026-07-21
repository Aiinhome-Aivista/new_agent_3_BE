import yt_dlp
import os
import speech_recognition as sr
from pydub import AudioSegment
import imageio_ffmpeg

ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
AudioSegment.converter = ffmpeg_path

url = "https://drive.google.com/file/d/1oT3i2Ec3zzQu5Coqtk82QR2mEl1oHrRp/view?usp=sharing"

def extract_and_transcribe(video_url):
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': 'temp_audio.%(ext)s',
        'quiet': False
    }
    
    print("Downloading audio...")
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            ext = info.get('ext', 'm4a')
    except Exception as e:
        print(f"Failed to download: {e}")
        return
        
    print(f"Downloaded. Converting {ext} to wav...")
    
    try:
        audio = AudioSegment.from_file(f"temp_audio.{ext}")
        audio.export("temp_audio.wav", format="wav")
    except Exception as e:
        print(f"Conversion failed: {e}")
        return
    
    print("Transcribing...")
    r = sr.Recognizer()
    try:
        with sr.AudioFile('temp_audio.wav') as source:
            # Try transcribing 60 seconds
            audio_data = r.record(source, duration=60)
            text = r.recognize_google(audio_data)
            print("Transcription snippet:")
            print(text)
    except Exception as e:
        print(f"Transcription failed: {e}")
        
    if os.path.exists(f'temp_audio.{ext}'):
        os.remove(f'temp_audio.{ext}')
    if os.path.exists('temp_audio.wav'):
        os.remove('temp_audio.wav')

if __name__ == "__main__":
    if os.path.exists('temp_audio.m4a'):
        os.remove('temp_audio.m4a')
    extract_and_transcribe(url)
