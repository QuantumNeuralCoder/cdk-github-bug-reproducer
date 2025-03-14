#!/usr/bin/env python3
"""
GitHub Issue Processor - ECS Task

This script processes GitHub issues by:
1. Reading a message from an SQS queue
2. Acquiring an account from the pool (with retries)
3. Assuming a cross-account role
4. Creating a result file in S3
5. Adding a comment to the GitHub issue with the result link
"""

import json
import os
import boto3
import logging
import time
import uuid
import requests
import shutil
from botocore.exceptions import ClientError
from github import Github
import sys
import random
import asyncio
from issue_processor.processor import process

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize AWS clients
lambda_client = boto3.client('lambda')
s3_client = boto3.client('s3')
sts_client = boto3.client('sts')
secretsmanager = boto3.client('secretsmanager')
sqs_client = boto3.client('sqs')
events_client = boto3.client('events')

# Environment variables
ACCOUNT_MANAGER_FUNCTION_ARN = os.environ.get('ACCOUNT_MANAGER_FUNCTION_ARN')
RESULTS_BUCKET = os.environ.get('RESULTS_BUCKET')
GITHUB_TOKEN_SECRET_ARN = os.environ.get('GITHUB_TOKEN_SECRET_ARN')
QUEUE_URL = os.environ.get('QUEUE_URL')
EVENT_BUS_NAME = os.environ.get('EVENT_BUS_NAME', 'default')

# Constants
MAX_ACCOUNT_ACQUIRE_RETRIES = 60  # Maximum number of retries to acquire an account
ACCOUNT_ACQUIRE_RETRY_DELAY_BASE = 10  # Base delay in seconds between retries
ACCOUNT_ACQUIRE_RETRY_DELAY_MAX = 60  # Maximum delay in seconds between retries

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

def acquire_account_with_retries():
    """
    Acquire an account from the account pool with retries
    """
    retries = 0
    task_id = str(uuid.uuid4())

    while retries < MAX_ACCOUNT_ACQUIRE_RETRIES:
        account_id, role_arn, success = acquire_account(task_id)

        if success and account_id and role_arn:
            logger.info(f"Successfully acquired account {account_id} after {retries} retries")
            return account_id, role_arn, task_id

        retries += 1

        # Calculate delay with exponential backoff and jitter
        delay = min(ACCOUNT_ACQUIRE_RETRY_DELAY_BASE * (2 ** (retries // 3)), ACCOUNT_ACQUIRE_RETRY_DELAY_MAX)
        delay = delay * (0.5 + random.random())  # Add jitter

        logger.info(f"No accounts available. Retry {retries}/{MAX_ACCOUNT_ACQUIRE_RETRIES} in {delay:.1f} seconds...")
        time.sleep(delay)

    logger.error(f"Failed to acquire an account after {MAX_ACCOUNT_ACQUIRE_RETRIES} retries")
    return None, None, None

def acquire_account(task_id):
    """
    Acquire an account from the account pool
    """
    if not ACCOUNT_MANAGER_FUNCTION_ARN:
        logger.error("Account manager function ARN not provided")
        return None, None, False

    try:
        response = lambda_client.invoke(
            FunctionName=ACCOUNT_MANAGER_FUNCTION_ARN,
            InvocationType='RequestResponse',
            Payload=json.dumps({
                'operation': 'acquire_account',
                'task_id': task_id
            })
        )

        payload = json.loads(response['Payload'].read().decode())

        if payload.get('statusCode') == 404:
            # No accounts available
            logger.info("No accounts available in the pool")
            return None, None, False

        if payload.get('statusCode') != 200:
            logger.error(f"Failed to acquire account: {payload}")
            return None, None, False

        body = json.loads(payload['body'])
        return body.get('account_id'), body.get('role_arn'), True

    except Exception as e:
        logger.error(f"Error acquiring account: {str(e)}")
        return None, None, False

def release_account(account_id, task_id):
    """
    Release an account back to the pool
    """
    if not account_id or not task_id or not ACCOUNT_MANAGER_FUNCTION_ARN:
        logger.warning("Missing required parameters for releasing account")
        return False

    try:
        response = lambda_client.invoke(
            FunctionName=ACCOUNT_MANAGER_FUNCTION_ARN,
            InvocationType='RequestResponse',
            Payload=json.dumps({
                'operation': 'release_account',
                'account_id': account_id,
                'task_id': task_id
            })
        )

        payload = json.loads(response['Payload'].read().decode())
        logger.info(f"Account release response: {payload}")

        if payload.get('statusCode') != 200:
            logger.error(f"Failed to release account: {payload}")
            return False

        return True

    except Exception as e:
        logger.error(f"Error releasing account: {str(e)}")
        return False

def upload_result_to_s3(issue_number, issue_id, repo_name):
    """
    Upload the GitHub issue reproduction code as a zip file to S3
    """
    if not RESULTS_BUCKET:
        logger.error("Results bucket not provided")
        return None

    # Define paths and file names
    source_dir = f"/app/gh_issues/gh_issue_{issue_number}"
    zip_file_name = f"{issue_id}_results.zip"
    temp_zip_path = f"/tmp/{zip_file_name}"
    s3_directory = f"{issue_id}/"
    s3_zip_key = f"{s3_directory}{zip_file_name}"
    s3_summary_key = f"{s3_directory}summary.txt"

    # Create summary content
    summary_content = f"""
    GitHub Issue Processing Results
    ------------------------------
    Issue ID: {issue_id}
    Repository: {repo_name}
    Processed at: {time.strftime('%Y-%m-%d %H:%M:%S')}

    The zip file contains a CDK application that reproduces the issue.
    """

    try:
        # Check if source directory exists
        if not os.path.exists(source_dir):
            logger.error(f"Source directory not found: {source_dir}")
            return None

        # Create zip file of the directory
        logger.info(f"Creating zip file of directory: {source_dir}")
        shutil.make_archive(
            base_name=temp_zip_path.replace('.zip', ''),  # remove .zip as make_archive adds it
            format='zip',
            root_dir=os.path.dirname(source_dir),
            base_dir=os.path.basename(source_dir)
        )

        # Upload the zip file to S3
        logger.info(f"Uploading zip file to S3: {s3_zip_key}")
        with open(temp_zip_path, 'rb') as zip_file:
            s3_client.put_object(
                Bucket=RESULTS_BUCKET,
                Key=s3_zip_key,
                Body=zip_file.read(),
                ContentType='application/zip'
            )

        # Upload the summary file to S3
        logger.info(f"Uploading summary file to S3: {s3_summary_key}")
        s3_client.put_object(
            Bucket=RESULTS_BUCKET,
            Key=s3_summary_key,
            Body=summary_content,
            ContentType='text/plain'
        )

        # Clean up the temporary zip file
        os.remove(temp_zip_path)
        logger.info(f"Removed temporary zip file: {temp_zip_path}")

        # Generate a presigned URL for the zip file that expires in 7 days
        url = s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': RESULTS_BUCKET, 'Key': s3_zip_key},
            ExpiresIn=604800  # 7 days in seconds
        )

        logger.info(f"Successfully uploaded results to S3: {s3_zip_key}")
        return url

    except Exception as e:
        logger.error(f"Error uploading results to S3: {str(e)}")
        return None

def add_github_comment(repo_name, issue_number, result_url):
    """
    Add a comment to a GitHub issue
    """
    token = get_github_token()
    if not token:
        logger.error("GitHub token not available")
        return False

    try:
        g = Github(token)
        repo = g.get_repo(repo_name)
        issue = repo.get_issue(int(issue_number))

        comment = f"""
        ## Issue Processing Complete

        This issue has been processed successfully.

        You can view the processing results here: [Results]({result_url})

        *This is an automated message from the GitHub Issue Processor.*
        """

        issue.create_comment(comment)
        logger.info(f"Successfully added comment to {repo_name}#{issue_number}")
        return True

    except Exception as e:
        logger.error(f"Error adding GitHub comment: {str(e)}")
        return False

def receive_message():
    """
    Receive a message from the SQS queue
    """
    if not QUEUE_URL:
        logger.error("Queue URL not provided")
        return None, None

    try:
        response = sqs_client.receive_message(
            QueueUrl=QUEUE_URL,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=20,  # Long polling
            AttributeNames=['All'],
            MessageAttributeNames=['All']
        )

        if 'Messages' not in response:
            logger.info("No messages available in the queue")
            return None, None

        message = response['Messages'][0]
        receipt_handle = message['ReceiptHandle']

        logger.info(f"Received message: {message['MessageId']}")
        return json.loads(message['Body']), receipt_handle

    except Exception as e:
        logger.error(f"Error receiving message from queue: {str(e)}")
        return None, None

def delete_message(receipt_handle, message_id=None):
    """
    Delete a message from the SQS queue
    """
    if not receipt_handle or not QUEUE_URL:
        logger.warning("Missing required parameters for deleting message")
        return False

    try:
        sqs_client.delete_message(
            QueueUrl=QUEUE_URL,
            ReceiptHandle=receipt_handle
        )

        logger.info("Successfully deleted message from queue")

        # Publish an event to EventBridge for scaling
        if message_id:
            try:
                events_client.put_events(
                    Entries=[
                        {
                            'Source': 'custom.githubIssueProcessor',
                            'DetailType': 'MessageRemovedFromQueue',
                            'Detail': json.dumps({
                                'queue_url': QUEUE_URL,
                                'message_id': message_id,
                                'timestamp': int(time.time())
                            }),
                            'EventBusName': EVENT_BUS_NAME
                        }
                    ]
                )
                logger.info(f"Published MessageRemovedFromQueue event for message {message_id}")
            except Exception as e:
                logger.error(f"Error publishing event: {str(e)}")
                # Don't fail the operation if event publishing fails

        return True

    except Exception as e:
        logger.error(f"Error deleting message from queue: {str(e)}")
        return False

def process_issue(message, receipt_handle):
    """
    Process a GitHub issue
    """
    try:
        # Extract issue details from the message
        issue_number = message.get('issue_number')
        repo_name = message.get('repository')
        message_id = None

        # Try to get the message ID from the receipt handle (for EventBridge event)
        try:
            response = sqs_client.list_queue_tags(
                QueueUrl=QUEUE_URL
            )
            # The receipt handle doesn't contain the message ID, so we'll have to use a placeholder
            message_id = f"msg-{int(time.time())}"
        except Exception:
            pass

        if not repo_name or not issue_number:
            logger.error(f"Invalid message format: {message}")
            return False

        issue_id = f"{repo_name}#{issue_number}"
        logger.info(f"Processing issue {issue_id}")

        # Acquire an account with retries
        logger.info("Attempting to acquire an account...")
        account_id, role_arn, task_id = acquire_account_with_retries()

        if not account_id or not role_arn:
            logger.error("Failed to acquire an account after multiple retries")
            return False

        logger.info(f"Acquired account {account_id} with role {role_arn}")

        try:
            # Create an event loop and run the async process function to completion
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(process(issue_number, repo_name, role_arn, get_github_token()))
                logger.info("Successfully processed issue %s", issue_id)
            except Exception as e:
                logger.error(f"Process function failed with error: {str(e)}")
                if hasattr(e, '__traceback__'):
                    import traceback
                    logger.error("Full traceback:\n%s", ''.join(traceback.format_tb(e.__traceback__)))
                raise  # Re-raise the exception to mark the task as failed
            finally:
                loop.close()

            # Upload result to S3
            result_url = upload_result_to_s3(issue_number, issue_id, repo_name)
            if not result_url:
                logger.error("Failed to upload result to S3")
                return False

            logger.info(f"Result URL: {result_url}")

            # Add comment to GitHub issue
            success = add_github_comment(repo_name, issue_number, result_url)
            if not success:
                logger.error("Failed to add comment to GitHub issue")
                return False

            # Delete the message from the queue only after successful processing
            if delete_message(receipt_handle, message_id):
                logger.info(f"Successfully processed issue {issue_id} and deleted message")
                return True
            else:
                logger.error("Failed to delete message from queue")
                return False

        finally:
            # Always release the account
            if account_id and task_id:
                release_account(account_id, task_id)
                logger.info(f"Released account {account_id}")

    except Exception as e:
        logger.error(f"Error processing issue: {str(e)}")
        return False

def main():
    """
    Main function - Process a single message and exit
    """
    logger.info("Starting GitHub Issue Processor")

    # Check environment variables
    if not ACCOUNT_MANAGER_FUNCTION_ARN:
        logger.error("ACCOUNT_MANAGER_FUNCTION_ARN environment variable not set")
        return 1

    if not RESULTS_BUCKET:
        logger.error("RESULTS_BUCKET environment variable not set")
        return 1

    if not GITHUB_TOKEN_SECRET_ARN:
        logger.error("GITHUB_TOKEN_SECRET_ARN environment variable not set")
        return 1

    if not QUEUE_URL:
        logger.error("QUEUE_URL environment variable not set")
        return 1

    # Process a single message
    message, receipt_handle = receive_message()
    if message:
        success = process_issue(message, receipt_handle)
        return 0 if success else 1
    else:
        logger.info("No messages to process")
        return 0

if __name__ == "__main__":
    sys.exit(main())