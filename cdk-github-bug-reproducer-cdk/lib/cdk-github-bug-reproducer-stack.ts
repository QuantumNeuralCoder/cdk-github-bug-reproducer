import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
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
        


        // Lambda Function
        // const issueProcessorLambda = new lambda.Function(this, 'IssueProcessorLambda', {
        //     runtime: lambda.Runtime.PYTHON_3_9,
        //     handler: 'issue_processor.lambda_handler',
        //     code: lambda.Code.fromAsset('../lambda/lambda_function.zip'), // Use ZIP instead of folder
        //     role: lambdaRole,
        //     environment: {
        //         S3_BUCKET: bucket.bucketName,
        //         GITHUB_TOKEN: process.env.GITHUB_TOKEN || '',
        //     },
        // });
        const issueProcessorLambda = new lambda.Function(this, 'IssueProcessorLambda', {
            runtime: lambda.Runtime.NODEJS_18_X,
            handler: 'index.handler',
            code: lambda.Code.fromAsset('../lambda-js'),
            environment: {
                S3_BUCKET: bucket.bucketName,
                GITHUB_TOKEN: process.env.GITHUB_TOKEN || '',
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
        api.root.addMethod('POST', webhookIntegration);

        bucket.grantPut(issueProcessorLambda);
        // Outputs
        new cdk.CfnOutput(this, 'APIGatewayURL', {
            value: api.url,
        });
    }
}