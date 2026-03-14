from flask import Flask, request, jsonify, redirect, session
from flask_cors import CORS
import requests
import json
import os
from datetime import datetime, timedelta
import base64

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-secret-key')
CORS(app, origins=['*'])

# QBO Config from environment variables
CLIENT_ID = os.environ.get('QBO_CLIENT_ID')
CLIENT_SECRET = os.environ.get('QBO_CLIENT_SECRET')
COMPANY_ID = os.environ.get('QBO_COMPANY_ID')
REDIRECT_URI = os.environ.get('REDIRECT_URI', 'https://banks-brewing-qbo.onrender.com/callback')
QBO_BASE_URL = os.environ.get('QBO_BASE_URL', 'https://quickbooks.api.intuit.com')
AUTH_BASE_URL = 'https://appcenter.intuit.com/connect/oauth2'
TOKEN_URL = 'https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer'
SCOPE = 'com.intuit.quickbooks.accounting'

# Simple token store (in production use a database)
token_store = {}

@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'message': 'Banks Brewing QBO Connector running'})

@app.route('/auth/start')
def auth_start():
    auth_url = (
        f"{AUTH_BASE_URL}?client_id={CLIENT_ID}"
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
    # Exchange code for tokens
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
        return jsonify({'error': 'Token exchange failed', 'details': response.text}), 400
    tokens = response.json()
    token_store['access_token'] = tokens.get('access_token')
    token_store['refresh_token'] = tokens.get('refresh_token')
    token_store['expires_at'] = (datetime.now() + timedelta(seconds=tokens.get('expires_in', 3600))).isoformat()
    return '''
    <html><body style="font-family:sans-serif;text-align:center;padding:60px;">
    <h2 style="color:#1D9E75;">Connected to QuickBooks!</h2>
    <p>You can close this window and return to the app.</p>
    <script>window.opener && window.opener.postMessage('qbo_connected', '*'); setTimeout(()=>window.close(), 2000);</script>
    </body></html>
    '''

@app.route('/auth/status')
def auth_status():
    if token_store.get('access_token'):
        return jsonify({'connected': True})
    return jsonify({'connected': False})

def refresh_token_if_needed():
    expires_at = token_store.get('expires_at')
    if expires_at:
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

def qbo_request(method, endpoint, data=None):
    refresh_token_if_needed()
    url = f"{QBO_BASE_URL}/v3/company/{COMPANY_ID}/{endpoint}"
    headers = {
        'Authorization': f"Bearer {token_store.get('access_token')}",
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }
    if method == 'POST':
        response = requests.post(url, headers=headers, json=data)
    else:
        response = requests.get(url, headers=headers)
    return response

def find_or_create_account(name, account_type, detail_type):
    """Find account by name in QBO"""
    response = qbo_request('GET', f"query?query=select * from Account where Name = '{name}'")
    if response.status_code == 200:
        data = response.json()
        accounts = data.get('QueryResponse', {}).get('Account', [])
        if accounts:
            return accounts[0]['Id']
    return None

def find_vendor(name):
    """Find vendor by name"""
    response = qbo_request('GET', f"query?query=select * from Vendor where DisplayName = '{name}'")
    if response.status_code == 200:
        data = response.json()
        vendors = data.get('QueryResponse', {}).get('Vendor', [])
        if vendors:
            return vendors[0]['Id']
    return None

@app.route('/deposit', methods=['POST'])
def create_deposit():
    if not token_store.get('access_token'):
        return jsonify({'error': 'Not connected to QuickBooks. Please authenticate first.'}), 401

    data = request.json
    sales_date = data.get('salesDate')
    deposit_date = data.get('depositDate')
    memo = data.get('memo', '')
    net_deposit = float(data.get('netDeposit', 0))
    accounts = data.get('accounts', {})
    tax_vendor = data.get('taxVendor', 'State of Mo')
    lines = data.get('lines', [])
    tax_amount = float(data.get('taxAmount', 0))
    tips_remainder = float(data.get('tipsRemainder', 0))
    cash_back = float(data.get('cashBack', 0))
    cash_drawer_account = data.get('cashDrawerAccount', 'Cash Drawer')
    processing_fee = float(data.get('processingFee', 0))
    fee_account = data.get('feeAccount', 'Toast Processing Fees')
    bank_account = data.get('bankAccount', 'FSCB 6747')

    # Build deposit lines
    deposit_lines = []
    line_num = 1

    for line in lines:
        if line.get('amount', 0) == 0:
            continue
        deposit_lines.append({
            "LineNum": line_num,
            "Amount": abs(float(line['amount'])),
            "DetailType": "DepositLineDetail",
            "DepositLineDetail": {
                "AccountRef": {"name": line['account']},
                "Memo": line.get('memo', ''),
            },
            "Amount": float(line['amount'])
        })
        line_num += 1

    # Build the deposit
    deposit_body = {
        "TxnDate": sales_date,
        "DepositToAccountRef": {"name": bank_account},
        "PrivateNote": memo,
        "Line": deposit_lines
    }

    # Add cash back if needed
    if cash_back > 0:
        deposit_body["CashBack"] = {
            "Amount": cash_back,
            "AccountRef": {"name": cash_drawer_account},
            "Memo": f"Daily drawer reset"
        }

    response = qbo_request('POST', 'deposit', deposit_body)

    if response.status_code not in [200, 201]:
        return jsonify({
            'error': 'Failed to create deposit',
            'details': response.text,
            'status': response.status_code
        }), 400

    deposit_result = response.json()
    deposit_id = deposit_result.get('Deposit', {}).get('Id', 'unknown')

    # Create AP bill for sales tax
    tax_bill_id = None
    if tax_amount > 0:
        vendor_id = find_vendor(tax_vendor)
        ap_account_id = find_or_create_account('Accounts Payable (A/P)', 'Accounts Payable', 'AccountsPayable')
        tax_account_id = find_or_create_account(accounts.get('tax', 'Sales Tax'), 'Other Current Liability', 'OtherCurrentLiabilities')

        if vendor_id:
            bill_body = {
                "VendorRef": {"value": vendor_id},
                "TxnDate": sales_date,
                "PrivateNote": f"Sales tax - {memo}",
                "Line": [{
                    "Amount": tax_amount,
                    "DetailType": "AccountBasedExpenseLineDetail",
                    "AccountBasedExpenseLineDetail": {
                        "AccountRef": {"name": accounts.get('tax', 'Sales Tax')}
                    }
                }]
            }
            bill_response = qbo_request('POST', 'bill', bill_body)
            if bill_response.status_code in [200, 201]:
                tax_bill_id = bill_response.json().get('Bill', {}).get('Id')

    return jsonify({
        'success': True,
        'depositId': deposit_id,
        'taxBillId': tax_bill_id,
        'message': f"Deposit DEP-{deposit_id} created successfully{' + AP bill for sales tax' if tax_bill_id else ''}",
        'salesDate': sales_date,
        'depositDate': deposit_date,
        'netDeposit': net_deposit
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
