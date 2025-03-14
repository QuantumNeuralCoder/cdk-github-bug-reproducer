#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { CdkGithubBugReproducerStack } from '../lib/cdk-github-bug-reproducer-stack';

const app = new cdk.App();

// Get GitHub token and repo from context if provided
const githubToken = app.node.tryGetContext('github-token');
const githubRepo = app.node.tryGetContext('github-repo');

const stack = new CdkGithubBugReproducerStack(app, 'CdkGithubBugReproducerStack', {
  githubToken: githubToken,
  githubRepo: githubRepo
});