#!/usr/bin/env python3
"""
GitHub Issue Migration Utility

This script migrates issues from one GitHub repository to another.
It creates new issues in the destination repository based on issues from the source repository.

Usage:
    python issue_migrator.py --token YOUR_GITHUB_TOKEN --source owner/repo --dest owner/repo --issues 1,2,3

Requirements:
    - requests
    - argparse
"""

import argparse
import json
import sys
import time
from typing import List, Dict, Any
import requests


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description='Migrate GitHub issues between repositories')
    parser.add_argument('--token', required=True, help='GitHub personal access token')
    parser.add_argument('--source', required=True, help='Source repository in format owner/repo')
    parser.add_argument('--dest', required=True, help='Destination repository in format owner/repo')
    parser.add_argument('--issues', required=True, help='Comma-separated list of issue numbers to migrate')
    parser.add_argument('--dry-run', action='store_true', help='Perform a dry run without creating issues')
    parser.add_argument('--add-reference', action='store_true', help='Add reference to original issue in the new issue')
    parser.add_argument('--include-comments', action='store_true', help='Include comments from original issues')

    return parser.parse_args()


def validate_repo_format(repo: str) -> bool:
    """Validate repository format (owner/repo)."""
    parts = repo.split('/')
    return len(parts) == 2 and all(parts)


def get_issue(token: str, repo: str, issue_number: int) -> Dict[str, Any]:
    """Fetch an issue from GitHub."""
    url = f"https://api.github.com/repos/{repo}/issues/{issue_number}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }

    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        return response.json()
    else:
        print(f"Error fetching issue #{issue_number} from {repo}: {response.status_code}")
        print(response.text)
        return {}


def get_issue_comments(token: str, repo: str, issue_number: int) -> List[Dict[str, Any]]:
    """Fetch comments for an issue from GitHub."""
    url = f"https://api.github.com/repos/{repo}/issues/{issue_number}/comments"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }

    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        return response.json()
    else:
        print(f"Error fetching comments for issue #{issue_number} from {repo}: {response.status_code}")
        print(response.text)
        return []


def create_issue(token: str, repo: str, title: str, body: str, labels: List[str]) -> Dict[str, Any]:
    """Create a new issue in the destination repository."""
    url = f"https://api.github.com/repos/{repo}/issues"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json"
    }

    payload = {
        "title": title,
        "body": body,
        "labels": labels
    }

    response = requests.post(url, headers=headers, data=json.dumps(payload))

    if response.status_code == 201:
        return response.json()
    else:
        print(f"Error creating issue in {repo}: {response.status_code}")
        print(response.text)
        return {}


def add_comment(token: str, repo: str, issue_number: int, body: str) -> Dict[str, Any]:
    """Add a comment to an issue."""
    url = f"https://api.github.com/repos/{repo}/issues/{issue_number}/comments"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json"
    }

    payload = {
        "body": body
    }

    response = requests.post(url, headers=headers, data=json.dumps(payload))

    if response.status_code == 201:
        return response.json()
    else:
        print(f"Error adding comment to issue #{issue_number} in {repo}: {response.status_code}")
        print(response.text)
        return {}


def format_comment_body(comment: Dict[str, Any]) -> str:
    """Format a comment for inclusion in the new issue."""
    created_at = comment.get('created_at', 'Unknown date')
    user = comment.get('user', {}).get('login', 'Unknown user')
    body = comment.get('body', '')

    return f"""
### Comment by @{user} on {created_at}

{body}

---
"""


def migrate_issues(args):
    """Migrate issues from source to destination repository."""
    if not validate_repo_format(args.source) or not validate_repo_format(args.dest):
        print("Error: Repository format should be 'owner/repo'")
        sys.exit(1)

    try:
        issue_numbers = [int(i.strip()) for i in args.issues.split(',')]
    except ValueError:
        print("Error: Issue numbers should be comma-separated integers")
        sys.exit(1)

    print(f"Migrating {len(issue_numbers)} issues from {args.source} to {args.dest}")

    if args.dry_run:
        print("DRY RUN: No issues will be created")

    for issue_number in issue_numbers:
        print(f"\nProcessing issue #{issue_number}...")

        # Get the original issue
        issue = get_issue(args.token, args.source, issue_number)
        if not issue:
            print(f"Skipping issue #{issue_number} - could not fetch")
            continue

        # Prepare the new issue
        title = issue.get('title', f"Issue #{issue_number} from {args.source}")

        # Prepare the body with attribution
        original_body = issue.get('body', '')
        original_user = issue.get('user', {}).get('login', 'Unknown')
        original_url = issue.get('html_url', '')
        created_at = issue.get('created_at', 'Unknown date')

        body = f"""
**Original issue by @{original_user} on {created_at}**

{original_body}
"""

        if args.add_reference:
            body += f"\n\n---\nOriginal issue: {original_url}"

        # Include comments if requested
        if args.include_comments:
            comments = get_issue_comments(args.token, args.source, issue_number)
            if comments:
                body += "\n\n## Original Comments\n"
                for comment in comments:
                    body += format_comment_body(comment)

        # Get labels and ensure 'bug' is included
        labels = [label.get('name') for label in issue.get('labels', [])]
        if 'bug' not in labels:
            labels.append('bug')

        if args.dry_run:
            print(f"Would create issue '{title}' in {args.dest} with {len(labels)} labels")
            continue

        # Create the new issue
        new_issue = create_issue(args.token, args.dest, title, body, labels)
        if new_issue:
            print(f"Created issue #{new_issue.get('number')} in {args.dest}: {new_issue.get('html_url')}")

            # Add a rate limit delay to avoid hitting GitHub API limits
            time.sleep(1)
        else:
            print(f"Failed to create issue for {args.source}#{issue_number}")


if __name__ == "__main__":
    args = parse_arguments()
    migrate_issues(args)