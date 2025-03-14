import * as cdk from 'aws-cdk-lib';
import { IConstruct } from 'constructs';
import { IAspect } from 'aws-cdk-lib';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';

/**
 * CDK Aspect that changes the period of all CloudWatch alarms to 30 seconds
 */
export class AlarmPeriodAspect implements IAspect {
  public visit(node: IConstruct): void {
    // Check if the node is a CloudWatch alarm
    if (node instanceof cloudwatch.CfnAlarm) {
      console.log(`Setting period to 30 seconds for alarm: ${node.node.path}`);

      // Set the period to 30 seconds
      node.period = 30;
    }
  }
}