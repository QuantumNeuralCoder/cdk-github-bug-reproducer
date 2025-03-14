import json
import logging
import os
import boto3
from boto3.dynamodb.conditions import Key, Attr

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
dynamodb = boto3.resource('dynamodb')
sqs = boto3.client('sqs')
ecs = boto3.client('ecs')
TABLE_NAME = os.environ['ACCOUNT_TABLE_NAME']
QUEUE_URL = os.environ['QUEUE_URL']
table = dynamodb.Table(TABLE_NAME)

def lambda_handler(event, context):
    """
    Update ECS service desired count based on:
    1. Total account count (max capacity)
    2. Number of visible messages in SQS queue (desired count)

    This function handles:
    - Account registration/deregistration events
    - Custom MessageAddedToQueue and MessageRemovedFromQueue events
    - QueueDepthCheck events from the scheduled monitor
    - Periodic scheduled events
    """
    logger.info(f"Received event: {json.dumps(event)}")

    try:
        # Get total number of accounts
        response = table.scan(
            Select='COUNT'
        )
        total_accounts = response['Count']
        logger.info(f"Total accounts found: {total_accounts}")

        queue_response = sqs.get_queue_attributes(
            QueueUrl=QUEUE_URL,
            AttributeNames=['ApproximateNumberOfMessages']
        )
        message_count = int(queue_response['Attributes']['ApproximateNumberOfMessages'])
        logger.info(f"Approximate number of messages in queue: {message_count}")

        # Set desired capacity to 0 if no messages, otherwise set to min(message_count, total_accounts)
        desired_capacity = 0 if message_count == 0 else min(message_count, max(1, total_accounts))

        # Extract cluster and service name from the resource ID
        resource_id = os.environ['ECS_SERVICE_RESOURCE_ID']
        parts = resource_id.split('/')

        if len(parts) != 3 or parts[0] != 'service':
            logger.error(f"Invalid ECS service resource ID format: {resource_id}")
            return {
                'statusCode': 400,
                'body': json.dumps({
                    'error': f'Invalid ECS service resource ID format: {resource_id}'
                })
            }

        cluster_name = parts[1]
        service_name = parts[2]

        logger.info(f"Updating ECS service {service_name} in cluster {cluster_name} to desired count: {desired_capacity}")

        # Update the ECS service desired count directly
        try:
            response = ecs.update_service(
                cluster=cluster_name,
                service=service_name,
                desiredCount=desired_capacity
            )

            logger.info(f"Successfully updated ECS service desired count to {desired_capacity}")
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': f'Updated ECS service desired count to {desired_capacity}',
                    'accountCount': total_accounts,
                    'messageCount': message_count
                })
            }

        except Exception as e:
            logger.error(f"Error updating ECS service: {str(e)}")
            raise

    except Exception as e:
        logger.error(f"Failed to update ECS scaling: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': f'Internal error: {str(e)}'
            })
        }