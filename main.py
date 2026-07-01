import os
import json
import logging
from flask import Flask, request, jsonify
from truecallerpy import search, login, verify_otp
import asyncio

# ============ CONFIGURATION ============
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ============ GLOBAL STATE ============
class TruecallerAccount:
    """Single Truecaller account"""
    
    def __init__(self):
        self.installation_id = None
        self.country_code = "IN"
        self.phone = None
        self.is_logged_in = False
        self.otp_data = None
        self.phone_number = None
    
    def set_login_data(self, installation_id, country_code, phone):
        self.installation_id = installation_id
        self.country_code = country_code
        self.phone = phone
        self.is_logged_in = True
        self.otp_data = None
        self.phone_number = None
        logger.info(f"✅ Logged in with: {phone}")
        return True
    
    def set_otp_request(self, phone, response):
        self.phone_number = phone
        self.otp_data = response
        logger.info(f"📨 OTP requested for: {phone}")
        return True
    
    def clear(self):
        self.installation_id = None
        self.country_code = "IN"
        self.phone = None
        self.is_logged_in = False
        self.otp_data = None
        self.phone_number = None
        logger.info("🔓 Logged out")

# Global account instance
account = TruecallerAccount()

# ============ API ENDPOINTS ============

@app.route('/login', methods=['GET'])
def api_login():
    """
    Step 1: Request OTP
    GET /login?num=+918815743146
    
    Step 2: Verify OTP
    GET /login?otp=123456
    """
    
    # Check if already logged in
    if account.is_logged_in:
        return jsonify({
            'success': True,
            'message': 'Already logged in',
            'phone': account.phone,
            'status': 'logged_in'
        })
    
    # ============ STEP 1: Request OTP ============
    phone = request.args.get('num')
    
    if phone:
        if not phone.startswith('+'):
            return jsonify({
                'success': False,
                'error': 'Phone number must include country code',
                'example': '+918815743146'
            }), 400
        
        try:
            # Run async function
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            response = loop.run_until_complete(login(phone))
            loop.close()
            
            if response.get('status') == 1 or response.get('message') == 'Sent':
                account.set_otp_request(phone, response)
                return jsonify({
                    'success': True,
                    'message': 'OTP sent successfully',
                    'phone': phone,
                    'status': 'otp_sent'
                })
            else:
                error_msg = response.get('message', 'Unknown error')
                return jsonify({
                    'success': False,
                    'error': error_msg,
                    'message': 'Failed to send OTP. Check if account is valid.'
                }), 400
                
        except Exception as e:
            logger.error(f"Login error: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
    # ============ STEP 2: Verify OTP ============
    otp = request.args.get('otp')
    
    if otp:
        if not account.otp_data or not account.phone_number:
            return jsonify({
                'success': False,
                'error': 'No OTP request pending. First call /login?num=+918815743146'
            }), 400
        
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            response = loop.run_until_complete(
                verify_otp(
                    account.phone_number,
                    account.otp_data,
                    otp
                )
            )
            loop.close()
            
            if response.get('installationId'):
                # Save login data
                account.set_login_data(
                    response['installationId'],
                    account.otp_data.get('parsedCountryCode', 'IN'),
                    account.phone_number
                )
                
                return jsonify({
                    'success': True,
                    'message': 'Login successful!',
                    'phone': account.phone,
                    'country_code': account.country_code,
                    'installation_id': account.installation_id,
                    'status': 'logged_in'
                })
            else:
                error = response.get('message', 'Invalid OTP')
                return jsonify({
                    'success': False,
                    'error': error,
                    'message': 'Invalid OTP. Try again.'
                }), 400
                
        except Exception as e:
            logger.error(f"OTP verification error: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
    # ============ No parameters ============
    return jsonify({
        'error': 'Missing parameters',
        'usage': {
            'request_otp': '/login?num=+918815743146',
            'verify_otp': '/login?otp=123456',
            'check_status': '/status'
        }
    }), 400


@app.route('/search', methods=['GET'])
def api_search():
    """
    Search phone number
    GET /search?num=+919876543210
    """
    
    # Check if logged in
    if not account.is_logged_in:
        return jsonify({
            'success': False,
            'error': 'Not logged in',
            'message': 'First login using /login?num=+918815743146',
            'login_url': '/login?num=+918815743146'
        }), 401
    
    # Get phone number
    phone = request.args.get('num')
    
    if not phone:
        return jsonify({
            'success': False,
            'error': 'Missing phone number',
            'usage': '/search?num=+919876543210'
        }), 400
    
    if not phone.startswith('+'):
        return jsonify({
            'success': False,
            'error': 'Phone number must include country code',
            'example': '+919876543210'
        }), 400
    
    try:
        # Search
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
        error_str = str(e)
        if '40101' in error_str or 'Unauthorized' in error_str:
            account.clear()
            return jsonify({
                'success': False,
                'error': 'Session expired',
                'message': 'Please re-login using /login?num=+918815743146'
            }), 401
        
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/logout', methods=['GET'])
def api_logout():
    """Logout - Clear account session"""
    account.clear()
    return jsonify({
        'success': True,
        'message': 'Logged out successfully'
    })


@app.route('/status', methods=['GET'])
def api_status():
    """Check login status"""
    return jsonify({
        'status': 'active' if account.is_logged_in else 'inactive',
        'phone': account.phone if account.is_logged_in else None,
        'country': account.country_code if account.is_logged_in else None,
        'logged_in': account.is_logged_in,
        'message': 'Account logged in' if account.is_logged_in else 'Account not logged in'
    })


@app.route('/', methods=['GET'])
def home():
    """Home page with documentation"""
    return jsonify({
        'name': 'Truecaller Search API',
        'version': '1.0.0',
        'status': 'active' if account.is_logged_in else 'inactive',
        'endpoints': {
            '/login?num=+918815743146': {
                'method': 'GET',
                'description': 'Request OTP for login',
                'params': {'num': 'Phone number with country code'}
            },
            '/login?otp=123456': {
                'method': 'GET',
                'description': 'Verify OTP and complete login',
                'params': {'otp': '6-digit code received via SMS'}
            },
            '/search?num=+919876543210': {
                'method': 'GET',
                'description': 'Search phone number (after login)',
                'params': {'num': 'Phone number to search'}
            },
            '/status': {
                'method': 'GET',
                'description': 'Check login status'
            },
            '/logout': {
                'method': 'GET',
                'description': 'Logout and clear session'
            }
        },
        'example_flow': [
            '1. GET /login?num=+918815743146  → OTP sent',
            '2. GET /login?otp=123456         → Login successful',
            '3. GET /search?num=+919876543210 → Search result'
        ]
    })


# ============ MAIN ============
if __name__ == '__main__':
    port = int(os.getenv('PORT', 8080))
    
    logger.info("🚀 Truecaller Search API starting...")
    logger.info(f"🌐 Running on port {port}")
    logger.info("📝 Endpoints:")
    logger.info("   /login?num=+918815743146  → Request OTP")
    logger.info("   /login?otp=123456         → Verify OTP")
    logger.info("   /search?num=+919876543210 → Search number")
    logger.info("   /status                   → Check login status")
    
    app.run(host='0.0.0.0', port=port, debug=False)
