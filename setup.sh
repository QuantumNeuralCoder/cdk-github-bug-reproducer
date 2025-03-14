#!/bin/bash

echo "🚀 Setting up the AWS CDK Issue Environment..."

# Ensure AWS CLI is configured
if ! aws sts get-caller-identity >/dev/null 2>&1; then
    echo "❌ AWS credentials not found. Ensure ~/.aws is mounted."
    exit 1
fi

# Create necessary directories
mkdir -p /workspace/aws-cdk /workspace/issue-app /workspace/cdk-env

# Clone AWS CDK Repository
if [ ! -d "/workspace/aws-cdk/.git" ]; then
    echo "🔹 Cloning AWS CDK repository..."
    git clone https://github.com/aws/aws-cdk.git /workspace/aws-cdk
else
    echo "✅ AWS CDK repository already exists."
fi

# Validate issue number
if [ -z "$ISSUE_NUMBER" ]; then
    echo "❌ ERROR: ISSUE_NUMBER environment variable is not set!"
    exit 1
fi

# Define S3 bucket
S3_BUCKET="cdkgithubbugreproducerstac-cdkissuesbucket1dde9f2a-sysnvpcukvfo"
ISSUE_METADATA_FILE="s3://$S3_BUCKET/issues/$ISSUE_NUMBER.txt"

echo "🔹  from $ISSUE_METADATA_FILE..."
aws s3 cp "$ISSUE_METADATA_FILE" "/tmp/$ISSUE_NUMBER.txt" --quiet

if [ $? -ne 0 ]; then
    echo "❌ ERROR: Could not retrieve issue metadata file $ISSUE_METADATA_FILE."
    exit 1
fi

# Read the file to extract the actual ZIP file path
APP_S3_PATH=$(cat "/tmp/$ISSUE_NUMBER.txt" | tr -d '[:space:]')  # Remove spaces/newlines

if [ -z "$APP_S3_PATH" ]; then
    echo "❌ ERROR: Issue metadata file is empty. No app found for this issue."
    exit 1
fi

echo "🔹 App ZIP file found at: $APP_S3_PATH"

# Download and extract the app
echo "🔹 Downloading app from $APP_S3_PATH..."
aws s3 cp "$APP_S3_PATH" "/workspace/issue-app/app.zip" --quiet

if [ $? -ne 0 ]; then
    echo "❌ ERROR: Failed to download app from $APP_S3_PATH."
    exit 1
fi

echo "🔹 Extracting app..."
cd /workspace/issue-app
unzip -o app.zip && rm app.zip

# Fetch issue details from GitHub (Assuming the issue ID is passed as an env variable)
echo "🔹 Fetching last known working CDK version from GitHub Issue #$ISSUE_NUMBER..."
LATEST_CDK_VERSION=$(curl -s -H "Authorization: token $GITHUB_TOKEN" \
    "https://api.github.com/repos/QuantumNeuralCoder/cdk-github-bug-reproducer/issues/$ISSUE_NUMBER" | \
    jq -r '.body' | grep -oP 'aws-cdk-lib@\K[0-9]+\.[0-9]+\.[0-9]+' || echo "latest")

echo "🛠️ Installing AWS CDK version $LATEST_CDK_VERSION"
cd /workspace/cdk-env
npm install aws-cdk-lib@$LATEST_CDK_VERSION

echo "✅ Issue environment setup complete!"
exec /bin/bash  # Keep container open for interactive debugging
