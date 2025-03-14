import json
import os
import boto3
import logging
import re
import requests
import hmac
import hashlib
import time

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
sqs = boto3.client('sqs')
secretsmanager = boto3.client('secretsmanager')
events = boto3.client('events')
QUEUE_URL = os.environ['SQS_QUEUE_URL']
REQUIRED_LABELS = os.environ.get('REQUIRED_LABELS', '').split(',')
GITHUB_TOKEN_SECRET_ARN = os.environ.get('GITHUB_TOKEN_SECRET_ARN')
WEBHOOK_SECRET_ARN = os.environ.get('WEBHOOK_SECRET_ARN')
EVENT_BUS_NAME = os.environ.get('EVENT_BUS_NAME', 'default')

def get_github_token():
    """
    Retrieve GitHub token from AWS Secrets Manager
    """
    if not GITHUB_TOKEN_SECRET_ARN:
        logger.warning("GitHub token secret ARN not provided")
        return None
    
    try:
        response = secretsmanager.get_secret_value(SecretId=GITHUB_TOKEN_SECRET_ARN)
        if 'SecretString' in response:
            return response['SecretString']
        else:
            logger.warning("GitHub token not found in secret")
            return None
    except Exception as e:
        logger.error(f"Error retrieving GitHub token: {str(e)}")
        return None

def get_webhook_secret():
    """
    Retrieve webhook secret from AWS Secrets Manager
    """
    if not WEBHOOK_SECRET_ARN:
        logger.warning("Webhook secret ARN not provided")
        return None
    
    try:
        response = secretsmanager.get_secret_value(SecretId=WEBHOOK_SECRET_ARN)
        if 'SecretString' in response:
            return response['SecretString']
        else:
            logger.warning("Webhook secret not found in secret")
            return None
    except Exception as e:
        logger.error(f"Error retrieving webhook secret: {str(e)}")
        return None

def validate_github_webhook(event, webhook_secret):
    """
    Validate that the webhook is coming from GitHub using the webhook secret
    """
    if not webhook_secret:
        logger.warning("No webhook secret available for validation")
        return True  # Continue processing without validation
    
    # Extract headers and body
    headers = event.get('headers', {})
    signature_header = headers.get('X-Hub-Signature-256')
    body = event.get('body', '')
    
    if not signature_header:
        logger.warning("No X-Hub-Signature-256 found in headers")
        return False
    
    # Compute expected signature
    signature = 'sha256=' + hmac.new(
        webhook_secret.encode('utf-8'),
        body.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    # Compare signatures
    if not hmac.compare_digest(signature, signature_header):
        logger.warning("Signature verification failed")
        return False
    
    return True

def lambda_handler(event, context):
    """
    Process GitHub webhook events for new issues.
    Filter issues based on configured labels and send qualifying issues to SQS.
    """
    logger.info(f"Received event: {json.dumps(event)}")
    
    try:
        # Get webhook secret for validation
        webhook_secret = get_webhook_secret()
        
        # Validate webhook signature
        if webhook_secret and not validate_github_webhook(event, webhook_secret):
            logger.error("Invalid webhook signature")
            return {
                'statusCode': 401,
                'body': json.dumps({'error': 'Invalid webhook signature'})
            }
        
        # Get GitHub token for API calls
        github_token = get_github_token()
        
        # Parse the webhook payload
        body = json.loads(event['body']) if isinstance(event.get('body'), str) else event.get('body', {})
        
        # Check if this is an issue event
        if event.get('headers', {}).get('X-GitHub-Event') != 'issues' or body.get('action') != 'opened':
            logger.info("Not a new issue event, ignoring")
            return {
                'statusCode': 200,
                'body': json.dumps({'message': 'Event ignored - not a new issue'})
            }
        
        # Extract issue details
        issue = body.get('issue', {})
        issue_number = issue.get('number')
        repo_name = body.get('repository', {}).get('full_name')
        issue_labels = [label.get('name') for label in issue.get('labels', [])]
        
        logger.info(f"Processing issue #{issue_number} from {repo_name} with labels: {issue_labels}")
        
        # Check if the issue has any of the required labels
        if not REQUIRED_LABELS or REQUIRED_LABELS[0] == '':
            should_process = True
            logger.info("No label filtering configured, processing all issues")
        else:
            should_process = any(label in issue_labels for label in REQUIRED_LABELS)
            logger.info(f"Issue {'has' if should_process else 'does not have'} required labels")
        
        if should_process:
            # If we have a GitHub token, fetch additional issue details
            if github_token:
                try:
                    # Get more detailed information about the issue
                    headers = {
                        'Authorization': f'token {github_token}',
                        'Accept': 'application/vnd.github.v3+json'
                    }
                    issue_url = f"https://api.github.com/repos/{repo_name}/issues/{issue_number}"
                    response = requests.get(issue_url, headers=headers)
                    
                    if response.status_code == 200:
                        issue_details = response.json()
                        logger.info(f"Retrieved additional issue details from GitHub API")
                        
                        # Add a comment to acknowledge receipt
                        comment_url = f"{issue_url}/comments"
                        comment_body = {
                            'body': 'This issue has been queued for processing by our automated system.'
                        }
                        requests.post(comment_url, headers=headers, json=comment_body)
                        logger.info("Added acknowledgment comment to the issue")
                    else:
                        logger.warning(f"Failed to get issue details: {response.status_code}")
                except Exception as e:
                    logger.error(f"Error interacting with GitHub API: {str(e)}")
            
            # Send to SQS for processing
            message = {
                'issue_number': issue_number,
                'repository': repo_name,
                'title': issue.get('title'),
                'body': issue.get('body'),
                'labels': issue_labels,
                'html_url': issue.get('html_url'),
                'user': issue.get('user', {}).get('login')
            }
            
            response = sqs.send_message(
                QueueUrl=QUEUE_URL,
                MessageBody=json.dumps(message)
            )
            
            message_id = response['MessageId']
            logger.info(f"Issue sent to SQS: {message_id}")
            
            # Publish an event to EventBridge for scaling
            try:
                events.put_events(
                    Entries=[
                        {
                            'Source': 'custom.githubIssueProcessor',
                            'DetailType': 'MessageAddedToQueue',
                            'Detail': json.dumps({
                                'queue_url': QUEUE_URL,
                                'message_id': message_id,
                                'timestamp': int(time.time())
                            }),
                            'EventBusName': EVENT_BUS_NAME
                        }
                    ]
                )
                logger.info(f"Published MessageAddedToQueue event for message {message_id}")
            except Exception as e:
                logger.error(f"Error publishing event: {str(e)}")
                # Don't fail the operation if event publishing fails
            
            return {
                'statusCode': 200,
                'body': json.dumps({'message': 'Issue queued for processing', 'messageId': message_id})
            }
        else:
            logger.info("Issue does not meet label criteria, ignoring")
            return {
                'statusCode': 200,
                'body': json.dumps({'message': 'Issue ignored - does not meet label criteria'})
            }
            
    except Exception as e:
        logger.error(f"Error processing webhook: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }