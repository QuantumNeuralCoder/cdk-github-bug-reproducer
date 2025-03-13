# Use an AWS CDK compatible Node.js image
FROM public.ecr.aws/docker/library/node:18

# Install system dependencies
RUN apt-get update && apt-get install -y git unzip python3-pip awscli jq

# Set working directory
WORKDIR /workspace

# Install AWS CDK globally
RUN npm install -g aws-cdk

# Copy setup script
COPY setup.sh /workspace/setup.sh
RUN chmod +x /workspace/setup.sh

# Set environment variables (can be overridden)
ENV AWS_REGION=us-east-1
ENV GITHUB_TOKEN=${GITHUB_TOKEN}

# Run setup script on container start
CMD ["./setup.sh"]
