import json
import os
import boto3
import logging
import time
from datetime import datetime
from boto3.dynamodb.conditions import Key, Attr
import uuid

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
dynamodb = boto3.resource('dynamodb')
events = boto3.client('events')
TABLE_NAME = os.environ['ACCOUNT_TABLE_NAME']
EVENT_BUS_NAME = os.environ['EVENT_BUS_NAME']
table = dynamodb.Table(TABLE_NAME)

# Constants
ACCOUNT_STATUS = {
    'AVAILABLE': 'AVAILABLE',
    'IN_USE': 'IN_USE'
}

def publish_account_event(detail_type: str, account_id: str):
    """Publish an account event to EventBridge"""
    try:
        events.put_events(
            Entries=[
                {
                    'Source': 'custom.githubIssueProcessor',
                    'DetailType': detail_type,
                    'Detail': json.dumps({
                        'account_id': account_id,
                        'timestamp': int(time.time())
                    }),
                    'EventBusName': EVENT_BUS_NAME
                }
            ]
        )
        logger.info(f"Published {detail_type} event for account {account_id}")
    except Exception as e:
        logger.error(f"Error publishing event: {str(e)}")
        # Don't fail the operation if event publishing fails

def lambda_handler(event, context):
    """
    Account Manager Lambda - Single source of truth for account allocation

    Operations:
    - register_account: Add a new account to the pool
    - deregister_account: Remove an account from the pool
    - acquire_account: Reserve an available account for processing
    - release_account: Return an account to the available pool
    - list_accounts: List all accounts and their status
    """
    logger.info(f"Received event: {json.dumps(event)}")

    operation = event.get('operation')

    if operation == 'register_account':
        return register_account(event.get('account_id'), event.get('role_arn'))
    elif operation == 'deregister_account':
        return deregister_account(event.get('account_id'))
    elif operation == 'acquire_account':
        return acquire_account(event.get('task_id'))
    elif operation == 'release_account':
        return release_account(event.get('account_id'), event.get('task_id'))
    elif operation == 'list_accounts':
        return list_accounts()
    elif operation == 'cleanup_stale_accounts':
        return cleanup_stale_accounts()

    return {
        'statusCode': 400,
        'body': json.dumps({
            'error': f'Unknown operation: {operation}'
        })
    }

def register_account(account_id, role_arn):
    """Register a new account in the pool or update existing one"""
    if not account_id or not role_arn:
        return {
            'statusCode': 400,
            'body': json.dumps({
                'error': 'Missing required parameters: account_id and role_arn'
            })
        }

    try:
        # First, check if the account already exists
        response = table.get_item(
            Key={
                'account_id': account_id
            }
        )

        if 'Item' in response:
            # Account exists, update the role ARN
            existing_item = response['Item']

            # Update the role ARN and last_updated timestamp
            table.update_item(
                Key={
                    'account_id': account_id
                },
                UpdateExpression='SET role_arn = :role_arn, last_updated = :timestamp',
                ExpressionAttributeValues={
                    ':role_arn': role_arn,
                    ':timestamp': int(time.time())
                }
            )

            logger.info(f"Updated existing account {account_id} with new role ARN")

            # Don't publish an event for updates to avoid unnecessary scaling changes

            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': f'Account {account_id} updated successfully',
                    'updated': True
                })
            }
        else:
            # Account doesn't exist, create a new one
            table.put_item(
                Item={
                    'account_id': account_id,
                    'role_arn': role_arn,
                    'status': ACCOUNT_STATUS['AVAILABLE'],
                    'registered_at': int(time.time()),
                    'last_updated': int(time.time())
                }
            )

            logger.info(f"Successfully registered new account {account_id}")

            # Publish account registered event
            publish_account_event('AccountRegistered', account_id)

            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': f'Account {account_id} registered successfully',
                    'updated': False
                })
            }

    except Exception as e:
        logger.error(f"Error registering/updating account {account_id}: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': f'Internal error: {str(e)}'
            })
        }

def deregister_account(account_id):
    """Remove an account from the pool"""
    if not account_id:
        return {
            'statusCode': 400,
            'body': json.dumps({
                'error': 'Missing required parameter: account_id'
            })
        }

    try:
        response = table.delete_item(
            Key={
                'account_id': account_id
            },
            ConditionExpression='attribute_exists(account_id)',
            ExpressionAttributeNames={
                '#status': 'status'
            },
            ExpressionAttributeValues={
                ':available': ACCOUNT_STATUS['AVAILABLE']
            }
        )

        logger.info(f"Successfully deregistered account {account_id}")

        # Publish account deregistered event
        publish_account_event('AccountDeregistered', account_id)

        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': f'Account {account_id} deregistered successfully'
            })
        }

    except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
        return {
            'statusCode': 200,
            'body': json.dumps({
                'error': f'Account {account_id} is already not registered yet'
            })
        }
    except Exception as e:
        logger.error(f"Error deregistering account {account_id}: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': f'Internal error: {str(e)}'
            })
        }

def acquire_account(task_id):
    """Acquire an available account for processing"""
    if not task_id:
        return {
            'statusCode': 400,
            'body': json.dumps({
                'error': 'Missing required parameter: task_id'
            })
        }

    try:
        # Query for available accounts
        response = table.scan(
            FilterExpression=Attr('status').eq(ACCOUNT_STATUS['AVAILABLE']),
            Limit=1
        )

        if not response['Items']:
            return {
                'statusCode': 404,
                'body': json.dumps({
                    'error': 'No available accounts found'
                })
            }

        account = response['Items'][0]
        account_id = account['account_id']

        # Update account status to IN_USE
        table.update_item(
            Key={
                'account_id': account_id
            },
            UpdateExpression='SET #status = :status, task_id = :task_id, last_updated = :timestamp',
            ExpressionAttributeNames={
                '#status': 'status'
            },
            ExpressionAttributeValues={
                ':status': ACCOUNT_STATUS['IN_USE'],
                ':task_id': task_id,
                ':timestamp': int(time.time())
            }
        )

        logger.info(f"Account {account_id} acquired by task {task_id}")
        return {
            'statusCode': 200,
            'body': json.dumps({
                'account_id': account_id,
                'role_arn': account['role_arn']
            })
        }

    except Exception as e:
        logger.error(f"Error acquiring account for task {task_id}: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': f'Internal error: {str(e)}'
            })
        }

def release_account(account_id, task_id):
    """Release an account back to the available pool"""
    if not account_id or not task_id:
        return {
            'statusCode': 400,
            'body': json.dumps({
                'error': 'Missing required parameters: account_id and task_id'
            })
        }

    try:
        # Verify the account is being released by the task that acquired it
        response = table.update_item(
            Key={
                'account_id': account_id
            },
            UpdateExpression='SET #status = :available_status, last_updated = :timestamp REMOVE task_id',
            ConditionExpression='attribute_exists(account_id) AND #status = :in_use_status AND task_id = :task_id',
            ExpressionAttributeNames={
                '#status': 'status'
            },
            ExpressionAttributeValues={
                ':available_status': ACCOUNT_STATUS['AVAILABLE'],
                ':in_use_status': ACCOUNT_STATUS['IN_USE'],
                ':task_id': task_id,
                ':timestamp': int(time.time())
            },
            ReturnValues='ALL_NEW'
        )

        logger.info(f"Account {account_id} released by task {task_id}")
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': f'Account {account_id} released successfully'
            })
        }

    except dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
        return {
            'statusCode': 400,
            'body': json.dumps({
                'error': f'Account {account_id} not found, not in use, or owned by different task'
            })
        }
    except Exception as e:
        logger.error(f"Error releasing account {account_id}: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': f'Internal error: {str(e)}'
            })
        }

def list_accounts():
    """List all accounts and their status"""
    try:
        response = table.scan()
        accounts = response['Items']

        # Sort accounts by status (AVAILABLE first) and then by last_updated
        accounts.sort(key=lambda x: (x['status'] != ACCOUNT_STATUS['AVAILABLE'], x.get('last_updated', 0)))

        return {
            'statusCode': 200,
            'body': json.dumps(accounts)
        }

    except Exception as e:
        logger.error(f"Error listing accounts: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': f'Internal error: {str(e)}'
            })
        }

def cleanup_stale_accounts():
    """Cleanup accounts that have been in use for too long"""
    try:
        # Get all IN_USE accounts
        response = table.scan(
            FilterExpression=Attr('status').eq(ACCOUNT_STATUS['IN_USE'])
        )

        current_time = int(time.time())
        stale_timeout = 3600  # 1 hour
        cleaned_accounts = []

        for account in response['Items']:
            # Check if account has been in use for too long
            if current_time - account.get('last_updated', 0) > stale_timeout:
                try:
                    # Reset account to AVAILABLE
                    table.update_item(
                        Key={
                            'account_id': account['account_id']
                        },
                        UpdateExpression='SET #status = :status, last_updated = :timestamp REMOVE task_id',
                        ExpressionAttributeNames={
                            '#status': 'status'
                        },
                        ExpressionAttributeValues={
                            ':status': ACCOUNT_STATUS['AVAILABLE'],
                            ':timestamp': current_time
                        }
                    )
                    cleaned_accounts.append(account['account_id'])
                    logger.info(f"Reset stale account {account['account_id']}")
                except Exception as e:
                    logger.error(f"Error resetting account {account['account_id']}: {str(e)}")

        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': f'Cleaned up {len(cleaned_accounts)} stale accounts',
                'accounts': cleaned_accounts
            })
        }

    except Exception as e:
        logger.error(f"Error cleaning up stale accounts: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': f'Internal error: {str(e)}'
            })
        }