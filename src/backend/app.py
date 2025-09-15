"""
The Count - Plaid Integration Backend
Main Flask application for handling Plaid API integration
"""

import os
import json
from datetime import datetime
from flask import Flask, request, jsonify, render_template
from dotenv import load_dotenv
import plaid
from plaid.api import plaid_api
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.accounts_get_request import AccountsGetRequest
from plaid.model.auth_get_request import AuthGetRequest
from plaid.model.transactions_sync_request import TransactionsSyncRequest
from plaid.model.country_code import CountryCode
from plaid.model.products import Products

# Load environment variables
load_dotenv()

app = Flask(__name__)

# Plaid configuration
PLAID_CLIENT_ID = os.getenv('PLAID_CLIENT_ID')
PLAID_SECRET = os.getenv('PLAID_SECRET')
PLAID_ENV = os.getenv('PLAID_ENVIRONMENT', 'sandbox')

# Map environment names to Plaid environment objects
PLAID_ENVIRONMENTS = {
    'sandbox': plaid.Environment.Sandbox,
    'development': plaid.Environment.Development,
    'production': plaid.Environment.Production,
}

# Initialize Plaid client
configuration = plaid.Configuration(
    host=PLAID_ENVIRONMENTS[PLAID_ENV],
    api_key={
        'clientId': PLAID_CLIENT_ID,
        'secret': PLAID_SECRET,
        'plaidVersion': '2020-09-14'
    }
)
api_client = plaid.ApiClient(configuration)
plaid_client = plaid_api.PlaidApi(api_client)

# In-memory storage (replace with database in production)
access_tokens = {}


@app.route('/')
def index():
    """Serve the main page with Plaid Link integration"""
    return render_template('index.html')


@app.route('/api/create_link_token', methods=['POST'])
def create_link_token():
    """Create a link token for Plaid Link initialization"""
    try:
        # Create link token request
        request = LinkTokenCreateRequest(
            products=[Products('auth'), Products('transactions')],
            client_name="The Count - Financial Tracker",
            country_codes=[CountryCode('US')],
            language='en',
            user=LinkTokenCreateRequestUser(client_user_id='user-1'),  # In production, use actual user ID
            webhook='https://your-webhook-url.com/webhook'  # Replace with actual webhook URL
        )
        
        response = plaid_client.link_token_create(request)
        return jsonify({
            'link_token': response['link_token'],
            'expiration': response['expiration']
        })
    
    except plaid.ApiException as e:
        return jsonify({'error': json.loads(e.body)}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/exchange_public_token', methods=['POST'])
def exchange_public_token():
    """Exchange public token for access token"""
    try:
        data = request.get_json()
        public_token = data.get('public_token')
        
        if not public_token:
            return jsonify({'error': 'public_token is required'}), 400
        
        # Exchange public token for access token
        exchange_request = ItemPublicTokenExchangeRequest(
            public_token=public_token
        )
        
        response = plaid_client.item_public_token_exchange(exchange_request)
        access_token = response['access_token']
        item_id = response['item_id']
        
        # Store access token (in production, store in database)
        access_tokens[item_id] = {
            'access_token': access_token,
            'item_id': item_id,
            'created_at': datetime.now().isoformat()
        }
        
        return jsonify({
            'success': True,
            'item_id': item_id,
            'message': 'Successfully connected account'
        })
    
    except plaid.ApiException as e:
        return jsonify({'error': json.loads(e.body)}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/accounts', methods=['GET'])
def get_accounts():
    """Get accounts for all connected items"""
    try:
        all_accounts = []
        
        app.logger.info(f"Getting accounts for {len(access_tokens)} connected items")
        
        for item_id, item_data in access_tokens.items():
            access_token = item_data['access_token']
            app.logger.info(f"Processing item {item_id}")
            
            # Get account information
            accounts_request = AccountsGetRequest(access_token=access_token)
            accounts_response = plaid_client.accounts_get(accounts_request)
            
            # Get auth information (routing/account numbers)
            auth_request = AuthGetRequest(access_token=access_token)
            auth_response = plaid_client.auth_get(auth_request)
            
            # Convert response to dict (Plaid API returns response objects, not dicts)
            accounts_data = accounts_response.to_dict()
            auth_data = auth_response.to_dict()
            
            app.logger.info(f"Found {len(accounts_data['accounts'])} accounts for item {item_id}")
            
            # Combine account and auth data
            for account in accounts_data['accounts']:
                account_data = {
                    'account_id': account['account_id'],
                    'name': account['name'],
                    'type': account['type'],
                    'subtype': account['subtype'],
                    'mask': account['mask'],
                    'balance': {
                        'available': account['balances']['available'],
                        'current': account['balances']['current'],
                        'currency': account['balances']['iso_currency_code']
                    }
                }
                
                # Add routing/account numbers if available
                for auth_account in auth_data['accounts']:
                    if auth_account['account_id'] == account['account_id']:
                        account_data['numbers'] = auth_account.get('numbers', {})
                        break
                
                all_accounts.append(account_data)
        
        app.logger.info(f"Returning {len(all_accounts)} total accounts")
        return jsonify({'accounts': all_accounts})
    
    except plaid.ApiException as e:
        error_details = json.loads(e.body)
        app.logger.error(f"Plaid API error in get_accounts: {error_details}")
        return jsonify({'error': error_details}), 400
    except Exception as e:
        app.logger.error(f"Unexpected error in get_accounts: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/transactions', methods=['GET'])
def get_transactions():
    """Get transactions for all connected accounts"""
    try:
        all_transactions = []
        
        app.logger.info(f"Getting transactions for {len(access_tokens)} connected items")
        
        for item_id, item_data in access_tokens.items():
            access_token = item_data['access_token']
            app.logger.info(f"Processing transactions for item {item_id}")
            
            # Get transactions using sync method
            transactions_request = TransactionsSyncRequest(
                access_token=access_token
            )
            
            response = plaid_client.transactions_sync(transactions_request)
            # Access response attributes directly (safer than to_dict conversion)
            transactions = response.added
            
            app.logger.info(f"Found {len(transactions)} initial transactions for item {item_id}")
            
            # Handle pagination
            while response.has_more:
                transactions_request = TransactionsSyncRequest(
                    access_token=access_token,
                    cursor=response.next_cursor
                )
                response = plaid_client.transactions_sync(transactions_request)
                transactions.extend(response.added)
                app.logger.info(f"Found {len(response.added)} additional transactions")
            
            all_transactions.extend(transactions)
        
        app.logger.info(f"Total transactions found: {len(all_transactions)}")
        
        # Format transactions for frontend
        formatted_transactions = []
        for transaction in all_transactions:
            # Access transaction attributes directly (transaction objects, not dicts)
            formatted_transactions.append({
                'transaction_id': transaction.transaction_id,
                'account_id': transaction.account_id,
                'amount': transaction.amount,
                'date': str(transaction.date),
                'name': transaction.name,
                'merchant_name': getattr(transaction, 'merchant_name', None),
                'category': getattr(transaction, 'category', []) or [],
                'account_owner': getattr(transaction, 'account_owner', None)
            })
        
        return jsonify({'transactions': formatted_transactions})
    
    except plaid.ApiException as e:
        error_details = json.loads(e.body)
        app.logger.error(f"Plaid API error in get_transactions: {error_details}")
        return jsonify({'error': error_details}), 400
    except Exception as e:
        app.logger.error(f"Unexpected error in get_transactions: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/status', methods=['GET'])
def get_status():
    """Get connection status"""
    return jsonify({
        'connected_accounts': len(access_tokens),
        'environment': PLAID_ENV,
        'items': list(access_tokens.keys())
    })


if __name__ == '__main__':
    app.run(debug=True, port=5000)