import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as organizations from 'aws-cdk-lib/aws-organizations';
import { PythonFunction } from '@aws-cdk/aws-lambda-python-alpha';
import { Construct } from 'constructs';
import * as path from 'path';
import {PolicyStatement, PrincipalBase, PrincipalPolicyFragment} from "aws-cdk-lib/aws-iam";
import {DeploymentType, StackSet, StackSetStack, StackSetTarget, StackSetTemplate} from "cdk-stacksets";
import {CfnOrganization} from "aws-cdk-lib/aws-organizations";
import {Capability} from "cdk-stacksets/lib/stackset";
import {Token} from "aws-cdk-lib";

const ACCOUNT_MANAGER_FUNCTION_NAME = "github-issue-processor-account-manager";

class OrganizationPrincipal extends PrincipalBase {
    /**
     *
     * @param organizationId The unique identifier (ID) of an organization (i.e. o-12345abcde)
     */
    constructor(public readonly organizationId: string) {
        super();
    }

    public get policyFragment(): PrincipalPolicyFragment {
        return new PrincipalPolicyFragment(
            { AWS: ['*'] },
            { StringEquals: { 'aws:PrincipalOrgID': this.organizationId } },
        );
    }

    public toString() {
        return `OrganizationPrincipal(${this.organizationId})`;
    }

    public dedupeString(): string | undefined {
        return `OrganizationPrincipal:${this.organizationId}`;
    }
}

class OrgAccountCommonStack extends StackSetStack{
    constructor(scope: Construct, id: string) {
        super(scope, id);

        const accountManagerFunctionArnParam = new cdk.CfnParameter(this, 'accountManagerFunctionArn', {
            type: 'String',
            description: 'account Manager Function Arn'
        });

        const ghIssueProcessorTaskExecutionRoleArn = new cdk.CfnParameter(this, 'issueProcessorExecutionRoleArn', {
            type: 'String',
            description: 'GH issue processor task execution role Arn'
        });

        // Create a role that can be assumed by the task execution role with Administrator permissions
        const role = new iam.Role(this, 'MyRole', {
            assumedBy: iam.Role.fromRoleArn(this, "gIssueProcessorImportedRole", ghIssueProcessorTaskExecutionRoleArn.valueAsString),
            managedPolicies: [
                iam.ManagedPolicy.fromAwsManagedPolicyName('AdministratorAccess')
            ]
        });

        // Create a custom resource to register/deregister this account with the account manager using inline Python code
        const accountRegistrationHandler = new lambda.Function(this, 'AccountRegistrationHandler', {
            runtime: lambda.Runtime.PYTHON_3_9,
            handler: 'index.lambda_handler',
            architecture: lambda.Architecture.ARM_64,
            timeout: cdk.Duration.minutes(15),
            environment: {
                AWS_ACCOUNT_ID: cdk.Stack.of(this).account,
                ROLE_ARN: role.roleArn,
                ACCOUNT_MANAGER_FUNCTION_ARN: accountManagerFunctionArnParam.valueAsString
            },
            code: lambda.Code.fromInline(`
import json
import os
import boto3
import logging
import urllib.request

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize AWS clients
lambda_client = boto3.client('lambda')

# Environment variables
ACCOUNT_ID = os.environ['AWS_ACCOUNT_ID']
ROLE_ARN = os.environ['ROLE_ARN']
ACCOUNT_MANAGER_FUNCTION_ARN = os.environ['ACCOUNT_MANAGER_FUNCTION_ARN']

def lambda_handler(event, context):
    """
    Custom resource handler for account registration/deregistration
    
    This function is triggered by CloudFormation when:
    - The stack is created (Register account)
    - The stack is updated (Re-register account)
    - The stack is deleted (Deregister account)
    """
    logger.info(f"Received event: {json.dumps(event)}")
    
    request_type = event['RequestType']
    
    try:
        if request_type in ['Create', 'Update']:
            # Register account
            logger.info(f"Registering account {ACCOUNT_ID} with role {ROLE_ARN}")
            
            response = lambda_client.invoke(
                FunctionName=ACCOUNT_MANAGER_FUNCTION_ARN,
                InvocationType='RequestResponse',
                Payload=json.dumps({
                    'operation': 'register_account',
                    'account_id': ACCOUNT_ID,
                    'role_arn': ROLE_ARN
                })
            )
        
            payload = json.loads(response['Payload'].read().decode())
            logger.info(f"Registration response: {payload}")
            
            if payload.get('statusCode') != 200:
                body = json.loads(payload.get('body', '{}'))
                error_message = body.get('error') or body.get('message') or 'Unknown error'
                raise Exception(f"Failed to register account: {error_message}")
            
            send_response(event, context, 'SUCCESS', {'AccountId': ACCOUNT_ID})
            
        elif request_type == 'Delete':
            # Deregister account
            logger.info(f"Deregistering account {ACCOUNT_ID}")
            
            response = lambda_client.invoke(
                FunctionName=ACCOUNT_MANAGER_FUNCTION_ARN,
                InvocationType='RequestResponse',
                Payload=json.dumps({
                    'operation': 'deregister_account',
                    'account_id': ACCOUNT_ID
                })
            )
            
            payload = json.loads(response['Payload'].read().decode())
            logger.info(f"Deregistration response: {payload}")
            
            # Even if deregistration fails, we should still complete the deletion
            send_response(event, context, 'SUCCESS', {'AccountId': ACCOUNT_ID})
            
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        send_response(event, context, 'FAILED', {'Error': str(e)})

def send_response(event, context, response_status, response_data):
    """Send a response to CloudFormation to handle the custom resource"""
    response_body = {
        'Status': response_status,
        'Reason': f'See the details in CloudWatch Log Stream: {context.log_stream_name}',
        'PhysicalResourceId': context.log_stream_name,
        'StackId': event['StackId'],
        'RequestId': event['RequestId'],
        'LogicalResourceId': event['LogicalResourceId'],
        'NoEcho': False,
        'Data': response_data
    }
    
    logger.info(f"Response body: {json.dumps(response_body)}")
    
    response_url = event['ResponseURL']
    
    headers = {
        'Content-Type': '',
        'Content-Length': str(len(json.dumps(response_body)))
    }
    
    req = urllib.request.Request(
        url=response_url,
        data=json.dumps(response_body).encode('utf-8'),
        headers=headers,
        method='PUT'
    )
    
    try:
        with urllib.request.urlopen(req) as response:
            logger.info(f"Status code: {response.status}")
            logger.info(f"Status message: {response.reason}")
    except Exception as e:
        logger.error(f"Error sending response: {str(e)}")
        raise
            `)
        });

        // Grant permission to invoke the account manager function
        lambda.Function.fromFunctionArn(this, 'AccountManagerFunction', accountManagerFunctionArnParam.valueAsString).grantInvoke(accountRegistrationHandler);

        // Create the custom resource that will register/deregister the account
        const accountRegistration = new cdk.CustomResource(this, 'AccountRegistration', {
            serviceToken: accountRegistrationHandler.functionArn,
            properties: {
                version: 1
            }
        });

        // Make sure the custom resource depends on the role
        accountRegistration.node.addDependency(role);
    }
}

export interface CdkGithubBugReproducerStackProps extends cdk.StackProps {
    /**
     * Optional GitHub token to populate the secret.
     * If not provided, you'll need to set it manually after deployment.
     */
    githubToken?: string;

    /**
     * GitHub repository to configure the webhook for (format: owner/repo)
     * If provided, a webhook will be automatically registered
     */
    githubRepo?: string;
}

export class CdkGithubBugReproducerStack extends cdk.Stack {
    private _org_activator_node : cdk.CfnResource;
    private accountEventBus: events.EventBus;
    private accountTable: dynamodb.Table;
    private clusterName?: string;
    private serviceName?: string;
    constructor(scope: Construct, id: string, props?: CdkGithubBugReproducerStackProps) {
        super(scope, id, props);

        // Create IAM role that will be assumed by ECS tasks in member accounts
        const crossAccountRole = new iam.Role(this, 'CrossAccountProcessingRole', {
            assumedBy: new iam.CompositePrincipal(
                new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
                new iam.AccountPrincipal(this.account) // Allow the management account to assume this role
            ),
            description: 'Role to be assumed by ECS tasks for cross-account processing',
            roleName: 'GithubIssueProcessorRole',  // Fixed name for easy reference
            maxSessionDuration: cdk.Duration.hours(3),
        });

        const org = this.createOrg();

        // Add permission to assume roles in the organization
        crossAccountRole.addToPolicy(new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ['sts:AssumeRole'],
            resources: ['arn:aws:iam::*:role/*'],
            conditions: {
                StringEquals: {
                    'aws:PrincipalOrgID': org.attrId
                }
            }
        }));

        const accountManagerLambda = this.createAccountManagementComponent(org);

        this.defineOrgAccountsCommonInfra(org, crossAccountRole, accountManagerLambda);

        // S3 Bucket to store processing results
        const resultsBucket = new s3.Bucket(this, 'GithubIssueResultsBucket', {
            removalPolicy: cdk.RemovalPolicy.RETAIN,
            encryption: s3.BucketEncryption.S3_MANAGED,
            blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
            versioned: true,
        });

        resultsBucket.grantReadWrite(crossAccountRole);

        // Create a secret for GitHub token
        const githubTokenSecret = new secretsmanager.Secret(this, 'GithubTokenSecret', {
            description: 'GitHub API token for issue processing',
            secretName: 'github-issue-processor/github-token',
            ...(props?.githubToken ? { secretStringValue: cdk.SecretValue.unsafePlainText(props.githubToken) } : {})
        });

        // Create a secret for webhook verification
        const webhookSecret = new secretsmanager.Secret(this, 'WebhookSecret', {
            description: 'Secret for GitHub webhook verification',
            secretName: 'github-issue-processor/webhook-secret',
            generateSecretString: {
                passwordLength: 32,
                excludeCharacters: '\\/@"\'',
            }
        });

        // Output the webhook secret ARN for reference
        new cdk.CfnOutput(this, 'WebhookSecretArn', {
            value: webhookSecret.secretArn,
            description: 'ARN of the webhook secret',
        });

        // SQS Queue for GitHub Issues
        const issueQueue = new sqs.Queue(this, 'GithubIssueQueue', {
            visibilityTimeout: cdk.Duration.minutes(5),
            retentionPeriod: cdk.Duration.days(14),
            deadLetterQueue: {
                queue: new sqs.Queue(this, 'GithubIssueDeadLetterQueue', {
                    retentionPeriod: cdk.Duration.days(14),
                }),
                maxReceiveCount: 3,
            },
        });

        // GitHub Webhook Lambda
        const githubWebhookLambda = new PythonFunction(this, 'GithubWebhookLambda', {
            entry: path.join(__dirname, '../lambda/github_webhook'),
            index: 'index.py',
            handler: 'lambda_handler',
            runtime: lambda.Runtime.PYTHON_3_9,
            architecture: lambda.Architecture.ARM_64,
            timeout: cdk.Duration.seconds(30),
            environment: {
                SQS_QUEUE_URL: issueQueue.queueUrl,
                REQUIRED_LABELS: 'bug', // Comma-separated list of labels to filter on
                GITHUB_TOKEN_SECRET_ARN: githubTokenSecret.secretArn,
                WEBHOOK_SECRET_ARN: webhookSecret.secretArn,
                EVENT_BUS_NAME: this.accountEventBus.eventBusName,
            },
        });

        // Grant permissions to the GitHub Webhook Lambda
        issueQueue.grantSendMessages(githubWebhookLambda);
        githubTokenSecret.grantRead(githubWebhookLambda);
        webhookSecret.grantRead(githubWebhookLambda);
        this.accountEventBus.grantPutEventsTo(githubWebhookLambda);

        // Create CloudWatch Logs role for API Gateway
        const apiGatewayLoggingRole = new iam.Role(this, 'ApiGatewayLoggingRole', {
            assumedBy: new iam.ServicePrincipal('apigateway.amazonaws.com'),
            managedPolicies: [
                iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AmazonAPIGatewayPushToCloudWatchLogs')
            ]
        });

        // Set the CloudWatch Logs role ARN in account settings
        const apiGatewayAccountConfig = new apigateway.CfnAccount(this, 'ApiGatewayAccount', {
            cloudWatchRoleArn: apiGatewayLoggingRole.roleArn
        });

        // API Gateway for GitHub Webhook
        const api = new apigateway.RestApi(this, 'GithubWebhookAPI', {
            restApiName: 'Github Issue Processor Webhook',
            deployOptions: {
                stageName: 'prod',
                loggingLevel: apigateway.MethodLoggingLevel.INFO,
                dataTraceEnabled: true,
            },
        });

        api.node.addDependency(apiGatewayAccountConfig);

        const webhookIntegration = new apigateway.LambdaIntegration(githubWebhookLambda);
        api.root.addMethod('POST', webhookIntegration);

        // Create a VPC for ECS
        const vpc = new ec2.Vpc(this, 'ProcessingVpc', {
            maxAzs: 2,
            natGateways: 1,
        });

        // Create ECS Cluster
        const cluster = new ecs.Cluster(this, 'ProcessingCluster', {
            vpc: vpc,
        });
        this.clusterName = cluster.clusterName;

        // Create Fargate Task Definition
        const taskDefinition = new ecs.FargateTaskDefinition(this, 'ProcessingTaskDefinition', {
            memoryLimitMiB: 2048,
            cpu: 1024,
            executionRole: crossAccountRole,
            taskRole: crossAccountRole,
            runtimePlatform: {
                cpuArchitecture: ecs.CpuArchitecture.ARM64,
                operatingSystemFamily: ecs.OperatingSystemFamily.LINUX
            }
        });

        // Add container to the task definition
        taskDefinition.addContainer('ProcessingContainer', {
            image: ecs.ContainerImage.fromAsset(path.join(__dirname, '../lambda/ecs_task'), {
                platform: cdk.aws_ecr_assets.Platform.LINUX_ARM64
            }),
            logging: ecs.LogDrivers.awsLogs({ streamPrefix: 'github-issue-processor' }),
            environment: {
                ACCOUNT_MANAGER_FUNCTION_ARN: accountManagerLambda.functionArn,
                RESULTS_BUCKET: resultsBucket.bucketName,
                GITHUB_TOKEN_SECRET_ARN: githubTokenSecret.secretArn,
                QUEUE_URL: issueQueue.queueUrl,
                EVENT_BUS_NAME: this.accountEventBus.eventBusName
            },
        });

        // Grant permissions to the ECS Task
        accountManagerLambda.grantInvoke(taskDefinition.taskRole);
        resultsBucket.grantReadWrite(taskDefinition.taskRole);
        githubTokenSecret.grantRead(taskDefinition.taskRole);
        issueQueue.grantConsumeMessages(taskDefinition.taskRole);
        this.accountEventBus.grantPutEventsTo(taskDefinition.taskRole);

        // Create Fargate Service
        const service = new ecs.FargateService(this, 'ProcessingService', {
            cluster: cluster,
            taskDefinition: taskDefinition,
            desiredCount: 0, // Start with 0 tasks
            assignPublicIp: false,
            minHealthyPercent: 100,
            maxHealthyPercent: 200,
        });
        this.serviceName = service.serviceName;

        // Create a Lambda function to update ECS scaling based on account count and queue depth
        const ecsScalingUpdaterLambda = new PythonFunction(this, 'EcsScalingUpdaterLambda', {
            entry: path.join(__dirname, '../lambda/ecs_scaling_updater'),
            index: 'index.py',
            handler: 'lambda_handler',
            runtime: lambda.Runtime.PYTHON_3_9,
            architecture: lambda.Architecture.ARM_64,
            timeout: cdk.Duration.minutes(5),
            environment: {
                ACCOUNT_TABLE_NAME: this.accountTable.tableName,
                QUEUE_URL: issueQueue.queueUrl,
                ECS_SERVICE_RESOURCE_ID: this.clusterName && this.serviceName ?
                    `service/${this.clusterName}/${this.serviceName}` :
                    ""
            }
        });

        // Grant permissions to update scaling, read DynamoDB, and access SQS
        this.accountTable.grantReadData(ecsScalingUpdaterLambda);
        issueQueue.grantConsumeMessages(ecsScalingUpdaterLambda);

        // Grant permissions to update ECS service
        ecsScalingUpdaterLambda.addToRolePolicy(new iam.PolicyStatement({
            actions: [
                'ecs:UpdateService',
                'ecs:DescribeServices'
            ],
            resources: ['*']
        }));

        // Create EventBridge rule to trigger the Lambda on account changes and queue message events
        new events.Rule(this, 'TasksScaleEventsRule', {
            eventBus: this.accountEventBus,
            eventPattern: {
                source: ['custom.githubIssueProcessor'],
                detailType: ['AccountRegistered', 'AccountDeregistered', 'MessageAddedToQueue', 'MessageRemovedFromQueue', 'QueueDepthCheck']
            },
            targets: [new targets.LambdaFunction(ecsScalingUpdaterLambda)]
        });

        // Schedule the queue monitor to run every 5 minutes
        new events.Rule(this, 'ScheduledBaseTasksScaleEventsRule', {
            schedule: events.Schedule.rate(cdk.Duration.minutes(5)),
            targets: [new targets.LambdaFunction(ecsScalingUpdaterLambda)]
        });

        // Outputs
        new cdk.CfnOutput(this, 'APIGatewayURL', {
            value: api.url,
            description: 'GitHub Webhook API URL',
        });

        // If GitHub repo is provided, register the webhook automatically
        if (props?.githubRepo && props?.githubToken) {
            // Create a Lambda function to register the webhook
            const webhookRegistratorLambda = new lambda.Function(this, 'WebhookRegistratorLambda', {
                runtime: lambda.Runtime.PYTHON_3_9,
                handler: 'index.lambda_handler',
                code: lambda.Code.fromAsset(path.join(__dirname, '../lambda/github_webhook_registrator')),
                timeout: cdk.Duration.minutes(5)
            });

            // Grant the Lambda function permissions to read secrets
            githubTokenSecret.grantRead(webhookRegistratorLambda);
            webhookSecret.grantRead(webhookRegistratorLambda);

            // Create the custom resource
            const webhookRegistration = new cdk.CustomResource(this, 'WebhookRegistration', {
                serviceToken: webhookRegistratorLambda.functionArn,
                properties: {
                    GitHubRepo: props.githubRepo,
                    WebhookUrl: api.url,
                    GitHubTokenSecretArn: githubTokenSecret.secretArn,
                    WebhookSecretArn: webhookSecret.secretArn
                }
            });

            // Make sure the webhook registration happens after everything else
            webhookRegistration.node.addDependency(api);
            webhookRegistration.node.addDependency(githubTokenSecret);
            webhookRegistration.node.addDependency(githubWebhookLambda);

            // Add output for the webhook ID
            new cdk.CfnOutput(this, 'WebhookId', {
                value: webhookRegistration.getAttString('WebhookId'),
                description: 'GitHub Webhook ID',
            });
        }

        new cdk.CfnOutput(this, 'ResultsBucketName', {
            value: resultsBucket.bucketName,
            description: 'S3 Bucket for storing processing results',
        });

    }

    private createAccountManagementComponent(org: CfnOrganization) {
        // Create an EventBridge event bus for account events
        const accountEventBus = new events.EventBus(this, 'AccountEventBus', {
            eventBusName: 'github-issue-processor-account-events'
        });

        this.accountEventBus = accountEventBus;

        // Account Management Components
        // DynamoDB Table for Account Management
        const accountTable = new dynamodb.Table(this, 'AccountManagementTable', {
            partitionKey: {name: 'account_id', type: dynamodb.AttributeType.STRING},
            billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
            removalPolicy: cdk.RemovalPolicy.DESTROY,
        });

        this.accountTable = accountTable;

        // Add GSI for status-based queries
        accountTable.addGlobalSecondaryIndex({
            indexName: 'status-index',
            partitionKey: {name: 'status', type: dynamodb.AttributeType.STRING},
            sortKey: {name: 'last_updated', type: dynamodb.AttributeType.NUMBER},
        });

        // Account Manager Lambda
        const accountManagerLambda = new PythonFunction(this, 'AccountManagerLambda', {
            entry: path.join(__dirname, '../lambda/account_manager'),
            index: 'index.py',
            handler: 'lambda_handler',
            functionName: ACCOUNT_MANAGER_FUNCTION_NAME,
            runtime: lambda.Runtime.PYTHON_3_9,
            architecture: lambda.Architecture.ARM_64,
            timeout: cdk.Duration.minutes(15),
            environment: {
                ACCOUNT_TABLE_NAME: accountTable.tableName,
                EVENT_BUS_NAME: accountEventBus.eventBusName
            }
        });

        // Grant permission to publish events to EventBridge
        accountEventBus.grantPutEventsTo(accountManagerLambda);
        // Allow all accounts in the org to invoke the Lambda Function
        accountManagerLambda.grantInvoke(new OrganizationPrincipal(org.attrId));
        accountManagerLambda.node.addDependency(this._org_activator_node);

        // Grant permissions to the Account Manager Lambda
        accountTable.grantReadWriteData(accountManagerLambda);

        return accountManagerLambda;
    }

    private defineOrgAccountsCommonInfra(org: CfnOrganization, crossAccountRole: iam.Role, accountManagerLambda: lambda.IFunction) {
        const orgsAccountsCommonStack = new OrgAccountCommonStack(this, "OrgsAccountsCommonStack",);

        const stackSet = new StackSet(this, 'StackSet', {
            stackSetName: 'gh-issue-reproducer-org-common-stack-set',
            target: StackSetTarget.fromOrganizationalUnits({
                regions: ['us-west-2'],
                organizationalUnits: [org.attrRootId],
                excludeAccounts: [org.attrManagementAccountId],
            }),
            deploymentType: DeploymentType.serviceManaged({
                autoDeployEnabled: true,
                autoDeployRetainStacks: false,
                delegatedAdmin: false
            }),
            operationPreferences: {
                failureTolerancePercentage: 20,
                maxConcurrentPercentage: 100,
            },
            capabilities: [Capability.NAMED_IAM],
            template: StackSetTemplate.fromStackSetStack(orgsAccountsCommonStack),
        });
        stackSet.node.findChild('Resource').node.addDependency()
        stackSet.node.findChild('Resource').node.addDependency(this._org_activator_node);
        (stackSet.node.findChild('Resource') as cdk.CfnResource).addPropertyOverride("Parameters", [
            {
                ParameterKey: "accountManagerFunctionArn",
                ParameterValue: accountManagerLambda.functionArn,
            },
            {
                ParameterKey: "issueProcessorExecutionRoleArn",
                ParameterValue: crossAccountRole.roleArn,
            }
        ]);
    }

    private createOrg() {
        // add the organization
        const org = new organizations.CfnOrganization(this, 'GithubReproducerOrg', {
            featureSet: "ALL",
        });

        // custom resource to activate trusted access with AWS Organizations
        const provider = new PythonFunction(this, 'TrustedAccessActivatorProvider', {
            entry: path.join(__dirname, '../lambda/trusted_access_activator'),
            index: 'index.py',
            handler: 'lambda_handler',
            runtime: lambda.Runtime.PYTHON_3_9,
            architecture: lambda.Architecture.ARM_64,
            timeout: cdk.Duration.minutes(5),
            initialPolicy: [
                new PolicyStatement({
                    actions: [
                        'cloudformation:ActivateOrganizationsAccess',
                        'organizations:EnableAWSServiceAccess',
                        'organizations:RegisterDelegatedAdministrator',
                        'iam:GetRole',
                        'iam:CreateServiceLinkedRole',
                        'organizations:*',
                        'sts:GetCallerIdentity'
                    ],
                    resources: ['*'],
                }),
            ],
        });

        const activateTrustedAccess = new cdk.CustomResource(this, 'TrustedAccessActivator', {
            serviceToken: provider.functionArn,
        });

        activateTrustedAccess.node.addDependency(org);
        this._org_activator_node = activateTrustedAccess.node.defaultChild as cdk.CfnResource;

        return org;
    }
}