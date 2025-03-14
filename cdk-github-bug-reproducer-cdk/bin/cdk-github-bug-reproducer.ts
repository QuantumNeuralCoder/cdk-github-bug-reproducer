#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { CdkGithubBugReproducerStack } from '../lib/cdk-github-bug-reproducer-stack';
import { AlarmPeriodAspect } from '../lib/alarm-period-aspect';

const app = new cdk.App();

// Get GitHub token and repo from context if provided
const githubToken = app.node.tryGetContext('github-token');
const githubRepo = app.node.tryGetContext('github-repo');

const stack = new CdkGithubBugReproducerStack(app, 'CdkGithubBugReproducerStack', {
  githubToken: githubToken,
  githubRepo: githubRepo
});

// Apply the alarm period aspect to the stack
cdk.Aspects.of(stack).add(new AlarmPeriodAspect());

// Log that the aspect has been applied
console.log('Applied AlarmPeriodAspect to set all CloudWatch alarm periods to 30 seconds');