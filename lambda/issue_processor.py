import os
import json
import boto3
import subprocess
import tempfile
import requests
from github import Github

S3_BUCKET = os.getenv("S3_BUCKET")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

s3_client = boto3.client("s3")

def extract_reproduction_steps(issue_body):
    """Extracts code snippets from GitHub issue description."""
    code_blocks = []
    in_block = False
    block_content = []
    for line in issue_body.split("\n"):
        if line.startswith("```typescript"):
            in_block = True
            block_content = []
        elif line.startswith("```") and in_block:
            in_block = False
            code_blocks.append("\n".join(block_content))
        elif in_block:
            block_content.append(line)
    return code_blocks[0] if code_blocks else None


def create_cdk_app(issue_number, reproduction_code):
    """Creates a CDK app with the provided reproduction code."""
    with tempfile.TemporaryDirectory() as temp_dir:
        app_dir = os.path.join(temp_dir, f"issue-{issue_number}")
        subprocess.run(["cdk", "init", "app", "--language=typescript"], cwd=temp_dir, check=True)
        lib_dir = os.path.join(app_dir, "lib")
        stack_file = os.path.join(lib_dir, "app-stack.ts")
        
        with open(stack_file, "w") as f:
            f.write(reproduction_code)
        
        subprocess.run(["cdk", "synth"], cwd=app_dir, check=True)
        
        return app_dir


def upload_to_s3(issue_number, local_path):
    """Uploads the CDK app and synthesized templates to S3."""
    s3_key = f"cdk-issues/{issue_number}/"
    for root, _, files in os.walk(local_path):
        for file in files:
            file_path = os.path.join(root, file)
            relative_path = os.path.relpath(file_path, local_path)
            s3_client.upload_file(file_path, S3_BUCKET, s3_key + relative_path)
    return f"https://{S3_BUCKET}.s3.amazonaws.com/{s3_key}"


def post_github_comment(issue_number, comment):
    """Posts a comment on the GitHub issue."""
    g = Github(GITHUB_TOKEN)
    repo = g.get_repo("aws/aws-cdk")
    issue = repo.get_issue(number=issue_number)
    issue.create_comment(comment)


def lambda_handler(event, context):
    """Lambda function entry point."""
    body = json.loads(event["body"])
    issue = body.get("issue", {})
    issue_number = issue.get("number")
    issue_body = issue.get("body", "")
    
    if "bug" not in [label["name"] for label in issue.get("labels", [])]:
        return {"statusCode": 200, "body": "Not a bug issue."}
    
    reproduction_code = extract_reproduction_steps(issue_body)
    if not reproduction_code:
        return {"statusCode": 200, "body": "No reproduction steps found."}
    
    cdk_app_path = create_cdk_app(issue_number, reproduction_code)
    s3_url = upload_to_s3(issue_number, cdk_app_path)
    
    comment = f"CDK app and synthesized template uploaded: {s3_url}"
    post_github_comment(issue_number, comment)
    
    return {"statusCode": 200, "body": "Processed successfully."}
