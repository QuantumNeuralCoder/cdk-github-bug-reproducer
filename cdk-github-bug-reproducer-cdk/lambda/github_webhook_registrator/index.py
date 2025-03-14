import json
import logging
import os
import boto3
import urllib.request
import urllib.parse

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
secretsmanager = boto3.client('secretsmanager')

def lambda_handler(event, context):
    """
    Custom resource handler for registering a GitHub webhook
    """
    request_type = event['RequestType']
    
    # Get properties from the event
    properties = event.get('ResourceProperties', {})
    github_repo = properties.get('GitHubRepo')
    webhook_url = properties.get('WebhookUrl')
    
    # Get old properties for update requests
    old_properties = event.get('OldResourceProperties', {})
    old_github_repo = old_properties.get('GitHubRepo')
    old_webhook_url = old_properties.get('WebhookUrl')
    
    # Log key information at the start
    logger.info(f"Request Type: {request_type}")
    logger.info(f"New Repository: {github_repo}")
    logger.info(f"New Webhook URL: {webhook_url}")
    if request_type == 'Update':
        logger.info(f"Old Repository: {old_github_repo}")
        logger.info(f"Old Webhook URL: {old_webhook_url}")
    
    # Log full event for debugging if needed
    logger.debug(f"Full Event: {json.dumps(event)}")
    
    physical_resource_id = event.get('PhysicalResourceId', f'github-webhook-{context.aws_request_id}')
    
    github_token_secret_arn = properties.get('GitHubTokenSecretArn')
    webhook_secret_arn = properties.get('WebhookSecretArn')
    
    # Validate required properties
    if not github_repo or not webhook_url or not github_token_secret_arn or not webhook_secret_arn:
        error_message = "Missing required properties: GitHubRepo, WebhookUrl, GitHubTokenSecretArn, or WebhookSecretArn"
        logger.error(error_message)
        send_response(event, context, 'FAILED', {'Error': error_message}, physical_resource_id)
        return
    
    try:
        # Get GitHub token from Secrets Manager
        token_response = secretsmanager.get_secret_value(SecretId=github_token_secret_arn)
        github_token = token_response['SecretString']
        
        # Get webhook secret from Secrets Manager
        secret_response = secretsmanager.get_secret_value(SecretId=webhook_secret_arn)
        webhook_secret = secret_response['SecretString']
        
        # Parse GitHub repository (format: owner/repo)
        try:
            owner, repo = github_repo.split('/')
        except ValueError:
            error_message = f"Invalid GitHub repository format: {github_repo}. Expected format: owner/repo"
            logger.error(error_message)
            send_response(event, context, 'FAILED', {'Error': error_message}, physical_resource_id)
            return
        
        if request_type == 'Create':
            # Register new webhook
            webhook_id = register_webhook(owner, repo, webhook_url, github_token, webhook_secret)
            logger.info(f"Successfully registered webhook with ID: {webhook_id}")
            
            send_response(event, context, 'SUCCESS', {
                'WebhookId': webhook_id,
                'WebhookUrl': webhook_url,
                'GitHubRepo': github_repo
            }, webhook_id)
            
        elif request_type == 'Update':
            # Check if the repository or webhook URL has changed
            if old_github_repo != github_repo or old_webhook_url != webhook_url:
                logger.info(f"Repository changed from {old_github_repo} to {github_repo} or URL changed from {old_webhook_url} to {webhook_url}")
                
                # If old repo exists, try to find and delete the old webhook
                old_webhook_id = None
                if old_github_repo and old_webhook_url:
                    try:
                        old_owner, old_repo = old_github_repo.split('/')
                        old_webhooks = get_webhooks(old_owner, old_repo, github_token)
                        
                        # Find and delete webhook with old URL
                        for webhook in old_webhooks:
                            if webhook.get('config', {}).get('url') == old_webhook_url:
                                old_webhook_id = str(webhook['id'])
                                delete_webhook(old_owner, old_repo, old_webhook_id, github_token)
                                logger.info(f"Successfully deleted webhook {old_webhook_id} from old repository {old_github_repo}")
                                break
                    except Exception as e:
                        logger.warning(f"Error cleaning up old webhook: {str(e)}")
                
                # Register webhook in new repository
                webhook_id = register_webhook(owner, repo, webhook_url, github_token, webhook_secret)
                logger.info(f"Successfully registered webhook with ID: {webhook_id}")
                
                send_response(event, context, 'SUCCESS', {
                    'WebhookId': webhook_id,
                    'WebhookUrl': webhook_url,
                    'GitHubRepo': github_repo,
                    'OldWebhookId': old_webhook_id
                }, webhook_id)
            else:
                # No changes to repository or URL, keep existing webhook
                logger.info("No changes to repository or webhook URL")
                
                # Find the current webhook ID for the URL
                webhook_id = physical_resource_id
                try:
                    webhooks = get_webhooks(owner, repo, github_token)
                    for webhook in webhooks:
                        if webhook.get('config', {}).get('url') == webhook_url:
                            webhook_id = str(webhook['id'])
                            logger.info(f"Found existing webhook with ID: {webhook_id}")
                            break
                except Exception as e:
                    logger.warning(f"Error finding existing webhook: {str(e)}")
                
                send_response(event, context, 'SUCCESS', {
                    'WebhookId': webhook_id,
                    'WebhookUrl': webhook_url,
                    'GitHubRepo': github_repo
                }, webhook_id)
            
        elif request_type == 'Delete':
            # Try to find and delete the webhook with the specified URL
            webhook_id = None
            try:
                webhooks = get_webhooks(owner, repo, github_token)
                for webhook in webhooks:
                    if webhook.get('config', {}).get('url') == webhook_url:
                        webhook_id = str(webhook['id'])
                        delete_webhook(owner, repo, webhook_id, github_token)
                        logger.info(f"Successfully deleted webhook {webhook_id} from {github_repo}")
                        break
            except Exception as e:
                logger.warning(f"Error deleting webhook: {str(e)}")
            
            send_response(event, context, 'SUCCESS', {
                'WebhookId': webhook_id or physical_resource_id,
                'WebhookUrl': webhook_url,
                'GitHubRepo': github_repo
            }, physical_resource_id)
            
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        send_response(event, context, 'FAILED', {'Error': str(e)}, physical_resource_id)

def register_webhook(owner, repo, webhook_url, github_token, webhook_secret):
    """Register a webhook with GitHub"""
    
    # Check if webhook already exists
    existing_webhooks = get_webhooks(owner, repo, github_token)
    
    for webhook in existing_webhooks:
        if webhook.get('config', {}).get('url') == webhook_url:
            logger.info(f"Webhook already exists with ID: {webhook['id']}")
            return str(webhook['id'])
    
    # Create new webhook
    url = f"https://api.github.com/repos/{owner}/{repo}/hooks"
    
    # Prepare webhook payload
    payload = {
        "name": "web",
        "active": True,
        "events": ["issues"],
        "config": {
            "url": webhook_url,
            "content_type": "json",
            "insecure_ssl": "0"
        }
    }
    
    # Add secret if provided
    if webhook_secret:
        payload["config"]["secret"] = webhook_secret
    
    # Convert payload to JSON
    data = json.dumps(payload).encode('utf-8')
    
    # Create request
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {github_token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json"
    }
    
    req = urllib.request.Request(url, data=data, headers=headers, method='POST')
    
    # Send request
    with urllib.request.urlopen(req) as response:
        response_data = json.loads(response.read().decode('utf-8'))
        return str(response_data['id'])

def get_webhooks(owner, repo, github_token):
    """Get all webhooks for a repository"""
    url = f"https://api.github.com/repos/{owner}/{repo}/hooks"
    
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {github_token}",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    
    req = urllib.request.Request(url, headers=headers)
    
    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            # Repository not found or no webhooks
            return []
        raise

def delete_webhook(owner, repo, webhook_id, github_token):
    """Delete a webhook from GitHub"""
    url = f"https://api.github.com/repos/{owner}/{repo}/hooks/{webhook_id}"
    
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {github_token}",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    
    req = urllib.request.Request(url, headers=headers, method='DELETE')
    
    try:
        urllib.request.urlopen(req)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            # Webhook already deleted or doesn't exist
            logger.info(f"Webhook {webhook_id} not found, may have been deleted already")
            return
        raise

def send_response(event, context, response_status, response_data, physical_resource_id):
    """Send a response to CloudFormation"""
    response_body = {
        'Status': response_status,
        'Reason': f'See the details in CloudWatch Log Stream: {context.log_stream_name}',
        'PhysicalResourceId': physical_resource_id,
        'StackId': event['StackId'],
        'RequestId': event['RequestId'],
        'LogicalResourceId': event['LogicalResourceId'],
        'NoEcho': False,
        'Data': response_data
    }
    
    logger.info(f"Response body: {json.dumps(response_body)}")
    
    response_url = event['ResponseURL']
    
    headers = {
        'Content-Type': '',
        'Content-Length': str(len(json.dumps(response_body)))
    }
    
    req = urllib.request.Request(
        url=response_url,
        data=json.dumps(response_body).encode('utf-8'),
        headers=headers,
        method='PUT'
    )
    
    try:
        with urllib.request.urlopen(req) as response:
            logger.info(f"Status code: {response.status}")
            logger.info(f"Status message: {response.reason}")
    except Exception as e:
        logger.error(f"Error sending response: {str(e)}")
        raise