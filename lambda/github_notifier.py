import os
from github import Github

def post_github_comment(issue_number, comment):
    """Posts a comment on the GitHub issue."""
    github_token = os.getenv("GITHUB_TOKEN")
    g = Github(github_token)
    repo = g.get_repo("aws/aws-cdk")
    issue = repo.get_issue(number=issue_number)
    issue.create_comment(comment)
