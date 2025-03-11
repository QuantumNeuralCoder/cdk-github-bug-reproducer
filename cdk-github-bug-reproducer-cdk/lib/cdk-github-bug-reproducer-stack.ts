import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';

export class CdkGithubBugReproducerStack extends cdk.Stack {
    constructor(scope: Construct, id: string, props?: cdk.StackProps) {
        super(scope, id, props);

        // S3 Bucket to store CDK artifacts
        const bucket = new s3.Bucket(this, 'CdkIssuesBucket', {
            removalPolicy: cdk.RemovalPolicy.RETAIN,
        });

        // IAM Role for Lambda Execution
        const lambdaRole = new iam.Role(this, 'LambdaExecutionRole', {
            assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
        });
        
        lambdaRole.addManagedPolicy(iam.ManagedPolicy.fromAwsManagedPolicyName("service-role/AWSLambdaBasicExecutionRole"));
        lambdaRole.addManagedPolicy(iam.ManagedPolicy.fromAwsManagedPolicyName("AmazonS3FullAccess"));

        // Lambda Function
        const issueProcessorLambda = new lambda.Function(this, 'IssueProcessorLambda', {
            runtime: lambda.Runtime.PYTHON_3_9,
            handler: 'issue_processor.lambda_handler',
            code: lambda.Code.fromAsset('../lambda'), // Assumes lambda code is inside `lambda/`
            role: lambdaRole,
            environment: {
                S3_BUCKET: bucket.bucketName,
                GITHUB_TOKEN: process.env.GITHUB_TOKEN || '',
            },
        });

        // API Gateway for GitHub Webhook
        const api = new apigateway.RestApi(this, 'GithubWebhookAPI', {
            restApiName: 'Github Issue Processor Webhook',
        });

        const webhookIntegration = new apigateway.LambdaIntegration(issueProcessorLambda);
        api.root.addMethod('POST', webhookIntegration);

        // Outputs
        new cdk.CfnOutput(this, 'APIGatewayURL', {
            value: api.url,
        });
    }
}
