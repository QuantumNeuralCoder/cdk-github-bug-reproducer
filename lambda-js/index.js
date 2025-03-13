import { CodeBuildClient, StartBuildCommand } from "@aws-sdk/client-codebuild";
import { Octokit } from "@octokit/rest";

const codebuild = new CodeBuildClient();
const octokit = new Octokit({ auth: process.env.GITHUB_TOKEN });

const AWS_ACCOUNT_ID = process.env.AWS_ACCOUNT_ID;
const AWS_REGION = process.env.AWS_REGION || "us-east-1";
const CODEBUILD_PROJECT_NAME = process.env.CODEBUILD_PROJECT_NAME;
const REPO_PREFIX = process.env.REPO_PREFIX;
const BUILDSPEC_BUCKET_NAME = process.env.BUILDSPEC_BUCKET_NAME;

export async function handler(event) {
    console.log("Received event:", JSON.stringify(event, null, 2));

    const body = JSON.parse(event.body || "{}");
    const issue = body.issue;

    if (!issue) {
        console.error("❌ ERROR: Missing 'issue' field in payload");
        return { statusCode: 400, body: "Invalid payload: Missing 'issue' field" };
    }

    const issueNumber = issue.number;
    console.log(`🔹 Processing GitHub Issue #${issueNumber}`);

    const imageTag = `${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${REPO_PREFIX}-${issueNumber}:latest`;

    // ✅ Trigger AWS CodeBuild using `buildspec.yml` in S3
    const buildParams = {
        projectName: CODEBUILD_PROJECT_NAME,
        sourceVersion: `s3://${BUILDSPEC_BUCKET_NAME}/buildspecs/buildspec.yml`,
        environmentVariablesOverride: [
            { name: "ISSUE_NUMBER", value: String(issueNumber), type: "PLAINTEXT" },
            { name: "IMAGE_TAG", value: imageTag, type: "PLAINTEXT" },
            { name: "ISSUE_METADATA", value: JSON.stringify(issue), type: "PLAINTEXT" },
        ],
    };

    console.log(`🚀 Starting CodeBuild for Issue #${issueNumber}...`);
    await codebuild.send(new StartBuildCommand(buildParams));

    return { statusCode: 200, body: `CodeBuild started for Issue #${issueNumber}` };
}
