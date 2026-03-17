from flask import Flask, request, jsonify, redirect, session
from flask_cors import CORS
import requests
import json
import os
from datetime import datetime, timedelta
import base64

app = Flask(__name__)
CORS(app, origins=['*'])

# Sensitive credentials from environment variables
CLIENT_ID = os.environ.get('QBO_CLIENT_ID', '')
CLIENT_SECRET = os.environ.get('QBO_CLIENT_SECRET', '')

# Non-sensitive config stored directly (private repo)
COMPANY_ID = '9341456604238693'
REDIRECT_URI = 'https://banks-brewing-qbo.onrender.com/callback'
FLASK_SECRET_KEY = 'banks-brewing-2026-mbbc'
QBO_BASE_URL = 'https://sandbox-quickbooks.api.intuit.com'

app.secret_key = FLASK_SECRET_KEY

AUTH_BASE_URL = 'https://appcenter.intuit.com/connect/oauth2'
TOKEN_URL = 'https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer'
SCOPE = 'com.intuit.quickbooks.accounting'

# Simple in-memory token store
token_store = {}

@app.route('/health')
def health():
    return jsonify({
        'status': 'ok',
        'message': 'Banks Brewing QBO Connector running',
        'company_id': COMPANY_ID,
        'client_id_set': bool(CLIENT_ID),
        'connected': bool(token_store.get('access_token'))
    })

@app.route('/auth/start')
def auth_start():
    if not CLIENT_ID:
        return jsonify({'error': 'QBO_CLIENT_ID environment variable not set'}), 500
    auth_url = (
        f"{AUTH_BASE_URL}"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_type=code"
        f"&scope={SCOPE}"
        f"&state=banks-brewing"
    )
    return redirect(auth_url)

@app.route('/callback')
def callback():
    code = request.args.get('code')
    if not code:
        return jsonify({'error': 'No authorization code received'}), 400
    credentials = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    response = requests.post(TOKEN_URL, headers={
        'Authorization': f'Basic {credentials}',
        'Content-Type': 'application/x-www-form-urlencoded'
    }, data={
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': REDIRECT_URI
    })
    if response.status_code != 200:
        return f"<html><body><h2>Token exchange failed</h2><p>{response.text}</p></body></html>", 400
    tokens = response.json()
    token_store['access_token'] = tokens.get('access_token')
    token_store['refresh_token'] = tokens.get('refresh_token')
    token_store['expires_at'] = (datetime.now() + timedelta(seconds=tokens.get('expires_in', 3600))).isoformat()
    return '''
    <html><body style="font-family:sans-serif;text-align:center;padding:60px;background:#f9f9f9;">
    <div style="background:white;border-radius:12px;padding:40px;max-width:400px;margin:0 auto;box-shadow:0 2px 12px rgba(0,0,0,0.1);">
    <div style="font-size:48px;margin-bottom:16px;">✓</div>
    <h2 style="color:#1D9E75;margin-bottom:8px;">Connected to QuickBooks!</h2>
    <p style="color:#666;">Banks Brewing Co is now connected.<br>You can close this window and return to the app.</p>
    </div>
    <script>
      if(window.opener) window.opener.postMessage('qbo_connected', '*');
      setTimeout(() => window.close(), 3000);
    </script>
    </body></html>
    '''

@app.route('/auth/status')
def auth_status():
    return jsonify({
        'connected': bool(token_store.get('access_token')),
        'expires_at': token_store.get('expires_at', None)
    })

@app.route('/auth/disconnect', methods=['POST'])
def disconnect():
    token_store.clear()
    return jsonify({'success': True, 'message': 'Disconnected from QuickBooks'})

def refresh_token_if_needed():
    expires_at = token_store.get('expires_at')
    if not expires_at:
        return False
    if datetime.now() >= datetime.fromisoformat(expires_at) - timedelta(minutes=5):
        credentials = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
        response = requests.post(TOKEN_URL, headers={
            'Authorization': f'Basic {credentials}',
            'Content-Type': 'application/x-www-form-urlencoded'
        }, data={
            'grant_type': 'refresh_token',
            'refresh_token': token_store.get('refresh_token')
        })
        if response.status_code == 200:
            tokens = response.json()
            token_store['access_token'] = tokens.get('access_token')
            token_store['refresh_token'] = tokens.get('refresh_token')
            token_store['expires_at'] = (datetime.now() + timedelta(seconds=tokens.get('expires_in', 3600))).isoformat()
            return True
        return False
    return True

def qbo_get(endpoint):
    refresh_token_if_needed()
    url = f"{QBO_BASE_URL}/v3/company/{COMPANY_ID}/{endpoint}"
    headers = {
        'Authorization': f"Bearer {token_store.get('access_token')}",
        'Accept': 'application/json'
    }
    return requests.get(url, headers=headers)

def qbo_post(endpoint, data):
    refresh_token_if_needed()
    url = f"{QBO_BASE_URL}/v3/company/{COMPANY_ID}/{endpoint}"
    headers = {
        'Authorization': f"Bearer {token_store.get('access_token')}",
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }
    return requests.post(url, headers=headers, json=data)

def find_account_id(name):
    safe_name = name.replace("'", "\\'")
    response = qbo_get(f"query?query=select Id,Name from Account where Name = '{safe_name}'")
    if response.status_code == 200:
        accounts = response.json().get('QueryResponse', {}).get('Account', [])
        if accounts:
            return accounts[0]['Id'], accounts[0]['Name']
    return None, name

def find_vendor_id(name):
    safe_name = name.replace("'", "\\'")
    response = qbo_get(f"query?query=select Id,DisplayName from Vendor where DisplayName = '{safe_name}'")
    if response.status_code == 200:
        vendors = response.json().get('QueryResponse', {}).get('Vendor', [])
        if vendors:
            return vendors[0]['Id']
    return None

@app.route('/deposit', methods=['POST'])
def create_deposit():
    if not token_store.get('access_token'):
        return jsonify({'error': 'Not connected to QuickBooks. Please authenticate first.'}), 401

    data = request.json
    sales_date = data.get('salesDate')
    memo = data.get('memo', '')
    net_deposit = float(data.get('netDeposit', 0))
    accounts = data.get('accounts', {})
    tax_vendor = data.get('taxVendor', 'State of Mo')
    tax_amount = float(data.get('taxAmount', 0))
    tips_remainder = float(data.get('tipsRemainder', 0))
    cash_back = float(data.get('cashBack', 0))
    cash_drawer_account = data.get('cashDrawerAccount', 'Cash Drawer')
    processing_fee = float(data.get('processingFee', 0))
    fee_account = data.get('feeAccount', 'Toast Processing Fees')
    bank_account = data.get('bankAccount', 'FSCB 6747')
    lines = data.get('lines', [])

    # Build deposit lines
    deposit_lines = []
    line_num = 1
    for line in lines:
        amount = float(line.get('amount', 0))
        if amount == 0:
            continue
        deposit_lines.append({
            "LineNum": line_num,
            "Amount": amount,
            "DetailType": "DepositLineDetail",
            "DepositLineDetail": {
                "AccountRef": {"name": line['account']},
            }
        })
        line_num += 1

    # Find bank account
    bank_id, bank_name = find_account_id(bank_account)

    deposit_body = {
        "TxnDate": sales_date,
        "DepositToAccountRef": {"name": bank_account},
        "PrivateNote": memo,
        "Line": deposit_lines
    }

    # Add cash back if applicable
    if cash_back > 0:
        drawer_id, drawer_name = find_account_id(cash_drawer_account)
        deposit_body["CashBack"] = {
            "Amount": cash_back,
            "AccountRef": {"name": cash_drawer_account},
            "Memo": "Daily drawer reset"
        }

    response = qbo_post('deposit', deposit_body)

    if response.status_code not in [200, 201]:
        return jsonify({
            'error': 'Failed to create deposit in QuickBooks',
            'details': response.text,
            'status': response.status_code
        }), 400

    deposit_result = response.json()
    deposit_id = deposit_result.get('Deposit', {}).get('Id', 'unknown')

    # Create AP bill for sales tax
    tax_bill_id = None
    if tax_amount > 0:
        vendor_id = find_vendor_id(tax_vendor)
        if vendor_id:
            bill_body = {
                "VendorRef": {"value": vendor_id},
                "TxnDate": sales_date,
                "PrivateNote": f"Sales tax — {memo}",
                "Line": [{
                    "Amount": tax_amount,
                    "DetailType": "AccountBasedExpenseLineDetail",
                    "AccountBasedExpenseLineDetail": {
                        "AccountRef": {"name": accounts.get('tax', 'Sales Tax')}
                    }
                }]
            }
            bill_response = qbo_post('bill', bill_body)
            if bill_response.status_code in [200, 201]:
                tax_bill_id = bill_response.json().get('Bill', {}).get('Id')

    return jsonify({
        'success': True,
        'depositId': deposit_id,
        'taxBillId': tax_bill_id,
        'message': f"Deposit created successfully in FSCB 6747{' + AP bill for sales tax to ' + tax_vendor if tax_bill_id else ''}",
        'salesDate': sales_date,
        'netDeposit': net_deposit
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
