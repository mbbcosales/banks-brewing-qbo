from flask import Flask, request, jsonify, redirect
from flask_cors import CORS
import requests
import os
from datetime import datetime, timedelta
import base64
 
app = Flask(__name__)
CORS(app, origins=['*'])
 
CLIENT_ID = os.environ.get('QBO_CLIENT_ID', '')
CLIENT_SECRET = os.environ.get('QBO_CLIENT_SECRET', '')
 
REDIRECT_URI = 'https://banks-brewing-qbo.onrender.com/callback'
FLASK_SECRET_KEY = 'banks-brewing-2026-mbbc'
QBO_BASE_URL = 'https://quickbooks.api.intuit.com'
 
app.secret_key = FLASK_SECRET_KEY
AUTH_BASE_URL = 'https://appcenter.intuit.com/connect/oauth2'
TOKEN_URL = 'https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer'
SCOPE = 'com.intuit.quickbooks.accounting'
 
token_store = {}
 
@app.route('/health')
def health():
    return jsonify({
        'status': 'ok',
        'message': 'Banks Brewing QBO Connector running',
        'company_id': token_store.get('company_id', 'not set'),
        'client_id_set': bool(CLIENT_ID),
        'connected': bool(token_store.get('access_token'))
    })
 
@app.route('/auth/start')
def auth_start():
    if not CLIENT_ID:
        return jsonify({'error': 'QBO_CLIENT_ID not set'}), 500
    auth_url = (f"{AUTH_BASE_URL}?client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}"
                f"&response_type=code&scope={SCOPE}&state=banks-brewing")
    return redirect(auth_url)
 
@app.route('/callback')
def callback():
    code = request.args.get('code')
    realm_id = request.args.get('realmId')  # This is the actual company ID
    if not code:
        return jsonify({'error': 'No authorization code'}), 400
    creds = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    r = requests.post(TOKEN_URL, headers={
        'Authorization': f'Basic {creds}',
        'Content-Type': 'application/x-www-form-urlencoded'
    }, data={'grant_type': 'authorization_code', 'code': code, 'redirect_uri': REDIRECT_URI})
    if r.status_code != 200:
        return f"<html><body><h2>Token exchange failed</h2><p>{r.text}</p></body></html>", 400
    tokens = r.json()
    token_store['access_token'] = tokens.get('access_token')
    token_store['refresh_token'] = tokens.get('refresh_token')
    token_store['expires_at'] = (datetime.now() + timedelta(seconds=tokens.get('expires_in', 3600))).isoformat()
    token_store['company_id'] = realm_id  # Store the real company ID from QBO
    return f'''<html><body style="font-family:sans-serif;text-align:center;padding:60px;background:#f9f9f9;">
    <div style="background:white;border-radius:12px;padding:40px;max-width:400px;margin:0 auto;">
    <div style="font-size:48px;">✓</div>
    <h2 style="color:#1D9E75;">Connected to QuickBooks!</h2>
    <p style="color:#666;">Company ID: {realm_id}</p>
    <p style="color:#666;">You can close this window and return to the app.</p>
    </div></body></html>'''
 
@app.route('/auth/status')
def auth_status():
    return jsonify({
        'connected': bool(token_store.get('access_token')),
        'expires_at': token_store.get('expires_at'),
        'company_id': token_store.get('company_id', 'not set')
    })
 
@app.route('/auth/disconnect', methods=['POST'])
def disconnect():
    token_store.clear()
    return jsonify({'success': True})
 
def get_company_id():
    return token_store.get('company_id', '')
 
def refresh_token_if_needed():
    expires_at = token_store.get('expires_at')
    if not expires_at:
        return
    if datetime.now() >= datetime.fromisoformat(expires_at) - timedelta(minutes=5):
        creds = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
        r = requests.post(TOKEN_URL, headers={
            'Authorization': f'Basic {creds}',
            'Content-Type': 'application/x-www-form-urlencoded'
        }, data={'grant_type': 'refresh_token', 'refresh_token': token_store.get('refresh_token')})
        if r.status_code == 200:
            tokens = r.json()
            token_store['access_token'] = tokens.get('access_token')
            token_store['refresh_token'] = tokens.get('refresh_token')
            token_store['expires_at'] = (datetime.now() + timedelta(seconds=tokens.get('expires_in', 3600))).isoformat()
 
def qbo_get(endpoint):
    refresh_token_if_needed()
    company_id = get_company_id()
    url = f"{QBO_BASE_URL}/v3/company/{company_id}/{endpoint}"
    headers = {'Authorization': f"Bearer {token_store.get('access_token')}", 'Accept': 'application/json'}
    return requests.get(url, headers=headers)
 
def qbo_post(endpoint, data):
    refresh_token_if_needed()
    company_id = get_company_id()
    url = f"{QBO_BASE_URL}/v3/company/{company_id}/{endpoint}"
    headers = {'Authorization': f"Bearer {token_store.get('access_token')}", 'Content-Type': 'application/json', 'Accept': 'application/json'}
    return requests.post(url, headers=headers, json=data)
 
def find_account(name):
    safe = name.replace("'", "\\'")
    r = qbo_get(f"query?query=select Id,Name,FullyQualifiedName from Account where Name = '{safe}'")
    if r.status_code == 200:
        accts = r.json().get('QueryResponse', {}).get('Account', [])
        if accts:
            return accts[0]['Id'], accts[0]['Name']
    short = name.split(':')[-1].strip()
    if short != name:
        safe_short = short.replace("'", "\\'")
        r2 = qbo_get(f"query?query=select Id,Name from Account where Name = '{safe_short}'")
        if r2.status_code == 200:
            accts = r2.json().get('QueryResponse', {}).get('Account', [])
            if accts:
                return accts[0]['Id'], accts[0]['Name']
    r3 = qbo_get(f"query?query=select Id,Name,FullyQualifiedName from Account where FullyQualifiedName = '{safe}'")
    if r3.status_code == 200:
        accts = r3.json().get('QueryResponse', {}).get('Account', [])
        if accts:
            return accts[0]['Id'], accts[0]['Name']
    return None, name
 
def find_vendor(name):
    safe = name.replace("'", "\\'")
    r = qbo_get(f"query?query=select Id,DisplayName from Vendor where DisplayName = '{safe}'")
    if r.status_code == 200:
        vendors = r.json().get('QueryResponse', {}).get('Vendor', [])
        if vendors:
            return vendors[0]['Id']
    return None
 
@app.route('/debug/all-accounts')
def debug_all_accounts():
    if not token_store.get('access_token'):
        return jsonify({'error': 'Not connected'}), 401
    company_id = get_company_id()
    r = qbo_get("query?query=select Id,Name,FullyQualifiedName,AccountType from Account MAXRESULTS 1000")
    if r.status_code == 200:
        accts = r.json().get('QueryResponse', {}).get('Account', [])
        return jsonify({
            'company_id_used': company_id,
            'count': len(accts),
            'accounts': [{'id': a['Id'], 'name': a['Name'], 'fqn': a.get('FullyQualifiedName',''), 'type': a.get('AccountType','')} for a in accts]
        })
    return jsonify({'error': r.text, 'status': r.status_code, 'company_id_used': company_id}), 400
 
@app.route('/debug/accounts')
def debug_accounts():
    if not token_store.get('access_token'):
        return jsonify({'error': 'Not connected'}), 401
    names = request.args.get('names', '').split(',')
    results = {}
    for name in names:
        name = name.strip()
        acct_id, acct_name = find_account(name)
        results[name] = {'id': acct_id, 'found': acct_id is not None, 'matched_name': acct_name}
    return jsonify(results)
 
@app.route('/deposit', methods=['POST'])
def create_deposit():
    if not token_store.get('access_token'):
        return jsonify({'error': 'Not connected to QuickBooks. Please authenticate first.'}), 401
    if not get_company_id():
        return jsonify({'error': 'Company ID not set. Please reconnect to QuickBooks.'}), 401
 
    data = request.json
    sales_date = data.get('salesDate')
    memo = data.get('memo', '')
    net_deposit = float(data.get('netDeposit', 0))
    accounts = data.get('accounts', {})
    tax_vendor = data.get('taxVendor', 'State of Mo')
    tax_amount = float(data.get('taxAmount', 0))
    cash_back = float(data.get('cashBack', 0))
    cash_drawer_account = data.get('cashDrawerAccount', 'Cash Drawer')
    bank_account = data.get('bankAccount', 'FSCB 6747')
    lines_data = data.get('lines', [])
    tax_acct_name = accounts.get('tax', 'Sales Tax')
 
    bank_id, bank_name = find_account(bank_account)
    if not bank_id:
        return jsonify({'error': f'Bank account not found: {bank_account}. Check Setup tab account name.'}), 400
 
    deposit_lines = []
    line_num = 1
    skipped = []
    for line in lines_data:
        amount = float(line.get('amount', 0))
        account_name = line.get('account', '')
        if not account_name:
            continue
        acct_id, acct_name = find_account(account_name)
        if not acct_id:
            skipped.append(account_name)
            continue
        dep_line = {
            "LineNum": line_num,
            "Amount": round(amount, 2),
            "DetailType": "DepositLineDetail",
            "DepositLineDetail": {
                "AccountRef": {"value": acct_id, "name": acct_name}
            }
        }
        deposit_lines.append(dep_line)
        line_num += 1
 
    if not deposit_lines:
        return jsonify({'error': 'No valid deposit lines found.', 'skipped': skipped}), 400
 
    deposit_body = {
        "TxnDate": sales_date,
        "PrivateNote": memo,
        "DepositToAccountRef": {"value": bank_id, "name": bank_name},
        "Line": deposit_lines
    }
 
    if cash_back > 0:
        drawer_id, drawer_name = find_account(cash_drawer_account)
        if drawer_id:
            deposit_body["CashBack"] = {
                "Amount": round(cash_back, 2),
                "AccountRef": {"value": drawer_id, "name": drawer_name},
                "Memo": "Daily drawer reset"
            }
 
    dep_response = qbo_post('deposit', deposit_body)
    if dep_response.status_code not in [200, 201]:
        return jsonify({
            'error': 'Failed to create deposit in QuickBooks',
            'details': dep_response.text,
            'skipped_accounts': skipped
        }), 400
 
    deposit_id = dep_response.json().get('Deposit', {}).get('Id', 'unknown')
 
    tax_bill_id = None
    if tax_amount > 0:
        vendor_id = find_vendor(tax_vendor)
        tax_acct_id, tax_acct_resolved = find_account(tax_acct_name)
        if vendor_id and tax_acct_id:
            bill_body = {
                "VendorRef": {"value": vendor_id},
                "TxnDate": sales_date,
                "PrivateNote": f"Sales tax — {memo}",
                "Line": [{
                    "Amount": round(tax_amount, 2),
                    "DetailType": "AccountBasedExpenseLineDetail",
                    "AccountBasedExpenseLineDetail": {
                        "AccountRef": {"value": tax_acct_id, "name": tax_acct_resolved}
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
        'skippedAccounts': skipped,
        'message': f"Deposit {deposit_id} created in {bank_name}{' + AP bill for sales tax' if tax_bill_id else ''}{' (skipped: ' + ', '.join(skipped) + ')' if skipped else ''}",
        'salesDate': sales_date,
        'netDeposit': net_deposit
    })
 
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
 
# This line intentionally left blank
