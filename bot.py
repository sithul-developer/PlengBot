import telebot
import yt_dlp
import logging
import os
import time
import io
import requests
import traceback
import threading
import concurrent.futures
import hashlib
from queue import Queue
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure detailed logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot_debug.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Get bot token
API_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
if not API_TOKEN:
    logger.error("No TELEGRAM_BOT_TOKEN found in environment variables")
    raise ValueError("No TELEGRAM_BOT_TOKEN found in environment variables")

bot = telebot.TeleBot(API_TOKEN)

# Remove webhook
try:
    bot.remove_webhook()
    time.sleep(1)
    logger.info("Webhook removed successfully")
except Exception as e:
    logger.warning(f"Could not remove webhook: {e}")

# ========== UPDATED DOWNLOAD ENGINE WITH DEBUGGING ==========

class FastYouTubeDownloader:
    def __init__(self):
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=3)
        self.cache = {}
    
    def get_audio_info(self, url):
        """Get audio info with detailed debugging"""
        logger.debug(f"🔍 Starting extraction for URL: {url}")
        
        # STRATEGY 1: Try multiple extraction methods
        strategies = [
            self._try_extract_with_verbose,
            self._try_extract_with_cookies,
            self._try_extract_simple
        ]
        
        for i, strategy in enumerate(strategies, 1):
            try:
                logger.debug(f"Trying strategy {i}/{len(strategies)}: {strategy.__name__}")
                result = strategy(url)
                if result:
                    logger.info(f"✅ Extraction successful with strategy {i}")
                    return result
            except Exception as e:
                logger.warning(f"Strategy {i} failed: {str(e)[:100]}")
                continue
        
        logger.error("❌ All extraction strategies failed")
        raise Exception("Could not extract audio information")
    
    def _try_extract_with_verbose(self, url):
        """Try extraction with verbose logging enabled"""
        ydl_opts = {
            'verbose': True,
            'quiet': False,
            'no_warnings': False,
            'ignoreerrors': True,
            'socket_timeout': 30,
            'extract_flat': False,
            'extractor_args': {
                'youtube': {
                    'player_client': ['android', 'web', 'ios'],
                    'skip': ['hls', 'dash'],
                }
            },
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate',
            },
            'format': 'bestaudio/best',
            'nocheckcertificate': True,
        }
        
        # Try with cookies if file exists
        if os.path.exists('cookies.txt'):
            ydl_opts['cookiefile'] = 'cookies.txt'
            logger.debug("Using cookies.txt for authentication")
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False, process=True)
            
            if not info:
                return None
            
            # Get the best audio format
            requested_formats = info.get('requested_formats')
            if requested_formats:
                selected_format = requested_formats[0]
            else:
                # Find best audio format manually
                formats = info.get('formats', [])
                audio_formats = [f for f in formats if f.get('acodec') != 'none']
                if not audio_formats:
                    logger.warning("No audio formats found in verbose extraction")
                    return None
                selected_format = audio_formats[0]
            
            return self._format_audio_info(info, selected_format)
    
    def _try_extract_with_cookies(self, url):
        """Try with explicit cookie settings"""
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'socket_timeout': 20,
            'extractor_args': {
                'youtube': {
                    'player_client': ['android'],
                }
            },
            'format': 'bestaudio[ext=m4a]/bestaudio',
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36',
                'Accept': '*/*',
            },
            'cookiefile': 'cookies.txt' if os.path.exists('cookies.txt') else None,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            if not info:
                return None
            
            selected_format = info.get('requested_formats', [info])[0]
            return self._format_audio_info(info, selected_format)
    
    def _try_extract_simple(self, url):
        """Simple extraction as last resort"""
        ydl_opts = {
            'quiet': True,
            'format': 'best',
            'socket_timeout': 15,
            'ignoreerrors': True,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            if not info:
                return None
            
            # Get smallest format for speed
            formats = info.get('formats', [info])
            formats_with_audio = [f for f in formats if f.get('acodec') != 'none']
            if formats_with_audio:
                selected_format = min(
                    formats_with_audio,
                    key=lambda x: x.get('filesize', float('inf'))
                )
            else:
                selected_format = formats[0]
            
            return self._format_audio_info(info, selected_format)
    
    def _format_audio_info(self, info, selected_format):
        """Format audio information consistently"""
        return {
            'url': selected_format.get('url'),
            'title': info.get('title', 'Audio'),
            'uploader': info.get('uploader', 'Unknown'),
            'duration': info.get('duration', 0),
            'filesize': selected_format.get('filesize'),
            'ext': selected_format.get('ext', 'm4a'),
            'has_video': selected_format.get('vcodec') != 'none',
            'is_live': info.get('is_live', False),
            'webpage_url': info.get('webpage_url', ''),
            'format_id': selected_format.get('format_id', 'unknown'),
        }
    
    def validate_direct_url(self, url):
        """Check if direct URL is accessible"""
        try:
            headers = {'User-Agent': 'Mozilla/5.0', 'Range': 'bytes=0-1000'}
            response = requests.head(url, headers=headers, timeout=10)
            return response.status_code in [200, 206]
        except:
            return False
    
    def download_audio(self, url, max_size=50*1024*1024):
        """Download audio with better error handling"""
        logger.debug(f"Downloading from: {url[:100]}...")
        
        # Validate URL first
        if not self.validate_direct_url(url):
            logger.warning(f"Direct URL validation failed: {url[:50]}...")
            raise Exception("Audio URL is not accessible")
        
        headers = {
            'User-Agent': 'Mozilla/5.0',
            'Accept': '*/*',
            'Accept-Encoding': 'identity',
        }
        
        try:
            response = requests.get(url, headers=headers, stream=True, timeout=45)
            response.raise_for_status()
            
            content_length = response.headers.get('Content-Length')
            if content_length and int(content_length) > max_size:
                raise Exception(f"File too large ({int(content_length)/1024/1024:.1f}MB)")
            
            audio_buffer = io.BytesIO()
            downloaded = 0
            
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    audio_buffer.write(chunk)
                    downloaded += len(chunk)
                    
                    if downloaded > max_size:
                        raise Exception(f"File exceeds size limit ({max_size/1024/1024}MB)")
            
            if downloaded == 0:
                raise Exception("Downloaded empty file")
            
            audio_buffer.seek(0)
            logger.debug(f"Downloaded {downloaded/1024/1024:.2f}MB successfully")
            return audio_buffer
            
        except requests.exceptions.Timeout:
            raise Exception("Download timeout - server too slow")
        except requests.exceptions.RequestException as e:
            raise Exception(f"Network error: {str(e)[:50]}")
    
    def fast_download(self, url):
        """Main download method with detailed logging"""
        logger.info(f"Starting download process for: {url}")
        
        try:
            # Get audio info
            start_time = time.time()
            audio_info = self.get_audio_info(url)
            extract_time = time.time() - start_time
            
            logger.info(f"Audio info extracted in {extract_time:.1f}s")
            logger.info(f"Title: {audio_info['title']}")
            logger.info(f"Duration: {audio_info['duration']}s")
            logger.info(f"Format: {audio_info['ext']} (ID: {audio_info['format_id']})")
            
            # Validate
            if audio_info['duration'] > 1800:
                raise Exception("Video too long (max 30 minutes)")
            
            if audio_info.get('is_live'):
                raise Exception("Live streams not supported")
            
            if not audio_info.get('url'):
                raise Exception("No audio URL available")
            
            # Download audio
            logger.debug(f"Audio URL: {audio_info['url'][:100]}...")
            audio_buffer = self.download_audio(audio_info['url'])
            
            return audio_buffer, audio_info
            
        except Exception as e:
            logger.exception(f"Download failed: {e}")
            raise

# Initialize downloader
downloader = FastYouTubeDownloader()

# ========== IMPROVED BOT HANDLERS ==========

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    welcome_text = """🎵 <b>YouTube Audio Downloader</b>

Send me any YouTube link and I'll download the audio for you!

<b>Features:</b>
• Audio extraction from YouTube videos
• Fast downloading with multiple fallbacks
• No files saved on server
• Detailed error logging
• No rate limits or restrictions

<b>Limits:</b>
• Max 30 minutes per video
• Max 50MB file size

<b>Troubleshooting:</b>
1. Make sure the video is publicly available
2. Try shorter videos first
3. Check bot_debug.log for detailed errors

Send a YouTube URL to begin!"""
    bot.send_message(message.chat.id, welcome_text, parse_mode='HTML')

@bot.message_handler(commands=['debug'])
def debug_info(message):
    """Debug command to check bot status"""
    info = f"""🤖 <b>Bot Debug Information</b>

<b>Status:</b> ✅ Running
<b>Downloader:</b> Ready
<b>Log File:</b> <code>bot_debug.log</code>

<b>Last Error:</b> Check bot_debug.log file
<b>Memory Usage:</b> {os.path.getsize('bot_debug.log')/1024:.1f}KB logs

Send a test URL or check the log file for details."""
    bot.send_message(message.chat.id, info, parse_mode='HTML')

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    """Handle incoming messages - NO RESTRICTIONS"""
    url = message.text.strip()
    logger.info(f"New request from user {message.from_user.id}: {url}")
    
    # Enhanced validation
    valid_domains = ['youtube.com/watch', 'youtu.be/', 'youtube.com/shorts/']
    if not any(domain in url for domain in valid_domains):
        bot.send_message(message.chat.id, 
                        "❌ Please send a valid YouTube URL.\n"
                        "Examples:\n"
                        "• https://www.youtube.com/watch?v=...\n"
                        "• https://youtu.be/...\n"
                        "• https://www.youtube.com/shorts/...")
        return
    
    # Delete user's message for privacy
    try:
        bot.delete_message(message.chat.id, message.message_id)
    except:
        pass
    
    # Start download immediately
    process_download_async(message.chat.id, message.from_user.id, url)

def clean_text(text):
    """Escape markdown special characters properly"""
    if not text:
        return ""
    # Escape markdown special characters with a backslash
    md_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    result = text
    for char in md_chars:
        result = result.replace(char, '\\' + char)
    return result

def process_download_async(chat_id, user_id, url):
    """Process download in background thread"""
    def download_task():
        status_msg = None
        
        try:
            # Send initial message (plain text)
            status_msg = bot.send_message(chat_id, "🔍 Analyzing video...")
            
            start_time = time.time()
            
            # Get and validate audio
            bot.edit_message_text("⚡ Extracting audio information...", 
                                chat_id, status_msg.message_id)
            
            audio_buffer, audio_info = downloader.fast_download(url)
            
            extract_time = time.time() - start_time
            filesize = len(audio_buffer.getvalue())
            
            if filesize == 0:
                raise Exception("Downloaded empty file")
            
            # Clean titles
            clean_title = clean_text(audio_info['title'][:40])
            clean_uploader = clean_text(audio_info.get('uploader', 'Unknown'))
            
            # Update status (plain text)
            bot.edit_message_text(
                f"✅ Audio Extracted!\n"
                f"Title: {clean_title}...\n"
                f"Uploader: {clean_uploader}\n"
                f"Duration: {audio_info['duration']}s\n"
                f"Size: {filesize/1024/1024:.1f}MB\n"
                f"Time: {extract_time:.1f}s",
                chat_id,
                status_msg.message_id
            )
            
            time.sleep(1)
            
            # Upload to Telegram
            bot.edit_message_text("📤 Uploading to Telegram...", 
                                chat_id, status_msg.message_id)
            
            upload_start = time.time()
            
            # Clean caption
            safe_caption = clean_text(audio_info['title'][:100])
            
            if filesize > 20 * 1024 * 1024:
                bot.send_document(
                    chat_id=chat_id,
                    document=audio_buffer,
                    caption=f"🎵 {safe_caption}",
                    timeout=120
                )
            else:
                bot.send_audio(
                    chat_id=chat_id,
                    audio=audio_buffer,
                    title=clean_text(audio_info['title'][:64]),
                    performer=clean_uploader[:64],
                    duration=min(audio_info['duration'], 600),
                    caption=f"🎵 {safe_caption}",
                    timeout=120
                )
            
            upload_time = time.time() - upload_start
            
            # Final success message
            bot.edit_message_text(
                f"✅ Download Complete!\n"
                f"Total Time: {extract_time + upload_time:.1f}s\n"
                f"Size: {filesize/1024/1024:.1f}MB\n"
                f"Title: {clean_title}...\n\n"
                f"✨ Ready for another download!",
                chat_id,
                status_msg.message_id
            )
            
            time.sleep(10)
            bot.delete_message(chat_id, status_msg.message_id)
            
        except Exception as e:
            logger.error(f"Error for user {user_id}: {traceback.format_exc()}")
            
            if status_msg:
                error_msg = str(e)
                user_friendly_msg = "❌ "
                
                if "too long" in error_msg.lower():
                    user_friendly_msg += "Video too long (max 30 minutes)"
                elif "too large" in error_msg.lower():
                    user_friendly_msg += "File too large (max 50MB)"
                elif "private" in error_msg.lower() or "unavailable" in error_msg.lower():
                    user_friendly_msg += "Video is private or unavailable"
                elif "age-restricted" in error_msg.lower():
                    user_friendly_msg += "Age-restricted content not supported"
                elif "could not extract" in error_msg.lower():
                    user_friendly_msg += "Could not extract audio. Video may be restricted."
                elif "timeout" in error_msg.lower():
                    user_friendly_msg += "Download timeout. Try a shorter video."
                else:
                    user_friendly_msg += f"Error: {str(e)[:60]}"
                
                # Send error (plain text)
                bot.edit_message_text(user_friendly_msg, 
                                     chat_id, status_msg.message_id)
            
            time.sleep(10)
            if status_msg:
                try:
                    bot.delete_message(chat_id, status_msg.message_id)
                except:
                    pass
    
    thread = threading.Thread(target=download_task, daemon=True)
    thread.start()

# ========== STARTUP AND MAINTENANCE ==========

def create_cookies_instructions():
    """Create instructions for cookies file if needed"""
    if not os.path.exists('cookies_instructions.txt'):
        instructions = """How to create cookies.txt for YouTube:

1. Install "Get cookies.txt" extension in Chrome/Firefox
2. Log into YouTube in your browser
3. Click the extension and export cookies
4. Save as 'cookies.txt' in the bot directory
5. Restart the bot 

This helps avoid YouTube bot detection.
"""
        with open('cookies_instructions.txt', 'w') as f:
            f.write(instructions)
        logger.info("Created cookies_instructions.txt")

if __name__ == '__main__':
    print("=" * 60)
    print("🎵 YOUTUBE AUDIO DOWNLOADER BOT")
    print("=" * 60)
    print("\n⚠️  IMPORTANT:")
    print("• Detailed logs will be saved to bot_debug.log")
    print("• Use /debug command to check bot status")
    print("• Create cookies.txt to avoid bot detection")
    print("\n📋 Quick Setup:")
    print("1. pip install --upgrade yt-dlp")
    print("2. Create cookies.txt (optional but recommended)")
    print("3. Run the bot")
    print("=" * 60)
    
    # Create instructions file
    create_cookies_instructions()
    
    # Check yt-dlp version
    try:
        import yt_dlp
        logger.info(f"yt-dlp version: {yt_dlp.version.__version__}")
    except:
        logger.warning("Could not determine yt-dlp version")
    
    try:
        bot.enable_save_next_step_handlers(delay=2)
        bot.load_next_step_handlers()
        
        logger.info("Starting bot infinity polling...")
        bot.infinity_polling(timeout=90, skip_pending=True, long_polling_timeout=90)
        
    except KeyboardInterrupt:
        print("\nBot stopped by user.")
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.critical(f"Bot crashed: {traceback.format_exc()}")
        print(f"Bot crashed: {e}")
        print("Check bot_debug.log for details")
    finally:
        downloader.executor.shutdown(wait=True)
        logger.info("Bot shutdown complete")