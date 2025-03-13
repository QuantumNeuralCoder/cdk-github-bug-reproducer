#!/bin/bash

echo "🚀 Setting up the AWS CDK Issue Environment..."

# Ensure AWS CLI is configured
if ! aws sts get-caller-identity >/dev/null 2>&1; then
    echo "❌ AWS credentials not found. Ensure ~/.aws is mounted."
    exit 1
fi

# Validate environment variables
if [ -z "$GITHUB_TOKEN" ]; then
    echo "❌ ERROR: GITHUB_TOKEN is not set! Pass it as an environment variable."
    exit 1
fi

if [ -z "$ISSUE_NUMBER" ]; then
    echo "❌ ERROR: ISSUE_NUMBER environment variable is not set!"
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

# Define S3 bucket
S3_BUCKET="cdkgithubbugreproducerstac-cdkissuesbucket1dde9f2a-sysnvpcukvfo"
ISSUE_METADATA_FILE="s3://$S3_BUCKET/issues/$ISSUE_NUMBER.txt"

echo "🔹 Retrieving issue metadata from $ISSUE_METADATA_FILE..."
aws s3 cp "$ISSUE_METADATA_FILE" "/tmp/$ISSUE_NUMBER.txt" --quiet

if [ $? -ne 0 ]; then
    echo "❌ ERROR: Could not retrieve issue metadata file $ISSUE_METADATA_FILE."
    exit 1
fi

# Function to extract the "Last Known Working CDK Version" from the metadata file
extract_cdk_version() {
    local metadata_file=$1
    local cdk_version=$(grep -i "Last Known Working CDK Version" "$metadata_file" | awk -F': ' '{print $2}' | tr -d '[:space:]')
    echo "$cdk_version"
}

# Extract the CDK version from the metadata file
CDK_VERSION=$(extract_cdk_version "/tmp/$ISSUE_NUMBER.txt")

# If version is missing, "No response", or invalid, default to latest
if [[ -z "$CDK_VERSION" || "$CDK_VERSION" == "No response" || ! "$CDK_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "⚠️ WARNING: 'Last Known Working CDK Version' not found or invalid. Installing latest CDK version."
    CDK_VERSION="latest"
fi

echo "🔹 Installing aws-cdk-lib version: $CDK_VERSION..."

# Directory to install the specified CDK version
CDK_DIR="/workspace/cdk-env"
mkdir -p "$CDK_DIR"
cd "$CDK_DIR" || exit

# Initialize a new Node.js project
npm init -y

# Install the appropriate version of aws-cdk-lib
if [[ "$CDK_VERSION" == "latest" ]]; then
    npm install aws-cdk-lib  # Installs the latest version
else
    npm install "aws-cdk-lib@$CDK_VERSION"
fi

if [ $? -ne 0 ]; then
    echo "❌ ERROR: Failed to install aws-cdk-lib version $CDK_VERSION."
    exit 1
fi

echo "✅ Successfully installed aws-cdk-lib@$CDK_VERSION in $CDK_DIR."

exec /bin/bash  # Keep container open for interactive debugging
