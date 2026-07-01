import os
import json
import logging
from flask import Flask, request, jsonify
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from truecallerpy import search, login, verify_otp
import asyncio
import threading

# ============ CONFIGURATION ============
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("8987138027:AAEjQqYk-8hB1pvnJZ3OQER3Nfi3FId7894")
if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN not set!")

app = Flask(__name__)

# ============ GLOBAL STATE (SINGLE ACCOUNT) ============
class TruecallerAccount:
    """Single Truecaller account shared between Telegram bot and API"""
    
    def __init__(self):
        self.installation_id = None
        self.country_code = "IN"
        self.phone = None
        self.is_logged_in = False
        self.waiting_for_otp = False
        self.otp_data = None
        self.phone_number = None
    
    def set_login_data(self, installation_id, country_code, phone):
        self.installation_id = installation_id
        self.country_code = country_code
        self.phone = phone
        self.is_logged_in = True
        self.waiting_for_otp = False
        self.otp_data = None
        logger.info(f"✅ Truecaller logged in with: {phone}")
    
    def set_otp_request(self, phone, response):
        self.phone_number = phone
        self.otp_data = response
        self.waiting_for_otp = True
        logger.info(f"📨 OTP requested for: {phone}")
    
    def clear(self):
        self.installation_id = None
        self.country_code = "IN"
        self.phone = None
        self.is_logged_in = False
        self.waiting_for_otp = False
        self.otp_data = None
        self.phone_number = None
        logger.info("🔓 Account logged out")

# Global single account instance
account = TruecallerAccount()

# ============ TELEGRAM BOT - ONLY FOR LOGIN ============
# Conversation states
PHONE, OTP = range(2)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command - Only login functionality"""
    if account.is_logged_in:
        await update.message.reply_text(
            f"✅ Already logged in!\n"
            f"📱 Phone: {account.phone}\n"
            f"🌍 Country: {account.country_code}\n\n"
            "API is now active for everyone to search numbers.\n"
            "To logout: /logout"
        )
        return ConversationHandler.END
    
    await update.message.reply_text(
        "🔐 *Login to Truecaller*\n\n"
        "This will set up the account for the public API.\n\n"
        "Enter your phone number in international format:\n"
        "Example: +919876543210",
        parse_mode="Markdown"
    )
    return PHONE

async def phone_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle phone number input"""
    phone = update.message.text.strip()
    
    if not phone.startswith('+'):
        await update.message.reply_text(
            "❌ Invalid format!\n"
            "Please enter with country code: +919876543210"
        )
        return PHONE
    
    try:
        response = await login(phone)
        
        if response.get('status') == 1 or response.get('message') == 'Sent':
            account.set_otp_request(phone, response)
            
            await update.message.reply_text(
                "📨 OTP sent via SMS/WhatsApp!\n"
                "Enter the 6-digit code:"
            )
            return OTP
        else:
            error_msg = response.get('message', 'Unknown error')
            await update.message.reply_text(
                f"❌ Failed: {error_msg}\n\n"
                "Check if you can login to Truecaller app."
            )
            return ConversationHandler.END
            
    except Exception as e:
        logger.error(f"Login error: {e}")
        await update.message.reply_text("❌ Server error. Try again.")
        return ConversationHandler.END

async def otp_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle OTP input"""
    otp = update.message.text.strip()
    
    if not account.waiting_for_otp or not account.otp_data:
        await update.message.reply_text(
            "❌ No OTP request pending. Use /start again."
        )
        return ConversationHandler.END
    
    try:
        response = await verify_otp(
            account.phone_number,
            account.otp_data,
            otp
        )
        
        if response.get('installationId'):
            # Save the login data globally
            account.set_login_data(
                response['installationId'],
                account.otp_data.get('parsedCountryCode', 'IN'),
                account.phone_number
            )
            
            await update.message.reply_text(
                "✅ *Login Successful!*\n\n"
                f"📱 Phone: {account.phone}\n"
                f"🌍 Country: {account.country_code}\n\n"
                "🔓 API is now ACTIVE!\n"
                "Anyone can search numbers using:\n"
                "`GET /search?num=+919876543210`\n\n"
                "To logout: /logout",
                parse_mode="Markdown"
            )
            return ConversationHandler.END
        else:
            error = response.get('message', 'Invalid OTP')
            await update.message.reply_text(
                f"❌ {error}\n\n"
                "Try again or /start to restart."
            )
            return OTP
            
    except Exception as e:
        logger.error(f"OTP error: {e}")
        await update.message.reply_text("❌ Server error. Try again.")
        return ConversationHandler.END

async def logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Logout the single account"""
    account.clear()
    await update.message.reply_text(
        "🔓 Logged out successfully!\n"
        "API is now inactive. Login again to activate."
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel conversation"""
    await update.message.reply_text("Cancelled. Use /start to begin.")
    return ConversationHandler.END

# ============ FLASK API - PUBLIC SEARCH ============
@app.route('/search', methods=['GET'])
def api_search():
    """
    Public API endpoint - Anyone can use this
    GET /search?num=+919876543210
    """
    
    # Check if account is logged in
    if not account.is_logged_in:
        return jsonify({
            'error': 'Truecaller account not logged in',
            'message': 'Please login via Telegram bot first',
            'telegram_bot': '@your_bot_username'  # Change this
        }), 401
    
    # Get phone number from query parameter
    phone = request.args.get('num')
    
    if not phone:
        return jsonify({
            'error': 'Missing phone number',
            'usage': '/search?num=+919876543210'
        }), 400
    
    if not phone.startswith('+'):
        return jsonify({
            'error': 'Phone number must include country code',
            'example': '+919876543210'
        }), 400
    
    try:
        # Use the single logged-in account
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        result = loop.run_until_complete(
            search(
                phone,
                account.country_code,
                account.installation_id
            )
        )
        loop.close()
        
        if result and result.get('name'):
            return jsonify({
                'success': True,
                'number': phone,
                'name': result.get('name'),
                'alternative_name': result.get('alternativeName'),
                'carrier': result.get('carrier'),
                'city': result.get('city'),
                'country': result.get('countryCode'),
                'raw_data': result
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Number not found in Truecaller database'
            }), 404
            
    except Exception as e:
        logger.error(f"Search error: {e}")
        
        # Check if session expired
        if '40101' in str(e) or 'Unauthorized' in str(e):
            account.clear()
            return jsonify({
                'error': 'Session expired',
                'message': 'Please re-login via Telegram bot'
            }), 401
        
        return jsonify({
            'error': 'Search failed',
            'message': str(e)
        }), 500

@app.route('/status', methods=['GET'])
def api_status():
    """Check if API is active"""
    return jsonify({
        'status': 'active' if account.is_logged_in else 'inactive',
        'phone': account.phone if account.is_logged_in else None,
        'country': account.country_code if account.is_logged_in else None,
        'message': 'Account logged in' if account.is_logged_in else 'Account not logged in'
    })

@app.route('/health', methods=['GET'])
def health():
    """Health check for Railway"""
    return jsonify({
        'status': 'healthy',
        'api': 'active' if account.is_logged_in else 'inactive (needs login)'
    })

# ============ TELEGRAM BOT SETUP ============
def run_telegram_bot():
    """Run Telegram bot for login only"""
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Conversation handler for login flow
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, phone_input)],
            OTP: [MessageHandler(filters.TEXT & ~filters.COMMAND, otp_input)]
        },
        fallbacks=[
            CommandHandler('cancel', cancel),
            CommandHandler('logout', logout)
        ]
    )
    
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler('logout', logout))
    
    # Start bot
    application.run_polling()

# ============ MAIN ============
if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    
    # Start Telegram bot in background thread
    bot_thread = threading.Thread(target=run_telegram_bot, daemon=True)
    bot_thread.start()
    
    logger.info("🚀 Server starting...")
    logger.info("📱 Telegram bot running for login")
    logger.info(f"🌐 API running on port {port}")
    logger.info("📝 Use: /search?num=+919876543210")
    
    # Start Flask API
    app.run(host='0.0.0.0', port=port)
