import os
import boto3

def upload_to_s3(issue_number, local_path):
    """Uploads the CDK app and synthesized templates to S3."""
    s3_client = boto3.client("s3")
    s3_bucket = os.getenv("S3_BUCKET")
    s3_key = f"cdk-issues/{issue_number}/"
    
    for root, _, files in os.walk(local_path):
        for file in files:
            file_path = os.path.join(root, file)
            relative_path = os.path.relpath(file_path, local_path)
            s3_client.upload_file(file_path, s3_bucket, s3_key + relative_path)
    
    return f"https://{s3_bucket}.s3.amazonaws.com/{s3_key}"