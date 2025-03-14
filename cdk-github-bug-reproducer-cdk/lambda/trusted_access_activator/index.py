import json
import logging
import boto3
import urllib.request
import os
import time

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize CloudFormation client
cfn = boto3.client('cloudformation')
org = boto3.client('organizations')
sts = boto3.client('sts')

def lambda_handler(event, context):
    """
    Custom resource handler for activating trusted access with AWS Organizations
    and registering the current account as a delegated administrator
    """
    logger.info(f"Event: {json.dumps(event)}")

    request_type = event['RequestType']
    physical_resource_id = event.get('PhysicalResourceId', f'trusted-access-activator-{context.aws_request_id}')
    response_data = {}

    try:
        if request_type == 'Create' or request_type == 'Update':
            # Get current account ID
            account_id = sts.get_caller_identity()['Account']

            # Activate Organizations access for CloudFormation
            logger.info('Activating Organizations access for CloudFormation')
            cfn.activate_organizations_access()

        # For Delete, we don't need to do anything
        # Once activated, trusted access remains until explicitly deactivated

        send_response(event, context, 'SUCCESS', response_data, physical_resource_id)

    except Exception as e:
        logger.error(f"Error: {str(e)}")
        send_response(event, context, 'FAILED', {'Error': str(e)}, physical_resource_id)

def send_response(event, context, response_status, response_data, physical_resource_id):
    """Send a response to CloudFormation to handle the custom resource"""
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