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
export class CdkGithubBugReproducerStack extends cdk.Stack {
    constructor(scope: Construct, id: string, props?: cdk.StackProps) {
        super(scope, id, props);

        const AWS_REGION = "us-east-1";
        const AWS_ACCOUNT_ID = cdk.Stack.of(this).account;
        const REPO_PREFIX = "cdk-debug-env";

        // ✅ Create S3 bucket for storing buildspec.yml
        const buildspecBucket = new s3.Bucket(this, "BuildspecBucket", {
            removalPolicy: cdk.RemovalPolicy.RETAIN, // Prevent accidental deletion
            blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
            autoDeleteObjects: false, // Change to true if you want to delete files on stack deletion
        });

        // ✅ Upload `buildspec.yml` to S3
        new s3deploy.BucketDeployment(this, 'DeployBuildspec', {
            sources: [s3deploy.Source.asset(path.join(__dirname, '../buildspecs'))], // Only this file
            destinationBucket: buildspecBucket,
            destinationKeyPrefix: 'buildspecs'
        });
          
        // ✅ IAM Role for CodeBuild
        const codeBuildRole = new iam.Role(this, "CodeBuildServiceRole", {
            assumedBy: new iam.ServicePrincipal("codebuild.amazonaws.com"),
        });

        codeBuildRole.addManagedPolicy(iam.ManagedPolicy.fromAwsManagedPolicyName("AWSCodeBuildAdminAccess"));
        codeBuildRole.addManagedPolicy(iam.ManagedPolicy.fromAwsManagedPolicyName("AmazonEC2ContainerRegistryFullAccess"));

        // ✅ Allow CodeBuild to read from the S3 bucket
        buildspecBucket.grantRead(codeBuildRole);

        // ✅ CodeBuild Project using `buildspec.yml` from S3
        const buildProject = new codebuild.Project(this, "CdkIssueDockerBuild", {
            projectName: "cdk-issue-docker-build",
            role: codeBuildRole,
            source: codebuild.Source.s3({
                bucket: buildspecBucket,
                path: "buildspecs/buildspec.yml", // Path to buildspec in S3
            }),
            environment: {
                buildImage: codebuild.LinuxBuildImage.STANDARD_5_0,
                privileged: true, // Required for Docker builds
                environmentVariables: {
                    "AWS_ACCOUNT_ID": { value: AWS_ACCOUNT_ID },
                    "AWS_REGION": { value: AWS_REGION },
                    "REPO_PREFIX": { value: REPO_PREFIX }
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
                BUILDSPEC_BUCKET_NAME: buildspecBucket.bucketName,
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
        new cdk.CfnOutput(this, "BuildspecS3Bucket", { value: buildspecBucket.bucketName });
    }
}
