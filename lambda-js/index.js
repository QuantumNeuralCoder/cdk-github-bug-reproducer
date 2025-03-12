import { S3Client, PutObjectCommand } from "@aws-sdk/client-s3";
import { Octokit } from "@octokit/rest";

const s3 = new S3Client();
const octokit = new Octokit({ auth: process.env.GITHUB_TOKEN });

export async function handler(event) {
    console.log("Received event:", JSON.stringify(event, null, 2));

    // Ensure API Gateway event body is parsed correctly
    let body;
    if (event.body) {
        try {
            body = JSON.parse(event.body);  // 🔥 FIX: Parse event.body
        } catch (error) {
            console.error("❌ Error parsing JSON body:", error);
            return { statusCode: 400, body: "Invalid JSON format" };
        }
    } else {
        body = event;  // If directly called without API Gateway
    }

    // ✅ FIX: Correctly access the issue object
    const issue = body.issue;  

    if (!issue) {
        console.error("❌ Error: Missing 'issue' field in payload");
        return { statusCode: 400, body: "Invalid payload: Missing 'issue' field" };
    }

    console.log("✅ Parsed issue:", issue);

    // Upload dummy file to S3
    const uploadParams = {
        Bucket: process.env.S3_BUCKET,
        Key: `issues/${issue.number}.txt`,
        Body: JSON.stringify(issue)
    };

    await s3.send(new PutObjectCommand(uploadParams));

    // Post comment to GitHub
    await octokit.issues.createComment({
        owner: "QuantumNeuralCoder",
        repo: "cdk-github-bug-reproducer",
        issue_number: issue.number,
        body: `Issue processed and uploaded to S3: ${uploadParams.Key}`
    });

    return { statusCode: 200, body: "Issue processed successfully!" };
}
