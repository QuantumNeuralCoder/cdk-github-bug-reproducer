import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as codebuild from 'aws-cdk-lib/aws-codebuild';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import { Construct } from 'constructs';
import * as path from 'path';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
export class CdkGithubBugReproducerStack extends cdk.Stack {
    constructor(scope: Construct, id: string, props?: cdk.StackProps) {
        super(scope, id, props);

        const AWS_REGION = "us-east-1";
        const AWS_ACCOUNT_ID = cdk.Stack.of(this).account;
        const REPO_PREFIX = "cdk-debug-env";
        const GITHUB_OWNER = "QuantumNeuralCoder"; // e.g., "QuantumNeuralCoder"
        const GITHUB_REPO = "cdk-github-bug-reproducer"; // e.g., "cdk-github-bug-reproducer"
        const GITHUB_BRANCH = "hackidea4docker"; // Change if needed

  
        // ✅ IAM Role for CodeBuild
        const codeBuildRole = new iam.Role(this, "CodeBuildServiceRole", {
            assumedBy: new iam.ServicePrincipal("codebuild.amazonaws.com"),
        });

        codeBuildRole.addManagedPolicy(iam.ManagedPolicy.fromAwsManagedPolicyName("AWSCodeBuildAdminAccess"));
        codeBuildRole.addManagedPolicy(iam.ManagedPolicy.fromAwsManagedPolicyName("AmazonEC2ContainerRegistryFullAccess"));

        // ✅ Step 1: Store GitHub Token in AWS Secrets Manager
        const githubToken = secretsmanager.Secret.fromSecretNameV2(
            this,
            "GitHubAccessTokenCodebuild",
            "GitHubAccessTokenCodebuild" // Make sure the secret exists in AWS Secrets Manager
        );
  
        new codebuild.GitHubSourceCredentials(this, 'CodeBuildGitHubCreds', {
            accessToken: cdk.SecretValue.secretsManager('GitHubAccessTokenCodebuild'),
          });
        // ✅ CodeBuild Project using GitHub as source
        const buildProject = new codebuild.Project(this, "CdkIssueDockerBuild", {
            projectName: "cdk-issue-docker-build",
            role: codeBuildRole,
            source: codebuild.Source.gitHub({
                owner: GITHUB_OWNER,
                repo: GITHUB_REPO,
                branchOrRef: GITHUB_BRANCH,
                webhook: true, // Automatically trigger on GitHub commits
            }),
            buildSpec: codebuild.BuildSpec.fromSourceFilename("buildspecs/buildspec.yml"), // Uses `buildspec.yml` from GitHub
            environment: {
            buildImage: codebuild.LinuxBuildImage.STANDARD_5_0,
            privileged: true, // Required for Docker builds
            environmentVariables: {
                "AWS_ACCOUNT_ID": { value: process.env.CDK_DEFAULT_ACCOUNT || "" },
                "AWS_REGION": { value: process.env.CDK_DEFAULT_REGION || "us-east-1" },
                "REPO_PREFIX": { value: "cdk-issue-repo" }, // Adjust if needed
            },
            },
        });

        // ✅ IAM Role for Lambda
        const lambdaRole = new iam.Role(this, "LambdaExecutionRole", {
            assumedBy: new iam.ServicePrincipal("lambda.amazonaws.com"),
        });
        
        // Add basic Lambda execution permissions
        lambdaRole.addManagedPolicy(iam.ManagedPolicy.fromAwsManagedPolicyName("service-role/AWSLambdaBasicExecutionRole"));
        
        // Add specific CodeBuild permissions
        lambdaRole.addToPolicy(new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: [
                'codebuild:StartBuild',
                'codebuild:BatchGetBuilds',
                'codebuild:ListBuildsForProject'
            ],
            resources: [buildProject.projectArn] // Only allow actions on your specific project
        }));

        // ✅ Lambda Function
        const issueProcessorLambda = new lambda.Function(this, "IssueProcessorLambda", {
            runtime: lambda.Runtime.NODEJS_18_X,
            handler: "index.handler",
            code: lambda.Code.fromAsset("../lambda-js"),
            role: lambdaRole,
            environment: {
                CODEBUILD_PROJECT_NAME: buildProject.projectName,
                AWS_ACCOUNT_ID: AWS_ACCOUNT_ID,
                REPO_PREFIX: REPO_PREFIX,
            },
        });

        // CloudWatch Log Group for API Gateway
        const logGroup = new logs.LogGroup(this, 'ApiGatewayAccessLogs', {
            removalPolicy: cdk.RemovalPolicy.RETAIN,
        });

        const apiGatewayLogRole = new iam.Role(this, 'ApiGatewayLogRole', {
            assumedBy: new iam.ServicePrincipal('apigateway.amazonaws.com'),
            managedPolicies: [
                iam.ManagedPolicy.fromAwsManagedPolicyName("service-role/AmazonAPIGatewayPushToCloudWatchLogs"),
            ],
        });

        const apiGatewayAccount = new apigateway.CfnAccount(this, 'ApiGatewayAccount', {
            cloudWatchRoleArn: apiGatewayLogRole.roleArn
        });
        

        
        // API Gateway for GitHub Webhook
        const api = new apigateway.RestApi(this, 'GithubWebhookAPI', {
            restApiName: 'Github Issue Processor Webhook',
            deployOptions: {
                loggingLevel: apigateway.MethodLoggingLevel.INFO,
                dataTraceEnabled: true,
                accessLogDestination: new apigateway.LogGroupLogDestination(logGroup),
                accessLogFormat: apigateway.AccessLogFormat.jsonWithStandardFields(),
            },
        });
        api.node.addDependency(apiGatewayLogRole);
        api.node.addDependency(apiGatewayAccount);

        const webhookIntegration = new apigateway.LambdaIntegration(issueProcessorLambda);
        api.root.addMethod("POST", webhookIntegration);

        // ✅ Outputs
        new cdk.CfnOutput(this, "APIGatewayURL", { value: api.url });
        new cdk.CfnOutput(this, "CodeBuildProjectName", { value: buildProject.projectName });
    }
}
